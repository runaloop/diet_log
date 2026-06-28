# diet_log — an LLM-driven calorie & diet tracker

A plain-text food, training and weight tracker meant to be driven by an AI coding
agent (Claude Code, or any agent that reads `AGENTS.md`). You type what you ate in
natural language; the agent looks up macros in a local product catalog (SQLite),
appends a row to today's diary, recomputes your daily energy balance, and tells you
how much you have left to eat.

The full agent behavior — parsing rules, macro math, training compensation, weekly
deficit cycling, the Mediterranean-leaning ration planner — lives in
[`AGENTS.md`](AGENTS.md) (`CLAUDE.md` is a symlink to it). That file *is* the program.

## What it does

- **Food logging.** "235г греческого йогурта" → catalog lookup → diary row with K/B/Zh/U.
  Unknown products are looked up online and added to the catalog.
- **Energy balance.** Base expenditure (resting metabolic rate) + logged training −
  food eaten → daily deficit, with macro targets (protein floor by bodyweight, carbs
  as the remainder, fat band).
- **Training compensation.** Logged workouts add back calories and post-workout carbs.
- **Weekly cycling.** Configurable deficit / maintenance week phases (`config/cycle.md`).
- **Ration planner.** Suggests what to eat from your frequent staples to hit the day's
  target instead of discovering an overshoot after the fact.
- **Period summaries.** Day / week / month rollups via `scripts/summary.py`.

## Layout

```
AGENTS.md            the agent's instructions (CLAUDE.md → symlink)
today.md             symlink → diaries/YYYY/MM/DD.md (current day)
config/              your inputs (goals, cycle, training types, weight history)
data/diet.db         product catalog (SQLite) — the single source of truth
diaries/YYYY/MM/DD   one markdown file per day
scripts/             python helpers (summary, ration planner, db wrapper, validators)
```

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
  `ration.md` / `pills.md`.

If you fork this and want a different split, edit `.gitignore` before your first commit.

## Requirements

- Python 3 (standard library only — no third-party packages)
- An LLM coding agent that reads `AGENTS.md`

## License

[MIT](LICENSE).
