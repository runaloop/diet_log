# Spec Delta

## Purpose

Define consistent calendar windows for food-group and fat-quality reporting, distinguishing a new-day retrospective from an ongoing daily dashboard.

## ADDED Requirements

### Requirement: New-day retrospective covers preceding calendar days
The new-day report for date D SHALL calculate group remainders, group limits, and fat quality using D−7 through D−1 inclusive. The window SHALL remain seven calendar days even when diaries are missing or the target diary already exists.

#### Scenario: Empty new day
- **WHEN** a new-day report opens 2026-11-17
- **THEN** all rolling nutrition blocks use 2026-11-10 through 2026-11-16
- **AND** intake on November 10 contributes, while intake on November 9 or November 17 does not

#### Scenario: Missing diary within the window
- **WHEN** one of the seven preceding calendar days has no diary
- **THEN** the window remains D−7 through D−1 without reaching back for a replacement day

#### Scenario: Calendar boundary
- **WHEN** a new-day report opens 2027-01-01
- **THEN** its rolling nutrition blocks use 2026-12-25 through 2026-12-31

#### Scenario: Existing target diary contains food
- **WHEN** the new-day workflow advances to a date whose diary already contains food
- **THEN** the retrospective still excludes that target date
- **AND** the existing target diary is preserved

### Requirement: Displayed report dates match their meaning
The report SHALL display the actual rolling date range for food groups and fat quality. Its heading and ISO-week deficit/protein trend SHALL remain anchored to the opened date D, with the trend using only prior dates in D's ISO week. The diary header SHALL continue to omit rolling nutrition blocks.

#### Scenario: Monday opens
- **WHEN** a new-day report opens Monday 2026-11-16
- **THEN** its heading identifies November 16 and its new ISO week
- **AND** it reports that no prior trend exists in that week
- **AND** rolling block dates are 2026-11-09 through 2026-11-15
- **AND** those rolling blocks are absent from the new diary header

### Requirement: Ordinary weekly dashboards retain inclusive windows
An ordinary `summary.py weektrend D` request SHALL retain the inclusive D−6 through D window regardless of whether D contains food. Selecting a new-day retrospective SHALL NOT change subsequent ordinary requests.

#### Scenario: Ordinary report after new-day creation
- **WHEN** an ordinary weekly dashboard is requested for 2026-11-17 after the new-day report
- **THEN** it uses November 11 through November 17 for groups and fat quality
- **AND** any food logged on November 17 contributes

#### Scenario: Ordinary report for an empty day
- **WHEN** an ordinary weekly dashboard is requested for D with an empty diary
- **THEN** it continues to use D−6 through D
