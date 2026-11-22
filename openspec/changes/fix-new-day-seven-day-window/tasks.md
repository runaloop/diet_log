# Tasks

## 1. Explicit rolling endpoint

- [x] 1.1 Create `tests/test_new_day_window.py` with a temporary copied engine and synthetic config, catalog and diaries before running product checks. Verify all paths are scratch-local, Garmin uses a nonexistent scratch token path, and git is disabled/stubbed.
- [x] 1.2 Add keyword-only `rolling_end` to `summary.weektrend`, defaulting to `ref`, and use the resolved endpoint for both rolling blocks. Add tests with distinct D−8, D−7, D−1 and D entries; verify actual group totals, fat-quality shares and printed dates for both explicit and default endpoints.
- [x] 1.3 Document the endpoint distinction in the function docstring; verify `with_groups=False` preserves the existing heading/trend output and contains no rolling blocks.

## 2. New-day integration

- [x] 2.1 Pass D−1 only to the final new-day dashboard. Add isolated real-workflow tests verifying D−7 is included, D excluded, Monday's heading/trend anchored to D, and the diary header omits rolling blocks.
- [x] 2.2 Extend regression cases for missing dates, year/month rollover, an already populated target diary, dry-run output and the existing no-op branch. Verify calendar bounds, target preservation, and absence of writes in dry-run/no-op cases.
- [x] 2.3 Update `docs/STRATEGY.md` and relevant `AGENTS.md` new-day/reporting guidance to state D−7…D−1 for new-day output and D−6…D for ordinary dashboards. Verify examples agree with the spec and unrelated guidance remains intact.

## 3. Combined verification

- [x] 3.1 Run `python3 -m unittest discover -s tests -p 'test_new_day_window.py'` and `python3 -m unittest discover -s tests -p 'test_protein_status.py'`; confirm they pass with isolated inputs and record actual results.
- [x] 3.2 Run strict OpenSpec validation for this change and review the scoped diff; verify no nutrition formulas, personal records, catalog data or unrelated concurrent edits were changed by this work.

## Verification record

- 2026-11-17: `test_new_day_window.py` — 7 tests passed; `test_protein_status.py` — 4 tests passed. Both suites use copied engines and synthetic inputs.
- Regression proof before the code change: the new suite failed on the old inclusive new-day range and unsupported endpoint argument; the no-op test already passed.
- Strict OpenSpec validation passed; `git diff --check` passed.
- Reviewed the four edited existing files against pre-implementation copies to distinguish this change from existing working-tree edits. Product changes are limited to endpoint selection and documentation; tests execute only inside temporary roots.
- Implementation complete (8/8). Ready for review and subsequent archive; no archive performed.
