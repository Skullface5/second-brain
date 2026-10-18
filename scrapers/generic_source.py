#!/usr/bin/env python
"""generic_source.py — ONE engine, six source kinds, driven by sources.json + cloud.

Kinds
  rss      feed_url -> title/link/summary/content/published
  json     API -> items via `lists` (or `items_path`) + field dot-paths
  html     index page -> items via XPath selectors
  extract  Tavily Extract: full page text for a source the box cannot fetch
           mode "index"  : index_url -> links (regex) -> Extract each item
           mode "urls"   : a fixed `urls` list -> Extract each
  search   Tavily Search for a saved query (topic tracking)
  site     a WHOLE website: sitemap/index -> filtered links -> page text per item
           (incremental — each run takes the next max_items pages it has not seen)

Where the source list comes from
  scrapers/sources.json is the seed and the offline fallback. Supabase
  public.sources (edited from the site's Sources panel on the phone) is the control
  panel and wins whenever it is reachable — see cloud_sources.py.

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
  generic_source.py --local          # ignore the cloud panel, use sources.json
"""
import argparse, hashlib, html as htmllib, json, os, re, subprocess, sys, time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

try:
    from cloud_sources import fetch_sources          # same dir
except ImportError:                                   # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cloud_sources import fetch_sources

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
# name -> the keys this source has already archived. Multi-URL kinds ("site") read
# it to skip pages BEFORE spending a request on them; run_one fills it per source.
KNOWN = {}


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


# ------------------------------------------------------------------- scheduling
def due_in(src, state):
    """Seconds until this source may run again, 0 when it is due.

    A source's cadence is its own (`every_minutes`); the hourly timer stays the
    heartbeat and simply skips what is not due yet. Default = every hour, which
    is what the timer does anyway.
    """
    every = int(src.get("every_minutes") or 60)
    runs = state.get("runs") or []
    last = runs[-1].get("at") if runs else None
    if not last:
        return 0
    return max(0, int(every * 60 - (time.time() - last)))


def keep_row(row, src):
    """Filters: drop rows a source says it does not want (kept out of the archive)."""
    f = src.get("filters") or {}
    title = str(row.get("title") or "")
    url = str(row.get("url") or row.get("link") or "")
    if f.get("min_title_len") and len(title) < int(f["min_title_len"]) and not url:
        return False
    for key, val in (("title_exclude", title), ("url_exclude", url)):
        if f.get(key) and re.search(f[key], val, re.I):
            return False
    for key, val in (("title_include", title), ("url_include", url)):
        if f.get(key) and not re.search(f[key], val, re.I):
            return False
    return True


def paged_urls(src, base, pages):
    """url + query params, then `pages` successive pages (page_param/page_size)."""
    import urllib.parse as up
    q = dict(src.get("query") or {})
    out = []
    param = src.get("page_param", "page")
    size_param = src.get("page_size_param") or src.get("page_size")
    size = src.get("page_size")
    start = int(src.get("page_start", 1))
    for i in range(start, start + max(1, int(pages or 1))):
        params = dict(q)
        if size is not None and size_param:
            params[size_param] = size
        if int(pages or 1) > 1 or i != start:
            params[param] = i
        parts = up.urlsplit(base)
        existing = dict(up.parse_qsl(parts.query))
        existing.update({k: str(v) for k, v in params.items()})
        out.append(up.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                  up.urlencode(existing), parts.fragment)))
    return out


# ------------------------------------------------------------------------- kinds
def kind_rss(src):
    """Feeds are a single small file on a metered, stall-prone link: give them a
    longer window and one more try than the default before calling it a failure."""
    root = ET.fromstring(fetch(src["url"], tries=4, timeout=90).content)
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
    """One request by default; `pages` walks the listing (this is how a whole
    site gets covered incrementally instead of page-by-page by hand)."""
    items = []
    for u in paged_urls(src, src["url"], src.get("pages", 1)):
        try:
            data = fetch(u).json()
        except Exception:
            if items:
                break          # page 4 failing must not throw page 1 away
            raise
        lists = src.get("lists")
        if lists:
            for path in lists:
                got = dig(data, path, [])
                if isinstance(got, list):
                    items += got
        else:
            got = dig(data, src.get("items_path", ""), data)
            items += got if isinstance(got, list) else [got]
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
            "title": squash(str(dig(it, src.get("title", "id"), key)), 300),
            "summary": "",
            "content": "",
            "first_seen": datetime.now(timezone.utc).isoformat(),
        }
        if src.get("bucket_path"):
            row["bucket"] = dig(it, src["bucket_path"], "")
        if src.get("fields"):
            row["meta"] = {k: dig(it, v) for k, v in src["fields"].items()}
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


