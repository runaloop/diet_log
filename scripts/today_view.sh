#!/usr/bin/env bash
# Live view of today.md: redraws only when something is written under diaries/.
set -u
cd "$(dirname "$0")/.."

command -v inotifywait >/dev/null || { echo "need inotifywait: sudo apt install inotify-tools" >&2; exit 1; }

# The pane title is how today_pane.sh tells that a view is already up.
set_title() {
  [ -n "${ORCA_TERMINAL_HANDLE:-}" ] && command -v orca-ide >/dev/null || return 0
  orca-ide terminal set-pane-title --terminal "$ORCA_TERMINAL_HANDLE" --title "$1" --json >/dev/null 2>&1
}
set_title today.md
trap 'set_title ""' EXIT

# glow renders stdin instead of the file when stdin is a pipe, and inside the loop it is inotifywait's
render() { clear; glow -s dark -w "$(tput cols)" today.md </dev/null; }

render
inotifywait -mrq -e close_write,moved_to diaries |
  while read -r _; do
    # log.py writes the diary several times per call; redraw once after the burst
    while read -r -t 0.3 _; do :; done
    render
  done
