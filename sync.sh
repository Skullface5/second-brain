#!/usr/bin/env bash
# sync.sh — after the farm runs: pull full page text, enrich with LLM, rebuild
# data/news.json (+ the data/full/ shards), push. Vercel auto-redeploys on push.
# Safe to run repeatedly (no-op if nothing changed). The branch is whatever is
# checked out (main = prod, design/v3 = preview).
set -e
cd "$(dirname "$0")"

PY=./venv/bin/python
BR=$(git rev-parse --abbrev-ref HEAD)
$PY enrich_fulltext.py
$PY enrich.py
$PY publish.py

if [ -n "$(git status --porcelain data/)" ]; then
  git add data/
  git commit -q -m "data: $(date -u +%FT%TZ)"
  git pull -q --rebase origin "$BR" 2>/dev/null || true
  git push -q origin "$BR" 2>&1 | tail -2 || echo "push failed (will retry next hour)"
  echo "synced: pushed $BR $(date -u +%FT%TZ)"
else
  echo "synced: no data change"
fi
