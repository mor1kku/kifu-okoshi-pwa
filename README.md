# 棋譜おこし — iPhone PWA

将棋アプリの画面録画から盤面の変化と合法手を照合し、KIF棋譜を作るWebアプリです。GitHub Pagesからアプリ本体を配信し、動画の読み込みと認識は端末内で行います。動画・棋譜をアップロードするサーバーや外部AI APIは使いません。

公開先: https://mor1kku.github.io/kifu-okoshi-pwa/

## iPhoneで使う

1. Safariで公開先を開き、共有メニューから「ホーム画面に追加」します。
2. 通信できる場所で「オフライン用にアプリを保存」を押し、完了を確認します。初回のダウンロードは約28MBです。
3. 「写真」または「ファイル」から動画を選びます。iCloud上だけにある動画は先に端末へダウンロードしてください。
4. 初期配置が表示される時刻と盤の範囲、アプリの種類、先後の向きを指定します。
5. 解析を実行し、結果を確認・修正してKIFを保存します。解析中は画面を開いておいてください。

途中で停止した場合は、同じ動画を選び直し、「保存済みの解析を開く」→「途中から再開」を使います。通常は約5秒間隔で途中経過を端末内に保存します。手修正した結果からの自動再開には対応していません。OSが保存領域を消去する場合があるため、結果はKIF・JSONでも保存してください。

## 対応範囲と検証状況

- 平手の初期配置から始まる画面録画に対応します。棋桜・将棋ウォーズ用の認識設定と汎用設定があります。
- 速度が変わる動画でもフレーム時刻を使って読み取ります。指し手の中間局面が録画に存在しない場合や、盤が隠れる場合には誤認識・欠落が起こり得ます。
- 未知の盤面テーマ・アプリ、途中局面からの開始、すべての動画形式への対応は保証していません。WebCodecsで読めない形式は理由を表示して停止します。
- 2026-10-30にPCのChromeで、棋桜の136手と将棋ウォーズの42手を実動画から認識し、正解棋譜との一致を確認しました。将棋ウォーズでは停止・ページ再読み込み・再開も検証しました。
- オフラインでの起動、短い区間の解析、修正、KIF/JSON保存、結果の再表示もChromeで確認済みです。
- **iPhone実機での速度・メモリー・発熱・動画形式ごとの動作は未検証の試作版です。** PCでの検証はiPhoneでの動作保証ではありません。

## ローカルビルド

Node.js 22、Python 3.12を使用します。

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm ci --ignore-scripts
npm test
python -m pytest -q
npm run prepare:pwa
npm run build:pwa
python -m http.server 8002 --bind 127.0.0.1
```

PCのブラウザーで `http://127.0.0.1:8002/dist-pwa/` を開きます。PWAはAPIサーバーなしで動作します。`prepare:pwa` は固定版の公式Pyodideパッケージを取得し、SHA-256を確認します。再ビルド時の旧成果物は `.local/pwa-builds/` に退避します。

公開リポジトリには実動画・正解棋譜・動画由来のテストfixtureを含めません。そのため、非公開の棋譜またはfixtureを使う4テストは公開ソースではskipされます。合成盤面を使う認識・速度・向き・途中再開テストは実行できます。公開用コピーではPythonの42テストとJavaScriptの7テストの成功を確認しています。

## GitHub Pages

Settings → Pages → Build and deployment → Source を **GitHub Actions** にします。`main`へのpush、またはActionsの「Build iPhone PWA」の手動実行でテスト、ビルド、デプロイします。

配信するのは `dist-pwa/` の静的ファイルだけです。相対URLで動作するため、リポジトリ名を変えてもビルドの変更は不要です。元動画・正解棋譜・`.local/`・fixtureを公開しないでください。

## 構成

- `backend/recognition.py` / `records.py`: PC版と共通の認識器と棋譜処理
- `backend/browser_bridge.py`: ブラウザー向けのフレーム入力・途中保存・復元
- `pwa/worker.js`: Mediabunnyで動画を逐次デコードし、PyodideでPython認識器を実行
- `pwa/local-engine.js` / `storage.js`: 端末内処理、IndexedDB保存、中断・再開
- `pwa/sw.js`: アプリと解析エンジンのオフライン保存
- `frontend/`: 盤面指定、結果確認・修正、KIF保存の画面

## ライセンス

アプリのソースはGPL-3.0-onlyです。`LICENSE`を参照してください。依存するpython-shogiのソースとライセンスは配布物の `python.zip` に、アプリのビルド可能なソースは `corresponding-source.zip` に含めます。

MediabunnyとPyodideはMPL-2.0、CPythonおよびその他の依存ライブラリにはそれぞれのライセンスが適用されます。配布物の `THIRD-PARTY.txt`、`licenses/`、同梱wheel内のライセンスを参照してください。
