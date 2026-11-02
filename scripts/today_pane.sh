#!/usr/bin/env bash
# Puts scripts/today_view.sh in a pane next to the agent terminal, unless one is already there.
# Called by the Claude Code SessionStart hook and by the opencode plugin (.opencode/plugins/today-pane).
# Outside Orca it does nothing.
set -u
cd "$(dirname "$0")/.."

command -v orca-ide >/dev/null || exit 0

view="$PWD/scripts/today_view.sh"
base=$(basename "$PWD")
list=$(orca-ide terminal list --json 2>/dev/null) || exit 0
[ -n "$list" ] || exit 0

# The pane we sit in: our own handle when Orca exported a live one, otherwise the newest agent pane
# of this worktree — the opencode server carries a handle of whichever terminal started it.
self=$(jq -r --arg h "${ORCA_TERMINAL_HANDLE:-}" --arg p "$PWD" '
  [.result.terminals[] | select($h != "" and .handle == $h and .worktreePath == $p)] as $own
  | if ($own | length) > 0 then $own[0].handle
    else ([.result.terminals[] | select(.worktreePath == $p and .agentIdentity != null)]
          | sort_by(.lastOutputAt) | last | .handle) // empty
    end' <<<"$list")
[ -n "$self" ] || exit 0

tab=$(jq -r --arg h "$self" '.result.terminals[] | select(.handle == $h) | .tabId' <<<"$list")
others=$(jq -c --arg h "$self" --arg t "$tab" \
  '[.result.terminals[] | select(.tabId == $t and .handle != $h)]' <<<"$list")

jq -e 'any(.title | test("today_view"))' <<<"$others" >/dev/null && exit 0

# Orca restores a split after restart as an idle shell titled with the (possibly truncated) cwd
idle=$(jq -r --arg base "$base" '
  [ .[] | select(.agentIdentity == null)
    | (.title | sub("^\\.+"; "")) as $t
    | select($t == "zsh" or $t == "bash" or (($t | length) > 3 and ($base | endswith($t))))
  ] | .[0].handle // empty' <<<"$others")
if [ -n "$idle" ]; then
  orca-ide terminal send --terminal "$idle" --text "$view" --enter --json >/dev/null
else
  orca-ide terminal split --terminal "$self" --direction vertical --command "$view" --json >/dev/null
fi
exit 0
