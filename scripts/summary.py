#!/usr/bin/env python3
"""Diet log summary: week / month / custom range / day.

Usage:
  summary.py day [YYYY-MM-DD]
  summary.py week [YYYY-MM-DD]
  summary.py month [YYYY-MM-DD]
  summary.py weektrend [YYYY-MM-DD]
  summary.py YYYY-MM-DD..YYYY-MM-DD
  summary.py since-weight
"""

import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from paths import DB_PATH, GOALS, CYCLE, USER, diary_path

# Weekly food-group quotas — servings/week. The numbers lean Mediterranean
# (STRATEGY.md §6) but are just a starting model, tunable per §15. kind:
# floor = добираем, limit = не превышаем. Groups without a quota omitted.
GROUP_QUOTA = {
    'овощи': ('floor', 9), 'фрукты': ('floor', 8), 'злаки': ('floor', 7),
    'бобовые': ('floor', 3), 'рыба': ('floor', 3), 'орехи': ('floor', 7),
    'молочка': ('floor', 7), 'яйца': ('floor', 2),
    'птица': ('limit', 3), 'красное_мясо': ('limit', 1),
    'сладкое': ('limit', 2), 'добавки': ('limit', 7),
}
GROUP_ORDER = ['рыба', 'бобовые', 'овощи', 'фрукты', 'злаки', 'орехи', 'молочка',
             'яйца', 'птица', 'красное_мясо', 'сладкое', 'добавки']

GOALS_RE = re.compile(r'Дефицит.*?~?(\d+)-(\d+).*?ккал', re.IGNORECASE)
GOAL_PROTEIN_RE = re.compile(r'Белок.*?min\s*(\d+)', re.IGNORECASE)
GOAL_FAT_RE = re.compile(r'Жиры.*?(\d+)-(\d+)', re.IGNORECASE)
БАЗОВЫЙ_РАСХОД_RE = re.compile(r'Базовый расход.*?~?(\d+)', re.IGNORECASE)


def load_goals():
    g = GOALS
    if not g.exists():
        return {}
    text = g.read_text()
    result = {}
    m = GOALS_RE.search(text)
    if m:
        result['deficit_min'] = int(m.group(1))
        result['deficit_max'] = int(m.group(2))
    m = GOAL_PROTEIN_RE.search(text)
    if m:
        result['protein'] = int(m.group(1))
    m = GOAL_FAT_RE.search(text)
    if m:
        result['fat'] = (int(m.group(1)) + int(m.group(2))) / 2
    m = БАЗОВЫЙ_РАСХОД_RE.search(text)
    if m:
        base = int(m.group(1))
        protein_kcal = result.get('protein', 0) * 4
        fat_kcal = result.get('fat', 0) * 9
        result['carbs'] = max(0, (base - protein_kcal - fat_kcal) / 4)
    return result


CYCLE_MODE_RE = re.compile(r'Глобальный режим:\s*(\S+)', re.IGNORECASE)
CYCLE_ANCHOR_RE = re.compile(r'Неделя поддержания.*?(\d{4}-\d{2}-\d{2})', re.IGNORECASE)


def load_cycle():
    c = CYCLE
    result = {'mode': 'похудение', 'anchor': None, 'cycle_len': 3, 'maintenance_idx': 0}
    if not c.exists():
        return result
    text = c.read_text()
    m = CYCLE_MODE_RE.search(text)
    if m:
        result['mode'] = m.group(1).lower()
    m = CYCLE_ANCHOR_RE.search(text)
    if m:
        try:
            result['anchor'] = date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return result


