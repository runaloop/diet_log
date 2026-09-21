#!/usr/bin/env python3
"""Recompute a diary's «## План» block and «**Потреблено:**» line from its table.

The food/activity table is the only source of truth; the numbers in the
diary head are derived from it here, never by hand:

  Съедено / Потрачено / Дефицит   deficit = base + spent − eaten
  Можно съесть до −N              base + spent − window floor − eaten
  Углеводы (цель)                 (base + spent − floor − protein·4 − fat_floor·9) / 4
  Белок (цель)                    summary.protein_floor()            (1.8 g/kg)
  Жиры (цель = cap of the day)    summary.fat_range(load, budget)    (STRATEGY.md §7)

Usage:
  recalc_plan.py <diary.md> [--base N] [--load low|mid|high] [--write]

Without --write: prints the block it would write, then the chat status
lines. With --write: replaces the three План bullets and the Потреблено
line in place (creating the block, the empty table and the line when the
diary has only its header yet) and prints the status lines.

Base expenditure: --base, else the diary's own «Базовый расход:» (a past
day keeps its number), else «Эффективный базовый расход … N ккал» in
config/user.md. Day load: --load, else classified from the table's
training rows (summary.day_load). Mode: config/cycle.md.
"""
import re
import sys
from pathlib import Path

from paths import USER
from summary import (DAY_FAT_SHARE, load_goals, global_mode, day_load,
                     day_deficit_window, day_budget, protein_floor, fat_range,
                     day_status_lines)
from validate_diary import parse_table
from plan_ration import diary_date

TABLE_HEADER = ['| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |',
                '|-------|--------------------|---|---|---|---|-----------|']
PLAN_HEADING = '## План'
USER_BASE_RE = re.compile(r'Эффективный базовый расход[^\n]*?(\d+)\s*ккал')
DIARY_BASE_RE = re.compile(r'Базовый расход:\s*(\d+(?:\.\d+)?)')
MODE_RE = re.compile(r'(?:Режим|Фаза цикла):\s*(\S+)')
STATUS_ORDER = ('дефицит', 'углеводы', 'белок')


def fmt_num(x):
    """Integers as integers; a genuinely fractional sum keeps one decimal so
    validate_diary (ε=0.15 against the table) still agrees."""
    return f'{int(round(x))}' if abs(x - round(x)) < 0.05 else f'{x:.1f}'


def window_label(lo):
    """«до −300» for a 300 kcal deficit floor, «до 0», «до +150» for −150."""
    if lo > 0:
        return f'−{lo}'
    return '0' if lo == 0 else f'+{-lo}'


def find_base(text, base=None):
    if base is not None:
        return round(base)
    m = DIARY_BASE_RE.search(text)
    if m:
        return round(float(m.group(1)))
    if USER.exists():
        m = USER_BASE_RE.search(USER.read_text(encoding='utf-8'))
        if m:
            return int(m.group(1))
    sys.exit('Базовый расход не найден ни в дневнике, ни в config/user.md — задай --base N')


def compute(lines, ref, base=None, load=None, goals=None, mode=None):
    """All План numbers for the diary `lines` of day `ref`."""
    goals = goals or load_goals()
    mode = mode or global_mode()
    text = '\n'.join(lines)
    food, spent = parse_table(lines)
    base = find_base(text, base)
    load = load or day_load(ref, lines)
    eaten = food['К']
    lo, hi = day_deficit_window(load, goals, mode)
    deficit = base + spent - eaten
    can_eat = base + spent - lo - eaten
    prot_t = protein_floor(goals)
    budget = day_budget(base, spent, load, goals, mode)
    ffloor, fcap = fat_range(load, goals, budget)
    carb_t = max(0, round((base + spent - lo - prot_t * 4 - ffloor * 9) / 4))
    carb_left = max(0, carb_t - round(food['У']))
    diary_mode = MODE_RE.search(text)
    if diary_mode and diary_mode.group(1).lower() != mode:
        print(f'⚠ режим в заголовке дневника ({diary_mode.group(1)}) ≠ config/cycle.md ({mode}) '
              f'— считаю по cycle.md', file=sys.stderr)
    return dict(base=base, eaten=eaten, spent=spent, deficit=deficit, lo=lo, hi=hi,
                can_eat=can_eat, prot_t=prot_t, ffloor=ffloor, fcap=fcap,
                carb_t=carb_t, carb_left=carb_left, food=food, load=load,
                mode=mode, goals=goals)


