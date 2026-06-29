#!/usr/bin/env python3
"""Suggest today's ration as a *weekly food-group debt-closer* (STRATEGY.md §12).

Not a bottom-up macro filler. The driver is the **week remainder**: which floor
food-groups (овощи/рыба/бобовые/…, leaning Mediterranean) the ISO-week is still
behind on. The planner proposes dishes that close the most-behind group first,
within today's soft kcal ceiling, keeping the daily-hard protein floor, biased to
no-cook food, without repeating a dish, and never pushing a limit group
(мясо/сладкое/добавки) over its weekly cap.

Priority stack (lexicographic, STRATEGY.md §3):
  1. food-group pattern     → pick the most-behind floor group, close it
  2. weekly cycle           → today's kcal ceiling already reflects the phase
  3. daily targets          → kcal ceiling (soft) + protein floor (hard) as bounds
  4. variety                → don't repeat a dish; deprioritise this-week's dishes

Data: week remainder + group tags come from diet.db via summary.py; concrete
dishes + real portions come from profile.json (the eating warehouse). The catalog
is the source of group membership and prep effort.

Usage:
  plan_ration.py YYYY/MM/DD.md
  plan_ration.py today.md
"""
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from summary import (GROUP_QUOTA, GROUP_ORDER, GRAM_GROUPS, week_range,
                     cycle_phase, protein_floor, group_servings)
from profile import parse_food_rows
from paths import DB_PATH, PROFILE_PATH, RATION, diary_path

# Daily caps so the suggestion is a sane day, not a feast. The kcal ceiling is
# the real size limiter; these stop one group from monopolising the plate.
PER_GROUP_DAY_CAP = 2
MAX_DISHES = 7
# A serving must be a real portion, not a condiment/garnish. Dishes whose median
# portion is below this contribute a phantom group serving — drop them.
MIN_DISH_KCAL = 30
# Group fill stops once it reaches this fraction of the weekly quota — the day is
# a free variable (STRATEGY.md §2), no single day must close a week's worth.
PROT_TOL = 6          # protein floor considered met within this many grams
PREP_RANK = {'low': 0, 'med': 1, None: 1.5, 'high': 2}

# Load-week carb layer (STRATEGY.md §9): on a maintenance/load week, carbs are
# pulled forward around training; on rest days they're moderated and spread.
CARB_TOL = 12
CARB_TOPUP_CAP = 2
REST_DAY_CARB_FRAC = 0.6   # untrained load day → moderate carb fill

SUPP_GROUP = 'добавки'
SUPP_CAP = 1          # at most one supplement/shake in a ration


def grab(text, pat):
    m = re.search(pat, text)
    return float(m.group(1).replace('−', '-')) if m else None


def parse_plan(text):
    """Today's soft budget from the `## План` block: kcal ceiling + protein eaten."""
    m = re.search(r'Фаза цикла:\s*(\S+)', text)
    phase = m.group(1) if m else 'дефицит'
    # `Можно съесть ...: <ceiling> ккал`. Tolerates both the compact format
    # (`до −500:` / `до 0:`, AGENTS.md) and the legacy verbose one
    # (`(чтобы сохранить дефицит 500 ккал):`). Minus may be ASCII `-` or U+2212.
    kcal = grab(text, r'Можно съесть[^:]*:\s*([−-]?[\d.]+)')
    prot_eaten = grab(text, r'Белок:[^(]*\(съедено:\s*([\d.]+)') or 0.0
    carb_rem = grab(text, r'Углеводы:[^)]*осталось:\s*([\d.]+)') or 0.0
    spent = grab(text, r'Потрачено:\s*([\d.]+)') or 0.0
    return {'phase': phase, 'kcal': kcal if kcal is not None else 0.0,
            'prot_eaten': prot_eaten, 'carb_rem': max(0.0, carb_rem),
            'spent': spent}


def load_catalog():
    """name(lower) -> {'groups': [(g, w)], 'prep': str|None, 'priority': int}."""
    con = sqlite3.connect(DB_PATH)
    pg = defaultdict(list)
    for pid, g, w in con.execute(
            """SELECT pg.product_id, mg.name, pg.weight FROM product_group pg
               JOIN food_group mg ON mg.id = pg.group_id"""):
        pg[pid].append((g, w))
    out = {}
    for pid, name, prep, prio in con.execute(
            'SELECT id, name, prep_effort, priority FROM product'):
        out[name.lower()] = {'groups': pg.get(pid, []), 'prep': prep,
                             'priority': prio}
    for pid, text in con.execute('SELECT product_id, text FROM alias'):
        out.setdefault(text.lower(), {'groups': pg.get(pid, []),
                                      'prep': None, 'priority': 0})
    con.close()
    return out


