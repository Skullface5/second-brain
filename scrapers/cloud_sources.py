#!/usr/bin/env python3
"""cloud_sources.py - the phone-editable half of the source list.

The PWA writes to Supabase public.sources (through the token-gated RPC); the farm
reads it here and prefers it over scrapers/sources.json. That makes the website the
control panel: toggle a source off, change its cadence, or add a brand new one and
the next run obeys it - no shell, no file edit, no redeploy.

Fail-soft by design: the link is metered and DNS is flaky, so ANY problem (no creds,
timeout, empty table) returns {} and the caller keeps using the local file. The farm
must never stop because the control panel is unreachable.

  SB_CLOUD=0            disable the cloud read entirely (local file only)
  SB_CLOUD_TIMEOUT=8    seconds
"""
import json, os, urllib.error, urllib.request

CRED = os.path.expanduser("~/.hermes/.supabase_second_brain.json")
SCALARS = ("kind", "enabled", "every_seconds", "every_minutes", "max_items", "category", "fulltext")


def _creds():
    c = json.load(open(CRED))
    return c["project_url"].rstrip("/"), c["service_key"]


def fetch_sources(timeout=None):
    """{name: {..scalars, **config}} straight off PostgREST. {} when unavailable."""
    if os.environ.get("SB_CLOUD", "1") == "0":
        return {}
    timeout = timeout or int(os.environ.get("SB_CLOUD_TIMEOUT", "8"))
    try:
        url, key = _creds()
    except Exception:
        return {}
    req = urllib.request.Request(
        url + "/rest/v1/sources?select=*&order=name",
        headers={"apikey": key, "Authorization": "Bearer " + key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            rows = json.loads(r.read().decode() or "[]")
    except Exception:
        return {}
    out = {}
    for row in rows or []:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        cfg = dict(row.get("config") or {})
        for k in SCALARS:
            v = row.get(k)
            if v is not None and v != "":
                cfg[k] = v
        cfg["name"] = name
        cfg["cloud"] = True
        out[name] = cfg
    return out