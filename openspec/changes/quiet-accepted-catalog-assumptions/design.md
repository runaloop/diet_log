# Design

## Context

See proposal.md for motivation. `group_remainder_lines` appends two independent sources: `warning_lines` for genuinely pending records and `uncertainty_lines` for accepted `estimated`/`unknown` decisions. The latter has only one production caller. Removing it from routine rendering does not require changing the saved annotation model. `db review --all` independently reads saved reasons.

The current catalog regression test explicitly expects the aggregate note, and catalog guidance requires it; both must be revised. The earlier new-day window change is implemented but unarchived and must be preserved. The configured planner-pilot scope refers to another change; its data-isolation constraints remain applicable here. Stored Plan freshness is unchanged and irrelevant to this presentation-only decision.

## Goals / Non-Goals

**Goals:** Separate accepted assumptions from unfinished work at the report boundary, preserving accounting and explicit inspection.

**Non-Goals:** New detail commands, automatic recipe research, rewriting catalog decisions, warning-system redesign, or changing the timing/format of initial catalog creation acknowledgements and KБЖУ source attribution.

## Decisions

1. Remove the `uncertainty_lines` call/import from routine report rendering and remove that unused helper after confirming there are no other callers. Retain `warning_lines`, unmatched-name diagnostics and partial-day notices. This suppresses all four generic accepted-decision notes consistently. Removing only the two `estimated` messages would leave accepted `unknown` decisions framed as recurring user work.
2. Keep stored reasons and `db review --all` intact. Document that the agent consults saved assumptions only when explaining or correcting a calculation on request, without manufacturing a recipe or turning accepted uncertainty into a new question. A new command or an automatic details prompt would add complexity and restore the noise the user rejected.
3. Update the obsolete assertion in `test_catalog_add.py` and add focused integration checks using a copied engine with synthetic config, diaries and catalog. Run existing tests from the copied root as well: the current catalog test imports checkout-bound `summary`, and merely setting `DIET_DB` does not fully isolate its configuration reads.
4. Include a mixed fixture with estimated groups/fat, unknown groups/fat, an unmatched product and a pending legacy decision. Verify absent accepted-decision notices, preserved actionable diagnostics, exact numeric contributions, and retained reasons under explicit review. Reuse the isolated new-day runner to ensure the window fix still composes with quiet output.

## Risks / Trade-offs

- [Accepted uncertainty is less visible in everyday output] → Keep the full explanation available on explicit inspection; do not silently promote annotations to known facts.
- [Broad removal hides unresolved records] → Remove only the accepted-decision renderer and test mixed states.
- [Tests accidentally read personal config] → Run copied scripts/tests with synthetic config and an isolated catalog, disable Garmin credentials and git in new-day checks.
- [Existing concurrent changes are overwritten] → Limit edits to the report call/helper, affected assertions and documentation; preserve the endpoint change.

## Migration Plan

No migration or personal-data write is required. Deploy the rendering/test/documentation edits together. Rollback restores the report call and helper; stored assumptions and numerical accounting remain untouched either way.
