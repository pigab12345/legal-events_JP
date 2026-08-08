# 法学イベント PWA 実用版

## Androidでの初期設定（推奨）
1. GitHubで新しいRepositoryを作成。
2. このフォルダの中身を `main` ブランチへアップロード。
3. GitHubの Settings → Pages → Source を GitHub Actions にする。
4. Actions の `Deploy PWA to GitHub Pages` が完了するとURLが発行される。
5. AndroidのChromeでそのURLを開き、メニュー → 「ホーム画面に追加」または「アプリをインストール」。
6. ホーム画面の「法学イベント」から起動。

## データ更新
`data/events.json` を更新して push すると、PWAが自動再公開される。

## 自動収集について
現段階では、誤情報防止のためイベントの自動「発見・意味解析」は分離しています。
次段階では、公式サイトの巡回→候補抽出→AI構造化→人間確認→events.json更新、というパイプラインを追加するのが安全です。