# --------------------------------------------------------------- whole-site kind
# "site": crawl a whole website instead of one page at a time. Sitemap first
# (sitemap.xml / sitemap_index.xml / robots.txt Sitemap: lines), plain index-page
# link harvesting as the fallback, then read each page. The state watermark means
# a big site is covered incrementally - each run takes the next `max_items` pages
# it has never seen, so a metered link never eats a whole site in one go.

SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
            ".json", ".zip", ".gz", ".mp4", ".mp3", ".wav", ".woff", ".woff2",
            ".ttf", ".eot", ".pdf", ".xml", ".rss", ".atom", ".webmanifest")


def _host(u):
    import urllib.parse as up
    return up.urlsplit(u).netloc.lower()


def sitemap_urls(src, cap=5000):
    """Every page <loc> in the site's sitemaps, one index level deep."""
    site = (src.get("site") or src.get("url") or "").rstrip("/")
    queue, seen, pages = [], set(), []
    if src.get("sitemap"):
        queue.append(src["sitemap"])
    if site:
        queue += [site + "/sitemap.xml", site + "/sitemap_index.xml"]
        try:                                     # robots.txt often names the real sitemap
            for m in re.finditer(r"(?im)^\s*sitemap:\s*(\S+)", fetch(site + "/robots.txt", tries=1, timeout=30).text):
                if m.group(1) not in queue:
                    queue.append(m.group(1))
        except Exception:
            pass
    fetches = 0
    while queue and len(pages) < cap and fetches < int(src.get("sitemap_max") or 10):
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        fetches += 1
        try:
            root = ET.fromstring(fetch(u, tries=1, timeout=60).content)
        except Exception:
            continue
        kind_tag = root.tag.split("}")[-1]
        locs = [(e.text or "").strip() for e in root.iter() if e.tag.split("}")[-1] == "loc"]
        if kind_tag == "sitemapindex":
            queue += [l for l in locs if l not in seen]
        else:
            pages += [l for l in locs if l]
    return pages[:cap]


def index_page_urls(src, cap=5000):
    """No sitemap? Harvest links off the index page, optionally one level deeper."""
    site = (src.get("site") or src.get("url") or "").rstrip("/")
    roots = [src.get("index_url") or site]
    depth = int(src.get("depth") or 1)
    seen, out = set(), []
    for level in range(max(1, depth)):
        nxt = []
        for u in roots:
            if u in seen or len(out) >= cap:
                continue
            seen.add(u)
            try:
                blob = fetch(u, tries=1, timeout=45).text
            except Exception:
                continue
            hrefs = re.findall(r'href=["\']([^"\'#]+)', blob, re.I)
            for h in hrefs:
                full = h if h.startswith("http") else site + ("" if h.startswith("/") else "/") + h
                if full not in seen and full not in out:
                    out.append(full)
                    if level + 1 < depth and re.search(r"/(page|category|archive|topic|blog|index)s?/", full):
                        nxt.append(full)
        roots = nxt[:20]
    return out[:cap]


