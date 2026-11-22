# Spec Delta

## ADDED Requirements

### Requirement: One global mode per planner invocation
With the stored Plan already recalculated under current configuration, the planner SHALL resolve
global mode once per invocation and use that value for every mode-dependent fat budget/range,
carbohydrate-layer decision and displayed mode. Diary mode labels SHALL NOT override it.

#### Scenario: Global maintenance conflicts with a cutting heading
- **WHEN** global mode is maintenance, the stored Plan was recalculated accordingly, and the heading says cutting
- **THEN** initial fat limits, final fat completion, rendered fat range and displayed mode use maintenance rules
- **AND** the maintenance carbohydrate layer is enabled on a rest day

#### Scenario: Global cutting conflicts with a maintenance heading
- **WHEN** global mode is cutting, the stored Plan was recalculated accordingly, and the heading says maintenance
- **THEN** all fat-budget and display paths use cutting rules
- **AND** maintenance-only carbohydrate completion is not enabled on a rest day

#### Scenario: Mode heading is missing
- **WHEN** supported Plan inputs omit a mode heading and the stored Plan was recalculated under current configuration
- **THEN** global mode governs every mode-dependent path without a heading-derived default overriding it

#### Scenario: Legacy mode heading disagrees
- **WHEN** supported Plan inputs contain a legacy `Фаза цикла:` heading that disagrees with global mode
- **THEN** legacy input remains readable and all mode-dependent decisions use global mode
- **AND** the input heading is not rewritten

#### Scenario: Matching modes preserve load-dependent behavior
- **WHEN** global mode and the heading agree and a recalculated Plan is evaluated for each of low, mid and high loads
- **THEN** mode-dependent budgets, fat ranges, carbohydrate activation and display preserve existing matching-mode behavior

#### Scenario: Explicit load takes precedence
- **WHEN** a supported `--load` is supplied with a Plan recalculated for that declared load and current global mode
- **THEN** that load is used consistently with the same global mode across all mode-dependent paths
- **AND** the declared load takes precedence over load inferred from logged activity

#### Scenario: One resolved value is used throughout
- **WHEN** the planner obtains the invocation's global mode
- **THEN** all subsequent mode-dependent calculations and rendering use that resolved value
- **AND** those paths do not independently resolve another mode during the invocation

### Requirement: Preserve stored-input and file boundaries during mode unification
The planner SHALL retain the existing stored-input, supported-label and explicit-write contracts
while changing mode authority. It SHALL NOT refresh or reject stale Plan values as part of this
change; callers remain responsible for recalculating the Plan through the existing workflow.

#### Scenario: Stored values are consumed rather than refreshed
- **WHEN** the planner consumes a valid stored Plan
- **THEN** changing mode authority does not itself refresh the stored calorie allowance or remaining carbohydrates
- **AND** no new stale-Plan rejection behavior is introduced

#### Scenario: Suggestion leaves prepared files unchanged
- **WHEN** a synthetic copied-engine invocation uses a readable existing catalog and valid Plan without `--write`
- **THEN** synthetic diary, catalog and existing ration contents remain unchanged even when mode headings disagree

#### Scenario: Same-date ration protection is retained
- **WHEN** a synthetic copied-engine invocation uses `--write` for an existing same-date ration without `--force`
- **THEN** the existing ration is not overwritten

#### Scenario: Explicit replacement and empty suggestion retain current behavior
- **WHEN** a synthetic copied-engine invocation requests `--write --force`
- **THEN** a nonempty suggestion can replace the ration
- **AND** an empty suggestion does not create or replace it
