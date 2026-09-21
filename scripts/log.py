#!/usr/bin/env python3
"""Log food or training into a diary in one call.

  log.py [<diary.md>] "<продукт>[:<граммы>]" ["<продукт2>[:г]" …] [options]

  --time HH:MM                    row time (default: now); rows land in time order
  --portion catalog|median|last   portion when no grams are given (default: catalog)
  --k K --b B --zh Ж --u У [--fiber F]
                                  explicit macros for ONE product: no catalog lookup,
                                  nothing added to the catalog («!»-dishes, user-given macros)
  --activity "Бег Z2 52 мин" --kcal 480
                                  training row (kcal stored negative); pairs may repeat
  --no-ration                     skip the plan_ration top-up at the end

Diary defaults to today.md (the symlink is resolved; the real file is written).
A product is resolved in the catalog (data/diet.db): exact name/alias first, then
a unique substring. No match or several candidates → nothing is written, the
candidates are printed, exit code ≠ 0. "Name 250г" / "Name 250" work like
"Name:250". Grams scale the catalog macros from its default portion; for
piece/ml portions the number is pieces/ml.

After the rows are in: План block + Потреблено recomputed (recalc_plan),
tables aligned (format_tables), diary validated (validate_diary), and the
reply printed — the new rows, the status block, the plan_ration top-up.
"""
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from paths import ROOT, DIARIES, PROFILE_PATH
from plan_ration import load_macros, find_canonical, diary_date
from profile import parse_food_rows, load_canon
from format_tables import format_file, format_text
from validate_diary import validate
import recalc_plan

TIME_RE = re.compile(r'^\d{2}:\d{2}$')
COLON_RE = re.compile(r'^(.*?):\s*(\d+(?:[.,]\d+)?)\s*$')
TRAIL_RE = re.compile(r'^(.*\S)\s+(\d+(?:[.,]\d+)?)\s*(?:г|гр|g)?\s*$', re.IGNORECASE)
LEAD_RE = re.compile(r'^(\d+(?:[.,]\d+)?)\s*(?:г|гр|g)?\s+(.+)$', re.IGNORECASE)
RAW_UNIT_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(мл|шт)', re.IGNORECASE)
MACRO_KEYS = ('k', 'b', 'zh', 'u', 'fiber')


def num(s):
    return float(str(s).replace(',', '.'))


def split_spec(spec):
    """'Экспонента:250' / 'Экспонента 250г' / '200гр помидоры' → (name, grams|None)."""
    s = spec.strip()
    m = COLON_RE.match(s)
    if m:
        return m.group(1).strip(), num(m.group(2))
    m = TRAIL_RE.match(s)
    if m and num(m.group(2)) > 0:
        return m.group(1).strip(), num(m.group(2))
    m = LEAD_RE.match(s)
    if m and num(m.group(1)) > 0:
        return m.group(2).strip(), num(m.group(1))
    return s, None


def fmt_g(x):
    return f'{x:g}'


def profile_median(name):
    if not PROFILE_PATH.exists():
        return None
    for p in json.loads(PROFILE_PATH.read_text(encoding='utf-8')):
        if p['name'].lower() == name.lower():
            g = p.get("median_grams")
            return round(g) if g else None
    return None


def last_logged_grams(name):
    canon = load_canon()
    target = name.lower()
    for path in sorted(DIARIES.glob('20*/[0-1][0-9]/[0-3][0-9].md'), reverse=True):
        for row_name, grams, *_ in parse_food_rows(path.read_text(encoding='utf-8').split('\n')):
            if canon.get(row_name.lower(), row_name).lower() == target and grams:
                return grams
    return None


def scale(rec, qty):
    """(label, macros) for `qty` of the catalog product `rec` (grams, or
    pieces/ml when the catalog portion is not in grams; None = one portion)."""
    name, raw, pg = rec['name'], rec['portion_raw'] or 'порция', rec['portion_g']
    base = {k: (rec.get(k) or 0.0) for k in MACRO_KEYS}
    if qty is None:
        label = f'{name} {fmt_g(pg)}г' if pg else f'{name} {raw}'
        return label, base
    if pg:
        factor, label = qty / pg, f'{name} {fmt_g(qty)}г'
    else:
        m = RAW_UNIT_RE.search(raw)
        if m:
            unit = m.group(2).lower()
            factor = qty / num(m.group(1))
            label = f'{name} {fmt_g(qty)}{unit}' if unit == 'мл' else f'{name} {fmt_g(qty)} шт'
        else:
            factor = qty
            label = f'{name} {raw}' if qty == 1 else f'{name} {fmt_g(qty)}x {raw}'
    return label, {k: v * factor for k, v in base.items()}


def food_row(t, label, m):
    cells = [str(int(round(m[k]))) for k in MACRO_KEYS]
    return f'| {t} | {label} | ' + ' | '.join(cells) + ' |'


def activity_row(t, name, kcal):
    return f'| {t} | {name} | -{int(round(kcal))} | - | - | - | - |'


