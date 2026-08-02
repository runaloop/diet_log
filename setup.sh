#!/usr/bin/env bash
# Idempotent setup for a fresh clone. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

# 1. local files from *.example (only if missing)
if [ ! -f config/medications.md ]; then
  cp config/medications.example.md config/medications.md
  echo "created config/medications.md (fill in your meds — it stays local, gitignored)"
fi

# 2. build data/profile.json from local diaries (needed by plan_ration.py)
python3 scripts/profile.py || echo "profile.py skipped (no diaries yet)"

# 3. anonymous local committer (keeps your real name/email out of public history)
git config --local user.name "dietlog"
git config --local user.email "dietlog@users.noreply.github.com"

# 4. today.md symlink → today's diary (create from example if missing)
today="diaries/$(date +%Y/%m/%d).md"
if [ ! -f "$today" ]; then
  mkdir -p "$(dirname "$today")"
  if [ -f diaries/2026/2026-01-01.example.md ]; then
    cp diaries/2026/2026-01-01.example.md "$today"
    echo "created $today from example — edit the H1 date for today"
  fi
fi
ln -sf "$today" today.md
echo "today.md -> $today"

echo "setup done."