def site_urls(src):
    """Candidate page URLs for a whole-site source, filtered by the source's rules."""
    urls = sitemap_urls(src)
    if not urls:
        urls = index_page_urls(src)
    site = (src.get("site") or src.get("url") or "").rstrip("/")
    host = _host(site) if site else ""
    keep, drop = src.get("link_filter"), src.get("link_exclude")
    kp = re.compile(keep) if keep else None
    dp = re.compile(drop) if drop else None
    same = src.get("same_host", True)
    out = []
    for u in urls:
        if not u.startswith("http"):
            continue
        low = u.lower().split("?")[0]
        if low.endswith(SKIP_EXT):
            continue
        if host and same and _host(u) != host:
            continue
        if kp and not kp.search(u):
            continue
        if dp and dp.search(u):
            continue
        out.append(u)
    if src.get("reverse") or src.get("order") == "newest":
        out.reverse()          # sitemaps usually run oldest-first; news sites newest-first
    return out


def kind_site(src):
    """Whole-site harvest: sitemap/index -> filtered links -> page text per item."""
    known = KNOWN.get(src.get("name"), set())
    urls = [u for u in site_urls(src) if u not in known]
    limit = int(src.get("max_items") or 20)
    print("  site: %d url(s) not archived yet, taking %d this run" % (len(urls), min(limit, len(urls))),
          flush=True)
    use_tavily = (src.get("extract") or "auto") != "local"
    min_words = int(src.get("min_words") or 120)
    out = []
    for u in urls[:limit]:
        text, status = "", "ok"
        try:
            blob = fetch(u, tries=2, timeout=60).text
            title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", blob)
            text = to_text(re.sub(r"(?is)<(script|style|nav|footer)[^>]*>.*?</\1>", " ", blob))
            head = squash(htmllib.unescape(title_m.group(1)), 300) if title_m else ""
        except Exception as e:
            head, text, status = "", "", "fetch failed: %s" % str(e)[:80]
        if len(text.split()) < min_words and use_tavily and budget_left(1) > 0:
            body = tavily("extract", {"urls": [u]})
            spend(1)
            res = (body.get("results") or [{}])[0]
            raw = (res.get("raw_content") or "").strip()
            if raw:
                lines = [l.strip() for l in raw.splitlines() if l.strip()]
                head = head or (lines[0][:300] if lines else u)
                text = raw
                status = "ok"
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        out.append({
            "title": head or (lines[0][:300] if lines else u),
            "link": u,
            "summary": squash(" ".join(lines[:3]), 280),
            "content": text[:80000],
            "word_count": len(text.split()),
            "published": "",
            "extract_status": status if text else "empty",
        })
    return out


KINDS = {"rss": kind_rss, "json": kind_json, "html": kind_html,
         "extract": kind_extract, "search": kind_search, "site": kind_site}


# --------------------------------------------------------------------------- main
def load_sources(use_cloud=True):
    """The effective source list.

    scrapers/sources.json is the SEED (what a fresh box gets, and the fallback when
    the link is down). Supabase public.sources is the CONTROL PANEL: once it has
    rows it wins outright - so toggling a source off, changing its cadence or adding
    one from the phone takes effect on the next run, and deleting a row really does
    retire the scraper. Cloud unreachable -> local file, always.
    """
    data = json.load(open(SOURCES))
    defaults = data.get("defaults", {})
    local = {}
    for s in data["sources"]:
        m = dict(defaults)
        m.update(s)
        local[m["name"]] = m

    cloud = fetch_sources() if use_cloud else {}
    if cloud:
        out = []
        for name, cs in cloud.items():
            merged = dict(defaults)
            merged.update(local.get(name, {}))
            merged.update(cs)                 # cloud wins for every field it carries
            out.append(merged)
        missing = [n for n in local if n not in cloud]
        if missing:
            print("sources: %d local-only source(s) not in the cloud panel: %s"
                  % (len(missing), ", ".join(sorted(missing))), flush=True)
        return out

    if use_cloud:                             # say it out loud: the panel did not load
        print("sources: cloud panel unreachable — using scrapers/sources.json "
              "(edits made on the site will not apply until this reads again)", flush=True)

    out = []
    for name, m in local.items():
        if m.get("enabled", True) is False:
            continue
        out.append(m)
    return out