def insert_rows(lines, rows, t):
    """Insert `rows` after the last table row whose time ≤ `t`; the table
    must exist (recalc_plan creates it for a fresh diary)."""
    header = next((i for i, l in enumerate(lines)
                   if l.strip().startswith('|') and 'Продукт/Активность' in l), None)
    if header is None:
        raise SystemExit('в дневнике нет таблицы питания')
    pos = header + 2  # after header + separator
    i = pos
    while i < len(lines) and lines[i].strip().startswith('|'):
        cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
        if cells and TIME_RE.match(cells[0]) and cells[0] > t:
            break
        i += 1
        pos = i
    return lines[:pos] + rows + lines[pos:]


def parse_args(argv):
    o = dict(diary=None, specs=[], time=None, portion='catalog', macros={},
             activities=[], ration=True)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--time':
            i += 1
            if i >= len(argv) or not TIME_RE.match(argv[i]):
                sys.exit('--time: требуется HH:MM')
            o['time'] = argv[i]
        elif a == '--portion':
            i += 1
            if i >= len(argv) or argv[i] not in ('catalog', 'median', 'last'):
                sys.exit('--portion: catalog|median|last')
            o['portion'] = argv[i]
        elif a in ('--k', '--b', '--zh', '--u', '--fiber'):
            i += 1
            try:
                o['macros'][a[2:]] = num(argv[i])
            except (IndexError, ValueError):
                sys.exit(f'{a}: требуется число')
        elif a == '--activity':
            i += 1
            if i >= len(argv):
                sys.exit('--activity: требуется название (с зоной: «Бег Z2 52 мин»)')
            o['activities'].append([argv[i], None])
        elif a == '--kcal':
            i += 1
            if not o['activities'] or o['activities'][-1][1] is not None:
                sys.exit('--kcal идёт после своего --activity')
            try:
                o['activities'][-1][1] = abs(num(argv[i]))
            except (IndexError, ValueError):
                sys.exit('--kcal: требуется число')
        elif a == '--no-ration':
            o['ration'] = False
        elif a.startswith('--'):
            sys.exit(f'неизвестный флаг {a}')
        elif o['diary'] is None and not o['specs'] and a.endswith('.md'):
            o['diary'] = a
        else:
            o['specs'].append(a)
        i += 1
    if any(k is None for _, k in o['activities']):
        sys.exit('у каждой --activity должен быть --kcal')
    if o['macros']:
        if len(o['specs']) != 1:
            sys.exit('--k/--b/--zh/--u — ровно для одного продукта')
        missing = [k for k in ('k', 'b', 'zh', 'u') if k not in o['macros']]
        if missing:
            sys.exit(f'явные КБЖУ: не хватает --{" --".join(missing)}')
    if not o['specs'] and not o['activities']:
        print(__doc__)
        sys.exit(1)
    return o


def main(argv):
    o = parse_args(argv)
    if o['diary']:
        path = Path(o['diary']).resolve()
    else:
        path = (ROOT / 'today.md').resolve()
    if not path.exists():
        sys.exit(f'нет дневника: {path}')
    ref = diary_date(str(path))
    if o['diary'] is None and ref != date.today():
        print(f'⚠ today.md указывает на {ref}, а сегодня {date.today()} — «новый день» не сделан?',
              file=sys.stderr)
    t = o['time'] or datetime.now().strftime('%H:%M')

    rows = []
    if o['macros']:
        name, grams = split_spec(o['specs'][0].lstrip('!').strip())
        label = f'{name} {fmt_g(grams)}г' if grams else name
        m = {k: o['macros'].get(k, 0.0) for k in MACRO_KEYS}
        rows.append(food_row(t, label, m))
    elif o['specs']:
        macros = load_macros()
        resolved = []
        for spec in o['specs']:
            name_q, grams = split_spec(spec)
            rec = find_canonical(name_q, macros, what='log')
            if grams is None and o['portion'] == 'median':
                grams = profile_median(rec['name'])
            elif grams is None and o['portion'] == 'last':
                grams = last_logged_grams(rec['name'])
            resolved.append(scale(rec, grams))
        rows += [food_row(t, label, m) for label, m in resolved]
    rows += [activity_row(t, name, kcal) for name, kcal in o['activities']]

    lines = path.read_text(encoding='utf-8').rstrip('\n').split('\n')
    if not any('Продукт/Активность' in l for l in lines):
        lines = recalc_plan.apply(lines, recalc_plan.compute(lines, ref))
    lines = insert_rows(lines, rows, t)
    c = recalc_plan.compute(lines, ref)
    lines = recalc_plan.apply(lines, c)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    format_file(path)

    print(format_text('\n'.join(recalc_plan.TABLE_HEADER + rows)))
    print()
    print('\n'.join(recalc_plan.status_block(c, ref, lines)))
    errs = validate(path)
    if errs:
        print('\n⚠ validate_diary:\n' + '\n'.join(f'   ✗ {e}' for e in errs))
    if o['ration']:
        r = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'plan_ration.py'), str(path)],
                           capture_output=True, text=True)
        out = (r.stdout or r.stderr).strip()
        if out:
            print()
            print(out)


if __name__ == '__main__':
    main(sys.argv[1:])
