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
    'бобовые': ('floor', 3), 'рыба': ('floor', 5), 'орехи': ('floor', 7),
    'молочка': ('floor', 7), 'яйца': ('floor', 2),
    'птица': ('limit', 4), 'красное_мясо': ('limit', 1),
    'обработка': ('limit', 2), 'добавки': ('limit', 20),
}

# Gram-anchored groups (STRATEGY.md §6a): 1 serving = 100 g of meat/fish as
# eaten. product_group.weight is the *meat fraction* of the dish (1.0 = pure
# cut, 0.1 = soup with a little chicken, >1 = dehydrated concentrate like
# jerky). servings = grams_logged * weight / 100. All other groups stay
# event-flag based (weight summed as-is).
GRAM_GROUPS = {'рыба', 'птица'}
GROUP_ORDER = ['рыба', 'бобовые', 'овощи', 'фрукты', 'злаки', 'орехи', 'молочка',
             'яйца', 'птица', 'красное_мясо', 'обработка', 'добавки']

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


def global_mode(cycle=None):
    """'похудение' | 'поддержание' from config/cycle.md.

    Week-level 2+1 cycling is retired (STRATEGY.md §7a) — deficit now
    periodizes per day by training load; a diet break is simply switching
    the mode to 'поддержание'.
    """
    return (cycle or load_cycle())['mode']


# Daily protein floor, g per kg body weight (STRATEGY.md §8). Flat 1.8 for
# the day-periodization trial — raise if holding muscle gets hard.
PROTEIN_PER_KG = 1.8


def protein_floor(goals=None):
    """Daily protein floor in grams: weight × 1.8 (STRATEGY.md §8).

    Weight comes from the latest user.md entry; falls back to the fixed
    goals.md value when no weight is on record.
    """
    lw = load_last_weight()
    if lw is None:
        return (goals or load_goals()).get('protein', 0)
    return round(lw[1] * PROTEIN_PER_KG)


# Daily fat safe range, g per kg body weight, cycled by the day's training
# load (STRATEGY.md §7): rest days run fat high (satiety on a deficit +
# hormones), hard days push it to the bottom of the band so the freed kcal
# go to peri-workout carbs. Cap is hard for the generated plan, floor is a
# soft top-up; period averages are judged against the full band.
DAY_FAT_RANGE = {           # g/kg (floor, cap)
    'low': (1.0, 1.2),      # rest day: walks / no real training
    'mid': (0.9, 1.0),      # ≥300 kcal in Z2
    'high': (0.8, 0.9),     # ≥300 kcal in Z3+ / strength, target the floor
}
FAT_RANGE_ALL = (0.8, 1.2)  # the full band, for period averages
DAY_LOAD_KCAL = 300         # training kcal that make a day mid/high
DAY_LOAD_LABEL = {'low': 'отдых', 'mid': 'средний', 'high': 'высокий'}

# Fat-quality shares of the rolling week's fat grams (STRATEGY.md §7):
# bad is a real status (✓/⚠/✗), good is a symbol-free nudge that hides
# once the target is reached.
FAT_BAD_SHARE_CAP = 0.15
FAT_GOOD_SHARE_TARGET = 0.40


def fat_range(load=None, goals=None):
    """(floor_g, cap_g) of the day's fat range (STRATEGY.md §7).

    load ∈ {'low','mid','high'} — the day's training load (see day_load);
    None → the full 0.8–1.2 band (period averages). Weight comes from the
    latest user.md entry; with no weight on record both bounds fall back to
    the goals.md range average.
    """
    per_kg = DAY_FAT_RANGE.get(load, FAT_RANGE_ALL)
    lw = load_last_weight()
    if lw is None:
        fallback = (goals or load_goals()).get('fat', 0)
        return fallback, fallback
    return round(lw[1] * per_kg[0]), round(lw[1] * per_kg[1])


ZONE_RE = re.compile(r'\bz\s*([1-5])', re.IGNORECASE)
HIGH_LOAD_RE = re.compile(r'силов|интервал', re.IGNORECASE)
# NEAT top-up and walks are not training for the fat cycle.
LOAD_IGNORE_RE = re.compile(r'прочая активность|прогулк|ходьб', re.IGNORECASE)


CARRYOVER_KCAL = 900  # yesterday's Z3+ kcal above this → today at least mid


