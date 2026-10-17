
#!/usr/bin/env python
"""Generic RSS/Atom feed watcher. Watermark dedup by entry GUID/link.
Usage: python rss_scraper.py <name> <feed_url>
"""
import json, os, sys
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from curl_cffi import requests

ROOT = os.path.expanduser("~/second-brain")


def fetch(feed_url):
    r = requests.get(feed_url, impersonate="chrome", timeout=30)
    r.raise_for_status()
    return ET.fromstring(r.content)


def entries_from(root):
    out = []
    for e in root.iter():
        tag = e.tag.split("}")[-1]
        if tag in ("item", "entry"):
            d = {}
            for c in e:
                t = c.tag.split("}")[-1].lower()
                if t == "link":
                    d.setdefault("link", c.get("href") or c.get("url") or (c.text or "").strip())
                elif t in ("guid", "id"):
                    d.setdefault("guid", (c.text or "").strip())
                else:
                    d[t] = (c.text or "").strip()
            out.append(d)
    return out


def key_for(e, name):
    return e.get("guid") or e.get("id") or e.get("link") or f"{name}|{e.get('title')}|{e.get('pubdate')}"


def main():
    if len(sys.argv) != 3:
        print("usage: rss_scraper.py <name> <feed_url>")
        sys.exit(2)
    name, feed_url = sys.argv[1], sys.argv[2]

    root = fetch(feed_url)
    entries = entries_from(root)
    state_path = os.path.join(ROOT, "scrapers", f".state_{name}.json")
    state = {"seen": []}
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
    seen = set(state["seen"])

    new_rows = []
    for e in entries:
        if not (e.get("title") or e.get("link")):
            continue
        k = key_for(e, name)
        if not k or k in seen:
            continue
        seen.add(k)
        new_rows.append({
            "title": e.get("title", "")[:300],
            "link": e.get("link", ""),
            "summary": e.get("description", e.get("summary", ""))[:600],
            "published": e.get("pubdate", e.get("published", "")),
        })

    state["seen"] = sorted(seen)
    with open(state_path, "w") as f:
        json.dump(state, f)

    if new_rows:
        out_dir = os.path.join(ROOT, "news")
        os.makedirs(out_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        with open(os.path.join(out_dir, f"{name}_{stamp}.jsonl"), "a") as f:
            for row in new_rows:
                f.write(json.dumps(row) + "\n")

    print(f"scraper={name} fetched={len(entries)} new={len(new_rows)} tracked={len(seen)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
