"""Local, never-committed history of pill intake.

pills.md holds only the current day and is overwritten when a new day
starts, so the record of what was actually taken used to vanish. This
script snapshots pills.md into pills_history.jsonl (one JSON line per
day, gitignored alongside pills.md) and reports adherence from it.

    python3 scripts/pills.py sync [date]     # upsert today's snapshot
    python3 scripts/pills.py history [days]  # last N days (default 30)
    python3 scripts/pills.py build [date] [--force]
                                             # write pills.md for the day from
                                             # config/medications.md

`sync` is idempotent: re-running it replaces that date's line rather
than appending a duplicate, so it is safe to call after every check-off
and again right before a new day overwrites pills.md.

`build` evaluates each medications.md row's period for the day
(`бессрочно` · `YYYY-MM-DD..YYYY-MM-DD` · `YYYY-MM-DD..бессрочно` ·
weekday list `пн,ср,пт`; note `через день` = every second day from the
period start) and writes the checklist (all 🔲). Nothing due → no file.
A same-day pills.md that already has check marks is left alone unless
--force.
"""
import json
import re
import sys
from datetime import date, timedelta

from paths import PILLS, PILLS_HISTORY, MEDICATIONS
from format_tables import format_file

TAKEN_MARK = '✅'
HEADER_CELLS = {'·', 'препарат', 'доза', 'приём', 'принято'}
WEEKDAYS = {'пн': 0, 'вт': 1, 'ср': 2, 'чт': 3, 'пт': 4, 'сб': 5, 'вс': 6}
PERIOD_RANGE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2}|бессрочно)$')
PILLS_HEADER = ['| · | Препарат | Доза | Приём | Принято |',
                '|---|----------|------|-------|---------|']


def parse_medications(path=MEDICATIONS):
    """Rows of config/medications.md: [{'name','dose','slot','period','note'}]."""
    rows = []
    for line in path.read_text(encoding='utf-8').split('\n'):
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if len(cells) < 4 or cells[0].lower() == 'препарат' or set(cells[0]) <= {'-', ' '}:
            continue
        rows.append({'name': cells[0], 'dose': cells[1], 'slot': cells[2],
                     'period': cells[3], 'note': cells[4] if len(cells) > 4 else ''})
    return rows


def due(row, day):
    """True if the row's period covers `day` (see module docstring)."""
    period = row['period'].strip().lower()
    start = None
    if period == 'бессрочно':
        ok = True
    elif (m := PERIOD_RANGE_RE.match(period)):
        start = date.fromisoformat(m.group(1))
        end = None if m.group(2) == 'бессрочно' else date.fromisoformat(m.group(2))
        ok = start <= day and (end is None or day <= end)
    else:
        days = {WEEKDAYS.get(t.strip()) for t in period.split(',')}
        if None in days:
            raise ValueError(f'период не распознан: {row["period"]!r} ({row["name"]})')
        ok = day.weekday() in days
    if ok and 'через день' in row['note'].lower() and start is not None:
        ok = (day - start).days % 2 == 0
    return ok


def build(day=None, force=False):
    """Write pills.md for `day`; (day, rows written) or (day, 0) when nothing is due."""
    day = day or date.today()
    if not MEDICATIONS.exists():
        return day, 0
    rows = [r for r in parse_medications() if due(r, day)]
    if not rows:
        return day, 0
    if PILLS.exists() and not force:
        cur_day, items = parse_pills()
        if cur_day == day and any(i['taken'] for i in items):
            sys.exit(f'pills.md на {day} уже с отметками — не трогаю (--force чтобы перезаписать)')
    lines = [f'# Таблетки {day.isoformat()}', '', *PILLS_HEADER]
    lines += [f'| 🔲 | {r["name"]} | {r["dose"]} | {r["slot"]} | |' for r in rows]
    PILLS.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    format_file(PILLS)
    return day, len(rows)