def load_dishes():
    """Profile dishes joined with catalog groups + prep. One atomic median portion."""
    catalog = load_catalog()
    dishes = []
    for p in json.loads(PROFILE_PATH.read_text(encoding='utf-8')):
        if not p['per_gram'] or not p['median_grams']:
            continue
        cat = catalog.get(p['name'].lower())
        if not cat or not cat['groups']:
            continue
        g = round(p['median_grams'])
        pgm = p['per_gram']
        if pgm['k'] * g < MIN_DISH_KCAL:
            continue
        groups = dict(cat['groups'])
        dishes.append({
            'name': p['name'], 'grams': g, 'count': p['count'],
            'k': pgm['k'] * g, 'b': pgm['b'] * g,
            'zh': pgm['zh'] * g, 'u': pgm['u'] * g,
            'groups': groups,
            'prep': cat['prep'],
            'priority': cat.get('priority', 0),
            'supp': SUPP_GROUP in groups,
        })
    return dishes


def eaten_this_week(week_start, ref):
    """Lowercased dish names already logged this ISO week (for anti-repeat)."""
    names = set()
    d = week_start
    while d <= ref:
        path = diary_path(d)
        if path.exists():
            for name, *_ in parse_food_rows(path.read_text().split('\n')):
                names.add(name.lower())
        d = date.fromordinal(d.toordinal() + 1)
    return names


def limit_headroom(servings):
    """group -> remaining servings before its weekly limit (limit groups only)."""
    room = {}
    for g, (kind, q) in GROUP_QUOTA.items():
        if kind == 'limit':
            room[g] = q - servings.get(g, 0.0)
    return room


def fits_limits(dish, room):
    """A dish is allowed only if it doesn't push any limit group past its cap."""
    for g, w in dish['groups'].items():
        if g in room and dish_servings(dish, g, w) > room[g] + 1e-9:
            return False
    return True


def behind_floors(servings):
    """Floor groups still under quota, most-behind (by fraction) first."""
    out = []
    for g, (kind, q) in GROUP_QUOTA.items():
        if kind != 'floor':
            continue
        got = servings.get(g, 0.0)
        if got < q:
            out.append((g, got / q if q else 1.0))
    out.sort(key=lambda x: x[1])
    return [g for g, _ in out]


def dish_servings(dish, group, weight):
    """Servings a dish contributes to `group`. Gram-anchored groups (рыба/птица)
    count grams*meat_fraction/100; others count the flat weight."""
    if group in GRAM_GROUPS:
        return dish['grams'] * weight / 100.0
    return weight


def pick_for_group(group, dishes, used, room, kcal_left, eaten, seed):
    """Best unused dish carrying `group` that fits kcal + limits. None if none."""
    cands = [d for d in dishes
             if d['name'] not in used
             and d['groups'].get(group, 0) > 0
             and d['k'] <= kcal_left
             and fits_limits(d, room)]
    if not cands:
        return None
    # fresh (not eaten this week) → higher priority → low-prep → more of this
    # group → date rotation
    cands.sort(key=lambda d: (
        d['name'].lower() in eaten,
        -d['priority'],
        PREP_RANK.get(d['prep'], 1.5),
        -d['groups'][group],
        (d['count'] + seed) % 5,
    ))
    return cands[0]


def commit(dish, ration, servings, used, group_count):
    ration.append(dish)
    used.add(dish['name'])
    for g, w in dish['groups'].items():
        servings[g] += dish_servings(dish, g, w)
        group_count[g] += 1


def is_carb_forward(d):
    """Clean peri-workout carb: carb-dominant, low fat (STRATEGY.md §9)."""
    if d['k'] <= 0:
        return False
    return d['u'] * 4 / d['k'] >= 0.5 and d['zh'] * 9 / d['k'] <= 0.25


