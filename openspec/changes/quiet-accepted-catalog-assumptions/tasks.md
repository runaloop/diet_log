# Tasks

## 1. Quiet accepted assumptions

- [x] 1.1 Prepare a temporary copied engine/tests root with synthetic config, diaries and catalog before executing product checks; verify imports and data paths resolve inside it and new-day checks cannot access real Garmin credentials or run git.
- [x] 1.2 Remove accepted-decision notices from report rendering and remove the unused renderer after checking callers. Update the existing catalog assertion and add isolated mixed-state coverage; verify ordinary logging, weekly and new-day output are quiet, exact group/fat totals and stored reasons are preserved, and unmatched/pending/partial diagnostics still appear. Verify `db review --all` retains accepted statuses and reasons.
- [x] 1.3 Update `docs/agent/catalog.md` and relevant `AGENTS.md` guidance: accepted assumptions remain silent in routine output and saved reasons support explicit explanation requests. Verify the guidance does not introduce approximation markers, detail prompts or repeated recipe questions.

## 2. Combined verification

- [x] 2.1 Run the affected catalog tests, new focused tests, `test_new_day_window.py` and `test_protein_status.py` from the isolated copied root. Record pass counts, run strict OpenSpec validation and review the scoped diff; verify no accounting formulas, date windows, personal data or unrelated concurrent changes were altered.

## Verification record

- 2026-11-17: all checks executed from `/tmp/quiet-catalog-qnjolhb7` with copied scripts/tests and synthetic config. New-day tests explicitly select a nonexistent scratch token path and `--no-commit`.
- `test_quiet_catalog.py`: 4 passed; `test_catalog_add.py`: 20 passed; `test_new_day_window.py`: 7 passed; `test_protein_status.py`: 4 passed (35 total).
- Before the rendering change, three new cases failed on the recurring notices; explicit inspection already passed. Two obsolete catalog expectations were updated to the approved quiet behavior while retaining numeric assertions.
- Verified unchanged report data and saved reasons, preserved unmatched/unfinished/partial diagnostics, and unchanged new-day window behavior. Strict OpenSpec validation and `git diff --check` passed.
- Reviewed existing-file edits against pre-implementation copies. No formulas, report windows, production catalog or diary records were edited. All 4 tasks complete; ready for review and archive.
