# Proposal

## Why

Routine reports repeat accepted catalog assumptions without offering a useful action. The user approved removing this noise while retaining the assumptions for explanations and corrections on request.

## What Changes

- Omit routine notes about accepted estimated or unknown group/fat annotations, including the two reported messages about approximate group contributions and typical preparation.
- Do not replace them with approximation symbols, counters, disclaimers or invitations to inspect assumptions.
- Preserve stored statuses, reasons and all calculations; accepted unknown groups remain uncounted and unknown fat retains its existing neutral fallback.
- Keep genuine unresolved-record diagnostics such as unmatched catalog names and unfinished legacy annotations, along with existing partial-day notices.
- Retain explicit inspection through `scripts/db review --all`; agent guidance uses saved reasons when the user asks about a calculation. No new command or interactive explanation feature is needed.

## Capabilities

### New Capabilities

- `catalog-annotation-reporting`: Visibility of accepted catalog assumptions versus unresolved annotation issues in routine output and explicit inspection.

### Modified Capabilities

None. `ration-planning` does not cover this behavior. The unarchived `weekly-nutrition-reporting` capability covers date windows; its behavior remains unchanged.

## Impact

Sources: `scripts/catalog_review.py:uncertainty_lines` emits four generic notices for accepted decisions; `scripts/summary.py:group_remainder_lines` appends them. `tests/test_catalog_add.py` currently requires an aggregate approximation note, and `docs/agent/catalog.md` prescribes it. These expectations must change together.

Expected implementation scope: those reporting helpers, focused tests, and catalog/agent guidance. `scripts/db.py:cmd_review` already exposes reasons on explicit inspection and needs no new behavior.

Non-goals: changing nutrition or source-confidence calculations, resolving old catalog records, changing logging or ambiguity selection, adding a detail command, changing report windows, or modifying personal diaries/catalog data. Tests must run with a copied engine and entirely synthetic inputs.
