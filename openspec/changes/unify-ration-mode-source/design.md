# Design

## Context

See `proposal.md` for motivation and `specs/ration-planning/spec.md` for proposed behavior.
This design is included because precedence and stored-input freshness need explicit decisions before coding.

`scripts/plan_ration.py:92-108` parses stored Plan fields and a current/legacy heading phase.
`plan_budget` (`:455-457`) uses that phase; initial fat limits (`:517`), final fat completion (`:467`)
and rendering (`:610`) all depend on it. The CLI separately resolves global mode (`:763`) and passes
it to `build` and `render`; carbohydrate activation (`:562`) uses that second value.
`scripts/recalc_plan.py:72-96` already calculates by global mode despite mismatching headings.

The inspected static caller search found `plan_budget` and `fat_floor_topup` callers inside the planner;
`scripts/ration_totals.py:97` also calls `parse_plan`. Preserve that parser's shape and legacy support.
The checkout has unrelated uncommitted planner matching changes, which must remain intact.

## Goals / Non-Goals

**Goals:** thread an explicit invocation mode through the existing helper graph and verify agreement
at all three fat-budget consumers, carbohydrate activation and display.

**Non-Goals:** recalculate stored Plan values, introduce freshness checks, rewrite headings, alter
load/formula policy, refactor catalog matching, or change ration scheduling. This is not a historical
mode-reconstruction feature for old records.

## Decisions

1. **Resolve once at the CLI boundary, pass explicitly.** Keep the CLI's existing global-mode resolution
   and pass that value to `plan_budget` and through `fat_floor_topup`, reusing the mode already available
   in `build` and `render`. Do not have helpers independently read global configuration. This keeps all
   decisions in one invocation coherent and permits deterministic injection in tests. A helper-local
   global lookup would be smaller at one call site but conceal repeated resolution and dependency ownership.

2. **Keep parsed heading phase as compatibility metadata.** Preserve `parse_plan` fields and both labels,
   but stop using that metadata as budget authority. Rewriting the parsed phase in place would obscure
   which value came from input and could surprise other parser callers. No heading rewrite is required.
   Audit callers when changing helper signatures; do not introduce a silent heading-based fallback.

3. **Require an already-recalculated Plan.** Continue using stored `kcal` and `carb_rem`; the existing
   recalculation workflow owns their freshness. Test mismatched headings with numeric Plan fields computed
   under the authoritative global mode and declared load. Automatically recomputing or rejecting stale
   Plan values would add behavior and side effects beyond this fix. A mode-consistent calculation can
   still consume stale stored numbers; this design explicitly does not claim to prevent that.

4. **Use a fully synthetic copied engine for regressions.** Follow `tests/test_protein_status.py:18-45`:
   copy scripts to a temporary root, generate synthetic config/diary/profile/catalog/ration fixtures,
   and launch only that copy with its own working directory. `scripts/paths.py:10-24` demonstrates why
   redirecting `DIET_DB` alone leaves other reads pointed at the real checkout. No production credentials,
   diary samples or research data belong in tests. Compare matching-mode outputs and shared calculation
   results under identical synthetic inputs; exercise mismatches to expose the old bug.

## Risks / Trade-offs

- [Stored numbers may be stale] → Keep the recalculation precondition visible in procedure and tests;
  do not imply single-mode selection refreshes them.
- [A fat consumer is missed] → Cover initial cap, final fat completion and rendering separately with
  conflicting heading/global modes, plus a test proving one resolution per invocation.
- [Legacy/helper compatibility regresses] → Retain parser fields/labels, inspect callers, and cover
  missing/legacy headings, matching modes and explicit load selection.
- [Tests leak into real user paths] → Build every fixture in the temporary copied root and assert
  non-write file contents and overwrite guards there; do not run against the checkout's personal files.
- [Concurrent work is lost] → Establish ownership before code edits and review the scoped diff against
  the existing matching changes; do not reset or commit other sessions' work.

## Migration Plan

No data/schema migration is needed. After a later explicit implementation request, coordinate file
ownership, add isolated regressions, thread the mode, and verify behavior before updating the focused
procedure/TODO. Deployment is the reviewed source change. Rollback would revert only that later scoped
change through a reviewed diff, without rewriting user records or resetting unrelated work.

Preparation ends with unchecked tasks and structural OpenSpec validation. Do not sync the proposed
delta into the current baseline or archive the change before implementation and behavioral verification.
