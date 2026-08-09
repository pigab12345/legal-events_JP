#!/usr/bin/env python3
"""
法学イベント自動巡回・AI構造化・自動登録スクリプト

やること:
  1. data/sources.json の各サイトを巡回してテキストを取得
  2. Claude API に投げてイベント候補をJSON構造化
     (一般参加可否・申込状況・中止/日程変更の判定も同時に行う)
  3. 開催日が既に過去のイベントはこの時点で除外する
  4. 既存の data/events.json と突合し、新規／日程変更／中止／申込開始を検出
  5. 結果を data/events.json に直接反映する（自動登録。人間の確認ステップは無し）
     ※ data/candidates.json には今回の抽出結果の記録（監査ログ）として残す
  6. 会場をジオコーディングして東京駅/横浜駅からの距離を付与（行きやすさランキング用）

必要な環境変数:
  ANTHROPIC_API_KEY  ... 必須
"""
import os
import re
import sys
import json
import time
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "data", "sources.json")
EVENTS_PATH = os.path.join(ROOT, "data", "events.json")
CANDIDATES_PATH = os.path.join(ROOT, "data", "candidates.json")
GEOCACHE_PATH = os.path.join(ROOT, "data", "geocache.json")
REJECTED_PATH = os.path.join(ROOT, "data", "rejected_sources.json")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = "claude-sonnet-5"
# 判断力を要さない機械的なJSON整形だけを行うタスクは、安価なモデルに任せてコストを抑える
MODEL_CHEAP = "claude-haiku-4-5-20251001"

# 東京駅・横浜駅（行きやすさランキングの基準点）
TOKYO_STA = (35.681236, 139.767125)
YOKOHAMA_STA = (35.465807, 139.622235)

USER_AGENT = "legal-events-jp-bot/1.0 (+https://github.com/pigab12345/legal-events_JP; contact via GitHub issues)"

EXTRACTION_INSTRUCTIONS = """あなたは日本の法学系イベント情報を抽出する専門アシスタントです。
以下はあるWebページのテキストです。このページに含まれる法学関連イベントを可能な限り正確に抽出し、
次のJSON配列だけを出力してください（説明文・コードフェンス禁止）。

対象とするイベントの範囲（幅広く拾ってください。一般公開されているかどうかで対象を絞り込まないでください）:
- 学会・専門シンポジウム・判例研究会（研究者・実務家向け。会員限定・招待制のものも対象）
- 大学等が開く一般向け公開講義・公開講座（学生・社会人向けの分かりやすい法学入門講座も含む）
- 市民向け法律講座・法教育イベント（消費者被害、労働問題など生活に身近なテーマも含む）
- 司法試験・予備試験の受験者向けガイダンス・説明会
- 司法修習生向けの説明会・研修イベント（修習生限定のものも対象）
- 法曹（弁護士・検察官・裁判官等）を目指す学生向けの就職・進路説明会、法律事務所や官公庁の業務説明会・インターンシップ説明会（学内限定・特定大学限定のものも対象）

重要: 「一般の人が参加できるかどうか」はイベントを載せるかどうかの判断基準にしてはいけません。
学会員限定、修習生限定、特定大学の学生限定、招待者限定など、参加対象が制限されているイベントも
必ず抽出してください。参加対象の制限は下記の access フィールドに正直に記録するだけで構いません。

イベントでない一般ニュースや無関係な内容は含めないでください。情報が不明な項目は空文字 "" にしてください。
推測で埋めず、ページに書かれている内容のみを根拠にしてください。

各要素のフィールド:
- title: イベント名
- category: 次のいずれか一つ: "学会・専門シンポジウム", "公開講義・市民講座", "受験生向けガイダンス", "就活・進路説明会", "その他"
- date: 開催日 (YYYY-MM-DD形式。不明ならば "")
- time: 開催時刻 (例 "13:00〜17:00"、不明なら "")
- org: 主催者
- area: 都道府県名など (例 "東京", "神奈川", "オンライン")
- place: 会場名・住所
- fee: 参加費 (例 "無料", "非会員2000円")
- access: 参加可否の実態を正直に記録する。"可"（一般の人でも参加できる）"不可"（会員限定・学内限定・修習生限定・招待制など）
  "要問合せ" （記載からは判断できない）のいずれか。"不可"であっても必ずイベント自体は抽出してください。
- speakers: 登壇者（カンマ区切り文字列）
- fields: 分野タグの配列 (例 ["民法","AI・法"]。就活説明会等で分野が特定できない場合は空配列でよい)
- application_status: "未開始" "受付中" "締切" "不明" のいずれか
- cancelled: 中止・延期の記載があれば true、なければ false
- source: このページのURL
- confidence: "公式確認済" 固定（このページ自体が主催者公式ページの場合）
"""

