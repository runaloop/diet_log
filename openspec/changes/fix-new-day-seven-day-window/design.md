# Design

## Context

See proposal.md for motivation. `new_day.main` renders a full `summary.weektrend(n)` after opening the diary. `weektrend` already separates its ISO trend (ending at `ref - 1 day`) from rolling blocks, but passes `ref` directly to both rolling helpers. Each helper uses an inclusive seven-day window. `open_day` separately requests `with_groups=False` for the diary header.

`docs/STRATEGY.md` currently states that rolling windows end today. This change introduces an explicit new-day reporting exception; ordinary reports and planner windows retain that rule. The OpenSpec configuration's planner-pilot scope describes a different change; this user-requested reporting fix does not extend that pilot. Its isolation and compatibility constraints still apply.

Stored-Plan freshness assumption: existing workflows prepare the Plan as before; this change neither refreshes nor validates stored values merely to select a report window.

## Goals / Non-Goals

**Goals:** Separate the dashboard's date from the rolling-window endpoint, with one endpoint shared by both nutrition blocks.

**Non-Goals:** No data-dependent empty-day detection, new CLI option, planner changes, or changes to `rolling7_med_verdict` in daily summaries.

## Decisions

1. Add an optional keyword-only `rolling_end` date to `weektrend`, defaulting to `ref`. Resolve it once and pass it to both `group_remainder_lines` and `fat_quality_lines`. Their existing date arithmetic and headings then remain consistent. Changing `ref` itself to yesterday would incorrectly shift the H1 and Monday's ISO-week trend.
2. Only the final new-day report passes `rolling_end=n - timedelta(days=1)`. The diary-header call stays unchanged. The existing dry-run report uses the same explicit endpoint. A no-op invocation already pointing at D still returns its existing message without a report. Inferring the endpoint from diary contents would unexpectedly change ordinary reports and create ambiguity around activity-only diaries.
3. Treat the window as calendar dates, not seven populated diaries. Missing dates contribute no entries through existing aggregation; do not backfill older dates. This matches current aggregation semantics.
4. Add isolated regression coverage using a copied `scripts/` tree, synthetic config and diaries, and a synthetic catalog created through `scripts/db`. Use distinguishable food-group and fat-quality contributions at D−8, D−7, D−1 and D to prove both inclusion and exclusion numerically, not only by heading text. Follow the subprocess isolation pattern in `tests/test_protein_status.py`; force Garmin's token path to a nonexistent scratch file and disable git with `--no-commit`. Exercise the real new-day report plus ordinary `weektrend` and `--no-groups` behavior.

## Risks / Trade-offs

- [Only one nutrition block shifts] → Resolve and share one endpoint, test both totals and dates.
- [ISO heading or trend moves backward] → Retain `ref` and cover Monday explicitly.
- [Tests access personal data or services] → Copy only engine code; synthesize all inputs and isolate tokens, git, pills configuration and environment paths.
- [Ordinary reports and new-day reports differ on an empty day] → This is an explicit workflow distinction; document it in strategy and agent instructions.
- [Concurrent working-tree edits exist] → Limit implementation to the targeted call/signature, docs and tests; preserve unrelated changes.

## Migration Plan

No data migration or historical rewrite. Apply the code and documentation change, run isolated regression checks, and use the new endpoint on the next new-day report. Reverting the targeted code restores the prior report behavior without changing stored records.
