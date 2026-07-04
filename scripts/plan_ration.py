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
  2. day periodization      → today's kcal ceiling reflects the day's
                              training load (§7a): deficit on easy days,
                              fueling on training days
  3. daily targets          → kcal ceiling (soft) + protein floor (hard)
                              + the day's fat range (rest 1.0–1.2 / mid
                              0.9–1.0 / high 0.8–0.9 g/kg by training load;
                              cap hard for the plan, floor topped up with
                              good fat) as bounds
  4. variety                → one dish = one portion; deprioritise recent dishes

Group debts and anti-repeat roll over a 7-day window ending today (STRATEGY.md
§2) — Monday doesn't amnesty limit overruns or burn floor debt. The ISO week
stays only for the phase cycle (deficit/load envelope).

Data: group remainder + tags come from diet.db via summary.py; concrete dishes
+ real portions come from profile.json (the eating warehouse). The catalog is
the source of group membership and prep effort.

Usage:
  plan_ration.py YYYY/MM/DD.md
  plan_ration.py today.md
"""
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from summary import (GROUP_QUOTA, GROUP_ORDER, GRAM_GROUPS, DAY_FAT_RANGE,
                     DAY_LOAD_LABEL, global_mode, protein_floor,
                     fat_range, day_load, group_servings)
from profile import parse_food_rows, load_canon
from paths import DB_PATH, PROFILE_PATH, RATION, diary_path

# Per-group daily dish cap: one group must not monopolise the plate.
PER_GROUP_DAY_CAP = 2
# Sanity guard against degenerate loops only — NOT a plate-size design cap.
# The kcal/fat budgets are the real limiter of how long the list gets.
MAX_DISHES = 12
# A serving must be a real portion, not a condiment/garnish. Dishes whose median
# portion is below this contribute a phantom group serving — drop them.
MIN_DISH_KCAL = 30
# A dish eaten once ever isn't a habit and its "median" portion is a single
# sample — keep it out of the auto-plan (pins still reach it via the catalog).
MIN_DISH_COUNT = 2
# One dish must not swallow the day: median portion above this share of the
# base expenditure is never auto-suggested (the 500g-scramble guard).
MAX_DISH_KCAL_SHARE = 0.35
PROT_TOL = 6          # protein floor considered met within this many grams
# Protein sources with more fat than this share of kcal count as "fatty" and
# are topped up only after lean ones (drinks, skyr) are exhausted.
LEAN_FAT_SHARE = 0.35
# A top-up position must carry real protein — no 6g-protein lentil crumbs
# occupying a plate slot for the floor's sake.
MIN_TOPUP_PROT = 10
# Same idea for the fat-floor layer: a top-up must carry real fat grams.
MIN_TOPUP_FAT = 4
FAT_TOL = 5           # fat floor considered met within this many grams
FATQ_RANK = {'good': 0, 'neutral': 1, 'bad': 2}
PREP_RANK = {'low': 0, 'med': 1, None: 1.5, 'high': 2}

# Training-day carb layer (STRATEGY.md §9): on mid/high days (and in
# maintenance mode) top up carb-forward dishes toward the full daily carb
# target. No dish-count cap — the kcal/fat budgets are the limiter.
CARB_TOL = 12

SUPP_GROUP = 'добавки'
SUPP_CAP = 1          # at most one supplement/shake in a ration
PROC_GROUP = 'обработка'  # processed food: last-resort sort penalty in every layer


def grab(text, pat):
    m = re.search(pat, text)
    return float(m.group(1).replace('−', '-')) if m else None


def parse_plan(text):
    """Today's soft budget from the `## План` block: kcal ceiling + protein eaten."""
    # H1 label: «Режим:» today; «Фаза цикла:» in pre-§7a diaries.
    m = re.search(r'(?:Режим|Фаза цикла):\s*(\S+)', text)
    phase = m.group(1) if m else 'похудение'
    # `Можно съесть ...: <ceiling> ккал`. Tolerates both the compact format
    # (`до −500:` / `до 0:`, AGENTS.md) and the legacy verbose one
    # (`(чтобы сохранить дефицит 500 ккал):`). Minus may be ASCII `-` or U+2212.
    kcal = grab(text, r'Можно съесть[^:]*:\s*([−-]?[\d.]+)')
    prot_eaten = grab(text, r'Белок:[^(]*\(съедено:\s*([\d.]+)') or 0.0
    fat_eaten = grab(text, r'Жиры:[^(]*\(съедено:\s*([\d.]+)') or 0.0
    carb_rem = grab(text, r'Углеводы:[^)]*осталось:\s*([\d.]+)') or 0.0
    spent = grab(text, r'Потрачено:\s*([\d.]+)') or 0.0
    base = grab(text, r'Базовый расход:\s*([\d.]+)') or 0.0
    return {'phase': phase, 'kcal': kcal if kcal is not None else 0.0,
            'prot_eaten': prot_eaten, 'fat_eaten': fat_eaten,
            'carb_rem': max(0.0, carb_rem), 'spent': spent, 'base': base}