DISCOVERY_CATEGORIES = [
    {
        "label": "弁護士会",
        "queries": '"東京弁護士会 講座", "第一東京弁護士会 イベント", "第二東京弁護士会 イベント", '
                   '"大阪弁護士会 講座 一覧", "愛知県弁護士会 イベント", "福岡県弁護士会 講座", '
                   '"神奈川県弁護士会 イベント", "弁護士会 一覧 47都道府県", "弁護士会 会員研修 案内"',
        "targets": "全国の弁護士会（東京三会・大阪・愛知・福岡・神奈川など各都道府県弁護士会）が"
                   "開催する講座・シンポジウム・研修・市民講座の一覧・お知らせページ。"
                   "日本弁護士連合会（日弁連）だけでなく、各地域の単位弁護士会を個別に探してください。",
    },
    {
        "label": "法律事務所",
        "queries": '"法律事務所 セミナー 一覧", "法律事務所 主催 無料セミナー", '
                   '"大手法律事務所 セミナー 企業法務", "法律事務所 新卒 説明会", '
                   '"法律事務所 ウェビナー 法律相談", "法律事務所 公開講座"',
        "targets": "個々の法律事務所（大手渉外事務所から中小事務所まで）が自ら主催する"
                   "セミナー・ウェビナー・説明会・法律相談会の告知ページ。特定の1〜2事務所に偏らず、"
                   "できるだけ多様な事務所を探してください。",
    },
    {
        "label": "学会・研究会",
        "queries": '"学会 大会案内 法学", "判例研究会 告知", "法学部 学内限定 説明会", "学会 会員限定 大会"',
        "targets": "私法学会・公法学会など学会の大会案内、判例研究会・法学系研究会の告知ページ",
    },
    {
        "label": "公開講義・市民講座",
        "queries": '"大学 法学部 公開講義", "市民 法律講座", "消費者被害 講座 無料"',
        "targets": "大学等が開く一般向け公開講義・公開講座、市民向け法律講座・法教育イベントの一覧ページ",
    },
    {
        "label": "受験生・修習生・就活",
        "queries": '"司法試験 予備試験 ガイダンス 説明会", "司法修習生 説明会", '
                   '"法律事務所 就活 説明会 学生", "検察庁 裁判所 業務説明会 学生"',
        "targets": "司法試験・予備試験受験生向けガイダンス、司法修習生向け説明会、"
                   "法曹志望学生向けの就活・進路説明会・業務説明会・インターンシップ情報サイト",
    },
]

DISCOVERY_SEARCH_PROMPT = """あなたは日本の法学系オンラインイベント情報サイトを探すリサーチャーです。
今回は「{label}」というカテゴリに絞って探索してください。
ウェブ検索ツールを積極的に使い、最低4回は異なるキーワードで検索してください
（例: {queries} などのバリエーション。同じ組織名だけに偏らず、複数の異なる組織・地域を探してください）。

探す対象: {targets}
会員限定・学内限定・招待制など「一般の人は参加できない」サイトも積極的に対象にしてください。
そのサイトが一般公開イベントを扱っているかどうかは、探索の可否とは無関係です。

以下は既に把握済みのサイトです。これらと同一・実質的に重複するものは除外してください:
{existing_urls}

以下は過去に「対象外」と判断されたサイトです。これらも除外してください:
{rejected_urls}

検索結果から、個別イベントページではなく「一覧・お知らせページ」を優先して、
見つかったサイトを最大6件、サイト名・URL・判断根拠とともに箇条書きでリストアップしてください
（説明や検索過程の記述があっても構いません）。
"""

