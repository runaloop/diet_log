#!/usr/bin/env bash
# Locks prove process liveness; a separate record remembers the pane's purpose.
today_lock_file() {
  local key directory
  directory="${TMPDIR:-/tmp}/diet-log-today-$UID"
  mkdir -p -m 700 "$directory" || return 1
  key=$(printf '%s\n%s\n' "$PWD" "$1" | sha256sum) || return 1
  printf '%s/%s.lock\n' "$directory" "${key%% *}"
}

today_view_running() {
  local file status
  file=$(today_lock_file "viewer:$1") || return 1
  flock -n "$file" true
  status=$?
  [ "$status" -eq 1 ]
}

# Orca handles change on restart; the tab and leaf identify the saved pane.
# Keep this on disk across reboots, independently of cosmetic pane titles.
today_pane_file() {
  local key directory
  directory="$PWD/data/today_panes"
  mkdir -p "$directory" || return 1
  key=$(printf '%s\n' "$1" | sha256sum) || return 1
  printf '%s/%s.json\n' "$directory" "${key%% *}"
}

today_remember_pane() {
  local file temporary
  [ -n "$1" ] && [ -n "$2" ] || return 1
  file=$(today_pane_file "$1") || return 1
  temporary=$(mktemp "$file.XXXXXX") || return 1
  if jq -n --arg tab "$1" --arg leaf "$2" '{tabId: $tab, leafId: $leaf}' >"$temporary"; then
    mv "$temporary" "$file"
  else
    rm -f "$temporary"
    return 1
  fi
}

today_shell_idle() {
  # --screen uses physical rows. Rejoin only soft wraps, never arbitrary lines
  # of program output (a narrow powerlevel10k prompt can end in a lone ╯).
  jq -e '
    .result.terminal as $screen
    | reduce range(0; ($screen.tail | length)) as $i
        ([]; if ($screen.tailRows[$i].wrapped == true) and length > 0
             then .[-1] += $screen.tail[$i]
             else . + [$screen.tail[$i]] end)
    | map(select(test("\\S"))) | last // ""
    | test("^([╰└─━ ]*(❯|➜)|[^<>❯➜]*[$#%])\\s*[─━╯╰┘└]*\\s*$")
  ' >/dev/null
}
