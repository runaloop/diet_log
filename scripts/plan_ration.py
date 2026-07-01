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
MAX_DISH_REPEATS = 2  # a dish may be topped up once (finish the container),
                      # never scaled into an unrealistic pile
# A serving must be a real portion, not a condiment/garnish. Dishes whose median
# portion is below this contribute a phantom group serving — drop them.
MIN_DISH_KCAL = 30
# Group fill stops once it reaches this fraction of the weekly quota — the day is
# a free variable (STRATEGY.md §2), no single day must close a week's worth.
PROT_TOL = 6          # protein floor considered met within this many grams
PREP_RANK = {'low': 0, 'med': 1, None: 1.5, 'high': 2}

# Load-week carb layer (STRATEGY.md §9): on a maintenance/load week, top up
# carb-forward dishes toward the full daily carb target.
CARB_TOL = 12
CARB_TOPUP_CAP = 2

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


def load_macros():
    """name/alias(lower) -> {'name', 'portion_g', 'k', 'b', 'zh', 'u'} straight
    from the catalog — unlike load_dishes(), covers every product regardless
    of eating history (needed to pin a dish that's never been a staple)."""
    con = sqlite3.connect(DB_PATH)
    out, by_id = {}, {}
    for pid, name, portion_g, k, b, zh, u in con.execute(
            'SELECT id, name, portion_g, k, b, zh, u FROM product'):
        rec = {'name': name, 'portion_g': portion_g, 'k': k, 'b': b, 'zh': zh, 'u': u}
        out[name.lower()] = rec
        by_id[pid] = rec
    for pid, text in con.execute('SELECT product_id, text FROM alias'):
        rec = by_id.get(pid)
        if rec:
            out.setdefault(text.lower(), rec)
    con.close()
    return out


def find_canonical(name_q, macros):
    """Resolve a --pin/--exclude name query to its catalog record — exact
    name/alias match first, substring fallback; exits on miss/ambiguity."""
    key = name_q.strip().lower()
    rec = macros.get(key)
    if rec is not None:
        return rec
    cands = {v['name'] for k, v in macros.items() if key in k}
    if not cands:
        sys.exit(f'--pin/--exclude: продукт не найден в каталоге: {name_q}')
    if len(cands) > 1:
        sys.exit(f'--pin/--exclude: неоднозначно ({", ".join(sorted(cands))}): {name_q}')
    only = next(iter(cands))
    return next(v for v in macros.values() if v['name'] == only)


def resolve_pin(spec, catalog, macros):
    """spec = 'Name' or 'Name:grams'. Scale the catalog product to `grams`
    (or its default portion if omitted) and shape it like a planner dish."""
    if ':' in spec:
        name_q, grams_s = spec.rsplit(':', 1)
        grams = float(grams_s)
    else:
        name_q, grams = spec, None
    rec = find_canonical(name_q, macros)
    portion_g = rec['portion_g']
    if grams is None:
        grams = portion_g or 0.0
        k, b, zh, u = rec['k'], rec['b'], rec['zh'], rec['u']
    elif portion_g:
        f = grams / portion_g
        k, b, zh, u = rec['k'] * f, rec['b'] * f, rec['zh'] * f, rec['u'] * f
    else:  # non-scalable "порция" item — grams requested but nothing to scale by
        k, b, zh, u = rec['k'], rec['b'], rec['zh'], rec['u']
    cat = catalog.get(rec['name'].lower(), {'groups': [], 'prep': None, 'priority': 0})
    groups = dict(cat['groups'])
    return {'name': rec['name'], 'grams': grams, 'count': 0,
            'k': k, 'b': b, 'zh': zh, 'u': u, 'groups': groups,
            'prep': cat['prep'], 'priority': cat.get('priority', 0),
            'supp': SUPP_GROUP in groups}


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