DISCOVERY_FORMAT_PROMPT = """以下はリサーチャーが調査した「法学系イベント一覧ページ」の候補メモです。
複数カテゴリ分のメモが含まれています。各メモに実際に登場するURLだけを使って、
次のJSON配列だけを出力してください（説明文・コードフェンス・前置き・後書き一切禁止。
出力の最初の文字は [ 、最後の文字は ] にしてください）。全カテゴリ分を1つの配列にまとめてください。

メモに具体的なURLが1件も含まれていない場合は、空配列 [] だけを出力してください。
URLを推測や創作で補ってはいけません。各要素にはどのカテゴリ由来かも記録してください。

[{{"name": "サイト名", "url": "https://...", "reason": "根拠", "category_label": "メモの【カテゴリ】名"}}]

{notes_by_category}
"""


def normalize_url(u: str) -> str:
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def extract_json_array(raw: str):
    """前置き・後書きが混ざっていても最初の[〜最後の]を取り出してJSON化する"""
    raw = raw.strip()
    raw = re.sub(r"^```json|```$", "", raw, flags=re.MULTILINE).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None


def call_claude(prompt: str, tools=None, max_tokens=2000, model=None, cache_static: str = None) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません")

    if cache_static:
        # 静的な指示文をキャッシュ対象としてマークし、同じ指示を繰り返し送る際の課金を抑える
        # （最小キャッシュサイズ未満の場合は単に無視されるだけで、動作・結果には影響しない）
        content = [
            {"type": "text", "text": cache_static, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": prompt},
        ]
    else:
        content = prompt

    payload = {
        "model": model or MODEL,
        "max_tokens": max_tokens,
        # 構造化JSON出力のみが目的で、可視の推論過程は不要なためAdaptive Thinkingを無効化し、
        # 出力トークン（課金対象）に無駄な推論分が乗らないようにする
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": content}],
    }
    if tools:
        payload["tools"] = tools
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # エラー本文を読んで詳細な理由を例外メッセージに含める（例: 残高不足、不正なmodel名など）
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = "(本文取得不可)"
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    blocks = data.get("content", [])
    search_calls = sum(1 for b in blocks if b.get("type") == "server_tool_use")
    usage = data.get("usage", {})
    cache_read = usage.get("cache_read_input_tokens", 0)
    if tools:
        print(f"[INFO] web_search呼び出し回数: {search_calls}", file=sys.stderr)
    if cache_read:
        print(f"[INFO] キャッシュヒット: {cache_read}トークン分を割引価格で処理", file=sys.stderr)
    if tools:
        print(f"[INFO] web_search呼び出し回数: {search_calls}", file=sys.stderr)
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def discover_new_sources(existing_sources: list, rejected: list, max_per_run: int = 25) -> list:
    """既存sources.jsonと重複しない新規サイトをカテゴリ別にweb検索で発見する
    （検索フェーズはカテゴリごと・整形フェーズは全カテゴリまとめて1回・安価モデルで実行してコストを抑える）
    """
    known = {normalize_url(s["url"]) for s in existing_sources}
    known |= {normalize_url(u) for u in rejected}

    existing_urls = "\n".join(f"- {s['url']}" for s in existing_sources) or "(なし)"
    rejected_urls = "\n".join(f"- {u}" for u in rejected) or "(なし)"

    notes_by_category = []
    for cat in DISCOVERY_CATEGORIES:
        search_prompt = DISCOVERY_SEARCH_PROMPT.format(
            label=cat["label"], queries=cat["queries"], targets=cat["targets"],
            existing_urls=existing_urls, rejected_urls=rejected_urls,
        )
        print(f"[INFO] サイト探索カテゴリ: {cat['label']}", file=sys.stderr)
        try:
            notes = call_claude(
                search_prompt,
                tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
                max_tokens=3000,
            )
        except Exception as e:
            print(f"[WARN] サイト探索(検索フェーズ/{cat['label']})に失敗: {e}", file=sys.stderr)
            continue

        if not notes:
            print(f"[WARN] 検索フェーズの出力が空でした（{cat['label']}）", file=sys.stderr)
            continue
        print(f"[DEBUG] {cat['label']}の検索結果(先頭300字): {notes[:300]}", file=sys.stderr)
        notes_by_category.append(f"【カテゴリ: {cat['label']}】\n{notes}")

    if not notes_by_category:
        return []

    # --- 整形フェーズは全カテゴリまとめて1回だけ・安価モデルで実行 ---
    combined_notes = "\n\n---\n\n".join(notes_by_category)
    try:
        raw = call_claude(
            DISCOVERY_FORMAT_PROMPT.format(notes_by_category=combined_notes),
            max_tokens=3000,
            model=MODEL_CHEAP,
        )
    except Exception as e:
        print(f"[WARN] サイト探索(整形フェーズ)に失敗: {e}", file=sys.stderr)
        return []

    found = extract_json_array(raw)
    if not found:
        return []

    all_new_sources = []
    for f in found:
        url = f.get("url", "").strip()
        if not url or normalize_url(url) in known:
            continue
        known.add(normalize_url(url))
        label = f.get("category_label", "")
        all_new_sources.append({
            "name": f.get("name", url),
            "url": url,
            "note": (f"[{label}] " if label else "") + f.get("reason", "自動発見"),
            "auto_discovered": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        })
        if len(all_new_sources) >= max_per_run:
            break

    return all_new_sources


def fetch_text(url: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    # プロンプトが肥大化しすぎないよう制限
    return text[:8000]


def extract_events_from_page(url: str, text: str) -> list:
    dynamic_part = f"ページURL: {url}\nページ本文:\n---\n{text}\n---"
    try:
        raw = call_claude(dynamic_part, max_tokens=4000, cache_static=EXTRACTION_INSTRUCTIONS)
    except Exception as e:
        print(f"[WARN] AI構造化に失敗しました {url}: {e}", file=sys.stderr)
        return []
    found = extract_json_array(raw)
    if found is None:
        print(f"[WARN] JSON解析失敗: {url}\n{raw[:500]}", file=sys.stderr)
        return []
    return found if isinstance(found, list) else []


def make_id(ev: dict) -> str:
    key = f"{ev.get('org','')}|{ev.get('title','')}".strip()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def is_past_date(date_str: str) -> bool:
    """開催日が今日(JST)より前ならTrue。日付不明("")の場合はFalse扱い（除外しない）"""
    if not date_str:
        return False
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False
    return d < datetime.now(JST).date()


EVENT_FIELDS = [
    "id", "title", "category", "date", "time", "org", "area", "place", "fee",
    "access", "speakers", "fields", "confidence", "source",
    "application_status", "cancelled", "lat", "lng",
    "dist_km_from_tokyo", "dist_km_from_yokohama",
]


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def haversine_km(a, b):
    from math import radians, sin, cos, asin, sqrt
    lat1, lon1, lat2, lon2 = map(radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def geocode(place: str, area: str, cache: dict):
    if not place and not area:
        return None, None
    query = f"{place} {area} 日本".strip()
    if query in cache:
        return cache[query].get("lat"), cache[query].get("lng")
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        time.sleep(1)  # Nominatim利用規約: 1req/秒
        results = resp.json()
        if results:
            lat, lng = float(results[0]["lat"]), float(results[0]["lon"])
            cache[query] = {"lat": lat, "lng": lng}
            return lat, lng
    except Exception as e:
        print(f"[WARN] geocode失敗 ({query}): {e}", file=sys.stderr)
    cache[query] = {"lat": None, "lng": None}
    return None, None


def main():
    sources = load_json(SOURCES_PATH, [])
    events = load_json(EVENTS_PATH, [])
    geocache = load_json(GEOCACHE_PATH, {})
    rejected = load_json(REJECTED_PATH, [])

    events_by_id = {make_id(e): e for e in events}
    candidates_log = []  # 監査ログ用（今回抽出した生データを記録するだけ。登録には使わない）

    now = datetime.now(timezone.utc).isoformat()
    report = {
        "discovered": [], "new": [], "date_changed": [],
        "cancelled": [], "application_open": [], "skipped_past": 0,
    }

    # --- 1. 新規サイトの自動発見 ---
    print("[INFO] 新規サイトを探索中...")
    discovered = discover_new_sources(sources, rejected)
    if discovered:
        sources.extend(discovered)
        save_json(SOURCES_PATH, sources)
        for d in discovered:
            report["discovered"].append(f"{d['name']} ({d['url']})")
        print(f"[INFO] {len(discovered)}件の新規サイトを発見・sources.jsonに追加しました")
    else:
        print("[INFO] 新規サイトは見つかりませんでした")

    for src in sources:
        url = src["url"]
        print(f"[INFO] 巡回中: {src.get('name', url)} ({url})")
        try:
            text = fetch_text(url)
        except Exception as e:
            print(f"[WARN] 取得失敗 {url}: {e}", file=sys.stderr)
            continue

        try:
            extracted = extract_events_from_page(url, text)
        except Exception as e:
            print(f"[WARN] AI構造化失敗 {url}: {e}", file=sys.stderr)
            continue

        for ev in extracted:
            # --- 開催日が既に過去のものはこの時点でリストアップしない ---
            if is_past_date(ev.get("date", "")):
                report["skipped_past"] += 1
                continue

            eid = make_id(ev)
            lat, lng = geocode(ev.get("place", ""), ev.get("area", ""), geocache)
            dist_tokyo = haversine_km(TOKYO_STA, (lat, lng)) if lat else None
            dist_yokohama = haversine_km(YOKOHAMA_STA, (lat, lng)) if lat else None

            record = {
                "id": eid,
                "lat": lat,
                "lng": lng,
                "dist_km_from_tokyo": round(dist_tokyo, 1) if dist_tokyo is not None else None,
                "dist_km_from_yokohama": round(dist_yokohama, 1) if dist_yokohama is not None else None,
                **ev,
            }
            candidates_log.append({"checked_at": now, **record})

            old_event = events_by_id.get(eid)

            if not old_event:
                report["new"].append(record["title"])
            if old_event and old_event.get("date") and ev.get("date") and old_event["date"] != ev["date"]:
                report["date_changed"].append(record["title"])
            if ev.get("cancelled") and not (old_event or {}).get("cancelled"):
                report["cancelled"].append(record["title"])
            prev_status = (old_event or {}).get("application_status")
            if ev.get("application_status") == "受付中" and prev_status in ("未開始", None, ""):
                report["application_open"].append(record["title"])

            # --- events.json へ直接自動登録（人間確認なし） ---
            events_by_id[eid] = {k: record.get(k) for k in EVENT_FIELDS if k in record}

    # 既存イベントも含め、開催日が過去になったものはサイトから自動的に間引く
    before_count = len(events_by_id)
    events_by_id = {
        eid: e for eid, e in events_by_id.items() if not is_past_date(e.get("date", ""))
    }
    pruned = before_count - len(events_by_id)
    if pruned:
        print(f"[INFO] 開催日が過去になった既存イベント {pruned}件をevents.jsonから削除しました")

    save_json(EVENTS_PATH, list(events_by_id.values()))
    save_json(CANDIDATES_PATH, candidates_log)
    save_json(GEOCACHE_PATH, geocache)

    summary_lines = []
    for key, label in [
        ("discovered", "🔎 新規発見サイト"), ("new", "🆕 新規登録"),
        ("date_changed", "📅 日程変更"), ("cancelled", "🚫 中止"),
        ("application_open", "📝 申込開始"),
    ]:
        if report[key]:
            summary_lines.append(f"### {label} ({len(report[key])}件)")
            summary_lines.extend(f"- {t}" for t in report[key])
    if report["skipped_past"]:
        summary_lines.append(f"\n開催日が既に過ぎていたため {report['skipped_past']}件を除外しました。")
    if pruned:
        summary_lines.append(f"開催日超過につき既存イベント {pruned}件を自動削除しました。")
    summary = "\n".join(summary_lines) if summary_lines else "変化はありませんでした。"

    with open(os.path.join(ROOT, "run_summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)

    print("\n" + summary)


if __name__ == "__main__":
    main()