def plan_bullets(c):
    f = c['food']
    return [
        f"- Базовый расход: {c['base']} ккал | Съедено: {fmt_num(c['eaten'])} | "
        f"Потрачено: {fmt_num(c['spent'])} | Дефицит: {fmt_num(c['deficit'])} ккал",
        f"- Можно съесть до {window_label(c['lo'])}: {fmt_num(c['can_eat'])} ккал",
        f"- Углеводы: {c['carb_t']}г (съедено: {fmt_num(f['У'])}, осталось: {c['carb_left']}) · "
        f"Белок: {c['prot_t']}г (съедено: {fmt_num(f['Б'])}) · "
        f"Жиры: {c['fcap']}г (съедено: {fmt_num(f['Ж'])})",
    ]


def consumed_line(food):
    return (f"**Потреблено:** К{fmt_num(food['К'])} | Б{fmt_num(food['Б'])} | "
            f"Ж{fmt_num(food['Ж'])} | У{fmt_num(food['У'])} | Клет{fmt_num(food['Клетчатка'])}")


def apply(lines, c):
    """`lines` with the План bullets and the Потреблено line replaced (or
    appended, with the empty table, when the diary is only a header)."""
    bullets, cons = plan_bullets(c), consumed_line(c['food'])
    out = list(lines)
    while out and out[-1].strip() == '':
        out.pop()

    heading = next((i for i, l in enumerate(out) if l.strip() == PLAN_HEADING), None)
    if heading is None:
        out += ['', PLAN_HEADING, '', *bullets, '', *TABLE_HEADER, '', cons]
        return out

    first = last = None
    for i in range(heading + 1, len(out)):
        s = out[i].strip()
        if s.startswith('- '):
            first = i if first is None else first
            last = i
        elif s == '' and first is None:
            continue
        else:
            break
    if first is None:
        out[heading + 1:heading + 1] = ['', *bullets]
        last = heading + len(bullets) + 1
    else:
        out[first:last + 1] = bullets
        last = first + len(bullets) - 1

    if not any(l.strip().startswith('| Время') for l in out):
        out[last + 1:last + 1] = ['', *TABLE_HEADER]

    k = next((i for i, l in enumerate(out) if '**Потреблено:**' in l), None)
    if k is None:
        out += ['', cons]
    else:
        out[k] = cons
    return out


def status_block(c, ref, lines):
    f = c['food']
    st = day_status_lines(ref, c['eaten'], c['spent'], c['deficit'], f['Б'], f['Ж'], f['У'],
                          c['carb_t'], load=c['load'], goals=c['goals'], mode=c['mode'],
                          lines=lines)
    block = [st[k] for k in STATUS_ORDER if k in st]
    if st.get('жиры_вне'):
        block.append(st['жиры'])
    return block


def main(argv):
    pos, flags, base, load = [], set(), None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--base':
            i += 1
            try:
                base = float(argv[i])
            except (IndexError, ValueError):
                sys.exit('--base: требуется число')
        elif a == '--load':
            i += 1
            if i >= len(argv) or argv[i] not in DAY_FAT_SHARE:
                sys.exit('--load: требуется low|mid|high')
            load = argv[i]
        elif a.startswith('--'):
            flags.add(a)
        else:
            pos.append(a)
        i += 1
    if len(pos) != 1:
        print(f'Usage: {sys.argv[0]} <diary.md> [--base N] [--load low|mid|high] [--write]')
        sys.exit(1)

    path = Path(pos[0]).resolve()
    if not path.exists():
        sys.exit(f'нет файла: {path}')
    ref = diary_date(str(path))
    lines = path.read_text(encoding='utf-8').rstrip('\n').split('\n')
    c = compute(lines, ref, base, load)
    new = apply(lines, c)
    if '--write' in flags:
        path.write_text('\n'.join(new) + '\n', encoding='utf-8')
    else:
        print('\n'.join(plan_bullets(c)))
        print(consumed_line(c['food']))
        print()
    print('\n'.join(status_block(c, ref, new)))


if __name__ == '__main__':
    main(sys.argv[1:])
