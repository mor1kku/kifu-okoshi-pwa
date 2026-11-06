import SwiftUI

@main
struct ProbeApp: App {
    @StateObject private var session = CaptureSession()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ProbeView(session: session)
                .onChange(of: scenePhase) { _, phase in
                    if phase == .background { session.setBackground(true) }
                    if phase == .active { session.setBackground(false) }
                }
        }
    }
}

struct ProbeView: View {
    @ObservedObject var session: CaptureSession
    @State private var showingShare = false
    private let ink = Color(red: 0.12, green: 0.25, blue: 0.21)
    private let paper = Color(red: 0.96, green: 0.95, blue: 0.91)

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                VStack(alignment: .leading, spacing: 10) {
                    Text("棋譜おこし / iPhone 試作 0.1.0")
                        .font(.caption.weight(.semibold)).tracking(1)
                    Text("観る、その間に。")
                        .font(.system(.largeTitle, design: .serif, weight: .semibold))
                    Text("画面取得のテスト")
                        .font(.title3.weight(.medium))
                    Text("別アプリで見ている映像が、このアプリに届くかを調べます。この試作版はまだ棋譜を作りません。")
                        .fixedSize(horizontal: false, vertical: true)
                }
                Divider()
                VStack(alignment: .leading, spacing: 16) {
                    instruction("01", "下のボタンから画面全体の共有を開始。")
                    instruction("02", "別アプリに移り、動きのある動画を20〜30秒再生。画面はロックしません。")
                    instruction("03", "このアプリに戻り「終了して確認」。結果を共有してください。")
                }
                VStack(alignment: .leading, spacing: 12) {
                    Label(session.status, systemImage: session.capturing ? "record.circle" : "circle.dotted")
                        .font(.headline).accessibilityIdentifier("captureStatus")
                    if session.busy {
                        Button("終了して確認") { Task { await session.stop() } }
                            .buttonStyle(.borderedProminent).controlSize(.large)
                            .accessibilityIdentifier("stopCapture")
                    } else {
                        Button("画面共有を開始") { session.presentPicker() }
                            .buttonStyle(.borderedProminent).controlSize(.large)
                            .accessibilityIdentifier("startCapture")
                    }
                    Text("映像・音声の録画やアップロードは行いません。端末に保存するのは回数・時刻などの診断だけです。最長120秒の試験です。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text(session.profile.detail).font(.footnote)
                    if let error = session.errorMessage {
                        Text(error).foregroundStyle(.red).textSelection(.enabled)
                    }
                }
                .padding(20).frame(maxWidth: .infinity, alignment: .leading)
                .background(.white.opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
                if let report = session.report {
                    VStack(alignment: .leading, spacing: 16) {
                        Text("受け取った記録").font(.title2.weight(.semibold))
                        Text(report.text).font(.callout).textSelection(.enabled)
                            .accessibilityIdentifier("captureReport")
                        if !session.busy {
                            Button {
                                session.prepareShare()
                                showingShare = session.sharedJSON != nil
                            } label: { Label("診断を共有", systemImage: "square.and.arrow.up") }
                                .buttonStyle(.borderedProminent).controlSize(.large)
                            Button("診断文をコピー") { UIPasteboard.general.string = report.text }
                                .buttonStyle(.bordered)
                        }
                    }
                }
                Divider()
                Text("次の工程：画面取得を確認 → 駒の認識を移植 → 読み取り速度と棋譜を検証")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            .padding(24).frame(maxWidth: 620)
        }
        .background(paper).foregroundStyle(ink).tint(ink)
        .preferredColorScheme(.light)
        .sheet(isPresented: $showingShare) {
            if let text = session.sharedJSON { DiagnosticShare(text: text) }
        }
        .task {
            while !Task.isCancelled {
                session.refresh()
                do { try await Task.sleep(for: .seconds(1)) }
                catch { return }
            }
        }
    }

    private func instruction(_ number: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Text(number).font(.system(.body, design: .serif)).foregroundStyle(.secondary)
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct DiagnosticShare: UIViewControllerRepresentable {
    let text: String
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [text], applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
