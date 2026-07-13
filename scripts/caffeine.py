#!/usr/bin/env python3
"""Average caffeine intake from diary coffee rows.

Caffeine is derived from the coffee dose (grams of beans/grounds) in the
row name — "Кофе эспрессо двойной 15г" → 15 g × MG_PER_G. Water volume is
irrelevant; extraction differences between brew methods are noise next to
the 10 mg/g estimate (arabica, brewed).

Usage:
  python3 scripts/caffeine.py                 # last 30 days
  python3 scripts/caffeine.py 7               # last N days
  python3 scripts/caffeine.py 2026-06-01..2026-06-30
"""

import re
import sys
from datetime import date, timedelta

from paths import diary_path

MG_PER_G = 10.0

ROW_RE = re.compile(
    r'^\|\s*\d{2}:\d{2}\s*\|\s*([^|]+?)\s*\|\s*(-?[\d.]+)', re.MULTILINE
)
GRAMS_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*г\b')
COFFEE_RE = re.compile(r'\bкофе\b|эспрессо', re.IGNORECASE)
DECAF_RE = re.compile(r'без\s*кофеина|декаф', re.IGNORECASE)


def parse_range(args):
    today = date.today()
    if not args:
        return today - timedelta(days=29), today
    if '..' in args[0]:
        a, b = args[0].split('..', 1)
        return date.fromisoformat(a), date.fromisoformat(b)
    n = int(args[0])
    return today - timedelta(days=n - 1), today


def main():
    start, end = parse_range(sys.argv[1:])
    per_day = {}          # date -> mg
    grams_total = 0.0
    days_with_food = 0
    no_grams = []

    d = start
    while d <= end:
        path = diary_path(d)
        if path.exists():
            rows = ROW_RE.findall(path.read_text())
            if any(float(kcal) > 0 for _, kcal in rows):
                days_with_food += 1
            for name, kcal in rows:
                if float(kcal) < 0 or not COFFEE_RE.search(name):
                    continue
                if DECAF_RE.search(name):
                    continue
                g = GRAMS_RE.search(name)
                if not g:
                    no_grams.append(f'{d} | {name}')
                    continue
                grams = float(g.group(1).replace(',', '.'))
                per_day[d] = per_day.get(d, 0.0) + grams * MG_PER_G
                grams_total += grams
        d += timedelta(days=1)

    print(f'Кофеин {start}..{end}')
    if not per_day:
        print('- записей кофе не найдено')
    else:
        total = sum(per_day.values())
        peak_d, peak = max(per_day.items(), key=lambda kv: kv[1])
        print(f'- всего: {round(total)} мг ({round(grams_total)}г зерна, '
              f'{MG_PER_G:g} мг/г)')
        if days_with_food:
            print(f'- среднее: {round(total / days_with_food)} мг/день '
                  f'(дней с записями: {days_with_food})')
        print(f'- в день с кофе: {round(total / len(per_day))} мг '
              f'(дней с кофе: {len(per_day)})')
        print(f'- максимум: {round(peak)} мг ({peak_d})')
    if no_grams:
        print('- без граммовки (не посчитано):')
        for line in no_grams:
            print(f'    {line}')


if __name__ == '__main__':
    main()
