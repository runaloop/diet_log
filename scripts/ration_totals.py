#!/usr/bin/env python3
"""Recompute the ИТОГО row of every table in ration.md from its own rows —
mechanical sums, never hand-added. In the two-table layout (Съедено/Осталось)
each table gets its own total. Optionally checks the day's protein floor
against a diary: diary's eaten protein + the «Осталось» table (checked dishes
are already logged in the diary, so only the remainder is added).

Usage:
  ration_totals.py ration.md [diary.md] [--write]
"""
import re
import sys
from pathlib import Path

from plan_ration import parse_plan, PROT_TOL
from summary import week_range, cycle_phase, protein_floor


def is_table_line(line):
    s = line.strip()
    return s.startswith('|') and s.endswith('|') and len(s) > 1


def parse_cells(line):
    return [c.strip() for c in line.strip()[1:-1].split('|')]


def is_separator(cells):
    return all(re.fullmatch(r':?-+:?', c) for c in cells if c)


def num(cell):
    return float(re.sub(r'\*', '', cell) or 0)


def block_totals(block):
    """Sum К/Б/Ж/У over the dish rows of one table block (header, separator
    and ИТОГО rows skipped)."""
    tk = tb = tz = tu = 0.0
    for line in block[1:]:
        cells = parse_cells(line)
        if is_separator(cells) or cells[0] == '—':
            continue
        tk += num(cells[2]); tb += num(cells[3]); tz += num(cells[4]); tu += num(cells[5])
    return tk, tb, tz, tu


def total_row(totals):
    tk, tb, tz, tu = totals
    return (f"| — | **ИТОГО** | **{tk:.0f}** | **{tb:.0f}** | "
            f"**{tz:.0f}** | **{tu:.0f}** | |")


def rewrite(lines):
    """Recompute ИТОГО of every table; return (new_lines, [(label, totals)])
    where label is the nearest heading above the table."""
    out, tables = [], []
    label = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if not is_table_line(line):
            m = re.match(r'#+\s+(.*)', line)
            if m:
                label = m.group(1).strip()
            out.append(line)
            i += 1
            continue
        block = []
        while i < len(lines) and is_table_line(lines[i]):
            block.append(lines[i])
            i += 1
        totals = block_totals(block)
        block = [l for l in block if parse_cells(l)[0] != '—']
        block.append(total_row(totals))
        out.extend(block)
        tables.append((label or f'таблица {len(tables) + 1}', totals))
    return out, tables


if __name__ == '__main__':
    pos = [a for a in sys.argv[1:] if not a.startswith('--')]
    write = '--write' in sys.argv
    if not pos:
        print(f'Usage: {sys.argv[0]} <ration.md> [diary.md] [--write]')
        sys.exit(1)
    ration_path = Path(pos[0])
    lines = ration_path.read_text(encoding='utf-8').rstrip('\n').split('\n')
    new_lines, tables = rewrite(lines)
    for label, (tk, tb, tz, tu) in tables:
        print(f'{label}: К{tk:.0f} Б{tb:.0f} Ж{tz:.0f} У{tu:.0f}')

    if len(pos) > 1 and tables:
        rem = next((t for label, t in tables if 'осталось' in label.lower()),
                   tables[-1][1])
        diary_text = Path(pos[1]).read_text(encoding='utf-8')
        plan = parse_plan(diary_text)
        d = re.search(r'(\d{2})-(\d{2})-(\d{4})', diary_text)
        from datetime import date
        ref = date(int(d.group(3)), int(d.group(2)), int(d.group(1))) if d else None
        if ref:
            week_start, _ = week_range(ref)
            floor = protein_floor(cycle_phase(week_start))
            day_prot = plan['prot_eaten'] + rem[1]
            sym = '✓' if day_prot >= floor - PROT_TOL else '⚠'
            print(f'Белок за день с добором остатка: {sym} {day_prot:.0f}/{floor:.0f}г')

    if write:
        ration_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')
        print(f'{ration_path} обновлён.')
