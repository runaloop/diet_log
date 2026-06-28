#!/usr/bin/env python3
"""Build eating profile from diary history.

Scans all YYYY/MM/DD.md diaries, extracts food rows from the food/activity
table, and aggregates per dish: frequency, median portion, per-gram macros.
Used by the ration planner.

Log timestamps reflect when a row was *logged*, not when food was eaten
(user batch-logs), so meal-slot inference is intentionally omitted.

Usage:
  profile.py            # rebuild profile.json and print top staples
  profile.py --json     # rebuild and dump raw json to stdout
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

from paths import DIARIES, PROFILE_PATH

STAPLE_MIN_COUNT = 5  # appears on >=5 days → staple, else rare

PORTION_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(г|гр|g|мл|шт)\b', re.IGNORECASE)


def parse_float(s):
    try:
        return float(s.strip().replace(',', '.'))
    except (ValueError, AttributeError):
        return None


def split_name_portion(label):
    """'Овсянка сырая 50г' -> ('Овсянка сырая', 50.0). None grams if absent.

    A number+unit counts as the *portion* only when it sits at the end of the
    label (after trailing punctuation). Mid-label figures like '30г белка'
    describe content, not serving size, so they are left in the name — this
    stops one dish from splitting into phantom variants.
    """
    end = len(label.rstrip(' ,.·-'))
    portion = None
    for m in PORTION_RE.finditer(label):
        if m.end() == end:
            portion = m
            break
    if not portion:
        return label.strip(), None
    grams = parse_float(portion.group(1))
    name = (label[:portion.start()] + label[portion.end():]).strip(' ,·-')
    name = re.sub(r'\s+', ' ', name)
    return name or label.strip(), grams


def parse_food_rows(lines):
    """Yield (name, grams, k, b, zh, u) for each food row (К>0)."""
    in_table = False
    col = {}
    for line in lines:
        s = line.strip()
        if not (s.startswith('|') and s.endswith('|') and len(s) > 1):
            in_table = False
            continue
        cells = [c.strip() for c in s[1:-1].split('|')]
        if 'Продукт/Активность' in cells or 'Продукт' in cells:
            in_table = True
            col = {c: i for i, c in enumerate(cells)}
            continue
        if in_table and all(re.fullmatch(r':?-+:?', c) for c in cells if c):
            continue
        if not in_table:
            continue

        k_i = col.get('К', 2)
        if len(cells) <= k_i:
            continue
        k = parse_float(cells[k_i])
        if k is None or k <= 0:  # skip training (k<0) and bad rows
            continue

        name_i = col.get('Продукт/Активность', col.get('Продукт', 1))
        name, grams = split_name_portion(cells[name_i]) if len(cells) > name_i else (None, None)
        if not name:
            continue

        def g(key, default):
            i = col.get(key, default)
            return parse_float(cells[i]) if len(cells) > i else None

        yield name, grams, k, g('Б', 3), g('Ж', 4), g('У', 5)


def build_profile():
    dishes = defaultdict(lambda: {
        'name': None, 'days': set(), 'grams': [],
        'k': [], 'b': [], 'zh': [], 'u': [],
    })
    for path in sorted(DIARIES.glob('20*/[0-1][0-9]/[0-3][0-9].md')):
        day = path.stem
        lines = path.read_text(encoding='utf-8').split('\n')
        for name, grams, k, b, zh, u in parse_food_rows(lines):
            key = name.lower()
            d = dishes[key]
            d['name'] = d['name'] or name
            d['days'].add(f'{path.parent.parent.name}-{path.parent.name}-{day}')
            if grams:
                d['grams'].append(grams)
            for col, val in (('k', k), ('b', b), ('zh', zh), ('u', u)):
                if val is not None:
                    d[col].append(val)

    out = []
    for key, d in dishes.items():
        count = len(d['days'])
        med_grams = median(d['grams']) if d['grams'] else None
        med_k = median(d['k']) if d['k'] else None
        # per-gram macros from the median portion (linear scaling for planner)
        per_g = None
        if med_grams and med_k is not None:
            per_g = {
                'k': median(d['k']) / med_grams,
                'b': (median(d['b']) / med_grams) if d['b'] else 0,
                'zh': (median(d['zh']) / med_grams) if d['zh'] else 0,
                'u': (median(d['u']) / med_grams) if d['u'] else 0,
            }
        out.append({
            'name': d['name'],
            'count': count,
            'staple': count >= STAPLE_MIN_COUNT,
            'median_grams': med_grams,
            'median_kcal': med_k,
            'per_gram': per_g,
        })
    out.sort(key=lambda x: x['count'], reverse=True)
    return out


def print_top(profile):
    staples = [p for p in profile if p['staple']]
    print('# Профиль питания — staple-продукты (по частоте)\n')
    print(f"  {'#':>3}  {'Блюдо':<34} {'порция':>7} {'ккал':>6}  Б/Ж/У на порцию")
    for p in staples:
        g = f"{p['median_grams']:.0f}г" if p['median_grams'] else '—'
        k = f"{p['median_kcal']:.0f}к" if p['median_kcal'] else '—'
        pg, mg = p['per_gram'], p['median_grams']
        if pg and mg:
            macros = f"Б{pg['b']*mg:.0f} Ж{pg['zh']*mg:.0f} У{pg['u']*mg:.0f}"
        else:
            macros = '—'
        print(f"  {p['count']:>3}× {p['name']:<34} {g:>7} {k:>6}  {macros}")
    rare = len(profile) - len(staples)
    print(f'\nВсего блюд: {len(profile)} | staple: {len(staples)} | редких: {rare}')


if __name__ == '__main__':
    profile = build_profile()
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
    if '--json' in sys.argv:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
    else:
        print_top(profile)
        print(f'\n→ {PROFILE_PATH.name} обновлён ({len(profile)} блюд)')
