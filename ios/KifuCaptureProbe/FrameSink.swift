import Foundation
import CoreMedia
import CoreVideo
@preconcurrency import ScreenCaptureKit

struct ProbeReport: Codable {
    let schemaVersion: Int
    let appVersion: String
    let systemVersion: String
    let startedAt: Date
    var elapsedSeconds: Double
    var state: String
    var message: String?
    let profileScreenRecordingAllowed: Bool?
    let profileInspection: String
    var frameWidth = 0
    var frameHeight = 0
    var pixelFormat: UInt32 = 0
    var metrics = ProbeMetrics()
    var intervals: [IntervalSummary] = []

    var summary: String {
        if intervals.contains(where: \.continuousChangesObserved) {
            return "別アプリ中の継続的な映像変化を観測しました。棋譜認識の速度・精度は次の段階で確認します。"
        }
        if intervals.isEmpty { return "別アプリへ移った区間がまだありません。" }
        return "別アプリ中の継続的な映像変化は未確認です。読取回数・映像変化・停止理由を確認してください。"
    }

    var text: String {
        let seconds = { (value: Double) in String(format: "%.1f", value) }
        var lines = ["棋譜おこし・画面取得テスト \(appVersion)", summary,
                     "状態：\(state) / iOS \(systemVersion)",
                     "署名プロファイル：\(profileInspection)",
                     "全体：\(seconds(elapsedSeconds))秒 / コールバック \(metrics.callbacks)回 / 比較用の画像 \(metrics.observations.count)枚",
                     "画像サイズ：\(frameWidth) × \(frameHeight)"]
        for (index, interval) in intervals.enumerated() {
            lines.append("別アプリ区間\(index + 1)：評価\(seconds(interval.evaluatedSeconds))秒、読取\(interval.samples)回、変化\(interval.changes)回、単色\(interval.flatSamples)回、最大読取間隔\(seconds(interval.maxSampleGap))秒、最大変化間隔\(seconds(interval.maxChangeGap))秒")
        }
        if let message { lines.append("詳細：\(message)") }
        lines.append("切替の前後2秒を除外。長い間隔はOSの休止を断定する値ではありません。映像・音声は保存していません。")
        return lines.joined(separator: "\n")
    }
}

final class FrameSink: NSObject, SCStreamOutput, @unchecked Sendable {
    let queue = DispatchQueue(label: "io.github.mor1kku.kifuprobe.frames", qos: .userInitiated)
    private let lock = NSLock()
    private let startedUptime = ProcessInfo.processInfo.systemUptime
    private var report: ProbeReport
    private var previousPixels: [UInt8]?
    private var previousTime: Double?
    private var lastSample = -1.0
    private var lastSave = -1.0
    private var finished = false
    private var timeoutRequested = false
    private var storageError: String?
    private let reportURL: URL
    var onTimeout: (@Sendable () -> Void)?

    init(report: ProbeReport, reportURL: URL) {
        self.report = report
        self.reportURL = reportURL
    }

    var elapsed: Double { max(0, ProcessInfo.processInfo.systemUptime - startedUptime) }

    func setBackground(_ background: Bool) {
        lock.lock()
        defer { lock.unlock() }
        guard !finished else { return }
        report.metrics.setBackground(background, at: elapsed)
        saveLocked()
    }

    func snapshot() -> ProbeReport {
        lock.lock()
        defer { lock.unlock() }
        return snapshotLocked()
    }

    func finish(state: String, message: String?) {
        lock.lock()
        defer { lock.unlock() }
        guard !finished else { return }
        report.elapsedSeconds = elapsed
        report.metrics.setBackground(false, at: elapsed)
        report.state = state
        report.message = message
        finished = true
        previousPixels = nil
        saveLocked()
    }

    private func snapshotLocked() -> ProbeReport {
        var result = report
        if !finished { result.elapsedSeconds = elapsed }
        result.intervals = report.metrics.summaries(until: result.elapsedSeconds)
        if let storageError {
            result.message = [result.message, "途中保存の失敗：\(storageError)"].compactMap { $0 }.joined(separator: " / ")
        }
        return result
    }

