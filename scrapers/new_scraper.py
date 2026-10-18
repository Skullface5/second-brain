#!/usr/bin/env python
"""new_scraper.py — scaffold a custom scraper when a config entry cannot express it.

  new_scraper.py mysite --kind html --url https://mysite.com/blog
  new_scraper.py mysite --kind rss  --url https://mysite.com/feed.xml

Writes scrapers/scrape_<name>.py from a working template (retries, watermark,
JSONL append, the farm's output contract) and prints the jobs.json entry to add.
Prefer adding an entry to sources.json — that needs no code at all.
"""
import argparse, json, os, re, stat, sys

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = os.path.join(HERE, "jobs.json")

TEMPLATE = '''#!/usr/bin/env python
"""{name} — custom scraper (scaffolded by new_scraper.py).

Contract: watermark dedup in .state_{name}.json, append news/{name}_<date>.jsonl,
print `scraper={name} new=N tracked=M`, exit 0.
"""
import json, os, re, sys, time
from datetime import datetime
from curl_cffi import requests

ROOT = os.path.expanduser("~/second-brain")
NEWS = os.path.join(ROOT, "news")
STATE = os.path.join(ROOT, "scrapers", ".state_{name}.json")
URL = {url!r}


def fetch(url, tries=3, timeout=45):
    last = None
    for _ in range(tries):
        try:
            r = requests.get(url, impersonate="chrome", timeout=timeout)
            if r.status_code < 400:
                return r
            last = "HTTP %s" % r.status_code
        except Exception as e:
            last = str(e)[:120]
        time.sleep(2)
    raise RuntimeError("fetch failed: %s" % last)


def scrape():
    """Return a list of {{title, link, summary, content, published}} dicts."""
{body}


def main():
    st = {{"seen": []}}
    if os.path.exists(STATE):
        try:
            st = json.load(open(STATE))
        except Exception:
            pass
    seen = set(st.get("seen", []))
    rows = scrape()
    new = []
    for row in rows:
        key = row.get("guid") or row.get("link") or row.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        row.setdefault("source", {name!r})
        new.append(row)
    st["seen"] = sorted(seen)
    json.dump(st, open(STATE, "w"))
    if new:
        os.makedirs(NEWS, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        with open(os.path.join(NEWS, "{name}_%s.jsonl" % stamp), "a") as f:
            for row in new:
                f.write(json.dumps(row, ensure_ascii=False) + "\\n")
    print("scraper={name} fetched=%d new=%d tracked=%d" % (len(rows), len(new), len(seen)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

BODIES = {
    "rss": '''    from xml.etree import ElementTree as ET
    root = ET.fromstring(fetch(URL).content)
    out = []
    for e in root.iter():
        if e.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = link = summary = published = ""
        for c in e:
            t = c.tag.split("}")[-1].lower()
            if t == "link":
                link = c.get("href") or c.get("url") or (c.text or "").strip()
            elif t == "title":
                title = (c.text or "").strip()
            elif t in ("description", "summary"):
                summary = summary or (c.text or "")
            elif t in ("pubdate", "published"):
                published = (c.text or "").strip()
        if title or link:
            out.append({{"title": title, "link": link, "summary": summary[:280],
                        "content": summary[:6000], "published": published}})
    return out''',
    "html": '''    from lxml import html as LH
    doc = LH.fromstring(fetch(URL).content)
    out = []
    for n in doc.xpath("//article"):
        t = n.xpath(".//h2//text() | .//h3//text()")
        a = n.xpath(".//a/@href")
        p = n.xpath(".//p//text()")
        out.append({{
            "title": " ".join(t).strip()[:300],
            "link": (a[0] if a else ""),
            "summary": re.sub(r"\\s+", " ", " ".join(p))[:280],
            "content": " ".join(p)[:6000],
            "published": "",
        }})
    return [r for r in out if r["title"] or r["link"]]''',
    "json": '''    data = fetch(URL).json()
    out = []
    for it in (data if isinstance(data, list) else data.get("items", [])):
        out.append({{
            "title": str(it.get("title") or it.get("name") or "")[:300],
            "link": it.get("url") or it.get("link") or "",
            "summary": str(it.get("description") or "")[:280],
            "content": "",
            "published": str(it.get("created_at") or it.get("date") or ""),
        }})
    return out''',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--kind", choices=sorted(BODIES), default="html")
    ap.add_argument("--url", required=True)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    name = re.sub(r"[^a-z0-9_\-]", "_", a.name.lower())
    path = os.path.join(HERE, "scrape_%s.py" % name)
    if os.path.exists(path) and not a.dry:
        sys.exit("%s already exists — pick another name or edit it directly" % path)
    src = TEMPLATE.format(name=name, url=a.url, body=BODIES[a.kind])
    entry = {"name": name, "module": "scrape_%s.py" % name, "args": []}

    if a.dry:
        print(src)
        print("--- jobs.json entry ---")
        print(json.dumps(entry))
        return 0

    with open(path, "w") as f:
        f.write(src)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    jobs = json.load(open(JOBS))
    if not any(j.get("name") == name for j in jobs.get("scrapers", [])):
        jobs.setdefault("scrapers", []).append(entry)
        json.dump(jobs, open(JOBS, "w"), indent=2)
        print("jobs.json: added %s" % name)
    print("wrote %s (kind=%s)\nrun it with:  %s/scrape_%s.py"
          % (path, a.kind, os.path.dirname(HERE), name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
