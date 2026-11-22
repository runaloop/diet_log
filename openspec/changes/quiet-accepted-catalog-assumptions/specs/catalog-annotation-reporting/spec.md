# Spec Delta

## Purpose

Keep routine nutrition output free of reminders about accepted catalog assumptions while preserving explicit inspection and diagnostics for unresolved records.

## ADDED Requirements

### Requirement: Accepted annotation decisions stay quiet in routine output
Routine food logging, weekly dashboards and new-day reports SHALL NOT emit reminders about accepted estimated or unknown group/fat annotations. They SHALL NOT replace those reminders with approximation markers, counters or prompts to inspect assumptions.

#### Scenario: Estimated composition and fat category
- **WHEN** the reporting window contains products with accepted estimated group contributions and fat categories
- **THEN** neither the approximate-group notice nor the typical-preparation notice appears
- **AND** no substitute annotation reminder appears

#### Scenario: Accepted unknown composition
- **WHEN** the window contains accepted unknown group or fat annotations
- **THEN** routine output omits generic incomplete-group and neutral-fat-fallback reminders
- **AND** it does not ask the user to obtain a recipe or cooking-oil details

### Requirement: Quiet reporting preserves accounting and stored explanations
Suppressing annotation reminders SHALL preserve numeric results, catalog decisions and their reasons. Estimated groups SHALL retain their saved contributions; accepted unknown groups SHALL remain uncounted, and accepted unknown fat SHALL retain the neutral fallback. Explicit catalog inspection SHALL continue to expose saved statuses and reasons.

#### Scenario: Report with mixed annotation states
- **WHEN** a report is generated from synthetic known, estimated and unknown annotations
- **THEN** group totals and fat shares match their saved contributions and existing fallback rules
- **AND** generating the report does not modify the catalog or diaries

#### Scenario: Explicit explanation inspection
- **WHEN** `scripts/db review --all` is requested for products with accepted estimated and unknown annotations
- **THEN** the output retains their saved statuses and reasons within the command's existing output limit
- **AND** accepted decisions do not become unfinished reviews merely because routine notices were suppressed

### Requirement: Unresolved-record diagnostics remain visible
Routine reports SHALL retain existing diagnostics for unmatched catalog names, unfinished legacy annotations and explicitly partial diaries. Suppressing accepted-decision reminders SHALL NOT suppress those independent diagnostics.

#### Scenario: Accepted assumptions coexist with an unmatched record
- **WHEN** a report includes an accepted estimate and a food record not matched to the catalog
- **THEN** the unmatched-record diagnostic remains visible
- **AND** the accepted estimate produces no reminder

#### Scenario: Unfinished legacy annotation or partial diary
- **WHEN** a report includes an unresolved legacy annotation or a diary explicitly marked partial
- **THEN** the corresponding existing diagnostic remains visible
