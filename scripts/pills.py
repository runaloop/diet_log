"""Local, never-committed history of pill intake.

pills.md holds only the current day and is overwritten when a new day
starts, so the record of what was actually taken used to vanish. This
script snapshots pills.md into pills_history.jsonl (one JSON line per
day, gitignored alongside pills.md) and reports adherence from it.

    python3 scripts/pills.py sync [date]     # upsert today's snapshot
    python3 scripts/pills.py history [days]  # last N days (default 30)

`sync` is idempotent: re-running it replaces that date's line rather
than appending a duplicate, so it is safe to call after every check-off
and again right before a new day overwrites pills.md.
"""
import json
import sys
from datetime import date, timedelta

from paths import PILLS, PILLS_HISTORY

TAKEN_MARK = '✅'
HEADER_CELLS = {'·', 'препарат', 'доза', 'приём', 'принято'}


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
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main(sys.argv)
