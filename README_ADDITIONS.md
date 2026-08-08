# 追加機能の導入手順

## 1. ファイル配置
このフォルダの中身を repo にそのままコピーしてください（同名ファイルは上書き）。
- `data/sources.json` … 巡回対象サイト一覧（**実在URLに書き換えてください**。今入っているのは日本私法学会の例1件＋プレースホルダー2件）
- `scripts/crawl_and_structure.py` … 巡回＋AI構造化＋差分検出
- `scripts/promote_candidate.py` … 人間確認後、候補を本番の events.json に反映する補助ツール
- `.github/workflows/crawl-and-structure.yml` … 毎日自動実行するワークフロー
- `index.html` … 行きやすさランキング・ステータスバッジ対応版（既存を置換）

## 2. GitHub Secrets 設定
Settings → Secrets and variables → Actions で以下を追加:
- `ANTHROPIC_API_KEY`（必須）: Claude APIキー
- `DISCORD_WEBHOOK_URL`（任意）: 通知を受け取りたい場合のみ

## 3. 動作の流れ
1. 毎日 JST 6:00 にワークフローが起動（`workflow_dispatch` で手動実行も可）
2. 各サイトを巡回 → Claude APIでイベント候補をJSON化
3. 既存 `events.json` と突合し、新規／日程変更／中止／申込開始を検出
4. `data/candidates.json` を更新し、変化があれば自動でPRを作成（**events.jsonは自動更新されません**＝誤情報防止のため必ず人間確認を挟む設計を維持）
5. PRがGitHub通知として届く（＋Discord Webhook設定時はそちらにも通知）

## 4. 候補を本番反映する
PRの `data/candidates.json` を確認し、問題なければ:

```bash
python scripts/promote_candidate.py --list          # 候補一覧とタグ確認
python scripts/promote_candidate.py <candidate_id>  # events.json へ反映
python scripts/promote_candidate.py --remove <id>   # 中止イベントを削除する場合
```

反映後、通常どおり `events.json` をpushすればPWAに反映されます。

## 5. 「行きやすさランキング」について
- クロール時に会場を無料のジオコーディングAPI（Nominatim）で緯度経度化し、東京駅・横浜駅からの直線距離を算出します。
- あくまで直線距離の目安であり、実際の乗換時間ではありません（Google Maps Directions API等の有料サービスを使えばより正確な所要時間ベースのランキングも可能です。必要であれば拡張します）。
- オンライン開催は「移動不要」として常に上位表示されます。

## 6. 今後の拡張候補
- 巡回対象を「一覧ページ」だけでなく個別イベント詳細ページまで再帰的に辿る
- 過去の中止・変更履歴を events.json 側にも保持して表示
- Directions APIによる実際の所要時間ベースのランキング
