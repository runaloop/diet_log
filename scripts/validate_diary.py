#!/usr/bin/env python3
"""Validate diary: table column sums vs Потреблено line and Plan header."""
import re
import sys
from pathlib import Path

EPSILON_CONSUMED = 0.15   # Потреблено has 1 decimal — tight tolerance
EPSILON_PLAN = 1.0        # Plan values are often rounded to int


def parse_float(s):
    try:
        return float(s.strip().replace(',', '.'))
    except (ValueError, AttributeError):
        return None


def parse_table(lines):
    """Return (food_totals, training_kcal) from the food/activity table."""
    in_table = False
    col = {}  # name -> index

    food = {'К': 0.0, 'Б': 0.0, 'Ж': 0.0, 'У': 0.0, 'Клетчатка': 0.0}
    training_kcal = 0.0

    for line in lines:
        s = line.strip()
        if not (s.startswith('|') and s.endswith('|') and len(s) > 1):
            if in_table:
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
        if k is None:
            continue

        if k < 0:
            training_kcal += abs(k)
        else:
            food['К'] += k
            for name, default_i in (('Б', 3), ('Ж', 4), ('У', 5), ('Клетчатка', 6)):
                i = col.get(name, default_i)
                v = parse_float(cells[i]) if len(cells) > i else None
                if v is not None:
                    food[name] += v

    return food, training_kcal


def parse_consumed_line(lines):
    """Parse **Потреблено:** line → dict of values."""
    for line in lines:
        if '**Потреблено:**' in line:
            def grab(pat):
                m = re.search(pat, line)
                return float(m.group(1)) if m else None
            return {
                'К':         grab(r'К([\d.]+)'),
                'Б':         grab(r'Б([\d.]+)'),
                'Ж':         grab(r'Ж([\d.]+)'),
                'У':         grab(r'У([\d.]+)'),
                'Клетчатка': grab(r'Клет([\d.]+)'),
            }
    return {}


def parse_plan_line(lines):
    """Parse Plan header → {съедено, потрачено}."""
    for line in lines:
        if 'Базовый расход' in line and 'Съедено' in line:
            def grab(pat):
                m = re.search(pat, line)
                return float(m.group(1)) if m else None
            return {
                'съедено':   grab(r'Съедено:\s*([\d.]+)'),
                'потрачено': grab(r'Потрачено:\s*([\d.]+)'),
            }
    return {}


def validate(path):
    lines = Path(path).read_text(encoding='utf-8').split('\n')
    errors = []

    food, training_kcal = parse_table(lines)
    consumed = parse_consumed_line(lines)
    plan = parse_plan_line(lines)

    def check(label, expected, actual, eps):
        if expected is None or actual is None:
            return
        if abs(expected - actual) > eps:
            errors.append(f'{label}: ожидалось {expected}, в таблице {actual:.1f} (δ={actual - expected:+.1f})')

    # Потреблено vs table sums (food rows only)
    for col in ('К', 'Б', 'Ж', 'У', 'Клетчатка'):
        label = f'Потреблено.{col}' if col != 'Клетчатка' else 'Потреблено.Клет'
        check(label, consumed.get(col), food[col], EPSILON_CONSUMED)

    # Plan header vs table
    check('План.Съедено',   plan.get('съедено'),   food['К'],      EPSILON_PLAN)
    check('План.Потрачено', plan.get('потрачено'), training_kcal,  EPSILON_PLAN)

    return errors


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} <file.md>')
        sys.exit(1)

    errs = validate(sys.argv[1])
    if errs:
        print(f'⚠  Расхождения в {sys.argv[1]}:')
        for e in errs:
            print(f'   ✗ {e}')
        sys.exit(1)
    else:
        print(f'✓ {sys.argv[1]} — данные согласованы')
        sys.exit(0)
