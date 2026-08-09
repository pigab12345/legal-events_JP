# 追加機能の導入手順（完全自動更新版）

## 1. ファイル配置
このフォルダの中身を repo にそのままコピーしてください（同名ファイルは上書き）。
- `data/sources.json` … 巡回対象サイト一覧（**実在URLに書き換えてください**）
- `data/candidates.json`, `data/geocache.json`, `data/rejected_sources.json` … 空の初期ファイル
- `scripts/crawl_and_structure.py` … 巡回＋AI構造化＋自動登録
- `scripts/promote_candidate.py` … 誤登録を手動で削除・修正するための補助ツール（通常は使いません）
- `.github/workflows/crawl-and-structure.yml` … 毎日自動実行するワークフロー（**events.jsonを直接更新**）
- `.github/workflows/promote-candidate.yml` … 手動修正用ワークフロー（通常は使いません）
- `index.html` … 行きやすさランキング・ステータスバッジ対応版（既存を置換）

## 2. GitHub Secrets 設定
`Settings → Secrets and variables → Actions` で以下を追加:
- `ANTHROPIC_API_KEY`（必須）: Claude APIキー
- `DISCORD_WEBHOOK_URL`（任意）: 通知を受け取りたい場合のみ

## 3. GitHubリポジトリ設定（重要・初回のみ）
`Settings → Actions → General → Workflow permissions` で
**「Read and write permissions」** になっていることを確認してください
（`contents: write`でのpush・Issue作成に必要です）。

## 4. 動作の流れ（すべて自動・人間の確認ステップなし）

1. 毎日 JST 6:00 にワークフローが起動（`workflow_dispatch` で手動実行も可）
2. **新規サイトを自動発見**: Claude APIのWeb検索機能で、`sources.json`にまだ無い法学系イベント一覧ページを探索し、見つかれば`sources.json`に自動追加
3. 各サイト（既存＋新規発見分）を巡回 → Claude APIでイベント候補をJSON化
4. **開催日が既に過去のイベントはこの時点で除外**（掲載しません）
5. 既存 `data/events.json` と突合し、新規登録／日程変更／中止／申込開始を検出
6. **`data/events.json` に直接反映**（自動登録・人間の確認は挟みません）
7. **開催日が過ぎた既存イベントも自動的に削除**（サイトが常に最新状態に保たれます）
8. 変更があれば `main` ブランチへ直接コミット・push
9. 変更内容をGitHub Issueとして自動投稿（承認は不要。記録・通知だけの目的）
10. Discord Webhook設定時はそちらにも通知

### ⚠️ この設計の注意点
- AIの抽出ミス（誤った日付・誤った参加可否判定など）が**そのままサイトに反映される**可能性があります
- 明らかな誤りに気づいたら、`Actions`タブ →「イベントを手動修正」→ `Run workflow`で該当IDを`remove`してください
- `data/candidates.json`には毎回の抽出結果の生データが記録として残るので、後から「なぜこの情報が載ったか」を追跡できます

### 見つけてほしくないサイトを除外する
自動発見が明後日の方向のサイトを拾ってきた場合、`data/rejected_sources.json` にURLを追加してください。

```json
["https://example.com/irrelevant-page"]
```

## 5. 誤登録の手動修正（Python不要・スマホのみ）

1. `data/events.json` または `data/candidates.json` をGitHub上で開き、対象の `id` を確認
2. `Actions` タブ → 「イベントを手動修正」→ `Run workflow`
3. `candidate_id` にid、`action`は通常 `remove`（削除）を選択
4. 実行すると自動で `data/events.json` が更新される

## 6. 「行きやすさランキング」について
- クロール時に会場を無料のジオコーディングAPI（Nominatim）で緯度経度化し、東京駅・横浜駅からの直線距離を算出・保存します。
- あくまで直線距離の目安であり、実際の乗換時間ではありません。
- オンライン開催は「移動不要」として常に上位表示されます。

## 7. 今後の拡張候補
- 巡回対象を「一覧ページ」だけでなく個別イベント詳細ページまで再帰的に辿る
- Directions APIによる実際の所要時間ベースのランキング
- 誤登録が多い場合、confidence="要確認"のものだけ自動登録せずcandidates.jsonに留める、といったハイブリッド運用への切り替え
