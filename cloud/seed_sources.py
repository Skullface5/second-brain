#!/usr/bin/env python3
"""seed_sources.py - push scrapers/sources.json up into Supabase public.sources.

Idempotent: upsert on name. Local file stays the seed/base; the cloud copy is what
the phone edits and what the farm prefers for enabled / every_minutes / max_items.

  seed_sources.py            # push everything in sources.json
  seed_sources.py --print    # show what is in the cloud now
"""
import json, os, sys, urllib.error, urllib.request

ROOT = os.path.expanduser("~/second-brain")
CRED = os.path.expanduser("~/.hermes/.supabase_second_brain.json")

# per-table scalar columns (everything else in a source dict goes into config jsonb)
SCALARS = ("kind", "enabled", "every_minutes", "max_items", "category", "fulltext")


def creds():
    c = json.load(open(CRED))
    return c["project_url"].rstrip("/"), c["service_key"]


def rest(url, key, path, method="GET", body=None, prefer=None):
    headers = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(url + "/rest/v1/" + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
    return json.loads(raw) if raw.strip() else None


def local_sources():
    data = json.load(open(os.path.join(ROOT, "scrapers", "sources.json")))
    defaults = data.get("defaults", {})
    out = []
    for s in data["sources"]:
        m = dict(defaults)
        m.update(s)
        out.append(m)
    return out


def row_of(s):
    cfg = {k: v for k, v in s.items() if k not in SCALARS and k != "name"}
    return {"name": s["name"], "kind": s.get("kind", "rss"),
            "enabled": bool(s.get("enabled", True)),
            "every_minutes": int(s.get("every_minutes") or 60),
            "max_items": s.get("max_items"),
            "category": s.get("category") or "news",
            "fulltext": s.get("fulltext") or "auto",
            "config": cfg}


def main():
    url, key = creds()
    if "--print" in sys.argv:
        rows = rest(url, key, "sources?select=name,kind,enabled,every_minutes,updated_at&order=name")
        for r in rows or []:
            print("  %-18s %-8s enabled=%-5s every=%-4s %s"
                  % (r["name"], r["kind"], r["enabled"], r["every_minutes"], r.get("updated_at", "")))
        print("  %d source(s) in cloud" % len(rows or []))
        return 0

    rows = [row_of(s) for s in local_sources()]
    rest(url, key, "sources", "POST", rows, prefer="resolution=merge-duplicates,return=minimal")
    print("seeded %d source(s) into public.sources" % len(rows))
    for r in rows:
        print("  %-18s %-8s enabled=%s every=%s" % (r["name"], r["kind"], r["enabled"], r["every_minutes"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())