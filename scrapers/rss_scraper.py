#!/usr/bin/env python
"""RSS/Atom feed watcher with content extraction. Watermark dedup by guid/link.
Usage: rss_scraper.py <name> <feed_url>
Stores title, link, summary, content (text from content:encoded / Atom content),
published. Handles both RSS and Atom.
"""
import re, html, json, os, sys, time
from datetime import datetime
from xml.etree import ElementTree as ET
from curl_cffi import requests

ROOT = os.path.expanduser("~/second-brain")


def get(url, tries=3):
    last = None
    for _ in range(tries):
        try:
            return requests.get(url, impersonate="chrome", timeout=40)
        except Exception as e:
            last = e
            time.sleep(2)
    raise last


def to_text(h):
    if not h:
        return ""
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>|</p>|</div>", "\n\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    t = html.unescape(h)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def entries_from(root):
    out = []
    for e in root.iter():
        if e.tag.split("}")[-1] not in ("item", "entry"):
            continue
        d = {"content": ""}
        for c in e:
            t = c.tag.split("}")[-1].lower()
            if t == "link":
                d.setdefault("link", c.get("href") or c.get("url") or (c.text or "").strip())
            elif t in ("guid", "id"):
                d.setdefault("guid", (c.text or "").strip())
            elif t in ("encoded", "content"):
                if len(c.text or "") > len(d["content"]):
                    d["content"] = c.text or ""
            elif t == "description" and not d.get("description"):
                d["description"] = c.text or ""
            elif t in ("title", "pubdate", "published", "summary"):
                d.setdefault(t, (c.text or "").strip())
        out.append(d)
    return out


def key_for(e, name):
    return e.get("guid") or e.get("id") or e.get("link") or f"{name}|{e.get('title')}"


def main():
    if len(sys.argv) != 3:
        print("usage: rss_scraper.py <name> <feed_url>")
        sys.exit(2)
    name, feed_url = sys.argv[1], sys.argv[2]

    entries = entries_from(ET.fromstring(get(feed_url).content))
    state_path = os.path.join(ROOT, "scrapers", f".state_{name}.json")
    state = {"seen": []}
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
    seen = set(state["seen"])

    new_rows = []
    for e in entries:
        k = key_for(e, name)
        if not k or k in seen or not (e.get("title") or e.get("link")):
            continue
        seen.add(k)
        body = to_text(e.get("content")) or to_text(e.get("description"))
        teaser = to_text(e.get("description")) or body
        new_rows.append({
            "title": (e.get("title") or "")[:300],
            "link": e.get("link", ""),
            "summary": re.sub(r"\s+", " ", teaser)[:280],
            "content": body[:6000],
            "published": e.get("pubdate") or e.get("published", ""),
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