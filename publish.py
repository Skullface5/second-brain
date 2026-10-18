#!/usr/bin/env python
"""publish.py — merge ~/second-brain/news/*.jsonl into data/news.json for the site.
Enriches HF model rows with live stats (downloads/likes/task/tags), cached so each
model is fetched once. Categories: hf_trending -> model, else news.
"""
import json, os, glob, sys, time, hashlib
from datetime import datetime, timezone
from curl_cffi import requests

ROOT = os.path.expanduser("~/second-brain")
NEWS = os.path.join(ROOT, "news")
OUT = os.path.join(ROOT, "data", "news.json")
FULL_DIR = os.path.join(ROOT, "data", "full")
FULL_INDEX = os.path.join(FULL_DIR, "index.json")
FT_CACHE = os.path.join(ROOT, ".cache", "fulltext.json")
CACHE = os.path.join(ROOT, ".cache", "hf_meta.json")
LLM_CACHE = os.path.join(ROOT, ".cache", "llm.json")


def item_id(url, title):
    """Same id rule as second_brain_archive.py, so a shard name == an archive id."""
    return hashlib.sha1(((url or "") + "||" + (title or "")).encode("utf-8", "replace")).hexdigest()[:16]


def load_llm():
    if os.path.exists(LLM_CACHE):
        try:
            return json.load(open(LLM_CACHE))
        except Exception:
            return {}
    return {}


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
    llm = load_llm()
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
                lg = llm.get(key)
                if lg:
                    if lg.get("summary"):
                        it["summary"] = lg["summary"]
                    if lg.get("tags"):
                        it["tags"] = lg["tags"]
                it["source"] = name
                it["category"] = cat_of(name)
                items.append(it)

    save_cache(cache)
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    items = items[:2000]

    # ---- C1/C2: give every item a stable id, overlay the full text, write shards.
    # news.json stays small (metadata + summary + feed body); the whole page lives
    # in data/full/<id>.json so the phone downloads a page only when it opens it.
    ft = {}
    if os.path.exists(FT_CACHE):
        try:
            ft = json.load(open(FT_CACHE))
        except Exception:
            ft = {}
    os.makedirs(FULL_DIR, exist_ok=True)
    shards, with_full = [], 0
    for it in items:
        url = (it.get("url") or "").strip()
        it["id"] = item_id(url, it.get("title", ""))
        rec = ft.get(url) or {}
        full = (rec.get("full") or "").strip()
        it["has_full"] = bool(full)
        it["word_count"] = rec.get("word_count") or len((it.get("content") or "").split())
        if not full:
            continue
        with_full += 1
        shards.append(it["id"])
        with open(os.path.join(FULL_DIR, it["id"] + ".json"), "w") as f:
            json.dump({"id": it["id"], "url": url, "title": it.get("title", ""),
                       "source": it.get("source", ""), "date": it.get("date", ""),
                       "word_count": it["word_count"],
                       "fetched_at": rec.get("fetched_at", ""),
                       "full": full}, f, ensure_ascii=False)
    json.dump({"count": len(shards), "ids": sorted(shards)}, open(FULL_INDEX, "w"))

    # ---- E: per-source health, read straight off the scraper state files.
    health = {}
    for st_path in sorted(glob.glob(os.path.join(ROOT, "scrapers", ".state_*.json"))):
        name = os.path.basename(st_path)[len(".state_"):-len(".json")]
        try:
            st = json.load(open(st_path))
        except Exception:
            continue
        runs = st.get("runs") or []
        last = runs[-1] if runs else {}
        recent = runs[-3:]
        health[name] = {
            "tracked": len(st.get("seen", [])),
            "last_run": last.get("at"),
            "last_new": last.get("new", 0),
            "ok": bool(last.get("ok", False)),
            "error": last.get("error", ""),
            # three dead runs in a row = a source that quietly stopped working
            "stale": bool(recent) and all(r.get("new", 0) == 0 for r in recent),
        }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    payload = {"generated": datetime.now(timezone.utc).isoformat(), "count": len(items),
               "full_count": with_full, "sources": health, "items": items}
    with open(OUT, "w") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    by = {}
    for i in items:
        by[i["source"]] = by.get(i["source"], 0) + 1
    print(f"publish: {len(items)} items ({with_full} with full text -> data/full/) -> {OUT}")
    for k in sorted(by):
        print(f"  {k}: {by[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())