#!/usr/bin/env bash
# Puts scripts/today_view.sh in a pane next to the current Orca terminal, unless one is already there.
# Meant for a SessionStart hook; outside Orca it does nothing.
set -u
cd "$(dirname "$0")/.."

[ -n "${ORCA_TERMINAL_HANDLE:-}" ] && command -v orca-ide >/dev/null || exit 0

view="$PWD/scripts/today_view.sh"
list=$(orca-ide terminal list --worktree active --json 2>/dev/null) || exit 0
tab=$(jq -r --arg h "$ORCA_TERMINAL_HANDLE" '.result.terminals[] | select(.handle == $h) | .tabId' <<<"$list")
[ -n "$tab" ] || exit 0
others=$(jq -c --arg h "$ORCA_TERMINAL_HANDLE" --arg t "$tab" \
  '[.result.terminals[] | select(.tabId == $t and .handle != $h)]' <<<"$list")

jq -e 'any(.title | test("today_view"))' <<<"$others" >/dev/null && exit 0

# Orca restores a split after restart as an idle shell titled with the cwd; reuse it
idle=$(jq -r 'map(select(.agentIdentity == null and (.title | test("log_public$|^(zsh|bash)$")))) | .[0].handle // empty' <<<"$others")
if [ -n "$idle" ]; then
  orca-ide terminal send --terminal "$idle" --text "$view" --enter --json >/dev/null
elif [ "$(jq length <<<"$others")" -eq 0 ]; then
  orca-ide terminal split --terminal "$ORCA_TERMINAL_HANDLE" --direction vertical --command "$view" --json >/dev/null
fi
exit 0
