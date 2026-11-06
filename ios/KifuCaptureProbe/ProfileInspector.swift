import Foundation

struct ProfileInspection {
    let allowed: Bool?
    let detail: String

    static func inspect() -> ProfileInspection {
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision") else {
            return ProfileInspection(allowed: nil, detail: "プロファイルなし（権限は未確認）")
        }
        do {
            let data = try Data(contentsOf: url)
            // 診断に使う公開権限だけを読む。アカウント・端末ID・証明書は保持しない。
            guard let start = data.range(of: Data("<?xml".utf8)),
                  let end = data.range(of: Data("</plist>".utf8), in: start.lowerBound..<data.endIndex) else {
                return ProfileInspection(allowed: nil, detail: "プロファイルの形式を読み取れませんでした")
            }
            let plistData = data.subdata(in: start.lowerBound..<end.upperBound)
            let plist = try PropertyListSerialization.propertyList(from: plistData, format: nil) as? [String: Any]
            guard let entitlements = plist?["Entitlements"] as? [String: Any] else {
                return ProfileInspection(allowed: nil, detail: "プロファイルの権限情報を読み取れませんでした")
            }
            let allowed = entitlements["com.apple.developer.screen-recording"] as? Bool == true
            return ProfileInspection(allowed: allowed,
                detail: allowed ? "画面取得権限あり（実際の取得は開始後に確認）" : "画面取得権限なし。再署名時に付与されなかった可能性があります")
        } catch {
            return ProfileInspection(allowed: nil, detail: "プロファイル読取失敗：\(error.localizedDescription)")
        }
    }
}
