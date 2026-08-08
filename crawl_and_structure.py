#!/usr/bin/env python3
"""
法学イベント自動巡回・AI構造化スクリプト

やること:
  1. data/sources.json の各サイトを巡回してテキストを取得
  2. Claude API に投げてイベント候補をJSON構造化
     (一般参加可否・申込状況・中止/日程変更の判定も同時に行う)
  3. 既存の data/events.json / data/candidates.json と突合して差分検出
     - 新規イベント
     - 日程変更
     - 中止
     - 申込開始
  4. 結果を data/candidates.json に書き出す（events.json は直接編集しない＝人間確認を必ず挟む）
  5. 会場をジオコーディングして東京駅/横浜駅からの距離を付与（行きやすさランキング用）

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
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "data", "sources.json")
EVENTS_PATH = os.path.join(ROOT, "data", "events.json")
CANDIDATES_PATH = os.path.join(ROOT, "data", "candidates.json")
GEOCACHE_PATH = os.path.join(ROOT, "data", "geocache.json")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = "claude-sonnet-5"

# 東京駅・横浜駅（行きやすさランキングの基準点）
TOKYO_STA = (35.681236, 139.767125)
YOKOHAMA_STA = (35.465807, 139.622235)

USER_AGENT = "legal-events-jp-bot/1.0 (+https://github.com/pigab12345/legal-events_JP; contact via GitHub issues)"

EXTRACTION_PROMPT = """あなたは日本の法学系イベント情報を抽出する専門アシスタントです。
以下はあるWebページのテキストです。このページに含まれる「公開シンポジウム・学会・講演会・研究会」などの
法学関連イベントを可能な限り正確に抽出し、次のJSON配列だけを出力してください（説明文・コードフェンス禁止）。

イベントでない一般ニュースや無関係な内容は含めないでください。情報が不明な項目は空文字 "" にしてください。
推測で埋めず、ページに書かれている内容のみを根拠にしてください。

各要素のフィールド:
- title: イベント名
- date: 開催日 (YYYY-MM-DD形式。不明ならば "")
- time: 開催時刻 (例 "13:00〜17:00"、不明なら "")
- org: 主催者
- area: 都道府県名など (例 "東京", "神奈川", "オンライン")
- place: 会場名・住所
- fee: 参加費 (例 "無料", "非会員2000円")
- access: 一般参加可否。"可" "不可" "要問合せ" のいずれか。会員限定なら"不可"、
  非会員も参加費等の条件付きで参加可能なら"可"としてください。
- speakers: 登壇者（カンマ区切り文字列）
- fields: 分野タグの配列 (例 ["民法","AI・法"])
- application_status: "未開始" "受付中" "締切" "不明" のいずれか
- cancelled: 中止・延期の記載があれば true、なければ false
- source: このページのURL
- confidence: "公式確認済" 固定（このページ自体が主催者公式ページの場合）

ページURL: {url}
ページ本文:
---
{text}
---
"""


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


def call_claude(url: str, text: str) -> list:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません")
    prompt = EXTRACTION_PROMPT.format(url=url, text=text)
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    raw = "".join(b.get("text", "") for b in data.get("content", []))
    raw = raw.strip()
    raw = re.sub(r"^```json|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        print(f"[WARN] JSON解析失敗: {url}\n{raw[:500]}", file=sys.stderr)
        return []


def make_id(ev: dict) -> str:
    key = f"{ev.get('org','')}|{ev.get('title','')}".strip()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


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
    candidates = load_json(CANDIDATES_PATH, [])
    geocache = load_json(GEOCACHE_PATH, {})

    events_by_id = {make_id(e): e for e in events}
    candidates_by_id = {c["id"]: c for c in candidates}

    now = datetime.now(timezone.utc).isoformat()
    report = {"new": [], "date_changed": [], "cancelled": [], "application_open": []}

    for src in sources:
        url = src["url"]
        print(f"[INFO] 巡回中: {src.get('name', url)} ({url})")
        try:
            text = fetch_text(url)
        except Exception as e:
            print(f"[WARN] 取得失敗 {url}: {e}", file=sys.stderr)
            continue

        try:
            extracted = call_claude(url, text)
        except Exception as e:
            print(f"[WARN] AI構造化失敗 {url}: {e}", file=sys.stderr)
            continue

        for ev in extracted:
            eid = make_id(ev)
            lat, lng = geocode(ev.get("place", ""), ev.get("area", ""), geocache)
            dist_tokyo = haversine_km(TOKYO_STA, (lat, lng)) if lat else None
            dist_yokohama = haversine_km(YOKOHAMA_STA, (lat, lng)) if lat else None

            candidate = {
                "id": eid,
                "checked_at": now,
                "lat": lat,
                "lng": lng,
                "dist_km_from_tokyo": round(dist_tokyo, 1) if dist_tokyo is not None else None,
                "dist_km_from_yokohama": round(dist_yokohama, 1) if dist_yokohama is not None else None,
                **ev,
            }

            old_event = events_by_id.get(eid)
            old_candidate = candidates_by_id.get(eid)

            tags = []
            if not old_event and not old_candidate:
                tags.append("new")
                report["new"].append(candidate["title"])
            if old_event and old_event.get("date") and ev.get("date") and old_event["date"] != ev["date"]:
                tags.append("date_changed")
                candidate["previous_date"] = old_event["date"]
                report["date_changed"].append(candidate["title"])
            if ev.get("cancelled") and not (old_event or {}).get("cancelled"):
                tags.append("cancelled")
                report["cancelled"].append(candidate["title"])
            prev_status = (old_event or old_candidate or {}).get("application_status")
            if ev.get("application_status") == "受付中" and prev_status in ("未開始", None, ""):
                tags.append("application_open")
                report["application_open"].append(candidate["title"])

            candidate["review_tags"] = tags
            candidates_by_id[eid] = candidate

    save_json(CANDIDATES_PATH, list(candidates_by_id.values()))
    save_json(GEOCACHE_PATH, geocache)

    summary_lines = []
    for key, label in [
        ("new", "🆕 新規候補"), ("date_changed", "📅 日程変更"),
        ("cancelled", "🚫 中止"), ("application_open", "📝 申込開始"),
    ]:
        if report[key]:
            summary_lines.append(f"### {label} ({len(report[key])}件)")
            summary_lines.extend(f"- {t}" for t in report[key])
    summary = "\n".join(summary_lines) if summary_lines else "変化はありませんでした。"

    with open(os.path.join(ROOT, "run_summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)

    print("\n" + summary)


if __name__ == "__main__":
    main()
