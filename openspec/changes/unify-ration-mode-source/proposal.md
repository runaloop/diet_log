# Proposal

## Why

The existing backlog (`docs/todo.md:56`) identifies two competing mode sources in the ration planner;
current code still uses the diary heading for fat budgets and global configuration for carbohydrate
selection and displayed mode. A bounded fix can remove contradictory decisions without redesigning
planning, and provides the first reviewable change for this OpenSpec pilot.

## What Changes

- Resolve `summary.global_mode()` once per planner invocation and pass that value through all mode-dependent paths.
- Use that value for fat budgets/ranges, maintenance-only carbohydrate selection and displayed mode,
  even when a current or legacy diary heading disagrees or is missing.
- Preserve matching-mode behavior, supported Markdown parsing, low/mid/high and explicit `--load`
  handling, and existing no-write/overwrite boundaries.
- Add regression coverage using a copied engine and entirely synthetic temporary inputs.

Boundary: the stored Plan has already been recalculated under current configuration through the
existing workflow. This change does not refresh or reject stale stored calorie/carbohydrate values.
It does not change formulas, catalog contents, automation, historical records or diary headings.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ration-planning`: add one global mode authority across mode-dependent planner decisions,
  retaining the current stored-input, parsing and write contracts.

## Impact

Expected implementation area: `scripts/plan_ration.py` and isolated regression tests, with a focused
procedure/backlog update after completion. Existing helpers that call planner functions must remain
compatible. No new runtime dependencies or schema migrations are expected.

The planner file already contains unrelated uncommitted matching work; coordinate ownership before
future implementation. This bundle changes planning artifacts only. Product tests, implementation,
spec synchronization and archival have not been performed.