def carb_fill(dishes, target, ration, servings, used, room, kcal_left, eaten, seed):
    """Load-week carb layer: pull carb-forward dishes toward `target` (peri-workout
    fuel), within remaining kcal. Skipped on deficit weeks (§9)."""
    cur = sum(d['u'] for d in ration)
    added = 0
    while cur < target - CARB_TOL and added < CARB_TOPUP_CAP:
        cands = [d for d in dishes
                 if d['name'] not in used and is_carb_forward(d)
                 and d['k'] <= kcal_left and fits_limits(d, room)]
        if not cands:
            break
        def overlap(d):  # how many chosen dishes already share a group (variety)
            return sum(1 for r in ration if r['groups'].keys() & d['groups'].keys())
        cands.sort(key=lambda d: (
            d['name'].lower() in eaten,
            -d['priority'],
            overlap(d),
            PREP_RANK.get(d['prep'], 1.5),
            -(d['u'] * 4 / d['k']),
            (d['count'] + seed) % 5,
        ))
        d = cands[0]
        commit(d, ration, servings, used, defaultdict(int))
        kcal_left -= d['k']
        room = limit_headroom(servings)
        cur += d['u']
        added += 1


def protein_topup(dishes, rem_prot, ration, servings, used, room, seed):
    """Daily-hard protein floor (STRATEGY.md §8): whole food first, supplements
    capped — kcal ceiling may be overshot, protein wins over exactness."""
    supp_used = sum(1 for d in ration if d['supp'])
    while rem_prot > PROT_TOL:
        cands = [d for d in dishes
                 if d['name'] not in used and d['b'] > 0
                 and fits_limits(d, room)
                 and not (d['supp'] and supp_used >= SUPP_CAP)]
        if not cands:
            break
        # whole food before supplements; higher priority first; then cheapest
        # kcal per gram of protein
        cands.sort(key=lambda d: (d['supp'], -d['priority'], d['k'] / d['b'],
                                  (d['count'] + seed) % 5))
        d = cands[0]
        commit(d, ration, servings, used, defaultdict(int))
        supp_used += d['supp']
        rem_prot -= d['b']
    return rem_prot


def build(plan, dishes, servings, eaten, seed, cphase):
    servings = defaultdict(float, servings)
    room = limit_headroom(servings)
    used = set()
    group_count = defaultdict(int)
    ration = []
    kcal_left = plan['kcal']

    while len(ration) < MAX_DISHES:
        targets = [g for g in behind_floors(servings)
                   if group_count[g] < PER_GROUP_DAY_CAP]
        if not targets:
            break
        progressed = False
        for g in targets:
            d = pick_for_group(g, dishes, used, room, kcal_left, eaten, seed)
            if d is None:
                continue
            commit(d, ration, servings, used, group_count)
            kcal_left -= d['k']
            room = limit_headroom(servings)
            progressed = True
            break
        if not progressed or kcal_left <= 0:
            break

    floor = protein_floor(cphase)
    rem_prot = max(0.0, floor - plan['prot_eaten']
                   - sum(d['b'] for d in ration))
    protein_topup(dishes, rem_prot, ration, servings, used, room, seed)

    # Load-week carb layer (§9): carbs forward around training, moderated on rest.
    if cphase == 'поддержание':
        trained = plan['spent'] > 0
        frac = 1.0 if trained else REST_DAY_CARB_FRAC
        kcal_left = plan['kcal'] - sum(d['k'] for d in ration)
        carb_fill(dishes, plan['carb_rem'] * frac, ration, servings, used,
                  room, kcal_left, eaten, seed)
    return ration, floor


def _phase_key(phase):
    return 'дефицит' if phase.startswith('дефицит') else 'поддержание'