def protein_topup(dishes, rem_prot, ration, servings, used, room, eaten, seed):
    """Daily-hard protein floor (STRATEGY.md §8): whole food first, supplements
    capped — kcal ceiling may be overshot, protein wins over exactness."""
    supp_used = sum(1 for d in ration if d['supp'])
    while rem_prot > PROT_TOL:
        repeat_count = defaultdict(int)
        for r in ration:
            repeat_count[r['name']] += 1
        # a dish may be topped up once (finish the container) — capped by
        # MAX_DISH_REPEATS so this can't spiral into an absurd gram pile.
        cands = [d for d in dishes
                 if repeat_count[d['name']] < MAX_DISH_REPEATS and d['b'] > 0
                 and fits_limits(d, room)
                 and not (d['supp'] and supp_used >= SUPP_CAP)]
        if not cands:
            break
        def overlap(d):  # a NEW dish sharing a group with something already
            # committed (e.g. a second, different canned-bean product) is
            # penalized; reopening that same dish again is not.
            return sum(1 for r in ration if r['name'] != d['name']
                       and r['groups'].keys() & d['groups'].keys())
        # fresh (not eaten this week) before repeats; whole food before
        # supplements; higher priority; don't open a new dish in an
        # already-active group when reopening the same one would do; least
        # overshoot past what's still needed (a 76g-protein dish for a 10g
        # gap blows way past the floor) — only then cheapest kcal per gram of
        # protein among equally tight fits; otherwise the single cheapest
        # protein source (jerky-style concentrates) wins every day regardless
        # of repetition.
        cands.sort(key=lambda d: (d['name'].lower() in eaten, d['supp'],
                                  -d['priority'], overlap(d),
                                  max(0.0, d['b'] - rem_prot),
                                  d['k'] / d['b'], (d['count'] + seed) % 5))
        d = cands[0]
        commit(d, ration, servings, used, defaultdict(int))
        supp_used += d['supp']
        rem_prot -= d['b']
    return rem_prot


def build(plan, dishes, servings, eaten, seed, cphase, pins=None, exclude=None):
    exclude_names = {e.lower() for e in (exclude or [])}
    dishes = [d for d in dishes if d['name'].lower() not in exclude_names]

    servings = defaultdict(float, servings)
    room = limit_headroom(servings)
    used = set()
    group_count = defaultdict(int)
    group_pick = {}  # group -> dish already opened for it today; finish that
                      # container (2nd serving of the same thing) before
                      # cracking open a different one for the same group.
    ration = []
    kcal_left = plan['kcal']

    # Pins are guaranteed dishes (user-committed, e.g. "must eat this today")
    # — locked in first, then the debt/floor/carb fill optimizes the rest of
    # the day around them, same as any other committed dish.
    for d in (pins or []):
        commit(d, ration, servings, used, group_count)
        for g in d['groups']:
            group_pick.setdefault(g, d)
        kcal_left -= d['k']
        room = limit_headroom(servings)

    while len(ration) < MAX_DISHES:
        targets = [g for g in behind_floors(servings)
                   if group_count[g] < PER_GROUP_DAY_CAP]
        if not targets:
            break
        progressed = False
        for g in targets:
            prior = group_pick.get(g)
            if prior is not None and prior['k'] <= kcal_left and fits_limits(prior, room):
                d = prior
            else:
                d = pick_for_group(g, dishes, used, room, kcal_left, eaten, seed)
            if d is None:
                continue
            commit(d, ration, servings, used, group_count)
            group_pick[g] = d
            kcal_left -= d['k']
            room = limit_headroom(servings)
            progressed = True
            break
        if not progressed or kcal_left <= 0:
            break

    floor = protein_floor(cphase)
    rem_prot = max(0.0, floor - plan['prot_eaten']
                   - sum(d['b'] for d in ration))
    protein_topup(dishes, rem_prot, ration, servings, used, room, eaten, seed)

    # Load-week carb layer (§9): top up carbs to the full daily target.
    if cphase == 'поддержание':
        kcal_left = plan['kcal'] - sum(d['k'] for d in ration)
        carb_fill(dishes, plan['carb_rem'], ration, servings, used,
                  room, kcal_left, eaten, seed)
    return merge_repeats(ration), floor


def merge_repeats(ration):
    """Collapse repeated picks of the same dish into one row with combined
    grams — 2x110g of the same can reads as 220g, not two identical lines."""
    merged, index = [], {}
    for d in ration:
        i = index.get(d['name'])
        if i is None:
            index[d['name']] = len(merged)
            merged.append(dict(d))
        else:
            m = merged[i]
            for f in ('grams', 'k', 'b', 'zh', 'u'):
                m[f] += d[f]
    return merged


