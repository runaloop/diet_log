#!/usr/bin/env bash
# Open one diary pane per Orca tab, or restart its viewer in the saved pane.
# Called by shell launchers, SessionStart hooks and the opencode plugin.
set -u
cd "$(dirname "$0")/.." || exit 1
source scripts/today_runtime.sh

if [ -n "${ORCA_CLI_COMMAND:-}" ]; then
  orca=("$ORCA_CLI_COMMAND")
elif command -v orca-ide >/dev/null; then
  orca=(orca-ide)
else
  exit 0
fi

read_terminals() {
  list=$("${orca[@]}" terminal list --json 2>/dev/null) || return 1
  jq -e '.ok == true and .result.truncated != true and
    (.result.terminals | type == "array")' <<<"$list" >/dev/null
}

read_terminals || exit 0
# A stale/missing caller is not permission to pick whichever agent printed last.
# The legacy fallback is safe only when exactly one agent could be the caller.
self=$(jq -r --arg h "${ORCA_TERMINAL_HANDLE:-}" --arg p "$PWD" '
  [.result.terminals[] | select(.worktreePath == $p and .connected == true)] as $panes
  | if $h != "" then ($panes[] | select(.handle == $h) | .handle)
    else [$panes[] | select(.agentIdentity != null)]
      | if length == 1 then .[0].handle else empty end
    end' <<<"$list")
[ -n "$self" ] || exit 0
tab=$(jq -r --arg h "$self" '
  .result.terminals[] | select(.handle == $h) | .tabId // empty' <<<"$list")
[ -n "$tab" ] || exit 0

# Startup/resume hooks can overlap, including while Orca starts the command.
launch_lock=$(today_lock_file "launcher:$tab") || exit 1
exec 9>"$launch_lock"
flock -n 9 || exit 0
read_terminals || exit 0
# The caller may have closed/moved while we were acquiring the lock.
jq -e --arg h "$self" --arg t "$tab" '
  any(.result.terminals[]; .handle == $h and .tabId == $t and .connected == true)
' <<<"$list" >/dev/null || exit 0
others=$(jq -c --arg h "$self" --arg t "$tab" --arg p "$PWD" '
  [.result.terminals[] | select(.worktreePath == $p and .tabId == $t and .handle != $h)]
' <<<"$list")

while IFS=$'\t' read -r handle leaf; do
  if today_view_running "$handle"; then
    today_remember_pane "$tab" "$leaf" || exit 1
    exit 0
  fi
done < <(jq -r '.[] | select(.connected == true) | [.handle, .leafId] | @tsv' <<<"$others")

state_file=$(today_pane_file "$tab") || exit 1
saved_leaf=""
if [ -f "$state_file" ]; then
  saved_leaf=$(jq -er --arg tab "$tab" '
    select(.tabId == $tab) | .leafId | select(type == "string" and length > 0)
  ' "$state_file") || { echo "today.md: invalid pane record $state_file" >&2; exit 1; }
fi
# A title can recover panes created before the persistent record was introduced.
# Disconnected/busy/unreadable known panes still count as existing panes.
candidates=$(jq -c --arg leaf "$saved_leaf" '
  [.[] | select($leaf != "" and .leafId == $leaf)] as $saved
  | if ($saved | length) > 0 then $saved
    else [.[] | select(.paneTitle == "today.md" or .paneTitle == "today_view")] end
' <<<"$others")

idle=""
while IFS=$'\t' read -r handle leaf; do
  screen=$("${orca[@]}" terminal read --terminal "$handle" --screen --json 2>/dev/null) || continue
  if today_shell_idle <<<"$screen"; then
    today_remember_pane "$tab" "$leaf" || exit 1
    idle="$handle"
    break
  fi
done < <(jq -r '.[] | select(.connected == true and .writable != false and
  .agentIdentity == null) | [.handle, .leafId] | @tsv' <<<"$candidates")

printf -v command '%q' "$PWD/scripts/today_view.sh"
if [ -n "$idle" ]; then
  "${orca[@]}" terminal send --terminal "$idle" --text "$command" --enter --json >/dev/null || exit 1
  viewer_handle="$idle"
elif jq -e 'length > 0' <<<"$candidates" >/dev/null; then
  echo "today.md: existing pane is busy or not ready; no extra split created" >&2
  exit 0
else
  receipt=$("${orca[@]}" terminal split --terminal "$self" --direction vertical --command "$command" --json) || exit 1
  viewer_handle=$(jq -er '.result.split.handle // .result.terminal.handle' <<<"$receipt") || exit 1
  leaf=$(jq -r '.result.split.leafId // .result.terminal.leafId // empty' <<<"$receipt")
  if [ -z "$leaf" ]; then
    read_terminals || exit 1
    leaf=$(jq -er --arg h "$viewer_handle" '
      .result.terminals[] | select(.handle == $h) | .leafId' <<<"$list") || exit 1
  fi
  # Reserve the pane before waiting: a slow or failed startup must not spawn more.
  today_remember_pane "$tab" "$leaf" || exit 1
fi
for ((attempt = 0; attempt < 30; attempt++)); do
  today_view_running "$viewer_handle" && exit 0
  sleep 0.1
done
echo "today.md viewer did not start in tab $tab; its pane is reserved for retry" >&2
exit 1
