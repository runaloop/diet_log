#!/usr/bin/env bash
# Live view of today.md: redraws only when something is written under diaries/.
set -u
cd "$(dirname "$0")/.."

command -v inotifywait >/dev/null || { echo "need inotifywait: sudo apt install inotify-tools" >&2; exit 1; }

# glow renders stdin instead of the file when stdin is a pipe, and inside the loop it is inotifywait's
render() { clear; glow -s dark -w "$(tput cols)" today.md </dev/null; }

render
inotifywait -mrq -e close_write,moved_to diaries |
  while read -r _; do
    # log.py writes the diary several times per call; redraw once after the burst
    while read -r -t 0.3 _; do :; done
    render
  done