def _phase_key(phase):
    return 'дефицит' if phase.startswith('дефицит') else 'поддержание'


def render(plan, ration, floor, servings, cphase):
    phase = plan['phase']
    print(f"Фаза: {phase} | потолок ккал: {plan['kcal']:.0f} | "
          f"белок-флор: {floor:.0f}г (съедено {plan['prot_eaten']:.0f})")

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

    print(f"| {'Блюдо':<30} | {'К':>4} | {'Б':>3} | {'Ж':>3} | {'У':>3} | Группы")
    print(f"|{'-'*32}|{'-'*6}|{'-'*5}|{'-'*5}|{'-'*5}|{'-'*7}")
    tk = tb = tz = tu = 0.0
    for d in ration:
        gr = ', '.join(f'{g}' for g in d['groups'])
        label = f"{d['name']} {d['grams']:.0f}г"
        print(f"| {label:<30} | {d['k']:>4.0f} | {d['b']:>3.0f} | "
              f"{d['zh']:>3.0f} | {d['u']:>3.0f} | {gr}")
        tk += d['k']; tb += d['b']; tz += d['zh']; tu += d['u']
    print(f"| {'ИТОГО':<30} | {tk:>4.0f} | {tb:>3.0f} | {tz:>3.0f} | {tu:>3.0f} |")

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
    tk = tb = tz = tu = 0.0
    for d in ration:
        label = f"{d['name']} {d['grams']:.0f}г"
        out.append(f"| 🔲 | {label} | {d['k']:.0f} | {d['b']:.0f} | "
                   f"{d['zh']:.0f} | {d['u']:.0f} | |")
        tk += d['k']; tb += d['b']; tz += d['zh']; tu += d['u']
    out.append(f"| — | **ИТОГО** | **{tk:.0f}** | **{tb:.0f}** | "
               f"**{tz:.0f}** | **{tu:.0f}** | |")
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
    argv = sys.argv[1:]
    pos, pin_specs, exclude_specs, flags = [], [], [], set()
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--pin', '--exclude'):
            i += 1
            if i >= len(argv):
                sys.exit(f'{a}: требуется значение "Название[:граммы]"')
            (pin_specs if a == '--pin' else exclude_specs).append(argv[i])
        elif a.startswith('--'):
            flags.add(a)
        else:
            pos.append(a)
        i += 1
    if len(pos) != 1:
        print(f'Usage: {sys.argv[0]} <diary.md> '
              '[--pin "Name[:grams]"]... [--exclude "Name"]... [--write] [--force]')
        sys.exit(1)
    diary_arg = pos[0]
    text = Path(diary_arg).read_text(encoding='utf-8')
    ref = diary_date(diary_arg)
    week_start, _ = week_range(ref)
    plan = parse_plan(text)
    dishes = load_dishes()
    catalog = load_catalog()
    macros = load_macros()
    pins = [resolve_pin(spec, catalog, macros) for spec in pin_specs]
    exclude = [find_canonical(spec, macros)['name'] for spec in exclude_specs]
    servings, _ = group_servings(week_start, ref)
    servings = servings or {}
    eaten = eaten_this_week(week_start, ref)
    seed = date_seed(diary_arg)
    cphase = cycle_phase(week_start)
    ration, floor = build(plan, dishes, dict(servings), eaten, seed, cphase,
                           pins=pins, exclude=exclude)
    # --write: ensure ration.md exists for this day (base plan, no leftovers).
    # Never clobber a current-day file — that would wipe checkmarks/leftovers,
    # unless --force (explicit "regenerate anyway", e.g. after --pin/--exclude).
    if '--write' in flags:
        if ration_is_current(ref) and '--force' not in flags:
            print(f'ration.md уже на {ref} — не трогаю (--force чтобы перезаписать).')
        elif not ration:
            print('Добор не нужен — ration.md не создаю.')
        else:
            RATION.write_text(render_ration_md(ref, ration), encoding='utf-8')
            print(f'ration.md создан на {ref} ({len(ration)} блюд).')
    else:
        render(plan, ration, floor, dict(servings), cphase)
