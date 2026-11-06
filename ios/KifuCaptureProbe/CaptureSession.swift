import SwiftUI
import CoreMedia
import CoreVideo
@preconcurrency import ScreenCaptureKit

@MainActor
final class CaptureSession: ObservableObject {
    @Published var report: ProbeReport?
    @Published var status = "開始前"
    @Published var errorMessage: String?
    @Published var busy = false
    @Published var capturing = false
    @Published var sharedJSON: String?
    let profile = ProfileInspection.inspect()
    private var stream: SCStream?
    private var sink: FrameSink?
    private var observer: CaptureObserver?
    private var generation = UUID()
    private var inBackground = false
    private var stopping = false
    private let picker = SCContentSharingPicker.shared
    private var reportURL: URL?

    init() {
        do {
            let directory = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                        appropriateFor: nil, create: true)
            let url = directory.appendingPathComponent("last-capture-probe.json")
            reportURL = url
            if FileManager.default.fileExists(atPath: url.path) {
                let decoder = JSONDecoder()
                decoder.dateDecodingStrategy = .iso8601
                var restored = try decoder.decode(ProbeReport.self, from: Data(contentsOf: url))
                if restored.state == "取得中" {
                    restored.state = "前回の途中保存"
                    restored.message = "前回の処理は終了を記録せず停止しました。保存時点までの診断です。"
                }
                report = restored
            }
        } catch { errorMessage = "診断保存の準備に失敗しました：\(error.localizedDescription)" }
    }

    func presentPicker() {
        guard !busy, let reportURL else { return }
        errorMessage = nil
        sharedJSON = nil
        generation = UUID()
        let initialReport = ProbeReport(schemaVersion: 1, appVersion: "0.1.0", systemVersion: UIDevice.current.systemVersion,
            startedAt: Date(), elapsedSeconds: 0, state: "取得中",
            profileScreenRecordingAllowed: profile.allowed, profileInspection: profile.detail)
        sink = FrameSink(report: initialReport, reportURL: reportURL)
        report = nil
        if profile.allowed == false {
            sink?.finish(state: "権限不足", message: "インストール時の署名プロファイルにScreen Recording権限がありません。無料署名で利用できるかは未確認です。診断を共有してください。")
            refresh()
            status = "署名に必要な権限がありません"
            return
        }
        let delegate = CaptureObserver(session: self, generation: generation)
        observer = delegate
        picker.add(delegate)
        var configuration = SCContentSharingPickerConfiguration()
        configuration.showsMicrophoneControl = false
        configuration.showsCameraControl = false
        picker.defaultConfiguration = configuration
        picker.isActive = true
        busy = true
        status = "画面共有の選択待ち"
        picker.present()
    }

    func start(with filter: SCContentFilter, generation expected: UUID) async {
        guard generation == expected, busy, stream == nil, !stopping, let sink else { return }
        status = "映像取得を開始しています"
        let configuration = SCStreamConfiguration()
        configuration.width = 640
        configuration.height = 1136
        // iOSではフレーム間隔・画素形式を指定できないため、受信側で間引く。
        configuration.capturesAudio = false
        let newStream = SCStream(filter: filter, configuration: configuration, delegate: observer)
        stream = newStream
        sink.onTimeout = { [weak self] in
            Task { @MainActor in await self?.stop(state: "時間上限", message: "120秒の試験を終了しました。") }
        }
        sink.setBackground(inBackground)
        do {
            try newStream.addStreamOutput(sink, type: .screen, sampleHandlerQueue: sink.queue)
            try await newStream.startCapture()
            guard generation == expected, stream === newStream, !stopping else {
                do { try await newStream.stopCapture() }
                catch { errorMessage = "取り消した共有の停止応答：\(Self.errorDescription(error))" }
                return
            }
            capturing = true
            sink.setBackground(inBackground)
            status = "取得中 — 別アプリで動画を20〜30秒再生してください"
        } catch {
            guard generation == expected else { return }
            await stop(state: "開始失敗", message: Self.errorDescription(error))
        }
    }

    func setBackground(_ background: Bool) {
        inBackground = background
        if capturing { sink?.setBackground(background) }
        // 非表示でもSCStreamと専用の受信キューを維持する。
        refresh()
    }

    func refresh() {
        if let sink { report = sink.snapshot() }
        if busy && !stopping, let sink, sink.elapsed >= 120 {
            Task { await stop(state: "時間上限", message: "120秒の試験を終了しました。") }
        }
    }

    func cancel(generation expected: UUID) async {
        guard expected == generation, stream == nil else { return }
        await stop(state: "選択取消", message: "画面共有を開始しませんでした。")
    }

    func fail(_ error: Error, generation expected: UUID) async {
        guard expected == generation else { return }
        await stop(state: "取得停止", message: Self.errorDescription(error))
    }

    func stop(state: String = "終了", message: String? = nil) async {
        guard !stopping else { return }
        stopping = true
        generation = UUID()
        capturing = false
        var detail = message
        if let current = stream {
            stream = nil
            do { try await current.stopCapture() }
            catch { detail = [detail, "停止応答：\(Self.errorDescription(error))"].compactMap { $0 }.joined(separator: " / ") }
        }
        sink?.finish(state: state, message: detail)
        picker.isActive = false
        if let observer { picker.remove(observer) }
        observer = nil
        busy = false
        stopping = false
        status = state
        refresh()
    }

    func prepareShare() {
        refresh()
        guard let report else { return }
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            encoder.dateEncodingStrategy = .iso8601
            sharedJSON = report.text + "\n\n" + String(decoding: try encoder.encode(report), as: UTF8.self)
        } catch { errorMessage = "診断を書き出せませんでした：\(error.localizedDescription)" }
    }

    private static func errorDescription(_ error: Error) -> String {
        let value = error as NSError
        return "\(value.domain) (\(value.code)): \(value.localizedDescription)"
    }
}

final class CaptureObserver: NSObject, SCContentSharingPickerObserver, SCStreamDelegate, @unchecked Sendable {
    private weak var session: CaptureSession?
    private let generation: UUID

    init(session: CaptureSession, generation: UUID) {
        self.session = session
        self.generation = generation
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didUpdateWith filter: SCContentFilter, for stream: SCStream?) {
        Task { @MainActor in await session?.start(with: filter, generation: generation) }
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        Task { @MainActor in await session?.cancel(generation: generation) }
    }

    func contentSharingPickerStartDidFailWithError(_ error: Error) {
        Task { @MainActor in await session?.fail(error, generation: generation) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        Task { @MainActor in await session?.fail(error, generation: generation) }
    }
}
