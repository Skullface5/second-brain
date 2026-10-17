#!/usr/bin/env python
"""publish.py — merge all ~/second-brain/news/*.jsonl into data/news.json for the site.
Categories: hf_trending -> model, everything else -> news.
"""
import json, os, glob, sys
from datetime import datetime, timezone

ROOT = os.path.expanduser("~/second-brain")
NEWS = os.path.join(ROOT, "news")
OUT = os.path.join(ROOT, "data", "news.json")


def cat_of(name):
    return "model" if name.startswith("hf") else "news"


def norm(name, row):
    if name.startswith("hf"):
        return {
            "title": row.get("repo", ""),
            "url": row.get("url", ""),
            "summary": "trending: " + (row.get("bucket", "").replace("trending", "trending ").strip().lower()),
            "date": row.get("first_seen", ""),
        }
    return {
        "title": row.get("title", ""),
        "url": row.get("link", ""),
        "summary": row.get("summary", ""),
        "date": row.get("published", "") or row.get("first_seen", ""),
    }


def main():
    items, seen = [], set()
    for path in sorted(glob.glob(os.path.join(NEWS, "*.jsonl"))):
        name = os.path.basename(path).rsplit("_", 1)[0]
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                it = norm(name, row)
                if not (it["title"] or it["url"]):
                    continue
                key = (it["url"] or it["title"]).strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                it["source"] = name
                it["category"] = cat_of(name)
                items.append(it)

    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    items = items[:2000]  # cap so the file/deploy stays small on slow net

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(), "count": len(items), "items": items}
    with open(OUT, "w") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    by = {}
    for i in items:
        by[i["source"]] = by.get(i["source"], 0) + 1
    print("publish: " + str(len(items)) + " items -> " + OUT)
    for k in sorted(by):
        print("  " + k + ": " + str(by[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())