# Tasks

Implementation is active; checkboxes record coordinator-verified completed tasks.
Product commands must use only the temporary copied engine and synthetic inputs described in the design.

## 1. Establish isolated regression fixtures

- [x] 1.1 Coordinate ownership of the already-modified planner and record its unrelated matching diff; verify the implementation scope will preserve that diff without resetting other work.
- [x] 1.2 Add a temporary copied-engine fixture following `tests/test_protein_status.py`, with generated config, diary, profile, catalog and ration inputs; verify all subprocess paths and data paths resolve inside the temporary root and no live credentials are used.
- [x] 1.3 Capture matching-mode low/mid/high and explicit-load expectations using recalculated synthetic Plans; verify they agree with the shared recalculation functions under identical synthetic inputs before changing authority.
- [x] 1.4 Add regressions for both conflicting mode directions, missing/legacy headings, all fat-budget consumers, carbohydrate activation and displayed mode; verify the mismatch cases expose the old dual-source behavior and legacy parsing still works in the isolated copy.

## 2. Unify mode authority and preserve contracts

- [x] 2.1 Thread the CLI's once-resolved global mode through `plan_budget`, initial fat limits, `fat_floor_topup` and rendering, preserving parser fields and helper callers; verify the isolated mode matrix and all consumer regressions pass, including a single-resolution assertion.
- [x] 2.2 Add and pass isolated file-content checks for no `--write`, same-date overwrite protection, explicit forced replacement and empty-suggestion behavior; verify synthetic diary/catalog/ration inputs retain their existing write boundaries.
- [x] 2.3 Add and pass a stored-value regression proving that the fix does not refresh or newly reject Plan calorie/carbohydrate values; document the recalculation precondition in the focused ration procedure and mark only the completed dual-mode TODO, verifying the documentation matches the tested behavior.

## 3. Integration and review

- [x] 3.1 Run the focused planner regressions and relevant existing caller/logging/parser checks from a reviewed isolated test setup; verify reported commands, fixture paths and results demonstrate no access to the real checkout's personal files.
- [x] 3.2 Review the combined implementation diff for matching-mode compatibility, preserved unrelated edits, unchanged formulas/catalog schema and no historical-file changes; verify strict OpenSpec validation still passes and report behavioral checks separately from structural validation.

## Workflow follow-up

- Keep this change active during review. Syncing the delta and archival require a later completed implementation and verification workflow; neither is part of preparation.
- After 2–3 actual changes, assess ambiguity reduction and review effort against duplicate-document maintenance before extending the pilot.
