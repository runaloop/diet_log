# Ration planning

## Purpose

Describe the small implemented planner surface relevant to the mode-authority pilot.
This baseline is intentionally incomplete. The active change proposes behavior that is not yet implemented.

## Requirements

### Requirement: Read stored Plan inputs
The planner SHALL consume the stored Plan's remaining calorie allowance, protein and fat eaten,
remaining carbohydrates, base expenditure and spent calories, rather than refresh those values
merely by parsing the Plan.

#### Scenario: Stored numeric inputs are available
- **WHEN** a valid Plan block contains the supported numeric labels
- **THEN** parsing returns those stored inputs for planning
- **AND** parsing does not recalculate them from current configuration

### Requirement: Recognize supported Markdown labels
The planner SHALL recognize `Режим:` and legacy `Фаза цикла:` mode labels and the supported
compact or legacy `Можно съесть ...:` calorie-allowance labels, including ASCII and Unicode minus signs.
Recognizing a mode label does not establish a single authoritative mode for all calculations.

#### Scenario: Legacy Plan text is supplied
- **WHEN** valid input uses `Фаза цикла:` and the legacy calorie-allowance label
- **THEN** the planner parses those supported labels without requiring a historical-file rewrite

### Requirement: Explicit ration write boundaries
Given an existing readable catalog and valid planning inputs, the planner SHALL render a suggestion
without rewriting the diary, catalog or ration file when `--write` is absent.
With `--write`, it SHALL preserve an existing ration for the requested date unless `--force` is supplied,
and SHALL not create or replace a ration when no suggestion is produced.

#### Scenario: Suggestion is requested without writing
- **WHEN** the planner runs without `--write` against prepared inputs
- **THEN** it prints the suggestion and leaves the diary, catalog and ration contents unchanged

#### Scenario: Existing same-date ration is protected
- **WHEN** `--write` is supplied for a ration already dated to the requested day without `--force`
- **THEN** the existing ration contents are retained

#### Scenario: Replacement is explicitly requested
- **WHEN** `--write --force` is supplied and a nonempty suggestion is produced
- **THEN** the ration file may be replaced with that suggestion

#### Scenario: There is no suggestion to write
- **WHEN** `--write` is supplied but planning returns no dishes
- **THEN** no ration file is created or replaced

## Implementation evidence and observed gap

Sources: `scripts/plan_ration.py:92-108` (stored fields and labels), `:772-781` (write branches),
and `docs/agent/ration.md:5-17` (procedure). Sources are engineering files, not live diary examples.

Observed gap, not a normative requirement: `parse_plan` obtains a heading-derived phase,
`plan_budget` uses it for fat budgets (`:455-457`), while the CLI obtains `global_mode()`
separately (`:763-768`) for carbohydrate-layer selection (`:560-568`) and displayed mode.
`docs/todo.md:56` requests one source. The active `unify-ration-mode-source` delta addresses this gap;
no fix has been applied to product code.