def _day_signals(ref: date):
    """(raw_load, hi_kcal) for the diary of `ref`, no carryover applied."""
    from profile import parse_activity_rows

    path = diary_path(ref)
    if not path.exists():
        return 'low', 0.0
    hi = mid = 0.0
    for name, kcal in parse_activity_rows(path.read_text().split('\n')):
        if LOAD_IGNORE_RE.search(name):
            continue
        m = ZONE_RE.search(name)
        zone = int(m.group(1)) if m else None
        if HIGH_LOAD_RE.search(name) or (zone is not None and zone >= 3):
            hi += kcal
        elif zone is None or zone == 2:
            mid += kcal
    if hi >= DAY_LOAD_KCAL:
        return 'high', hi
    if hi + mid >= DAY_LOAD_KCAL:
        return 'mid', hi
    return 'low', hi


def day_load(ref: date):
    """Classify the day's training load (STRATEGY.md §7/§7a).

    'high' — ≥300 kcal in Z3+/strength/intervals; 'mid' — ≥300 kcal of
    training counting Z2 and unknown-zone workouts (the agent asks the zone
    anyway); 'low' — rest, walks, NEAT only. A rest day after >900 kcal of
    Z3+ work is bumped to 'mid' (recovery carryover) — for the whole day:
    fat range and deficit window alike.
    """
    load, _ = _day_signals(ref)
    if load == 'low':
        _, hi_prev = _day_signals(ref - timedelta(days=1))
        if hi_prev > CARRYOVER_KCAL:
            return 'mid'
    return load


# Daily target-deficit window by day load (STRATEGY.md §7a): the deficit
# lives on easy days, training days are fueled. 'low' reads config/goals.md.
DAY_DEFICIT_WINDOW = {'mid': (0, 150), 'high': (-150, 150)}
MAINT_DEFICIT_WINDOW = (-150, 150)


def day_deficit_window(load, goals=None, mode=None):
    """(min, max) kcal target deficit for a day of the given load.

    Maintenance mode → ~0 for every day (a diet break is just this mode).
    """
    if (mode or global_mode()) == 'поддержание':
        return MAINT_DEFICIT_WINDOW
    if load == 'low':
        g = goals or load_goals()
        return g.get('deficit_min', 300), g.get('deficit_max', 500)
    return DAY_DEFICIT_WINDOW[load]


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
    goals = load_goals()
    mode = global_mode()
    # Per-day deficit-target window summed over data days (STRATEGY.md §7a):
    # the period target is the mix of its days' windows, not one flat number.
    target_min = target_max = 0.0

    d = start
    while d <= end:
        data = parse_day(d)
        if data is None:
            missing.append(d.isoformat())
        elif not data['has_data']:
            no_data.append(d.isoformat())
        else:
            rows.append(data)
            lo, hi = day_deficit_window(day_load(d), goals, mode)
            target_min += lo
            target_max += hi
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
        цель_min=target_min, цель_max=target_max,
        weights=weights,
    )


