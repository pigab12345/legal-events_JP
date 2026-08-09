#!/usr/bin/env python3
"""
人間が内容を確認した candidates.json の項目を events.json へ反映するための補助スクリプト。
events.json を直接AIに書き換えさせないための「人間確認」ステップ用。

使い方:
  python scripts/promote_candidate.py <candidate_id> [--confidence 公式確認済]
  python scripts/promote_candidate.py --remove <candidate_id>   # 中止イベントの削除など
  python scripts/promote_candidate.py --list                    # 候補一覧表示
"""
import os
import sys
import json
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS_PATH = os.path.join(ROOT, "data", "events.json")
CANDIDATES_PATH = os.path.join(ROOT, "data", "candidates.json")

FIELDS_FOR_EVENTS = [
    "id", "title", "date", "time", "org", "area", "place", "fee",
    "access", "speakers", "fields", "confidence", "source",
    "application_status", "cancelled", "lat", "lng",
    "dist_km_from_tokyo", "dist_km_from_yokohama",
]


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate_id", nargs="?")
    ap.add_argument("--confidence", default="公式確認済")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    candidates = load(CANDIDATES_PATH, [])
    events = load(EVENTS_PATH, [])

    if args.list:
        for c in candidates:
            tags = ",".join(c.get("review_tags", [])) or "-"
            print(f"{c['id']}  [{tags}]  {c.get('date','????-??-??')}  {c.get('title','')}")
        return

    if not args.candidate_id:
        ap.error("candidate_id を指定してください（--list で一覧表示）")

    cand = next((c for c in candidates if c["id"] == args.candidate_id), None)
    if not cand:
        print(f"candidate id {args.candidate_id} が見つかりません", file=sys.stderr)
        sys.exit(1)

    idx = next((i for i, e in enumerate(events) if e.get("id") == cand["id"]), None)

    if args.remove:
        if idx is not None:
            del events[idx]
            save(EVENTS_PATH, events)
            print(f"events.json から削除しました: {cand.get('title')}")
        else:
            print("該当イベントは events.json に存在しません")
        return

    record = {k: cand.get(k) for k in FIELDS_FOR_EVENTS if k in cand}
    record["confidence"] = args.confidence

    if idx is not None:
        events[idx] = {**events[idx], **record}
        print(f"更新しました: {record.get('title')}")
    else:
        events.append(record)
        print(f"追加しました: {record.get('title')}")

    save(EVENTS_PATH, events)


if __name__ == "__main__":
    main()
