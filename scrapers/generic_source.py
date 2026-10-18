#!/usr/bin/env python
"""generic_source.py — ONE engine, five source kinds, driven by sources.json.

Kinds
  rss      feed_url -> title/link/summary/content/published
  json     API -> items via `lists` (or `items_path`) + field dot-paths
  html     index page -> items via XPath selectors
  extract  Tavily Extract: full page text for a source the box cannot fetch
           mode "index"  : index_url -> links (regex) -> Extract each item
           mode "urls"   : a fixed `urls` list -> Extract each
  search   Tavily Search for a saved query (topic tracking)

Contract (same as every other farm scraper):
  * watermark dedup in .state_<name>.json — a second run prints new=0
  * appends news/<name>_<YYYYMMDD>.jsonl, never rewrites history
  * prints `scraper=<name> new=N tracked=M`
  * per-run budget for the paid/counted Tavily calls (FULLTEXT_BUDGET)

Usage
  generic_source.py                 # every enabled source
  generic_source.py --list          # what is configured + last run stats
  generic_source.py --only hn,verge  # a subset
  generic_source.py --dry            # fetch + report, write nothing
"""
import argparse, hashlib, html as htmllib, json, os, re, subprocess, sys, time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

ROOT = os.path.expanduser("~/second-brain")
NEWS = os.path.join(ROOT, "news")
HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "sources.json")
TAVILY = os.path.expanduser("~/.local/bin/tavily")
BUDGET = int(os.environ.get("FULLTEXT_BUDGET", "60"))        # URLs per run
DAILY = int(os.environ.get("FULLTEXT_DAILY", "150"))          # URLs per day
USAGE = os.path.expanduser("~/.hermes/cache/fulltext_usage.json")

try:
    from curl_cffi import requests
except Exception:                                             # pragma: no cover
    requests = None

# --------------------------------------------------------------------------- fetch
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


class Resp:
    """Minimal response object for the stdlib fallback (curl_cffi absent)."""

    def __init__(self, content, status_code):
        self.content = content
        self.status_code = status_code
        self.text = content.decode("utf-8", "replace")
        self.encoding = "utf-8"

    def json(self):
        return json.loads(self.text)