def cycle_phase(week_start: date, cycle=None):
    """Return 'поддержание' or 'дефицит' for the ISO week starting at week_start."""
    cycle = cycle or load_cycle()
    if cycle.get('mode') == 'поддержание' or cycle.get('anchor') is None:
        return 'поддержание'
    anchor = cycle['anchor'] - timedelta(days=cycle['anchor'].weekday())
    idx = ((week_start - anchor).days // 7) % cycle['cycle_len']
    return 'поддержание' if idx == cycle['maintenance_idx'] else 'дефицит'


# Daily protein floor, g per kg body weight, by week phase (STRATEGY.md §8).
PROTEIN_PER_KG = {'дефицит': 2.0, 'поддержание': 1.8}


def protein_floor(phase=None, goals=None):
    """Daily protein floor in grams: weight × phase factor (STRATEGY.md §8).

    Weight comes from the latest user.md entry; falls back to the fixed
    goals.md value when no weight is on record. phase=None (month/range, mixed
    phases) uses a blended factor.
    """
    lw = load_last_weight()
    if lw is None:
        return (goals or load_goals()).get('protein', 0)
    factor = PROTEIN_PER_KG.get(phase, 1.9)
    return round(lw[1] * factor)


def phase_deficit_target(phase, goals):
    if phase == 'поддержание':
        return 0
    dmin = goals.get('deficit_min', 0)
    dmax = goals.get('deficit_max', 0)
    return (dmin + dmax) / 2 if (dmin or dmax) else 0


def status(actual, target, invert=False):
    """Return ✓/⚠/✗ symbol. invert=True for deficit (higher is better)."""
    if target == 0:
        return '✓'
    pct = actual / target
    if invert:
        return '✓' if actual >= target else ('⚠' if actual > 0 else '✗')
    return '✓' if pct >= 1.0 else ('⚠' if pct >= 0.5 else '✗')


PLAN_RE = re.compile(
    r'Базовый расход:\s*([\d.]+).*?Съедено:\s*([\d.]+).*?Потрачено:\s*([\d.]+).*?Дефицит:\s*(-?[\d.]+)'
)
CARB_TARGET_RE = re.compile(r'Углеводы:\s*([\d.]+)г')
TOTAL_RE = re.compile(
    r'\*\*(?:Итого|Потреблено):\*\*\s*К(-?[\d.]+)\s*\|\s*Б(-?[\d.]+)\s*\|\s*Ж(-?[\d.]+)\s*\|\s*У(-?[\d.]+)\s*\|\s*Клет(-?[\d.]+)'
)
WEIGHT_RE = re.compile(r'^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|', re.MULTILINE)


def parse_day(d: date):
    path = diary_path(d)
    if not path.exists():
        return None
    text = path.read_text()

    total = TOTAL_RE.search(text)
    plan = PLAN_RE.search(text)

    if not total:
        return {'has_data': False}

    result = {
        'has_data': True,
        'к': float(total.group(1)),
        'б': float(total.group(2)),
        'ж': float(total.group(3)),
        'у': float(total.group(4)),
        'клет': float(total.group(5)),
    }
    if plan:
        result['база'] = float(plan.group(1))
        result['съедено'] = float(plan.group(2))
        result['потрачено'] = float(plan.group(3))
        result['дефицит'] = float(plan.group(4))
    else:
        result['съедено'] = result['к']
        result['потрачено'] = 0.0
        result['дефицит'] = 0.0
        result['база'] = 0.0
    ct = CARB_TARGET_RE.search(text)
    result['у_цель'] = float(ct.group(1)) if ct else 0.0
    return result


def load_weights(start: date, end: date):
    user_md = USER
    if not user_md.exists():
        return []
    entries = []
    for m in WEIGHT_RE.finditer(user_md.read_text()):
        try:
            d = date.fromisoformat(m.group(1))
            if start <= d <= end:
                entries.append((d, float(m.group(2))))
        except ValueError:
            pass
    return sorted(entries)


def summarize(start: date, end: date):
    days_total = (end - start).days + 1
    missing, no_data = [], []
    rows = []

    d = start
    while d <= end:
        data = parse_day(d)
        if data is None:
            missing.append(d.isoformat())
        elif not data['has_data']:
            no_data.append(d.isoformat())
        else:
            rows.append(data)
        d += timedelta(days=1)

    n = len(rows)
    coverage = n / days_total * 100
    status = 'полный' if coverage == 100 else ('частичный' if coverage >= 80 else 'недостаточно данных')

    def total(k):
        return sum(r.get(k, 0) for r in rows)

    def avg(k):
        return total(k) / n if n else 0

    weights = load_weights(start, end)

    return dict(
        start=start, end=end,
        days_total=days_total, n=n, coverage=coverage, status=status,
        missing=missing, no_data=no_data,
        съедено=total('съедено'), потрачено=total('потрачено'), дефицит=total('дефицит'),
        avg_съедено=avg('съедено'), avg_дефицит=avg('дефицит'),
        б=total('б'), ж=total('ж'), у=total('у'), клет=total('клет'),
        avg_б=avg('б'), avg_ж=avg('ж'), avg_у=avg('у'), avg_клет=avg('клет'),
        у_цель=total('у_цель'),
        weights=weights,
    )


def fmt(r, label, phase=None):
    goals = load_goals()
    deficit_min = goals.get('deficit_min', 0)
    deficit_max = goals.get('deficit_max', 0)
    target_protein = protein_floor(phase, goals)
    weight_loss_mode = deficit_min > 0 or deficit_max > 0
    if phase == 'поддержание':
        weight_loss_mode = False

    lines = [
        f'## {label} {r["start"]}..{r["end"]}',
        '',
    ]
    if phase:
        lines.append(f'- Неделя года: {iso_week_label(r["start"])}')
        lines.append(f'- Фаза цикла: {phase}')
    lines += [
        f'- Покрытие: {r["n"]}/{r["days_total"]} дней ({r["coverage"]:.1f}%) | Статус: {r["status"]}',
        f'- Дней без данных: {len(r["no_data"])}',
        f'- Съедено: {r["съедено"]:.0f} ккал',
        f'- Потрачено тренировками: {r["потрачено"]:.0f} ккал',
        f'- Суммарный дефицит: {r["дефицит"]:.0f} ккал',
    ]
    if r['дефицит'] != 0:
        projected = r['дефицит'] / 7700
        sign = '−' if projected > 0 else '+'
        lines.append(f'- Прогнозируемое снижение: {sign}{abs(projected):.2f} кг')
    if r['n']:
        lines += [
            f'- Среднее съедено на день с записью: {r["avg_съедено"]:.0f} ккал',
            f'- Средний дефицит на день с записью: {r["avg_дефицит"]:.0f} ккал',
            f'- Белок: {r["б"]:.0f}г всего | {r["avg_б"]:.0f}г/день с записью',
            f'- Жиры: {r["ж"]:.0f}г всего | {r["avg_ж"]:.0f}г/день с записью',
            f'- Углеводы: {r["у"]:.0f}г всего | {r["avg_у"]:.0f}г/день с записью',
            f'- Клетчатка: {r["клет"]:.0f}г всего | {r["avg_клет"]:.1f}г/день с записью',
        ]
        if r['status'] != 'недостаточно данных':
            lines.append('')
            lines.append('### Статус целей (среднее/день)')
            if weight_loss_mode:
                avg_d = r['avg_дефицит']
                if avg_d < 0:
                    d_sym, d_note = '✗', ' (профицит)'
                elif deficit_min <= avg_d <= deficit_max:
                    d_sym, d_note = '✓', ''
                elif avg_d < deficit_min:
                    d_sym, d_note = '⚠', f' (перебор {deficit_min - avg_d:.0f})'
                else:
                    d_sym, d_note = '⚠', f' (выше коридора на {avg_d - deficit_max:.0f})'
                lines.append(f'- Дефицит:  {d_sym} {avg_d:.0f} ккал/день (цель {deficit_min}–{deficit_max}){d_note}')
            if target_protein:
                p_sym = status(r['avg_б'], target_protein)
                p_note = f' (недобор {target_protein - r["avg_б"]:.0f}г)' if r['avg_б'] < target_protein else ''
                lines.append(f'- Белок:    {p_sym} {r["avg_б"]:.0f}г/день (цель {target_protein}г){p_note}')
            у_цель = r.get('у_цель', 0)
            if у_цель:
                delta = r['у'] - у_цель
                if delta > у_цель * 0.05:
                    c_sym, c_note = '⚠', f', перебор {delta:.0f}г'
                elif delta < 0:
                    c_sym, c_note = '⚠' if r['у'] >= у_цель * 0.5 else '✗', f', недобор {-delta:.0f}г'
                else:
                    c_sym, c_note = '✓', ''
                lines.append(f'- Углеводы: {c_sym} {r["у"]:.0f}г (цель {у_цель:.0f}г суммарно{c_note})')
            target_fat = goals.get('fat', 0)
            if target_fat and r['avg_ж'] > target_fat * 1.2:
                overrun = r['avg_ж'] - target_fat
                lines.append(f'- Жиры:     ⚠ {r["avg_ж"]:.0f}г/день (цель {target_fat:.0f}г, перебор {overrun:.0f}г)')

    lines.append(f'- Пропущенные дни: {", ".join(r["missing"]) or "нет"}')
    lines.append(f'- Дни без данных: {", ".join(r["no_data"]) or "нет"}')

    w = r['weights']
    if len(w) >= 2:
        delta = w[-1][1] - w[0][1]
        sign = '+' if delta > 0 else ''
        lines.append(f'- Вес: {w[0][1]} -> {w[-1][1]} кг ({sign}{delta:.1f} кг)')
    elif len(w) == 1:
        lines.append(f'- Вес: {w[0][1]} кг на {w[0][0]} (одна запись в периоде)')

    return '\n'.join(lines)


def load_last_weight():
    """Return (date, kg) of the last weight entry in user.md, or None."""
    user_md = USER
    if not user_md.exists():
        return None
    entries = []
    for m in WEIGHT_RE.finditer(user_md.read_text()):
        try:
            entries.append((date.fromisoformat(m.group(1)), float(m.group(2))))
        except ValueError:
            pass
    return max(entries, key=lambda x: x[0]) if entries else None


def week_range(d: date):
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=6)


