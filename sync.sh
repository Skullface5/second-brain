#!/usr/bin/env bash
# sync.sh — after the farm runs: rebuild data/news.json and push to GitHub.
# Vercel auto-redeploys on push. Safe to run repeatedly (no-op if nothing changed).
set -e
cd "$(dirname "$0")"

PY=./venv/bin/python
$PY publish.py

if [ -n "$(git status --porcelain data/news.json)" ]; then
  git add data/news.json
  git commit -q -m "data: $(date -u +%FT%TZ)"
  git push -q origin main 2>&1 | tail -2 || echo "push failed (will retry next hour)"
  echo "synced: pushed $(date -u +%FT%TZ)"
else
  echo "synced: no data change"
fi