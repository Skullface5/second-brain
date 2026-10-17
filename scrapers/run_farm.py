#!/usr/bin/env python
"""run_farm.py — runs every scraper in jobs.json. Called by systemd timer."""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(os.path.dirname(HERE), "venv", "bin", "python")

with open(os.path.join(HERE, "jobs.json")) as f:
    jobs = json.load(f)["scrapers"]

fails = 0
for j in jobs:
    cmd = [PY, os.path.join(HERE, j["module"])] + j["args"]
    print(">>> " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.strip(), flush=True)
    if r.returncode != 0 or r.stderr.strip():
        print("ERR", r.stderr.strip()[:500], flush=True)
        fails += 1

print(f"farm done: {len(jobs)-fails}/{len(jobs)} scrapers OK")
sys.exit(1 if fails else 0)
