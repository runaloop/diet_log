#!/usr/bin/env python3
"""Rebuild ration.md into its two-table layout (AGENTS.md §Рекомендуемый рацион):
«## Осталось» (🔲 rows) on top, «## Съедено» (✅ rows) below, each with its own
ИТОГО row recomputed from that table's dishes.

Rows travel between tables by their `·` status cell; relative order within each
group is preserved. Header/separator/ИТОГО rows are rebuilt, not carried, so a
legacy single-table file is migrated by the same pass. Non-table prose (the H1
title) stays on top.
"""
import re
import sys

HEADER = '| · | Блюдо | К | Б | Ж | У | Съедено |'
SEPARATOR = '| --- | --- | --- | --- | --- | --- | --- |'
SECTIONS = (('## Осталось', False), ('## Съедено', True))


def is_table_line(line):
    s = line.strip()
    return s.startswith('|') and s.endswith('|') and len(s) > 1


def parse_cells(line):
    return [c.strip() for c in line.strip()[1:-1].split('|')]


def is_separator(cells):
    return all(re.fullmatch(r':?-+:?', c) for c in cells if c)


def num(cell):
    try:
        return float(cell.replace('*', '') or 0)
    except ValueError:
        return 0.0


def total_row(rows):
    tk = tb = tz = tu = 0.0
    for r in rows:
        c = parse_cells(r)
        tk += num(c[2]); tb += num(c[3]); tz += num(c[4]); tu += num(c[5])
    return (f'| — | **ИТОГО** | **{tk:.0f}** | **{tb:.0f}** | '
            f'**{tz:.0f}** | **{tu:.0f}** | |')


def sort_file(path):
    with open(path, encoding='utf-8') as f:
        lines = f.read().rstrip('\n').split('\n')

    section_titles = {title for title, _ in SECTIONS}
    prefix, dishes = [], []
    for line in lines:
        if is_table_line(line):
            cells = parse_cells(line)
            if is_separator(cells) or cells[0] in ('·', '—'):
                continue
            dishes.append(line)
        elif line.strip() and line.strip() not in section_titles:
            prefix.append(line)

    out = prefix[:]
    for title, checked in SECTIONS:
        rows = [r for r in dishes if ('✅' in parse_cells(r)[0]) == checked]
        out += ['', title, '', HEADER, SEPARATOR] + rows + [total_row(rows)]

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} <ration.md>')
        sys.exit(1)
    sort_file(sys.argv[1])