def iso_week_label(d: date):
    """ISO week-of-year label, e.g. 2026-W24."""
    y, w, _ = d.isocalendar()
    return f'{y}-W{w:02d}'


def month_range(d: date):
    start = date(d.year, d.month, 1)
    next_m = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return start, next_m - timedelta(days=1)


def weektrend(ref: date, with_groups: bool = True):
    """Weekly cumulative dashboard at the start of day `ref`: deficit/protein
    trend (records of the ISO week BEFORE `ref`). With `with_groups` (chat
    default) also appends the food-group remainder; the diary block omits it
    (`--no-groups`) since group servings drift over the day and the live view
    is always `summary.py weektrend`.
    """
    week_start, week_end = week_range(ref)
    cycle = load_cycle()
    phase = cycle_phase(week_start, cycle)
    goals = load_goals()
    target_protein = protein_floor(phase, goals)
    daily_target = phase_deficit_target(phase, goals)

    lines = [
        f'# {ref.day:02d}-{ref.month:02d}-{iso_week_label(week_start)} '
        f'({week_start}..{week_end}) | Фаза цикла: {phase}',
        '',
    ]

    prior_end = ref - timedelta(days=1)
    if prior_end < week_start:
        lines.append('- Первый день недели, тренда пока нет')
    else:
        r = summarize(week_start, prior_end)
        if r['n'] == 0:
            lines.append('- Записей за неделю пока нет')
        else:
            avg_def = r['avg_дефицит']
            avg_prot = r['avg_б']
            projected = avg_def * 7
            weekly_target = daily_target * 7
            if phase == 'поддержание':
                lines.append(f'- Средний суточный дефицит: {avg_def:.0f} ккал (поддержание, цель ~0)')
            else:
                lines.append(f'- Средний суточный дефицит: {avg_def:.0f} ккал (цель фазы {daily_target:.0f})')
            if target_protein and avg_prot < target_protein:
                lines.append(f'- Белок: {avg_prot:.0f}г/день (цель {target_protein}) ⚠ недобор {target_protein - avg_prot:.0f}г/день')
            if phase == 'поддержание':
                sym = '✓' if abs(projected) <= 700 else '⚠'
                lines.append(f'- Прогноз недельного баланса при тренде: {projected:+.0f} ккал (поддержание) {sym}')
            else:
                sym = status(projected, weekly_target, invert=True)
                lines.append(f'- Прогноз недельного дефицита при тренде: {projected:.0f} ккал (цель {weekly_target:.0f}) {sym}')

    if with_groups:
        lines.append('')
        lines += group_remainder_lines(week_start, ref)
    return '\n'.join(lines)