def render(plan, ration, floor, servings, cphase):
    phase = plan['phase']
    load = cphase == 'поддержание'
    trained = plan['spent'] > 0
    print(f"Фаза: {phase} | потолок ккал: {plan['kcal']:.0f} | "
          f"белок-флор: {floor:.0f}г (съедено {plan['prot_eaten']:.0f})")
    if load:
        hint = ('тренировка была — угли вокруг неё' if trained
                else 'до трени умеренно, основная загрузка вокруг тренировки')
        print(f"Загрузка: {hint}, остальное размазать по дню.")

    behind = behind_floors(defaultdict(float, servings))
    if behind:
        chips = []
        for g in behind[:4]:
            q = GROUP_QUOTA[g][1]
            chips.append(f"{g} {servings.get(g, 0.0):.1f}/{q}")
        print('Отстаёт за неделю: ' + ', '.join(chips))
    print()

    if not ration:
        if behind and plan['kcal'] <= MIN_DISH_KCAL:
            print('Бюджет дня исчерпан — остаток групп переносится на след. дни.')
        else:
            print('Добор не нужен — недельные группы и белок в норме.')
        return

    when = ' Когда |' if load else ''
    when_sep = f"{'-'*8}|" if load else ''
    print(f"| {'Блюдо':<30} | {'К':>4} | {'Б':>3} | {'Ж':>3} | {'У':>3} |{when} Группы")
    print(f"|{'-'*32}|{'-'*6}|{'-'*5}|{'-'*5}|{'-'*5}|{when_sep}{'-'*7}")
    tk = tb = tz = tu = 0.0
    for d in ration:
        gr = ', '.join(f'{g}' for g in d['groups'])
        label = f"{d['name']} {d['grams']:.0f}г"
        wcol = f" {'трен' if is_carb_forward(d) else 'день':<6} |" if load else ''
        print(f"| {label:<30} | {d['k']:>4.0f} | {d['b']:>3.0f} | "
              f"{d['zh']:>3.0f} | {d['u']:>3.0f} |{wcol} {gr}")
        tk += d['k']; tb += d['b']; tz += d['zh']; tu += d['u']
    pad = f"{'':>8}|" if load else ''
    print(f"| {'ИТОГО':<30} | {tk:>4.0f} | {tb:>3.0f} | {tz:>3.0f} | {tu:>3.0f} |{pad}")

    day_prot = plan['prot_eaten'] + tb
    p_sym = '✓' if day_prot >= floor - PROT_TOL else '⚠'
    print(f"\nБелок за день с добором: {p_sym} {day_prot:.0f}/{floor:.0f}г")
    over = tk - plan['kcal']
    if over > 5:
        print(f"Добор {tk:.0f}к превышает потолок на {over:.0f}к — "
              f"белок-флор дороже точного дефицита (норма).")
    else:
        print(f"Остаток потолка после добора: {plan['kcal'] - tk:.0f}к.")


def render_ration_md(ref, ration):
    """ration.md checklist format (AGENTS.md §Рекомендуемый рацион). Base plan
    only — no yesterday's leftovers (the agent prepends those interactively)."""
    out = [f'# Рекомендуемый рацион {ref.isoformat()}', '',
           '| · | Блюдо | К | Б | Ж | У | Съедено |',
           '| --- | --- | --- | --- | --- | --- | --- |']
    for d in ration:
        label = f"{d['name']} {d['grams']:.0f}г"
        out.append(f"| 🔲 | {label} | {d['k']:.0f} | {d['b']:.0f} | "
                   f"{d['zh']:.0f} | {d['u']:.0f} | |")
    return '\n'.join(out) + '\n'


def ration_is_current(ref):
    """True if ration.md exists and its H1 date equals `ref` (don't clobber a
    live file with its checkmarks / leftovers)."""
    if not RATION.exists():
        return False
    first = RATION.read_text(encoding='utf-8').split('\n', 1)[0]
    m = re.search(r'(\d{4}-\d{2}-\d{2})', first)
    return bool(m and m.group(1) == ref.isoformat())


def date_seed(arg):
    p = Path(arg)
    try:
        p = p.resolve()
    except OSError:
        pass
    digits = re.sub(r'\D', '', f'{p.parent.parent.name}{p.parent.name}{p.stem}')
    return int(digits) if digits else 0


def diary_date(arg):
    p = Path(arg)
    try:
        p = p.resolve()
    except OSError:
        pass
    try:
        return date(int(p.parent.parent.name), int(p.parent.name), int(p.stem))
    except (ValueError, TypeError):
        return date.today()


if __name__ == '__main__':
    pos = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = {a for a in sys.argv[1:] if a.startswith('--')}
    if len(pos) != 1:
        print(f'Usage: {sys.argv[0]} <diary.md> [--write]')
        sys.exit(1)
    diary_arg = pos[0]
    text = Path(diary_arg).read_text(encoding='utf-8')
    ref = diary_date(diary_arg)
    week_start, _ = week_range(ref)
    plan = parse_plan(text)
    dishes = load_dishes()
    servings, _ = group_servings(week_start, ref)
    servings = servings or {}
    eaten = eaten_this_week(week_start, ref)
    seed = date_seed(diary_arg)
    cphase = cycle_phase(week_start)
    ration, floor = build(plan, dishes, dict(servings), eaten, seed, cphase)
    # --write: ensure ration.md exists for this day (base plan, no leftovers).
    # Never clobber a current-day file — that would wipe checkmarks/leftovers.
    if '--write' in flags:
        if ration_is_current(ref):
            print(f'ration.md уже на {ref} — не трогаю.')
        elif not ration:
            print('Добор не нужен — ration.md не создаю.')
        else:
            RATION.write_text(render_ration_md(ref, ration), encoding='utf-8')
            print(f'ration.md создан на {ref} ({len(ration)} блюд).')
    else:
        render(plan, ration, floor, dict(servings), cphase)
