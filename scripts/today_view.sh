#!/usr/bin/env bash
# Live view of today.md: redraws only when something is written under diaries/.
set -u
cd "$(dirname "$0")/.."
source scripts/today_runtime.sh

command -v inotifywait >/dev/null || { echo "need inotifywait: sudo apt install inotify-tools" >&2; exit 1; }
command -v glow >/dev/null || { echo "need glow" >&2; exit 1; }

if [ -n "${ORCA_TERMINAL_HANDLE:-}" ]; then
  viewer_lock=$(today_lock_file "viewer:$ORCA_TERMINAL_HANDLE") || exit 1
  exec 8>"$viewer_lock"
  # The launcher's liveness probe briefly takes the same lock while it is free.
  # Wait through that probe instead of occasionally exiting during startup.
  flock -w 1 8 || exit 0
fi

if [ -n "${ORCA_CLI_COMMAND:-}" ]; then
  orca=("$ORCA_CLI_COMMAND")
elif command -v orca-ide >/dev/null; then
  orca=(orca-ide)
else
  orca=()
fi

# Cosmetic title only; liveness and pane ownership are tracked separately.
set_title() {
  [ -n "${ORCA_TERMINAL_HANDLE:-}" ] && [ "${#orca[@]}" -gt 0 ] || return 0
  "${orca[@]}" terminal set-pane-title --terminal "$ORCA_TERMINAL_HANDLE" --title "$1" --json >/dev/null
}
# Leave the pane's name on exit so older launchers can also recover it.
set_title today.md || echo "today.md: could not set pane title" >&2

# glow renders stdin instead of the file when stdin is a pipe, and inside the loop it is inotifywait's
render() { clear; glow -s dark -w "$(tput cols)" today.md </dev/null; }

render
inotifywait -mrq -e close_write,moved_to diaries |
  while read -r _; do
    # log.py writes the diary several times per call; redraw once after the burst
    while read -r -t 0.3 _; do :; done
    render
  done