def load_catalog_groups():
    """Map name/alias (lowercased) -> [(group, weight), ...] from diet.db."""
    if not DB_PATH.exists():
        return None
    con = sqlite3.connect(DB_PATH)
    pid_groups = defaultdict(list)
    for pid, g, w in con.execute(
            """SELECT pg.product_id, mg.name, pg.weight FROM product_group pg
               JOIN food_group mg ON mg.id = pg.group_id"""):
        pid_groups[pid].append((g, w))
    out = {}
    for pid, name in con.execute('SELECT id, name FROM product'):
        out[name.lower()] = pid_groups.get(pid, [])
    for pid, text in con.execute('SELECT product_id, text FROM alias'):
        out.setdefault(text.lower(), pid_groups.get(pid, []))
    con.close()
    return out


def group_servings(start: date, end: date):
    """Sum food-group servings over the diaries in [start, end].

    Each logged food row contributes its product's group weights as servings
    (portion size ignored — a yogurt is a serving). Returns (servings, unmatched).
    """
    from profile import parse_food_rows  # reuse diary food-row parser

    catalog = load_catalog_groups()
    if catalog is None:
        return None, []
    servings = defaultdict(float)
    unmatched = []
    d = start
    while d <= end:
        path = diary_path(d)
        if path.exists():
            for name, *_ in parse_food_rows(path.read_text().split('\n')):
                groups = catalog.get(name.lower())
                if groups is None:
                    unmatched.append(name)
                else:
                    for g, w in groups:
                        servings[g] += w
        d += timedelta(days=1)
    return servings, unmatched