def run_one(src, dry=False):
    name, kind = src["name"], src["kind"]
    if kind not in KINDS:
        print("scraper=%s SKIPPED unknown kind '%s'" % (name, kind))
        return 1
    st = load_state(name)
    seen = set(str(x) for x in st.get("seen", []))
    KNOWN[name] = seen
    wait = due_in(src, st)
    if wait > 0 and not dry:
        print("scraper=%s deferred (not due yet, %d min left, every %s min)"
              % (name, wait // 60 + 1, src.get("every_minutes") or 60))
        return 0
    try:
        rows = KINDS[kind](src)
    except Exception as e:
        print("scraper=%s FAILED %s" % (name, str(e)[:160]))
        st.setdefault("runs", []).append({"at": time.time(), "new": 0, "ok": False,
                                          "error": str(e)[:200], "fetched": 0})
        save_state(name, st)
        return 2
    cap = int(src.get("max_items") or 0)
    new_rows, filtered = [], 0
    for row in rows:
        k = item_key(row, src)
        if not k or k in seen:
            continue
        seen.add(k)
        if not keep_row(row, src):
            filtered += 1
            continue
        row.setdefault("source", name)
        row.setdefault("category", src.get("category", "news"))
        new_rows.append(row)
    capped = 0
    if cap and len(new_rows) > cap:
        capped = len(new_rows) - cap
        new_rows = new_rows[:cap]
    if not dry:
        append_rows(name, new_rows)
    st["seen"] = sorted(seen)
    st.setdefault("runs", []).append({"at": time.time(), "new": len(new_rows), "ok": True,
                                      "fetched": len(rows),
                                      "filtered": filtered, "capped": capped})
    if not dry:
        save_state(name, st)
    extra = ""
    if filtered:
        extra += " filtered=%d" % filtered
    if capped:
        extra += " capped=%d (next run takes the rest)" % capped
    print("scraper=%s fetched=%d new=%d tracked=%d%s%s"
          % (name, len(rows), len(new_rows), len(seen), extra, " (dry)" if dry else ""))
    return 0


def cmd_list(sources):
    for s in sources:
        st = load_state(s["name"])
        runs = st.get("runs") or []
        last = runs[-1] if runs else {}
        when = (time.strftime("%m-%d %H:%M", time.localtime(last["at"]))
                if last.get("at") else "never")
        every = int(s.get("every_minutes") or 60)
        wait = due_in(s, st)
        due = "due" if wait <= 0 else "%dm" % (wait // 60 + 1)
        print("  %-18s %-8s %-9s every=%-5s %-6s last=%-12s new=%-4s ok=%-5s tracked=%s"
              % (s["name"], s["kind"], "enabled" if s.get("enabled", True) else "DISABLED",
                 "%dm" % every, due, when, last.get("new", "-"), last.get("ok", "-"),
                 len(st.get("seen", []))))
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
    ap.add_argument("--local", action="store_true", help="ignore the cloud source panel")
    a = ap.parse_args()

    sources = load_sources(use_cloud=not a.local)
    if a.list:
        cmd_list(sources)
        return 0
    only = set(x.strip() for x in a.only.split(",")) if a.only else None
    fails, ran, failed_names = 0, 0, []
    for s in sources:
        if not s.get("enabled", True):
            continue
        if only and s["name"] not in only:
            continue
        ran += 1
        if run_one(s, dry=a.dry):
            fails += 1
            failed_names.append(s["name"])
    if not a.dry:
        # reset the per-run Tavily counter for the next run
        try:
            u = json.load(open(USAGE))
            u["run"] = 0
            json.dump(u, open(USAGE, "w"), indent=1)
        except Exception:
            pass
    # One flaky source (a feed that stalls mid-transfer on a metered link) must
    # not paint the whole unit red — its own run record and the site's health
    # strip already carry the failure. Exit non-zero only when NOTHING worked,
    # which is a real outage worth alerting on.
    if ran and fails == ran:
        print("farm done: 0/%d scrapers OK (FAILED: %s)" % (ran, ", ".join(failed_names)))
        return 1
    if fails:
        print("farm done: %d/%d scrapers OK (WARN failed: %s)"
              % (ran - fails, ran, ", ".join(failed_names)))
    else:
        print("farm done: %d/%d scrapers OK" % (ran, ran))
    return 0


if __name__ == "__main__":
    sys.exit(main())
