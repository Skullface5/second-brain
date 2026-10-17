#!/usr/bin/env python
"""publish.py — merge ~/second-brain/news/*.jsonl into data/news.json for the site.
Enriches HF model rows with live stats (downloads/likes/task/tags), cached so each
model is fetched once. Categories: hf_trending -> model, else news.
"""
import json, os, glob, sys, time
from datetime import datetime, timezone
from curl_cffi import requests

ROOT = os.path.expanduser("~/second-brain")
NEWS = os.path.join(ROOT, "news")
OUT = os.path.join(ROOT, "data", "news.json")
CACHE = os.path.join(ROOT, ".cache", "hf_meta.json")


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            return json.load(f)
    return {}


def save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f:
        json.dump(c, f)


def hf_meta(repo, cache, budget):
    if repo in cache:
        return cache[repo]
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    for _ in range(2):
        try:
            d = requests.get(f"https://huggingface.co/api/models/{repo}",
                             impersonate="chrome", timeout=25).json()
            m = {
                "downloads": d.get("downloads", 0),
                "likes": d.get("likes", 0),
                "pipeline": d.get("pipeline_tag") or "",
                "library": d.get("library_name") or "",
                "tags": [t for t in (d.get("tags") or []) if not t.startswith(("transformers", "safetensors", "region:", "license:"))][:6],
                "created": (d.get("createdAt") or "")[:10],
            }
            cache[repo] = m
            return m
        except Exception:
            time.sleep(1)
    return None


def fmt_n(n):
    if not n:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def cat_of(name):
    return "model" if name.startswith("hf") else "news"


def build_model(name, row, cache, budget):
    repo = row.get("repo", "")
    it = {"title": repo, "url": f"https://huggingface.co/{repo}",
          "summary": "", "content": "", "date": row.get("first_seen", "")}
    m = hf_meta(repo, cache, budget)
    if m:
        it["meta"] = m
        bits = [b for b in [m["pipeline"], m["library"],
                            fmt_n(m["downloads"]) + " downloads",
                            fmt_n(m["likes"]) + " likes"] if b]
        it["summary"] = " · ".join(bits)
        if m["tags"]:
            it["tags"] = m["tags"]
    else:
        it["summary"] = "trending: " + row.get("bucket", "").replace("trending", "trending ").strip().lower()
    return it


def build_news(name, row):
    return {
        "title": row.get("title", ""), "url": row.get("link", ""),
        "summary": row.get("summary", ""), "content": row.get("content", ""),
        "date": row.get("published", "") or row.get("first_seen", ""),
    }


def main():
    cache = load_cache()
    # fetch budget per run: don't hammer HF or the net on slow links
    budget = [40]
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
                it = build_model(name, row, cache, budget) if name.startswith("hf") else build_news(name, row)
                if not (it["title"] or it["url"]):
                    continue
                key = (it["url"] or it["title"]).strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                it["source"] = name
                it["category"] = cat_of(name)
                items.append(it)

    save_cache(cache)
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    items = items[:2000]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(), "count": len(items), "items": items}
    with open(OUT, "w") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    by = {}
    for i in items:
        by[i["source"]] = by.get(i["source"], 0) + 1
    print(f"publish: {len(items)} items -> {OUT}")
    for k in sorted(by):
        print(f"  {k}: {by[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())