def fetch(url, tries=3, timeout=45):
    """GET with retries. curl_cffi impersonates Chrome (defeats most bot walls)."""
    last = None
    for _ in range(tries):
        try:
            if requests is not None:
                r = requests.get(url, impersonate="chrome", timeout=timeout,
                                 headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
            else:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as rr:
                    r = Resp(rr.read(), rr.status)
            if int(getattr(r, "status_code", 200)) < 400:
                return r
            last = "HTTP %s" % r.status_code
        except Exception as e:
            last = str(e)[:120]
        time.sleep(2)
    raise RuntimeError("fetch failed for %s (%s)" % (url, last))


def to_text(h):
    """HTML -> readable text (same normalisation the RSS scraper uses)."""
    if not h:
        return ""
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>|</p>|</div>|</h[1-6]>", "\n\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", "", h)
    t = htmllib.unescape(h)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def squash(t, n=None):
    t = re.sub(r"\s+", " ", (t or "")).strip()
    return t[:n] if n else t


# ------------------------------------------------------------------- tavily budget
def budget_left(n_want):
    """How many Extract calls this run may still make (run cap AND daily cap)."""
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    try:
        u = json.load(open(USAGE))
    except Exception:
        u = {}
    if u.get("day") != day:
        u = {"day": day, "urls": 0}
    if u["urls"] >= DAILY:
        return 0
    return max(0, min(n_want, BUDGET - u.get("run", 0), DAILY - u["urls"]))


def spend(n):
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    try:
        u = json.load(open(USAGE))
    except Exception:
        u = {}
    if u.get("day") != day:
        u = {"day": day, "urls": 0}
    u["urls"] = u.get("urls", 0) + n
    u["run"] = u.get("run", 0) + n
    u["last"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(USAGE), exist_ok=True)
    json.dump(u, open(USAGE, "w"), indent=1)
    return u


def tavily(kind, payload, timeout=120):
    """Call the pooled Tavily CLI (13 rotating keys). Returns the parsed body."""
    cmd = [TAVILY, kind]
    if kind == "extract":
        cmd += list(payload["urls"])
    else:
        cmd += [payload["query"], "--max", str(payload.get("max", 5))]
        if payload.get("deep"):
            cmd += ["--deep"]
    cmd.append("--json")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {}
    if p.returncode != 0:
        return {}
    try:
        return json.loads(p.stdout)
    except Exception:
        return {}


# ------------------------------------------------------------------------ state
def load_state(name):
    p = os.path.join(HERE, ".state_%s.json" % name)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            pass
    return {"seen": [], "runs": []}


def save_state(name, st):
    st["seen"] = sorted(set(st.get("seen", [])))
    st["runs"] = st.get("runs", [])[-40:]
    os.makedirs(HERE, exist_ok=True)
    json.dump(st, open(os.path.join(HERE, ".state_%s.json" % name), "w"))


def append_rows(name, rows):
    if not rows:
        return
    os.makedirs(NEWS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    with open(os.path.join(NEWS, "%s_%s.jsonl" % (name, stamp)), "a") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ field access
def dig(obj, path, default=None):
    """'repoData.id' / 'a.0.b' style lookup into parsed JSON."""
    cur = obj
    for part in str(path).split("."):
        if cur is None:
            return default
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return default
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return default
    return default if cur is None else cur


def item_key(row, src):
    for f in ("guid", "id", "link", "url", "repo"):
        v = row.get(f)
        if v:
            return str(v)
    return "%s|%s" % (src.get("name"), row.get("title", ""))


# ------------------------------------------------------------------------- kinds
def kind_rss(src):
    root = ET.fromstring(fetch(src["url"]).content)
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
        body = to_text(d.get("content")) or to_text(d.get("description"))
        teaser = to_text(d.get("description")) or body
        if not (d.get("title") or d.get("link")):
            continue
        out.append({
            "title": (d.get("title") or "")[:300],
            "link": d.get("link", ""),
            "guid": d.get("guid", ""),
            "summary": squash(teaser, 280),
            "content": body[:6000],
            "published": d.get("pubdate") or d.get("published", ""),
        })
    return out


def kind_json(src):
    data = fetch(src["url"]).json()
    lists = src.get("lists")
    if lists:
        items = []
        for path in lists:
            got = dig(data, path, [])
            if isinstance(got, list):
                items += got
    else:
        got = dig(data, src.get("items_path", ""), data)
        items = got if isinstance(got, list) else [got]
    out, seen = [], set()
    for it in items:
        if not isinstance(it, dict):
            continue
        rid = dig(it, src.get("id", "id"))
        if rid in (None, ""):
            continue
        key = str(rid)
        if key in seen:
            continue
        seen.add(key)
        row = {
            "id": key,
            "repo": key,
            "url": (src.get("url_tmpl", "") or "").format(id=key) or key,
            "title": str(rid),
            "summary": "",
            "content": "",
            "first_seen": datetime.now(timezone.utc).isoformat(),
        }
        if src.get("bucket_path"):
            row["bucket"] = dig(it, src["bucket_path"], "")
        out.append(row)
    return out


def kind_html(src):
    from lxml import html as LH
    doc = LH.fromstring(fetch(src["url"]).content)
    nodes = doc.xpath(src["item_xpath"])
    out = []
    for n in nodes:
        row = {}
        for f in ("title", "link", "summary", "published", "content"):
            xp = src.get(f + "_xpath")
            if not xp:
                continue
            got = n.xpath(xp)
            if not got:
                continue
            v = got[0]
            if isinstance(v, str):
                row[f] = squash(v)
            else:
                row[f] = squash(" ".join(v.itertext()))
        if src.get("link_xpath") and not row.get("link"):
            got = n.xpath(src["link_xpath"])
            if got:
                row["link"] = got[0] if isinstance(got[0], str) else ""
        if not (row.get("title") or row.get("link")):
            continue
        row.setdefault("summary", squash(row.get("content", ""), 280))
        out.append(row)
    return out


def links_from_index(src):
    """Pull item URLs off an index page. Local fetch first; Tavily Extract if blocked."""
    pat = re.compile(src["link_pattern"])
    base = src.get("link_base", "")
    seen, out = set(), []
    def harvest(blob):
        for m in pat.finditer(blob):
            u = m.group(1)
            if not u.startswith("http"):
                u = base + (u if u.startswith("/") or not base.endswith("/") else u.lstrip("/"))
            if u.startswith("http") and u not in seen:
                seen.add(u)
                out.append(u)
    try:
        harvest(fetch(src["index_url"]).text)
    except Exception as e:
        print("  index fetch failed (%s) — trying Tavily Extract" % str(e)[:60], flush=True)
    if len(out) < max(3, (src.get("max_items") or 8) // 3):
        if budget_left(1) <= 0:
            print("  index fallback skipped: Tavily budget exhausted", flush=True)
            return out
        body = tavily("extract", {"urls": [src["index_url"]]})
        res = (body.get("results") or [{}])[0]
        spend(1)
        harvest(res.get("raw_content") or "")
    keep = src.get("link_filter")
    if keep:
        kp = re.compile(keep)
        out = [u for u in out if kp.search(u)]
    return out[: src.get("max_items", 10)]


def kind_extract(src):
    urls = list(src.get("urls") or [])
    if src.get("mode", "index") == "index":
        urls = links_from_index(src)
    out = []
    for u in urls:
        if budget_left(1) <= 0:
            print("  budget reached — %d url(s) deferred to the next run" % (len(urls) - len(out)), flush=True)
            break
        body = tavily("extract", {"urls": [u]})
        spend(1)
        res = (body.get("results") or [{}])[0]
        raw = res.get("raw_content") or ""
        if not raw:
            out.append({"title": "", "link": u, "summary": "", "content": "",
                        "extract_status": "empty"})
            continue
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        title = lines[0][:300] if lines else u
        out.append({
            "title": title,
            "link": res.get("url") or u,
            "summary": squash(" ".join(lines[1:3]), 280),
            "content": raw[:80000],
            "full": raw[:400000],
            "word_count": len(raw.split()),
            "extract_status": "ok",
            "published": "",
        })
    return out


def kind_search(src):
    body = tavily("search", {"query": src["query"], "max": src.get("max", 8),
                             "deep": bool(src.get("deep"))})
    spend(0)
    out = []
    for r in body.get("results") or []:
        out.append({
            "title": squash(r.get("title"), 300),
            "link": r.get("url", ""),
            "summary": squash(r.get("content"), 280),
            "content": (r.get("content") or "")[:6000],
            "published": r.get("published_date") or "",
            "query": src["query"],
            "score": r.get("score"),
        })
    return out


KINDS = {"rss": kind_rss, "json": kind_json, "html": kind_html,
         "extract": kind_extract, "search": kind_search}


# --------------------------------------------------------------------------- main
def load_sources():
    data = json.load(open(SOURCES))
    defaults = data.get("defaults", {})
    out = []
    for s in data["sources"]:
        merged = dict(defaults)
        merged.update(s)
        out.append(merged)
    return out


def run_one(src, dry=False):
    name, kind = src["name"], src["kind"]
    if kind not in KINDS:
        print("scraper=%s SKIPPED unknown kind '%s'" % (name, kind))
        return 1
    st = load_state(name)
    seen = set(str(x) for x in st.get("seen", []))
    try:
        rows = KINDS[kind](src)
    except Exception as e:
        print("scraper=%s FAILED %s" % (name, str(e)[:160]))
        st.setdefault("runs", []).append({"at": time.time(), "new": 0, "ok": False,
                                          "error": str(e)[:200], "fetched": 0})
        save_state(name, st)
        return 2
    new_rows = []
    for row in rows:
        k = item_key(row, src)
        if not k or k in seen:
            continue
        seen.add(k)
        row.setdefault("source", name)
        row.setdefault("category", src.get("category", "news"))
        new_rows.append(row)
    if not dry:
        append_rows(name, new_rows)
    st["seen"] = sorted(seen)
    st.setdefault("runs", []).append({"at": time.time(), "new": len(new_rows), "ok": True,
                                      "fetched": len(rows)})
    if not dry:
        save_state(name, st)
    print("scraper=%s fetched=%d new=%d tracked=%d%s"
          % (name, len(rows), len(new_rows), len(seen), " (dry)" if dry else ""))
    return 0


def cmd_list(sources):
    for s in sources:
        st = load_state(s["name"])
        runs = st.get("runs") or []
        last = runs[-1] if runs else {}
        when = (time.strftime("%m-%d %H:%M", time.localtime(last["at"]))
                if last.get("at") else "never")
        print("  %-16s %-8s %-9s last=%-12s new=%-4s ok=%-5s tracked=%s"
              % (s["name"], s["kind"], "enabled" if s.get("enabled", True) else "DISABLED",
                 when, last.get("new", "-"), last.get("ok", "-"), len(st.get("seen", []))))
    u = {}
    try:
        u = json.load(open(USAGE))
    except Exception:
        pass
    print("  tavily urls today=%s (cap %d/run, %d/day)" % (u.get("urls", 0), BUDGET, DAILY))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    sources = load_sources()
    if a.list:
        cmd_list(sources)
        return 0
    only = set(x.strip() for x in a.only.split(",")) if a.only else None
    fails = 0
    for s in sources:
        if not s.get("enabled", True):
            continue
        if only and s["name"] not in only:
            continue
        fails += 1 if run_one(s, dry=a.dry) else 0
    if not a.dry:
        # reset the per-run Tavily counter for the next run
        try:
            u = json.load(open(USAGE))
            u["run"] = 0
            json.dump(u, open(USAGE, "w"), indent=1)
        except Exception:
            pass
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