def load_catalog():
    """name(lower) -> {'groups': [(g, w)], 'prep': str|None, 'priority': int,
    'fatq': 'good'|'neutral'|'bad'}."""
    con = sqlite3.connect(DB_PATH)
    pg = defaultdict(list)
    for pid, g, w in con.execute(
            """SELECT pg.product_id, mg.name, pg.weight FROM product_group pg
               JOIN food_group mg ON mg.id = pg.group_id"""):
        pg[pid].append((g, w))
    out, by_id = {}, {}
    for pid, name, prep, prio, fatq in con.execute(
            'SELECT id, name, prep_effort, priority, fat_quality FROM product'):
        rec = {'groups': pg.get(pid, []), 'prep': prep,
               'priority': prio, 'fatq': fatq}
        out[name.lower()] = rec
        by_id[pid] = rec
    for pid, text in con.execute('SELECT product_id, text FROM alias'):
        base = by_id.get(pid)
        out.setdefault(text.lower(),
                       {'groups': pg.get(pid, []), 'prep': None, 'priority': 0,
                        'fatq': base['fatq'] if base else 'neutral'})
    con.close()
    return out


def load_macros():
    """name/alias(lower) -> {'name', 'portion_g', 'k', 'b', 'zh', 'u'} straight
    from the catalog — unlike load_dishes(), covers every product regardless
    of eating history (needed to pin a dish that's never been a staple)."""
    con = sqlite3.connect(DB_PATH)
    out, by_id = {}, {}
    for pid, name, portion_raw, portion_g, k, b, zh, u in con.execute(
            'SELECT id, name, portion_raw, portion_g, k, b, zh, u FROM product'):
        rec = {'name': name, 'portion_raw': portion_raw, 'portion_g': portion_g,
               'k': k, 'b': b, 'zh': zh, 'u': u}
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
    cat = catalog.get(rec['name'].lower(), {'groups': [], 'prep': None,
                                            'priority': 0, 'fatq': 'neutral'})
    groups = dict(cat['groups'])
    return {'name': rec['name'], 'grams': grams, 'count': 0,
            'units': 1, 'portion_raw': rec.get('portion_raw'),
            'k': k, 'b': b, 'zh': zh, 'u': u, 'groups': groups,
            'prep': cat['prep'], 'priority': cat.get('priority', 0),
            'fatq': cat.get('fatq', 'neutral'),
            'supp': SUPP_GROUP in groups}


def load_dishes():
    """Profile dishes joined with catalog groups + prep. One atomic median portion."""
    catalog = load_catalog()
    dishes = []
    for p in json.loads(PROFILE_PATH.read_text(encoding='utf-8')):
        if not p['per_gram'] or not p['median_grams']:
            continue
        if p['count'] < MIN_DISH_COUNT:
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
            'fatq': cat.get('fatq', 'neutral'),
            'supp': SUPP_GROUP in groups,
        })
    return dishes


def eaten_recent(win_start, ref):
    """Lowercased dish names logged in the rolling window (for anti-repeat).
    Diary spellings are folded to catalog-canonical names so a non-canonical
    log row still counts as 'eaten' for its dish."""
    canon = load_canon()
    names = set()
    d = win_start
    while d <= ref:
        path = diary_path(d)
        if path.exists():
            for name, *_ in parse_food_rows(path.read_text().split('\n')):
                names.add(canon.get(name.lower(), name).lower())
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


def first_word(name):
    return name.split()[0].lower() if name.split() else ''