def group_remainder_lines(week_start: date, ref: date):
    """Food-group week remainder as markdown lines (no header): floor groups to
    top up, limit groups' headroom. Folded into weektrend."""
    servings, unmatched = group_servings(week_start, ref)
    if servings is None:
        return ['- data/diet.db не найден — остаток групп недоступен']

    floors = [g for g in GROUP_ORDER if GROUP_QUOTA.get(g, ('', 0))[0] == 'floor']
    limits = [g for g in GROUP_ORDER if GROUP_QUOTA.get(g, ('', 0))[0] == 'limit']

    out = ['### Остаток групп — добрать']
    for g in floors:
        quota = GROUP_QUOTA[g][1]
        got = servings.get(g, 0.0)
        left = max(0.0, quota - got)
        sym = '✓' if got >= quota else ('⚠' if got >= quota * 0.5 else '✗')
        note = '' if left <= 0 else f' — добрать {left:.1f}'
        out.append(f'- {g+":":<13} {sym} {got:.1f}/{quota}{note}')

    out += ['', '### Остаток групп — потолки']
    for g in limits:
        limit = GROUP_QUOTA[g][1]
        got = servings.get(g, 0.0)
        if got > limit:
            out.append(f'- {g+":":<13} ✗ {got:.1f}/{limit} — перебор {got-limit:.1f}')
        else:
            out.append(f'- {g+":":<13} ✓ {got:.1f}/{limit} (запас {limit-got:.1f})')

    if unmatched:
        uniq = sorted(set(unmatched))
        sample = ', '.join(uniq[:6]) + ('…' if len(uniq) > 6 else '')
        out += ['', f'- Не привязаны к каталогу: {len(unmatched)} записей ({sample})']
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]
    flags = [a for a in args[1:] if a.startswith('--')]
    pos = [a for a in args[1:] if not a.startswith('--')]
    ref = date.fromisoformat(pos[0]) if pos else date.today()
    phase = None

    if cmd == 'day':
        ref = date.fromisoformat(args[1]) if len(args) > 1 else date.today()
        data = parse_day(ref)
        if data is None:
            print(f'Нет дневника за {ref}')
            sys.exit(1)
        if not data.get('has_data'):
            print(f'Дневник за {ref} есть, но записей нет')
            sys.exit(1)
        goals = load_goals()
        deficit_min = goals.get('deficit_min', 0)
        deficit_max = goals.get('deficit_max', 0)
        day_phase = cycle_phase(week_range(ref)[0])
        target_protein = protein_floor(day_phase, goals)
        deficit = data.get('дефицит', 0)
        projected = deficit / 7700
        lines = [
            f'## Итоги дня {ref}',
            '',
            f'- Съедено: {data["съедено"]:.0f} ккал',
            f'- Потрачено тренировками: {data["потрачено"]:.0f} ккал',
            f'- Дефицит: {deficit:.0f} ккал',
        ]
        if deficit != 0:
            sign = '−' if projected > 0 else '+'
            lines.append(f'- Прогнозируемое снижение: {sign}{abs(projected):.2f} кг')
        lines.append('')
        lines.append('### Макросы')
        if target_protein:
            p_sym = status(data['б'], target_protein)
            p_note = f' (недобор {target_protein - data["б"]:.0f}г)' if data['б'] < target_protein else ''
            lines.append(f'- Белок:     {p_sym} {data["б"]:.0f}/{target_protein}г{p_note}')
        у_цель = data.get('у_цель', 0)
        if у_цель:
            delta = data['у'] - у_цель
            if delta > у_цель * 0.05:
                c_sym, c_note = '⚠', f' (перебор {delta:.0f}г)'
            elif delta < 0:
                c_sym, c_note = ('⚠' if data['у'] >= у_цель * 0.5 else '✗'), f' (недобор {-delta:.0f}г)'
            else:
                c_sym, c_note = '✓', ''
            lines.append(f'- Углеводы:  {c_sym} {data["у"]:.0f}/{у_цель:.0f}г{c_note}')
        target_fat = goals.get('fat', 0)
        fat_sym = '⚠' if target_fat and data['ж'] > target_fat * 1.2 else '✓'
        fat_note = f' (перебор {data["ж"] - target_fat:.0f}г)' if target_fat and data['ж'] > target_fat * 1.2 else ''
        lines.append(f'- Жиры:      {fat_sym} {data["ж"]:.0f}/{target_fat:.0f}г{fat_note}')
        if deficit_min and deficit_max:
            if deficit < 0:
                d_sym, d_note = '✗', ' (профицит)'
            elif deficit_min <= deficit <= deficit_max:
                d_sym, d_note = '✓', ''
            elif deficit < deficit_min:
                d_sym, d_note = '⚠', f' (меньше цели на {deficit_min - deficit:.0f})'
            else:
                d_sym, d_note = '✓', f' (выше коридора на {deficit - deficit_max:.0f})'
            lines.append(f'- Дефицит:   {d_sym} {deficit:.0f} ккал (цель {deficit_min}–{deficit_max}){d_note}')
        print('\n'.join(lines))
        return
    elif cmd == 'weektrend':
        print(weektrend(ref, with_groups='--no-groups' not in flags))
        return
    elif cmd == 'week':
        start, end = week_range(ref)
        label = 'Итоги недели'
        phase = cycle_phase(start)
    elif cmd == 'month':
        start, end = month_range(ref)
        label = 'Итоги месяца'
    elif cmd == 'since-weight':
        last = load_last_weight()
        if not last:
            print('Нет записей веса в user.md')
            sys.exit(1)
        start, end = last[0], date.today()
        label = f'Итоги с последнего взвешивания ({last[1]} кг)'
        r = summarize(start, end)
        out = fmt(r, label)
        estimated = last[1] - r['дефицит'] / 7700
        out += f'\n- Расчётный вес сейчас: {estimated:.1f} кг'
        print(out)
        return
    elif '..' in cmd:
        a, b = cmd.split('..')
        start, end, label = date.fromisoformat(a), date.fromisoformat(b), 'Итоги периода'
    else:
        print(f'Unknown command: {cmd}')
        sys.exit(1)

    print(fmt(summarize(start, end), label, phase))


if __name__ == '__main__':
    main()
