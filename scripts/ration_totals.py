#!/usr/bin/env python3
"""Recompute the ИТОГО row of ration.md from its own table — mechanical sum,
never hand-added. Optionally checks the day's protein floor against a diary.

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


def num(cell):
    return float(re.sub(r'\*', '', cell) or 0)


def sum_table(lines):
    """Sum К/Б/Ж/У over every dish row of the first table (checked or not;
    ИТОГО is the whole day's plan, matching plan_ration.render_ration_md)."""
    tk = tb = tz = tu = 0.0
    in_table = False
    header_seen = False
    for line in lines:
        if not is_table_line(line):
            if in_table:
                break
            continue
        in_table = True
        cells = parse_cells(line)
        if not header_seen:
            header_seen = True
            continue
        if re.fullmatch(r':?-+:?', cells[0]) or cells[0] == '—':
            continue
        tk += num(cells[2]); tb += num(cells[3]); tz += num(cells[4]); tu += num(cells[5])
    return tk, tb, tz, tu


def rewrite_total(lines, totals):
    tk, tb, tz, tu = totals
    total_row = (f"| — | **ИТОГО** | **{tk:.0f}** | **{tb:.0f}** | "
                 f"**{tz:.0f}** | **{tu:.0f}** | |")
    out, replaced, last_table_idx = [], False, None
    for i, line in enumerate(lines):
        if is_table_line(line):
            last_table_idx = len(out)
            if parse_cells(line)[0] == '—':
                out.append(total_row)
                replaced = True
                continue
        out.append(line)
    if not replaced:
        out.insert(last_table_idx + 1, total_row)
    return out


if __name__ == '__main__':
    pos = [a for a in sys.argv[1:] if not a.startswith('--')]
    write = '--write' in sys.argv
    if not pos:
        print(f'Usage: {sys.argv[0]} <ration.md> [diary.md] [--write]')
        sys.exit(1)
    ration_path = Path(pos[0])
    lines = ration_path.read_text(encoding='utf-8').rstrip('\n').split('\n')
    totals = sum_table(lines)
    tk, tb, tz, tu = totals
    print(f'ИТОГО: К{tk:.0f} Б{tb:.0f} Ж{tz:.0f} У{tu:.0f}')

    if len(pos) > 1:
        diary_text = Path(pos[1]).read_text(encoding='utf-8')
        plan = parse_plan(diary_text)
        d = re.search(r'(\d{2})-(\d{2})-(\d{4})', diary_text)
        from datetime import date
        ref = date(int(d.group(3)), int(d.group(2)), int(d.group(1))) if d else None
        floor = None
        if ref:
            week_start, _ = week_range(ref)
            floor = protein_floor(cycle_phase(week_start))
        if floor is not None:
            day_prot = plan['prot_eaten'] + tb
            sym = '✓' if day_prot >= floor - PROT_TOL else '⚠'
            print(f'Белок за день с рационом: {sym} {day_prot:.0f}/{floor:.0f}г')

    if write:
        ration_path.write_text('\n'.join(rewrite_total(lines, totals)) + '\n',
                                encoding='utf-8')
        print(f'{ration_path} обновлён.')
