# 棋譜おこし — iPhone画面取得テスト 0.1.0

別アプリで動画を見ながら棋譜を起こすための、最初の実機確認用アプリです。**この版は画面の受信を診断するだけで、棋譜認識はまだ行いません。** 既存のPWAはそのまま使えます。

## Macを持っていない場合の工程

1. この公開リポジトリのGitHub ActionsでiOSアプリをビルドします。標準の `xcode-27` runnerを使い、Appleのアカウント・証明書・秘密鍵は渡しません。
2. WindowsのAltStore Classicで自分のiPhone用に署名し直して入れられるかを試します。
3. アプリ内で署名プロファイルの画面取得権限を確認し、別アプリ中に変化する映像が届くかを調べます。
4. 取得できた場合に、盤面指定・駒の認識・合法手照合・棋譜保存を順に移植します。実動画で速さ・発熱・メモリー・棋譜一致を検証します。

**無料のAppleアカウントでScreen Recording権限を付与できるかは未確認です。** AltStoreがIPAをインストールできても、必要な権限がなく開始できない可能性があります。現時点で有料のApple Developer Programへの登録は不要です。登録しても動作を確認したことにはなりません。

GitHubの[公開リポジトリ向け標準runner](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)は無料です。`xcode-27` はpreviewのため、提供状況・SDKが変わることがあります。大型runnerや有料のMacレンタルは使いません。

## Windowsから入れる手順

対象は **iOS 27以降の実機iPhone** です。AppleのサンプルではSimulator非対応とされているため、PC上の試験だけで画面取得成功とは判定しません。

1. GitHubの **Actions → Build iPhone capture probe → 成功した実行 → Artifacts** から `KifuCaptureProbe-0.1.0-resign` をダウンロードします。GitHubへのログインが必要です。ZIPの中の `KifuCaptureProbe-0.1.0-resign.ipa` を取り出してください。
2. [AltStore Classicの公式Windows手順](https://faq.altstore.io/altstore-classic/how-to-install-altstore-windows)に沿ってAltServerを用意します。Windows 10以降、Appleが配布するiTunes・iCloud、USB接続が前提です。Microsoft Store版との違いを公式手順で確認してください。
3. iPhoneをUSB接続して信頼し、AltServerからAltStore Classicを入れます。Appleアカウントは自分でAltServerに入力します。このリポジトリやGitHubには入力しません。iPhoneで開発元を信頼し、必要に応じてDeveloper Modeを有効にします。
4. IPAをiPhoneの「ファイル」に移し、AltStore Classicの **My Apps → ＋** から選んでインストールします。AltServerが動くWindowsとの接続が必要です。
5. 「棋譜・取得テスト」を開きます。インストールでエラーになる場合は、その文面を記録してください。アカウント情報・証明書・プロファイル全体を共有する必要はありません。

無料署名は通常[7日ごとの更新、AltStoreを含め3アプリまで](https://faq.altstore.io/altstore-classic/your-altstore)という制限があります。この試作は拡張を含まない1アプリです。成果物のGitHub保存は7日間です。期限が切れた場合はworkflowを再実行できます。

IPAは再署名に権限情報を渡すためのad-hoc署名付きです。Appleの有効な配布署名やprovisioning profileは含みません。IPAをタップするだけでインストールすることはできません。AltStore等での再署名後にも必要な権限が残るかは別途確認が必要です。

## 画面取得を確かめる

1. 「画面共有を開始」から、iOSの確認画面で画面全体を共有します。
2. 別アプリに移り、動きのある動画を20〜30秒再生します。画面はロックしません。
3. このアプリに戻り「終了して確認」を押します。
4. 診断を共有します。映像や音声は含みません。

権限不足が分かった場合は開始せず、その理由を表示します。「プロファイルに権限あり」は最終署名の確認や取得成功を意味しません。画面取得開始時のOSエラーも記録します。旧ReplayKit APIはiOS 27でdeprecated／サポート終了の記載があるため、今回は同梱しません。

画面の受信はUIとは別のキューで行い、非表示を理由に停止しません。中央80%を粗く比較し、約10回/秒まで診断します。画面切替の前後2秒を除いて、別アプリ中の読取回数・変化回数・単色の画像数・最大読取間隔・最大変化間隔を表示します。変化がなかった場合でも、OS休止・静止画・動画側の制限のどれかを断定しません。成功判定も、画像が届くことの確認に限ります。

120秒を上限として停止を試みます。OSが処理を止めた場合は復帰時にも上限を確認します。必要なときはiOSの画面共有表示から停止できます。約2秒ごとに診断を端末内へ保存し、次の起動時には途中保存として復元します。自動で共有を再開することはありません。

## ビルドと試験

Xcode 27のある環境で:

```sh
swift test --package-path ios/ProbeCore
xcodebuild -project ios/KifuCaptureProbe.xcodeproj -scheme KifuCaptureProbe \
  -configuration Release -destination 'generic/platform=iOS' \
  -derivedDataPath build-ios CODE_SIGNING_ALLOWED=NO build
```

テストは、切替映像だけで成功にしないこと、フレームの凍結、短時間の受信後の途絶、複数の非表示区間、画像比較、診断の保存形式を検証します。Swiftコードのビルド・集計テストと実機の画面取得・署名・速度確認は別です。

## 採用したAPIと署名の制約

- [Apple公式サンプル：Capturing screen content on iOS](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-on-ios)：`SCContentSharingPicker`、`SCStream`、`screen-capture` background mode、`com.apple.developer.screen-recording`。
- [AltSignの署名処理](https://github.com/rileytestut/AltSign/blob/5b216f45162e2896df5f5927b373d26d66cb05ae/AltSign/Signing/ALTSigner.mm#L335-L375)：元IPAに権限を書くだけでは、Appleのプロファイルが許可しない権限は付与できません。
- [Apple：RPBroadcastSampleHandler](https://developer.apple.com/documentation/replaykit/rpbroadcastsamplehandler)：iOS 27の旧方式の制約。

Appleのサンプルを参考にした画面共有のライフサイクルは、同梱の `APPLE-SAMPLE-LICENSE.txt` に従います。独自の診断コードを含め、映像・音声を外部へ送る処理はありません。