def parse_pills(path=PILLS):
    """(date, items) from pills.md; (None, []) when the file is absent.

    items = [{'name','dose','slot','taken'}], taken = 'HH:MM'/'✓'/None.
    """
    if not path.exists():
        return None, []
    lines = path.read_text().split('\n')
    day = None
    items = []
    for line in lines:
        s = line.strip()
        if s.startswith('#') and not day:
            try:
                day = date.fromisoformat(s.split()[-1])
            except ValueError:
                pass
            continue
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if len(cells) < 5 or set(c.lower() for c in cells) & HEADER_CELLS:
            continue
        if set(cells[0]) <= {'-', ' '}:
            continue
        items.append({
            'name': cells[1],
            'dose': cells[2],
            'slot': cells[3],
            'taken': cells[4] or None,
        })
    return day, items


def load_history(path=PILLS_HISTORY):
    """{date_str: record} from the jsonl log, empty when it does not exist."""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().split('\n'):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        out[rec['date']] = rec
    return out


def save_history(records, path=PILLS_HISTORY):
    """Rewrite the log, one record per line, oldest first."""
    lines = [json.dumps(records[k], ensure_ascii=False)
             for k in sorted(records)]
    path.write_text('\n'.join(lines) + '\n')


def sync(day=None):
    """Snapshot pills.md into the history, replacing that date's record."""
    parsed_day, items = parse_pills()
    if not items:
        return None, 0
    day = day or parsed_day or date.today()
    records = load_history()
    records[day.isoformat()] = {'date': day.isoformat(), 'items': items}
    save_history(records)
    return day, sum(1 for i in items if i['taken'])


def history(days=30, ref=None):
    """Adherence report over the last `days` days ending at `ref`."""
    ref = ref or date.today()
    start = ref - timedelta(days=days - 1)
    records = load_history()
    rows = [r for k, r in sorted(records.items())
            if start.isoformat() <= k <= ref.isoformat()]
    if not rows:
        return f'Истории приёма за {start}..{ref} нет'

    out = [f'## Таблетки {start}..{ref}', '']
    for rec in reversed(rows):
        marks = ''.join(TAKEN_MARK if i['taken'] else '🔲' for i in rec['items'])
        got = sum(1 for i in rec['items'] if i['taken'])
        out.append(f'- {rec["date"]}  {marks}  {got}/{len(rec["items"])}')

    tally = {}
    for rec in rows:
        for i in rec['items']:
            key = (i['name'], i['slot'])
            got, total = tally.get(key, (0, 0))
            tally[key] = (got + (1 if i['taken'] else 0), total + 1)
    out += ['', f'### Соблюдение ({len(rows)} дней с записями)']
    for (name, slot), (got, total) in sorted(tally.items(),
                                             key=lambda kv: kv[1][0] / kv[1][1]):
        pct = round(got / total * 100)
        sym = '✓' if pct >= 90 else ('⚠' if pct >= 70 else '✗')
        out.append(f'- {sym} {name} ({slot}): {got}/{total} — {pct}%')
    return '\n'.join(out)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'history'
    if cmd == 'sync':
        day = date.fromisoformat(argv[2]) if len(argv) > 2 else None
        day, got = sync(day)
        if day is None:
            print('pills.md пуст или отсутствует — нечего сохранять')
            return
        print(f'✓ {day} записан в {PILLS_HISTORY.name} (принято {got})')
    elif cmd == 'history':
        days = int(argv[2]) if len(argv) > 2 else 30
        print(history(days))
    elif cmd == 'build':
        pos = [a for a in argv[2:] if not a.startswith('--')]
        day = date.fromisoformat(pos[0]) if pos else None
        day, n = build(day, force='--force' in argv)
        if n == 0:
            print(f'на {day} приёмов нет — pills.md не создаю')
        else:
            print(f'✓ pills.md на {day} ({n} строк)')
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main(sys.argv)
