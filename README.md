# diet_log — an LLM-driven calorie & diet tracker

A plain-text food, training and weight tracker meant to be driven by an AI coding
agent (Claude Code, or any agent that reads `AGENTS.md`). You type what you ate in
natural language; the agent looks up macros in a local product catalog (SQLite),
appends a row to today's diary, recomputes your daily energy balance, and tells you
how much you have left to eat.

The full agent behavior — parsing rules, per-day deficit periodization, the
Mediterranean-leaning ration planner — lives in [`AGENTS.md`](AGENTS.md) (core rules)
and `docs/agent/` (procedures read on demand); the arithmetic lives in `scripts/`.

## What it does

- **Food logging.** "235г греческого йогурта" → catalog lookup → diary row with K/B/Zh/U.
  Unknown products are looked up online and added to the catalog.
- **Automatic food classification.** The agent estimates food-group contributions
  from the dish type when a recipe is unavailable. Annotation estimates are separate
  from nutritional values. Unknown composition is an accepted outcome, with no
  repeated logging warnings; assumptions remain inspectable via `db review --all`.
  See [`catalog.md`](docs/agent/catalog.md).
- **Energy balance.** Base expenditure (resting metabolic rate) + logged training −
  food eaten → daily deficit, with macro targets (protein floor by bodyweight, carbs
  as the remainder, fat band).
- **Training days.** Logged workouts add back calories; the day's deficit window and
  macro targets follow its training load (rest / moderate / hard).
- **Per-day periodization.** The deficit lives on rest days, training days are fueled;
  `config/cycle.md` holds the global mode (cutting or maintenance).
- **Ration planner.** Suggests what to eat from your frequent staples to hit the day's
  target instead of discovering an overshoot after the fact.
- **Period summaries.** Day / week / month rollups via `scripts/summary.py`.
## Layout

```
AGENTS.md            the agent's instructions — that file *is* the program
today.md             symlink → diaries/YYYY/MM/DD.md (current day)
config/              your inputs (goals, cycle, training types, weight history)
data/diet.db         product catalog (SQLite) — the single source of truth
data/garmin_pending.json  days whose Garmin sync failed, retried by the next apply (absent when empty)
diaries/YYYY/MM/DD   one markdown file per day
scripts/             python helpers (log, recalc_plan, summary, ration planner, db wrapper, validators)
.codex/hooks.json    Codex SessionStart hook that opens the live today.md pane in Orca
tests/               unittest suite — `python3 -m unittest discover -s tests`
.pi/extensions/      pi-only project extension for the local Bonsai model — `node --test tests/bonsai_guard.test.mjs`
```

Tests of the catalog CLI use `DIET_DB=/tmp/catalog.db` to select an isolated
database; without this override all scripts use `data/diet.db`.

### Codex diary pane in Orca

`scripts/today_pane.sh` opens the live diary next to the calling terminal. A lock
detects a running viewer; local records in `data/today_panes/` remember each tab's
viewer pane across restarts, using its stable leaf ID rather than its title.
The launcher restarts a stopped viewer only at an empty shell prompt, accounting
for wrapped screen rows. If its pane is busy, disconnected, or unreadable, it
leaves the layout unchanged instead of adding a duplicate. Saved `today.md`
titles recover older panes; the viewer leaves its title in place on exit.
Concurrent hooks are serialized per tab. An ambiguous caller does not trigger a
split in another agent's tab. The viewer requires `glow`,
`inotifywait`, and `flock` (util-linux).

Codex 0.166 defers `SessionStart` until the
first message, so `.codex/hooks.json` alone cannot open the pane on the empty start
screen. To open it immediately, call the script before `codex` from your shell
launcher. Keep the hook as a fallback for session startup and resume.

## Use it for yourself

This repo is a GitHub **template**. Click **Use this template**, then on your clone:

```bash
./setup.sh
```

`setup.sh` is idempotent. It:
1. copies `config/medications.example.md` → `config/medications.md` (local, gitignored);
2. builds `data/profile.json` from your diaries (used by the ration planner);
3. sets a local anonymous git committer so your real name/email never enters history;
4. points `today.md` at today's diary (seeded from the example day).

Then open the repo with an agent that reads `AGENTS.md` and start typing what you eat.

## Privacy model (single public repo)

Everything committed here is **public** by design — including the weight history in
`config/user.md` and the food diaries. The deliberate trade-off:

- **Public & synced:** engine, product catalog, diaries, weight history, goals.
- **Local only (gitignored, never published, single-device):** medication / supplement
  tracking (`config/medications.md`, `config/meds/`) and the daily working files
  `ration.md` / `pills.md`, and `docs/research/` (training experiments: raw workout
  data, analysis scripts, notes).

If you fork this and want a different split, edit `.gitignore` before your first commit.

## Requirements

- Python 3 (standard library only — no third-party packages)
- An LLM coding agent that reads `AGENTS.md` natively: Claude Code ≥ 2.1.277, opencode, or any other

## License

[MIT](LICENSE).
