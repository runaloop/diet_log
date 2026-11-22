# Proposal

## Why

The new-day report currently counts food groups and fat quality over D−6 through D, even though D has just opened without food. This drops the oldest completed day and effectively reports only six preceding days of intake.

## What Changes

- Use the seven calendar days preceding the opened date (D−7 through D−1, inclusive) for all rolling nutrition blocks in the new-day report: group remainders, group limits, and fat quality.
- Display those exact dates in the report while retaining D as the diary date and ISO-week anchor.
- Keep ordinary `summary.py weektrend D` inclusive of D for ongoing daily use. Select the preceding-days window explicitly in the new-day workflow, without inferring it from diary contents.
- Document this new-day exception to the general inclusive rolling-window rule.

## Capabilities

### New Capabilities

- `weekly-nutrition-reporting`: Date boundaries and output consistency for rolling nutrition blocks in weekly dashboards and new-day reports.

### Modified Capabilities

None. The existing `ration-planning` capability does not cover reporting windows.

## Impact

- Sources: `scripts/new_day.py:main` calls `summary.weektrend(n)`; `scripts/summary.py:weektrend` passes `ref` to both `group_remainder_lines` and `fat_quality_lines`, which each start six days earlier.
- Expected edits: `scripts/summary.py`, `scripts/new_day.py`, focused synthetic regression tests, `docs/STRATEGY.md`, and the relevant new-day/reporting guidance in `AGENTS.md`.
- Non-goals: changing nutrition formulas, quotas, planner windows, daily summaries, ISO-week deficit/protein trends, Garmin/pills/git behavior, or rewriting existing diary/catalog data.
- Verification uses a copied engine and synthetic inputs, with external services and git disabled or stubbed. No new dependency is needed.