    private func saveLocked() {
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            encoder.dateEncodingStrategy = .iso8601
            let data = try encoder.encode(snapshotLocked())
            try data.write(to: reportURL, options: .atomic)
            storageError = nil
        } catch {
            storageError = error.localizedDescription
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen else { return }
        lock.lock()
        guard !finished else { lock.unlock(); return }
        let time = elapsed
        if time >= 120 {
            let request = !timeoutRequested
            timeoutRequested = true
            lock.unlock()
            if request { onTimeout?() }
            return
        }
        let image = sampleBuffer.isValid ? CMSampleBufferGetImageBuffer(sampleBuffer) : nil
        report.metrics.recordCallback(valid: image != nil)
        guard let image, time - lastSample >= 0.095 else { lock.unlock(); return }
        lastSample = time
        let width = CVPixelBufferGetWidth(image)
        let height = CVPixelBufferGetHeight(image)
        if report.frameWidth != width || report.frameHeight != height {
            previousPixels = nil
            previousTime = nil
        }
        report.frameWidth = width
        report.frameHeight = height
        let format = CVPixelBufferGetPixelFormatType(image)
        report.pixelFormat = format
        let isBGRA = format == kCVPixelFormatType_32BGRA
        let isLuma8 = format == kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange || format == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange
        let isLuma10 = format == kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange || format == kCVPixelFormatType_420YpCbCr10BiPlanarFullRange
        guard isBGRA || isLuma8 || isLuma10 else {
            report.message = "未対応の画素形式：\(format)。画面は受信しましたが比較できません。"
            if time - lastSave >= 2 { lastSave = time; saveLocked() }
            lock.unlock()
            return
        }
        guard CVPixelBufferLockBaseAddress(image, .readOnly) == kCVReturnSuccess else {
            report.message = "画像メモリーへのアクセスを確認できませんでした。"
            lock.unlock()
            return
        }
        var pixels: [UInt8] = []
        let planar = !isBGRA && CVPixelBufferGetPlaneCount(image) > 0
        let base = planar ? CVPixelBufferGetBaseAddressOfPlane(image, 0) : CVPixelBufferGetBaseAddress(image)
        if let base, width > 0, height > 0 {
            let bytes = base.assumingMemoryBound(to: UInt8.self)
            let stride = planar ? CVPixelBufferGetBytesPerRowOfPlane(image, 0) : CVPixelBufferGetBytesPerRow(image)
            pixels.reserveCapacity(48 * 72)
            // 通知バーなどの影響を減らすため、中央80%だけを粗く比較する。
            for row in 0..<72 {
                let y = min(height - 1, Int(Double(height) * (0.1 + 0.8 * (Double(row) + 0.5) / 72)))
                for column in 0..<48 {
                    let x = min(width - 1, Int(Double(width) * (0.1 + 0.8 * (Double(column) + 0.5) / 48)))
                    if isBGRA {
                        let offset = y * stride + x * 4
                        pixels.append(UInt8((Int(bytes[offset]) + 2 * Int(bytes[offset + 1]) + Int(bytes[offset + 2])) / 4))
                    } else if isLuma10 {
                        let value = base.advanced(by: y * stride).assumingMemoryBound(to: UInt16.self)[x]
                        pixels.append(UInt8(value >> 8))
                    } else {
                        pixels.append(bytes[y * stride + x])
                    }
                }
            }
        }
        CVPixelBufferUnlockBaseAddress(image, .readOnly)
        if !pixels.isEmpty {
            report.metrics.recordSample(at: time, comparisonTime: previousTime,
                changed: ImageComparison.changed(pixels, from: previousPixels), flat: ImageComparison.isFlat(pixels))
            previousPixels = pixels
            previousTime = time
        }
        if time - lastSave >= 2 {
            lastSave = time
            saveLocked()
        }
        lock.unlock()
    }
}