def pick_for_group(group, dishes, ration, used, room, kcal_left, fat_left,
                   cal_cap, eaten, seed):
    """Best unused dish carrying `group` that fits kcal + fat + limits."""
    cands = [d for d in dishes
             if d['name'] not in used
             and d['groups'].get(group, 0) > 0
             and d['k'] <= min(kcal_left, cal_cap)
             and d['zh'] <= fat_left
             and fits_limits(d, room)]
    if not cands:
        return None
    # Same-kind penalties: a third "Салат …" or a dish rehashing already
    # covered groups reads as a monotonous plate — prefer a different kind
    # when the group has one.
    chosen_words = {first_word(r['name']) for r in ration}
    def overlap(d):
        return sum(1 for r in ration if r['groups'].keys() & d['groups'].keys())
    # least processed → fresh (not eaten in the window) → higher priority →
    # different kind/groups → low-prep → more of this group → date rotation
    cands.sort(key=lambda d: (
        d['groups'].get(PROC_GROUP, 0),
        d['name'].lower() in eaten,
        -d['priority'],
        first_word(d['name']) in chosen_words,
        overlap(d),
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


def carb_fill(dishes, target, ration, servings, used, room, group_count,
              kcal_left, fat_left, cal_cap, max_dishes, eaten, seed):
    """Training-day carb layer: pull carb-forward dishes toward `target`
    (peri-workout fuel). The kcal/fat budgets are the limiter, not a dish
    count. Skipped on rest days in похудение mode (§9)."""
    cur = sum(d['u'] for d in ration)
    while cur < target - CARB_TOL and n_dishes(ration) < max_dishes:
        cands = [d for d in dishes
                 if d['name'] not in used and is_carb_forward(d)
                 and d['k'] <= min(kcal_left, cal_cap)
                 and d['zh'] <= fat_left
                 and fits_day_groups(d, group_count)
                 and fits_limits(d, room)]
        if not cands:
            break
        def overlap(d):  # how many chosen dishes already share a group (variety)
            return sum(1 for r in ration if r['groups'].keys() & d['groups'].keys())
        cands.sort(key=lambda d: (
            d['groups'].get(PROC_GROUP, 0),
            d['name'].lower() in eaten,
            -d['priority'],
            overlap(d),
            PREP_RANK.get(d['prep'], 1.5),
            -(d['u'] * 4 / d['k']),
            (d['count'] + seed) % 5,
        ))
        d = cands[0]
        commit(d, ration, servings, used, group_count)
        kcal_left -= d['k']
        fat_left -= d['zh']
        room = limit_headroom(servings)
        cur += d['u']


def is_fatty(d):
    """Fat carries more than LEAN_FAT_SHARE of the dish's kcal."""
    return d['k'] > 0 and d['zh'] * 9 / d['k'] > LEAN_FAT_SHARE


def n_dishes(ration):
    return len({d['name'] for d in ration})


def fits_day_groups(d, group_count):
    """Daily per-group dish cap applies to every layer — a third yogurt from
    the protein top-up is as monotonous as one from the floor loop."""
    return all(group_count[g] < PER_GROUP_DAY_CAP for g in d['groups'])


def protein_topup(dishes, rem_prot, ration, servings, used, room, group_count,
                  fat_left, cal_cap, max_dishes, eaten, seed):
    """Daily-hard protein floor (STRATEGY.md §8): lean sources first — a
    protein drink (экспонента/сывороточный, ~0 fat) is a first-class citizen
    here, so the floor closes without touching the fat cap. The kcal ceiling
    may be overshot (protein wins over deficit exactness), the fat cap not.
    One dish = one portion, no second helpings."""
    supp_used = sum(1 for d in ration if d['supp'])
    while rem_prot > PROT_TOL and n_dishes(ration) < max_dishes:
        # The protein drink is always available to the floor (user rule): the
        # weekly 'добавки' limit is soft here — SUPP_CAP alone gates it.
        cands = [d for d in dishes
                 if d['name'] not in used and d['b'] >= MIN_TOPUP_PROT
                 and d['zh'] <= fat_left and d['k'] <= cal_cap
                 and fits_day_groups(d, group_count)
                 and (fits_limits(d, room) or d['supp'])
                 and not (d['supp'] and supp_used >= SUPP_CAP)]
        if not cands:
            break
        def overlap(d):  # a dish rehashing an already-covered group (e.g. a
            # second, different yogurt) reads monotonous — penalize.
            return sum(1 for r in ration if r['groups'].keys() & d['groups'].keys())
        # least processed; lean before fatty (fat budget is for the floor
        # layer's whole food, not for closing protein); fresh first; higher
        # priority; different groups; least overshoot past what's still
        # needed — only then cheapest kcal per gram of protein.
        cands.sort(key=lambda d: (d['groups'].get(PROC_GROUP, 0),
                                  is_fatty(d),
                                  d['name'].lower() in eaten,
                                  -d['priority'], overlap(d),
                                  max(0.0, d['b'] - rem_prot),
                                  d['k'] / d['b'], (d['count'] + seed) % 5))
        d = cands[0]
        commit(d, ration, servings, used, group_count)
        supp_used += d['supp']
        rem_prot -= d['b']
        fat_left -= d['zh']
    return rem_prot


def fat_floor_topup(dishes, ration, plan, servings, used, room, group_count,
                    kcal_left, cal_cap, max_dishes, eaten, seed, load):
    """Fat floor — the bottom of the day's fat range (STRATEGY.md §7: rest
    1.0–1.2, mid 0.9–1.0, high 0.8–0.9 g/kg): don't leave the planned day
    below it. Good fat first (олива/орехи/рыба — the default fat, §7),
    bad-fat dishes never close a health floor. Softer than protein: stays
    inside the kcal ceiling and under the cap."""
    ffloor, fcap = fat_range(load)
    day_fat = plan['fat_eaten'] + sum(d['zh'] for d in ration)
    room = limit_headroom(servings)
    while day_fat < ffloor - FAT_TOL and n_dishes(ration) < max_dishes:
        cands = [d for d in dishes
                 if d['name'] not in used
                 and d.get('fatq', 'neutral') != 'bad'
                 and d['zh'] >= MIN_TOPUP_FAT
                 and d['zh'] <= fcap - day_fat
                 and d['k'] <= min(kcal_left, cal_cap)
                 and fits_day_groups(d, group_count)
                 and fits_limits(d, room)]
        if not cands:
            break
        def overlap(d):
            return sum(1 for r in ration if r['groups'].keys() & d['groups'].keys())
        # good before neutral (the weekly good-share nudge, §7), then the
        # usual stack; least overshoot past the remaining need — no 30g of
        # nuts to close a 6g gap — then cheapest kcal per gram of fat.
        cands.sort(key=lambda d: (
            FATQ_RANK.get(d.get('fatq'), 1),
            d['groups'].get(PROC_GROUP, 0),
            d['name'].lower() in eaten,
            -d['priority'], overlap(d),
            PREP_RANK.get(d['prep'], 1.5),
            max(0.0, d['zh'] - (ffloor - day_fat)),
            d['k'] / d['zh'],
            (d['count'] + seed) % 5,
        ))
        d = cands[0]
        commit(d, ration, servings, used, group_count)
        kcal_left -= d['k']
        day_fat += d['zh']
        room = limit_headroom(servings)


def build(plan, dishes, servings, eaten, seed, mode, load='low',
          pins=None, exclude=None):
    exclude_names = {e.lower() for e in (exclude or [])}
    dishes = [d for d in dishes if d['name'].lower() not in exclude_names]

    servings = defaultdict(float, servings)
    room = limit_headroom(servings)
    used = set()
    group_count = defaultdict(int)
    ration = []
    kcal_left = plan['kcal']
    # The day's fat cap (top of the load-cycled range, STRATEGY.md §7) is
    # hard for the generated plan; freed kcal flow to carbs, not to fattier
    # dishes. The bottom of the range is topped up last (fat_floor_topup).
    fcap = fat_range(load)[1]
    fat_left = max(0.0, fcap - plan['fat_eaten'])
    # One dish must not swallow the day (median-portion giants).
    cal_cap = MAX_DISH_KCAL_SHARE * plan['base'] if plan['base'] else float('inf')
    max_dishes = MAX_DISHES

    # Pins are guaranteed dishes (user-committed, e.g. "must eat this today")
    # — locked in first and exempt from the auto-plan caps, then the
    # debt/floor/carb fill optimizes the rest of the day around them.
    for d in (pins or []):
        commit(d, ration, servings, used, group_count)
        kcal_left -= d['k']
        fat_left = max(0.0, fat_left - d['zh'])
        room = limit_headroom(servings)

    # Floor layer: one dish = one median portion, a group topped up only by a
    # *different* dish (PER_GROUP_DAY_CAP) — no container-refill doubling.
    while n_dishes(ration) < max_dishes:
        targets = [g for g in behind_floors(servings)
                   if group_count[g] < PER_GROUP_DAY_CAP]
        if not targets:
            break
        progressed = False
        for g in targets:
            d = pick_for_group(g, dishes, ration, used, room, kcal_left,
                               fat_left, cal_cap, eaten, seed)
            if d is None:
                continue
            commit(d, ration, servings, used, group_count)
            kcal_left -= d['k']
            fat_left -= d['zh']
            room = limit_headroom(servings)
            progressed = True
            break
        if not progressed or kcal_left <= 0:
            break

    floor = protein_floor()
    rem_prot = max(0.0, floor - plan['prot_eaten']
                   - sum(d['b'] for d in ration))
    protein_topup(dishes, rem_prot, ration, servings, used, room, group_count,
                  fat_left, cal_cap, max_dishes, eaten, seed)

    # Training-day carb layer (§9): top up carbs to the full daily target on
    # mid/high days; in maintenance mode — every day.
    if mode == 'поддержание' or load in ('mid', 'high'):
        kcal_left = plan['kcal'] - sum(d['k'] for d in ration)
        fat_left = max(0.0, fcap - plan['fat_eaten']
                       - sum(d['zh'] for d in ration))
        carb_fill(dishes, plan['carb_rem'], ration, servings, used, room,
                  group_count, kcal_left, fat_left, cal_cap, max_dishes,
                  eaten, seed)

    # Fat floor layer (§7), lowest priority: top up toward the day-range
    # bottom with good fat inside whatever kcal ceiling is left.
    kcal_left = plan['kcal'] - sum(d['k'] for d in ration)
    fat_floor_topup(dishes, ration, plan, servings, used, room, group_count,
                    kcal_left, cal_cap, max_dishes, eaten, seed, load)
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
            m['units'] = m.get('units', 0) + d.get('units', 0)
    return merged


def dish_label(d):
    """Weight-based dishes read as '150г'; unit-based ones (portion_g is null,
    e.g. '1шт') read by their catalog portion scaled to the pinned count."""
    if d['grams']:
        return f"{d['name']} {d['grams']:.0f}г"
    n = d.get('units') or 1
    raw = (d.get('portion_raw') or 'порция').strip()
    m = re.match(r'(\d+(?:[.,]\d+)?)\s*(\D.*)', raw)
    if m:
        total = n * float(m.group(1).replace(',', '.'))
        return f"{d['name']} {total:g}{m.group(2)}"
    return f"{d['name']} {raw}" if n == 1 else f"{d['name']} {n}x {raw}"


def render(plan, ration, floor, servings, mode, load):
    ffloor, fcap = fat_range(load)
    print(f"Режим: {mode} | потолок ккал: {plan['kcal']:.0f} | "
          f"белок-флор: {floor:.0f}г (съедено {plan['prot_eaten']:.0f}) | "
          f"жир: {ffloor:.0f}–{fcap:.0f}г (день: {DAY_LOAD_LABEL[load]}, "
          f"съедено {plan['fat_eaten']:.0f})")

    behind = behind_floors(defaultdict(float, servings))
    if behind:
        chips = []
        for g in behind[:4]:
            q = GROUP_QUOTA[g][1]
            chips.append(f"{g} {servings.get(g, 0.0):.1f}/{q}")
        print('Отстаёт (окно 7 дней): ' + ', '.join(chips))
    print()

    if not ration:
        if behind:
            print('Бюджет дня (ккал/жир) исчерпан — остаток групп переносится '
                  'на след. дни.')
        else:
            print('Добор не нужен — недельные группы и белок в норме.')
        return

    print(f"| {'Блюдо':<30} | {'К':>4} | {'Б':>3} | {'Ж':>3} | {'У':>3} | Группы")
    print(f"|{'-'*32}|{'-'*6}|{'-'*5}|{'-'*5}|{'-'*5}|{'-'*7}")
    tk = tb = tz = tu = 0.0
    for d in ration:
        gr = ', '.join(f'{g}' for g in d['groups'])
        label = dish_label(d)
        print(f"| {label:<30} | {d['k']:>4.0f} | {d['b']:>3.0f} | "
              f"{d['zh']:>3.0f} | {d['u']:>3.0f} | {gr}")
        tk += d['k']; tb += d['b']; tz += d['zh']; tu += d['u']
    print(f"| {'ИТОГО':<30} | {tk:>4.0f} | {tb:>3.0f} | {tz:>3.0f} | {tu:>3.0f} |")

    day_prot = plan['prot_eaten'] + tb
    p_sym = '✓' if day_prot >= floor - PROT_TOL else '⚠'
    print(f"\nБелок за день с добором: {p_sym} {day_prot:.0f}/{floor:.0f}г")
    day_fat = plan['fat_eaten'] + tz
    if day_fat > fcap:
        f_sym, f_note = '⚠', ' — выше капа'
    elif day_fat < ffloor - FAT_TOL:
        f_sym, f_note = '⚠', ' — ниже безопасного минимума'
    else:
        f_sym, f_note = '✓', ''
    print(f"Жир за день с добором: {f_sym} {day_fat:.0f}г "
          f"(день: {DAY_LOAD_LABEL[load]}, диапазон {ffloor:.0f}–{fcap:.0f}){f_note}")
    over = tk - plan['kcal']
    if over > 5:
        print(f"Добор {tk:.0f}к превышает потолок на {over:.0f}к — "
              f"белок-флор дороже точного дефицита (норма).")
    else:
        print(f"Остаток потолка после добора: {plan['kcal'] - tk:.0f}к.")


def render_ration_md(ref, ration):
    """ration.md two-table checklist (AGENTS.md §Рекомендуемый рацион):
    «## Осталось» (proposed remainder) + «## Съедено» (checked-off rows), each
    with its own ИТОГО. A fresh plan is all-Осталось; sort_ration.py moves rows
    across as they get checked. Base plan only — no yesterday's leftovers (the
    agent pins those via --pin)."""
    header = ['| · | Блюдо | К | Б | Ж | У | Съедено |',
              '| --- | --- | --- | --- | --- | --- | --- |']

    def total(tk, tb, tz, tu):
        return (f"| — | **ИТОГО** | **{tk:.0f}** | **{tb:.0f}** | "
                f"**{tz:.0f}** | **{tu:.0f}** | |")

    out = [f'# Рекомендуемый рацион {ref.isoformat()}', '',
           '## Осталось', '', *header]
    tk = tb = tz = tu = 0.0
    for d in ration:
        label = dish_label(d)
        out.append(f"| 🔲 | {label} | {d['k']:.0f} | {d['b']:.0f} | "
                   f"{d['zh']:.0f} | {d['u']:.0f} | |")
        tk += d['k']; tb += d['b']; tz += d['zh']; tu += d['u']
    out += [total(tk, tb, tz, tu), '',
            '## Съедено', '', *header, total(0, 0, 0, 0)]
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
    load_arg = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--pin', '--exclude'):
            i += 1
            if i >= len(argv):
                sys.exit(f'{a}: требуется значение "Название[:граммы]"')
            (pin_specs if a == '--pin' else exclude_specs).append(argv[i])
        elif a == '--load':
            i += 1
            if i >= len(argv) or argv[i] not in DAY_FAT_RANGE:
                sys.exit('--load: требуется low|mid|high')
            load_arg = argv[i]
        elif a.startswith('--'):
            flags.add(a)
        else:
            pos.append(a)
        i += 1
    if len(pos) != 1:
        print(f'Usage: {sys.argv[0]} <diary.md> '
              '[--pin "Name[:grams]"]... [--exclude "Name"]... '
              '[--load low|mid|high] [--write] [--force]')
        sys.exit(1)
    diary_arg = pos[0]
    text = Path(diary_arg).read_text(encoding='utf-8')
    ref = diary_date(diary_arg)
    win_start = ref - timedelta(days=6)  # rolling frame: groups + anti-repeat
    plan = parse_plan(text)
    dishes = load_dishes()
    catalog = load_catalog()
    macros = load_macros()
    pins = [resolve_pin(spec, catalog, macros) for spec in pin_specs]
    exclude = [find_canonical(spec, macros)['name'] for spec in exclude_specs]
    servings, _ = group_servings(win_start, ref)
    servings = servings or {}
    eaten = eaten_recent(win_start, ref)
    seed = date_seed(diary_arg)
    mode = global_mode()
    # Day load: --load declares the planned training up front; otherwise
    # classified from the workouts already logged in the diary.
    load = load_arg or day_load(ref)
    ration, floor = build(plan, dishes, dict(servings), eaten, seed, mode,
                           load, pins=pins, exclude=exclude)
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
        render(plan, ration, floor, dict(servings), mode, load)
