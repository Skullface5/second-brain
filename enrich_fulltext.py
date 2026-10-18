#!/usr/bin/env python
"""enrich_fulltext.py — hold the WHOLE page, not just a link (workstream C1).

For every harvested item that has a URL and no cached full text yet, fetch the
page with Tavily Extract: it runs server-side (zero metered bandwidth here),
returns clean text, and defeats the blocks that kill a local fetch.

* cache:  .cache/fulltext.json  {url: {full, word_count, fetched_at, status}}
          (a page is never fetched twice — that is what keeps the credits flat)
* budget: FULLTEXT_BUDGET urls per run (default 60), FULLTEXT_DAILY per day
          (default 150), shared counter with generic_source.py
* order:  sources listed in priority.json / env FULLTEXT_PRIORITY first, newest first
* writes NOTHING to news/*.jsonl (append-only) — publish.py overlays the text

Usage:  enrich_fulltext.py [--only src1,src2] [--budget N] [--dry] [--stats]
"""
import argparse, glob, json, os, re, subprocess, sys, time

ROOT = os.path.expanduser("~/second-brain")
NEWS = os.path.join(ROOT, "news")
CACHE = os.path.join(ROOT, ".cache", "fulltext.json")
TAVILY = os.path.expanduser("~/.local/bin/tavily")
USAGE = os.path.expanduser("~/.hermes/cache/fulltext_usage.json")
BATCH = 5                      # Tavily bills 1 credit per 5 urls
RUN_BUDGET = int(os.environ.get("FULLTEXT_BUDGET", "60"))
DAILY = int(os.environ.get("FULLTEXT_DAILY", "150"))
# teaser-only or blocked sources: their feed never carries the article
PRIORITY = [s for s in os.environ.get(
    "FULLTEXT_PRIORITY",
    "mit-tech-review,venturebeat,ars,verge,hn").split(",") if s]


def load_json(p, default):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def budget(count):
    """Trim `count` to what this run and this day still allow."""
    day = time.strftime("%Y%m%d", time.gmtime())
    u = load_json(USAGE, {})
    if u.get("day") != day:
        u = {"day": day, "urls": 0}
    return max(0, min(count, RUN_BUDGET - u.get("run", 0), DAILY - u.get("urls", 0)))


def spend(n):
    day = time.strftime("%Y%m%d", time.gmtime())
    u = load_json(USAGE, {})
    if u.get("day") != day:
        u = {"day": day, "urls": 0}
    u["urls"] = u.get("urls", 0) + n
    u["run"] = u.get("run", 0) + n
    u["last"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_json(USAGE, u)


def extract(urls):
    """One Tavily /extract call for up to BATCH urls -> {url: raw_content}."""
    try:
        p = subprocess.run([TAVILY, "extract", *urls, "--json"],
                           capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return {}
    if p.returncode != 0:
        return {}
    try:
        body = json.loads(p.stdout)
    except Exception:
        return {}
    out = {}
    for r in body.get("results") or []:
        if r.get("url"):
            out[r["url"]] = r.get("raw_content") or ""
    return out


def load_modes():
    """Per-source full-text preference: never | auto | always.

    Read off the EFFECTIVE source list, so a `full text` choice made in the site's
    Sources panel (Supabase) counts, not just the seed file.
    """
    sys.path.insert(0, os.path.join(ROOT, "scrapers"))
    try:
        from generic_source import load_sources
        return {s["name"]: (s.get("fulltext") or "auto") for s in load_sources()}
    except Exception:
        try:
            data = json.load(open(os.path.join(ROOT, "scrapers", "sources.json")))
            return {s["name"]: (s.get("fulltext") or "auto") for s in data.get("sources", [])}
        except Exception:
            return {}


def candidates(only=None):
    """Every item with a URL that has no full text yet, newest first, priority first."""
    cache = load_json(CACHE, {})
    modes = load_modes()
    rows = []
    for path in sorted(glob.glob(os.path.join(NEWS, "*.jsonl"))):
        name = os.path.basename(path).rsplit("_", 1)[0]
        if only and name not in only:
            continue
        if modes.get(name) == "never":
            continue          # metadata-only source (an API listing): no page to hold
        try:
            day = int(os.path.basename(path).rsplit("_", 1)[1].split(".")[0])
        except Exception:
            day = 0
        for line in open(path, errors="replace"):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            url = (row.get("link") or row.get("url") or "").strip()
            if not url.startswith("http") or url in cache:
                continue
            if name in PRIORITY:
                prio = PRIORITY.index(name)
            elif modes.get(name) == "always":
                prio = -1
            else:
                prio = len(PRIORITY)
            rows.append((prio, -day, name, url))
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    seen, out = set(), []
    for prio, _d, name, url in rows:
        if url in seen:
            continue
        seen.add(url)
        out.append((name, url))
    return out, cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--budget", type=int)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    only = set(x.strip() for x in a.only.split(",")) if a.only else None

    global RUN_BUDGET
    if a.budget:
        RUN_BUDGET = a.budget

    cache = load_json(CACHE, {})
    if a.stats:
        have = {u: v for u, v in cache.items() if v.get("full")}
        words = sum(v.get("word_count", 0) for v in have.values())
        print("fulltext cache: %d urls (%d with text, %.1fM words, %.1f MB on disk)"
              % (len(cache), len(have), words / 1e6,
                 os.path.getsize(CACHE) / 1e6 if os.path.exists(CACHE) else 0))
        return 0

    todo, cache = candidates(only)
    allow = budget(len(todo))
    todo = todo[:allow]
    print("fulltext: %d url(s) missing, %d allowed this run (cap %d/run, %d/day)"
          % (len(todo), allow, RUN_BUDGET, DAILY))
    if a.dry or not todo:
        for name, url in todo[:10]:
            print("   would fetch [%s] %s" % (name, url))
        return 0

    ok = empty = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        urls = [u for _n, u in batch]
        got = extract(urls)
        spend(len(urls))
        for name, url in batch:
            raw = (got.get(url) or "").strip()
            rec = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "source": name, "status": "ok" if raw else "empty",
                   "full": raw, "word_count": len(raw.split())}
            if not raw:
                empty += 1
            else:
                ok += 1
            cache[url] = rec
        save_json(CACHE, cache)
        print("   batch %d/%d: %d/%d fetched" % (min(i + BATCH, len(todo)), len(todo),
                                                 sum(1 for _n, u in batch if got.get(u)), len(batch)),
              flush=True)
    print("fulltext: stored %d page(s), %d empty; cache=%d" % (ok, empty, len(cache)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
