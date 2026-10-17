#!/usr/bin/env python
"""HF trending scraper — dumps NEW trending models to ~/second-brain/news/ as JSONL.

First scraper in the farm. Marks seen repo IDs in .state_hf.json so re-runs
only append genuinely-new items (watermark dedup). Safe to cron every N hours.
"""
import json, os, sys
from datetime import datetime, timezone
from curl_cffi import requests

ROOT = os.path.expanduser("~/second-brain")
OUT = os.path.join(ROOT, "news")
STATE = os.path.join(ROOT, "scrapers", ".state_hf.json")


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"seen": []}


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(s, f)


def main():
    r = requests.get("https://huggingface.co/api/trending",
                     impersonate="chrome", timeout=30)
    r.raise_for_status()
    data = r.json()

    state = load_state()
    seen = set(state["seen"])
    new_rows = []

    for bucket in ("recentlyTrending", "trendingThisWeek", "trendingThisMonth"):
        for item in data.get(bucket, []):
            rid = item.get("repoData", {}).get("id") or item.get("repoName")
            if not rid:
                continue
            if rid not in seen:
                seen.add(rid)
                new_rows.append({
                    "repo": rid,
                    "bucket": bucket,
                    "url": f"https://huggingface.co/{rid}",
                    "first_seen": datetime.now(timezone.utc).isoformat(),
                })

    state["seen"] = sorted(seen)
    save_state(state)

    if new_rows:
        os.makedirs(OUT, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        with open(os.path.join(OUT, f"hf_trending_{stamp}.jsonl"), "a") as f:
            for row in new_rows:
                f.write(json.dumps(row) + "\n")

    print(f"scraper=hf_trending new={len(new_rows)} tracked={len(seen)}")
    return 0 if not new_rows else 0


if __name__ == "__main__":
    sys.exit(main())