def fmt(r, label, show_week=False):
    goals = load_goals()
    mode = global_mode()
    target_protein = protein_floor(goals)
    weight_loss_mode = mode == 'похудение'

    lines = [
        f'## {label} {r["start"]}..{r["end"]}',
        '',
    ]
    if show_week:
        lines.append(f'- Неделя года: {iso_week_label(r["start"])}')
        lines.append(f'- Режим: {mode}')
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
            # Deficit target = the mix of the period's day windows (§7a):
            # rest days carry the deficit, training days are fueled.
            lo, hi = r['цель_min'] / r['n'], r['цель_max'] / r['n']
            avg_d = r['avg_дефицит']
            if weight_loss_mode:
                if avg_d < 0:
                    d_sym, d_note = '✗', ' (профицит)'
                elif avg_d < lo:
                    d_sym, d_note = '⚠', f' (переедание: ниже окна на {lo - avg_d:.0f})'
                elif avg_d <= hi:
                    d_sym, d_note = '✓', ''
                else:
                    d_sym, d_note = '⚠', f' (недоедание в тренировочные дни: выше окна на {avg_d - hi:.0f})'
                lines.append(f'- Дефицит:  {d_sym} {avg_d:.0f} ккал/день (цель по миксу дней {lo:.0f}–{hi:.0f}){d_note}')
            else:
                b_sym = '✓' if lo <= avg_d <= hi else '⚠'
                lines.append(f'- Баланс:   {b_sym} {avg_d:.0f} ккал/день (поддержание, цель ~0)')
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
            # Period averages are judged against the full band, strictly —
            # the 1.2 tolerance is a single-day softness, not an average one,
            # and the day cycle (§7) averages out inside the band.
            ffloor_, fcap_ = fat_range(None, goals)
            if fcap_ and r['avg_ж'] > fcap_:
                overrun = r['avg_ж'] - fcap_
                lines.append(f'- Жиры:     ⚠ {r["avg_ж"]:.0f}г/день (диапазон {ffloor_:.0f}–{fcap_:.0f}г, перебор {overrun:.0f}г)')
            elif ffloor_ and r['avg_ж'] < ffloor_:
                lines.append(f'- Жиры:     ⚠ {r["avg_ж"]:.0f}г/день (диапазон {ffloor_:.0f}–{fcap_:.0f}г, ниже минимума)')

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
    mode = global_mode()
    goals = load_goals()
    target_protein = protein_floor(goals)

    lines = [
        f'# {ref.day:02d}-{ref.month:02d}-{iso_week_label(week_start)} '
        f'({week_start}..{week_end}) | Режим: {mode}',
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
            # Target = the mix of the week's day windows so far (§7a).
            lo, hi = r['цель_min'] / r['n'], r['цель_max'] / r['n']
            if mode == 'поддержание':
                bal_label = 'дефицит' if avg_def >= 0 else 'профицит'
                lines.append(f'- Средний суточный {bal_label}: {abs(avg_def):.0f} ккал (поддержание, цель ~0)')
            else:
                d_sym = '✓' if lo <= avg_def <= hi else ('✗' if avg_def < 0 else '⚠')
                lines.append(f'- Средний суточный дефицит: {avg_def:.0f} ккал (цель по миксу дней {lo:.0f}–{hi:.0f}) {d_sym}')
            if target_protein and avg_prot < target_protein:
                lines.append(f'- Белок: {avg_prot:.0f}г/день (цель {target_protein}) ⚠ недобор {target_protein - avg_prot:.0f}г/день')
            # Fat line only when the weekly average leaves the full band —
            # the day cycle (§7) swings inside it by design, the smoothed
            # average is judged strictly.
            ffloor, fcap = fat_range(None, goals)
            avg_fat = r['avg_ж']
            if fcap and avg_fat > fcap:
                lines.append(f'- Жиры: {avg_fat:.0f}г/день ⚠ выше диапазона {ffloor}–{fcap}г (перебор {avg_fat - fcap:.0f}г/день)')
            elif ffloor and avg_fat < ffloor:
                lines.append(f'- Жиры: {avg_fat:.0f}г/день ⚠ ниже диапазона {ffloor}–{fcap}г — добрать хорошим жиром')
            if mode == 'поддержание':
                sym = '✓' if abs(projected) <= 700 else '⚠'
                proj_label = 'дефицита' if projected >= 0 else 'профицита'
                lines.append(f'- Прогноз недельного {proj_label} при тренде: {abs(projected):.0f} ккал (поддержание) {sym}')
            else:
                wlo, whi = lo * 7, hi * 7
                sym = '✓' if wlo <= projected <= whi else ('✗' if projected < 0 else '⚠')
                lines.append(f'- Прогноз недельного дефицита при тренде: {projected:.0f} ккал (окно {wlo:.0f}–{whi:.0f}) {sym}')

    if with_groups:
        lines.append('')
        lines += group_remainder_lines(ref)
        lines += fat_quality_lines(ref)
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

    Each logged food row contributes its product's group weights as servings.
    Most groups are event-flag based (portion ignored — a yogurt is a serving);
    GRAM_GROUPS (рыба/птица) count grams*weight/100 instead (STRATEGY.md §6a).
    Returns (servings, unmatched).
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
            for name, grams, *_ in parse_food_rows(path.read_text().split('\n')):
                groups = catalog.get(name.lower())
                if groups is None:
                    unmatched.append(name)
                else:
                    for g, w in groups:
                        if g in GRAM_GROUPS and grams:
                            servings[g] += grams * w / 100.0
                        else:
                            servings[g] += w
        d += timedelta(days=1)
    return servings, unmatched


def group_remainder_lines(ref: date):
    """Food-group remainder as markdown lines (no header): floor groups to
    top up, limit groups' headroom. Folded into weektrend.

    Groups roll over a 7-day window ending at `ref` (not the ISO week): limit
    overruns aren't amnestied at Monday midnight and floor debt doesn't burn.
    The ISO frame stays only for the phase cycle / deficit trend."""
    win_start = ref - timedelta(days=6)
    servings, unmatched = group_servings(win_start, ref)
    if servings is None:
        return ['- data/diet.db не найден — остаток групп недоступен']

    floors = [g for g in GROUP_ORDER if GROUP_QUOTA.get(g, ('', 0))[0] == 'floor']
    limits = [g for g in GROUP_ORDER if GROUP_QUOTA.get(g, ('', 0))[0] == 'limit']

    out = [f'### Остаток групп (7 дней {win_start}..{ref}) — добрать']
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


def load_catalog_fat_quality():
    """Map name/alias (lowercased) -> fat_quality ('good'|'neutral'|'bad')."""
    if not DB_PATH.exists():
        return None
    con = sqlite3.connect(DB_PATH)
    out, by_id = {}, {}
    for pid, name, q in con.execute('SELECT id, name, fat_quality FROM product'):
        by_id[pid] = q
        out[name.lower()] = q
    for pid, text in con.execute('SELECT product_id, text FROM alias'):
        out.setdefault(text.lower(), by_id.get(pid, 'neutral'))
    con.close()
    return out


def fat_quality_grams(start: date, end: date):
    """Fat grams by catalog fat_quality over the diaries in [start, end].
    Rows not in the catalog count as neutral — unknown isn't bad."""
    from profile import parse_food_rows

    catalog = load_catalog_fat_quality()
    if catalog is None:
        return None
    grams = {'good': 0.0, 'neutral': 0.0, 'bad': 0.0}
    d = start
    while d <= end:
        path = diary_path(d)
        if path.exists():
            for name, _grams, _k, _b, zh, _u in parse_food_rows(
                    path.read_text().split('\n')):
                if zh:
                    grams[catalog.get(name.lower(), 'neutral')] += zh
        d += timedelta(days=1)
    return grams


def fat_quality_lines(ref: date):
    """Fat-quality block over the rolling 7-day window (STRATEGY.md §7).

    bad share carries a status symbol (it's harm); good share is a plain
    recommendation line that only appears while below target (it's an
    opportunity, not a problem). Both fine → one green line."""
    win_start = ref - timedelta(days=6)
    grams = fat_quality_grams(win_start, ref)
    if grams is None:
        return []
    total = sum(grams.values())
    if total <= 0:
        return []
    bad = grams['bad'] / total
    good = grams['good'] / total
    bad_ok = bad <= FAT_BAD_SHARE_CAP
    good_ok = good >= FAT_GOOD_SHARE_TARGET
    out = ['', f'### Качество жира (7 дней {win_start}..{ref})']
    if bad_ok and good_ok:
        out.append(f'- bad: ✓ {bad*100:.0f}% · good {good*100:.0f}%')
        return out
    if bad_ok:
        out.append(f'- bad: ✓ {bad*100:.0f}% (потолок {FAT_BAD_SHARE_CAP*100:.0f})')
    else:
        sym = '⚠' if bad <= FAT_BAD_SHARE_CAP * 2 else '✗'
        out.append(f'- bad: {sym} {bad*100:.0f}% (потолок {FAT_BAD_SHARE_CAP*100:.0f})'
                   ' — фритюр/выпечка/переработка')
    if not good_ok:
        out.append(f'- good {good*100:.0f}% — можно больше '
                   f'(ориентир {FAT_GOOD_SHARE_TARGET*100:.0f}+): рыба, орехи, оливковое')
    return out


def day_med_verdict(ref: date):
    """Compact Mediterranean verdict for today: score + what was missed."""
    servings_day, _ = group_servings(ref, ref)
    if servings_day is None:
        return ['', '### Средиземноморский вердикт — сегодня', '- diet.db не найден']

    floors = [g for g in GROUP_ORDER if GROUP_QUOTA.get(g, ('', 0))[0] == 'floor']
    limits = [g for g in GROUP_ORDER if GROUP_QUOTA.get(g, ('', 0))[0] == 'limit']

    hit = [g for g in floors if servings_day.get(g, 0) > 0]
    missed = [g for g in floors if servings_day.get(g, 0) == 0]
    score = round(len(hit) / len(floors) * 100) if floors else 0
    sym = '✓' if score >= 70 else ('⚠' if score >= 40 else '✗')

    lines = ['', '### Средиземноморский вердикт — сегодня']
    lines.append(f'Счёт: {sym} {score}% ({len(hit)}/{len(floors)} групп)')
    if missed and score < 80:
        lines.append(f'Упущено: {", ".join(missed)}')

    return lines


def rolling7_med_verdict(ref: date):
    """7-day rolling Med score: per-group day counts, chronic gaps, limit totals."""
    from profile import parse_food_rows

    start7 = ref - timedelta(days=6)
    catalog = load_catalog_groups()
    if catalog is None:
        return ['', f'### Средиземноморский вердикт — 7 дней', '- diet.db не найден']

    floors = [g for g in GROUP_ORDER if GROUP_QUOTA.get(g, ('', 0))[0] == 'floor']
    limits = [g for g in GROUP_ORDER if GROUP_QUOTA.get(g, ('', 0))[0] == 'limit']

    floor_totals = defaultdict(float)
    limit_totals = defaultdict(float)
    days_with_data = 0

    d = start7
    while d <= ref:
        path = diary_path(d)
        if path.exists():
            servings = defaultdict(float)
            for name, grams, *_ in parse_food_rows(path.read_text().split('\n')):
                for g, w in catalog.get(name.lower(), []):
                    if g in GRAM_GROUPS and grams:
                        servings[g] += grams * w / 100.0
                    else:
                        servings[g] += w
            if servings:
                days_with_data += 1
                for g in floors:
                    floor_totals[g] += servings.get(g, 0)
                for g in limits:
                    limit_totals[g] += servings.get(g, 0)
        d += timedelta(days=1)

    lines = ['', f'### Средиземноморский вердикт — 7 дней ({start7}..{ref})']
    if not days_with_data:
        lines.append('Нет данных')
        return lines

    floors_met = sum(1 for g in floors if floor_totals.get(g, 0) >= GROUP_QUOTA[g][1])
    score = round(floors_met / len(floors) * 100) if floors else 0
    sym = '✓' if score >= 70 else ('⚠' if score >= 40 else '✗')
    lines.append(f'Счёт: {sym} {score}% ({floors_met}/{len(floors)} групп)')

    gaps = sorted(
        [(g, floor_totals.get(g, 0), GROUP_QUOTA[g][1])
         for g in floors if floor_totals.get(g, 0) < GROUP_QUOTA[g][1]],
        key=lambda x: x[1] / x[2],
    )
    if gaps:
        parts = [f'{g} {v:.1f}/{q}' for g, v, q in gaps]
        lines.append(f'Не добрали: {", ".join(parts)}')

    overruns = [(g, limit_totals[g]) for g in limits if limit_totals.get(g, 0) > GROUP_QUOTA[g][1]]
    if overruns:
        o_parts = [f'{g} {v:.1f} (лимит {GROUP_QUOTA[g][1]})' for g, v in overruns]
        lines.append(f'Лимиты превышены: {", ".join(o_parts)}')

    return lines


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]
    flags = [a for a in args[1:] if a.startswith('--')]
    pos = [a for a in args[1:] if not a.startswith('--')]
    ref = date.fromisoformat(pos[0]) if pos else date.today()
    show_week = False

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
        mode = global_mode()
        target_protein = protein_floor(goals)
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
        load = day_load(ref)
        ffloor, fcap = fat_range(load, goals)
        if fcap and data['ж'] > fcap * 1.2:
            fat_sym, fat_note = '⚠', f' (перебор {data["ж"] - fcap:.0f}г)'
        elif ffloor and data['ж'] < ffloor:
            fat_sym, fat_note = '⚠', f' (ниже минимума на {ffloor - data["ж"]:.0f}г)'
        else:
            fat_sym, fat_note = '✓', ''
        lines.append(f'- Жиры:      {fat_sym} {data["ж"]:.0f}г (день: {DAY_LOAD_LABEL[load]}, '
                     f'диапазон {ffloor:.0f}–{fcap:.0f}){fat_note}')
        lo, hi = day_deficit_window(load, goals, mode)
        if mode == 'поддержание':
            bal_sym = '✓' if lo <= deficit <= hi else '⚠'
            lines.append(f'- Баланс:    {bal_sym} {deficit:.0f} ккал (поддержание, цель ~0)')
        else:
            if deficit < lo:
                d_sym = '✗' if deficit < 0 else '⚠'
                d_note = f' (переедание: ниже окна на {lo - deficit:.0f})'
            elif deficit <= hi:
                d_sym, d_note = '✓', ''
            elif load == 'low':
                d_sym, d_note = '✓', f' (жёстче цели на {deficit - hi:.0f})'
            else:
                d_sym, d_note = '⚠', f' (недоедание: выше окна на {deficit - hi:.0f} — добрать углей)'
            lines.append(f'- Дефицит:   {d_sym} {deficit:.0f} ккал (день: {DAY_LOAD_LABEL[load]}, окно {lo}–{hi}){d_note}')
        lines += day_med_verdict(ref)
        lines += rolling7_med_verdict(ref)
        print('\n'.join(lines))
        return
    elif cmd == 'weektrend':
        print(weektrend(ref, with_groups='--no-groups' not in flags))
        return
    elif cmd == 'week':
        start, end = week_range(ref)
        label = 'Итоги недели'
        show_week = True
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

    print(fmt(summarize(start, end), label, show_week))


if __name__ == '__main__':
    main()
