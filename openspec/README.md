# Diet Log OpenSpec pilot

**Current stage:** implementation is verified, 9/9 tasks, and ready for user review.
The change remains active and unarchived; no user acceptance is implied by completed tasks.


This is a small planning layer for one real change, not a migration of existing documentation.
The planner fix is implemented and verified in the working tree. All existing project instructions still apply.

## Start here

- [Current ration-planning behavior](specs/ration-planning/spec.md): a minimal implemented baseline.
- [Active proposal](changes/unify-ration-mode-source/proposal.md): the mode-authority gap and scope.
- [Design](changes/unify-ration-mode-source/design.md), [proposed requirements](changes/unify-ration-mode-source/specs/ration-planning/spec.md), and [verified implementation tasks](changes/unify-ration-mode-source/tasks.md).
- [Project context and rules](config.yaml).

Authoritative existing context remains [AGENTS.md](../AGENTS.md), [strategy](../docs/STRATEGY.md),
[ration procedure](../docs/agent/ration.md), and [backlog](../docs/todo.md).
Read only the engineering sections needed for a change; do not pull live diaries, configuration,
catalog contents, personal datasets, credentials, or research experiments into specifications.

## Pinned CLI and local tools

Use Node 24.16.0 (the setup host version) and OpenSpec **2.0.0**. From the repository root:

```sh
export OPENSPEC_TELEMETRY=0 DO_NOT_TRACK=1
npm exec --yes --package=@fission-ai/openspec@2.0.0 -- openspec --version
npm exec --yes --package=@fission-ai/openspec@2.0.0 -- openspec list --json
npm exec --yes --package=@fission-ai/openspec@2.0.0 -- openspec status --change unify-ration-mode-source --json
npm exec --yes --package=@fission-ai/openspec@2.0.0 -- openspec validate --all --strict --json --no-interactive
npm exec --yes --package=@fission-ai/openspec@2.0.0 -- openspec instructions apply --change unify-ration-mode-source --json
```

`npm exec` may populate npm's package cache; no global install or global profile is needed.
The commands above inspect artifacts. `instructions apply` reports instructions and outstanding
tasks; it does not execute the fix. Validation checks document structure, not planner correctness.

Initialization used `init --tools codex,claude --profile core --language English --no-animation .`.
Do not rerun initialization to begin a change. Six generated skills live in `.agents/skills/`;
`.claude/skills/` contains individual relative links to them. Codex can select `openspec-propose`
or invoke `$openspec-propose`; Claude uses `/openspec-propose`. The other core skills cover
exploration, updating a proposal, implementation, spec synchronization and archival.
Implementation is complete under its explicit assignment. Review the working candidate; syncing
deltas and archival remain later actions after acceptance.

## Current versus proposed behavior

The base spec describes implemented input parsing and write protection. It deliberately does
not promise a single mode authority: current code takes mode from two sources.
The active change's delta specifies the implemented single-global-mode behavior awaiting review.
Keep the delta active; do not sync it into the accepted baseline or archive it before acceptance.

The proposal assumes the stored Plan was recalculated through the existing workflow under
current configuration. Selecting one mode does **not** refresh stored calorie/carbohydrate
values. Automatic refresh or stale-Plan rejection is outside this change.

## Test isolation and next step

Preparation ran no product tests. Implementation then passed eight focused and 37 selected integration
tests using synthetic copied roots. Future tests must continue to follow the
temporary copied-engine pattern in `tests/test_protein_status.py`: generate all configuration,
diaries, profile, catalog and ration fixtures in that temporary root. `DIET_DB` alone redirects
only the catalog; other paths remain tied to the engine checkout. Do not run ordinary product
commands against live data as part of this pilot's checks.

Next: review the actual synthetic before/after results and the freshness assumption. Existing
unrelated planner matching work was preserved. All nine implementation tasks are verified;
acceptance, spec synchronization and archive are still separate steps. Existing instructions
continue to govern writes and checks.

## Decide after 2–3 actual changes

Record whether scenarios resolved a real ambiguity, caught a missed path, or reduced handoff
work; also record authoring/review effort and duplicate-document drift. Continue only if that
benefit outweighs the overhead. Prefer the existing docs plus focused tests if OpenSpec merely
duplicates them. Do not backfill untouched domains to make the pilot look complete.
