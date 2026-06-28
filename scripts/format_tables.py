#!/usr/bin/env python3
"""Align all markdown tables in a file."""
import re
import sys
import unicodedata


def display_width(s):
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ('W', 'F') else 1
    return w


def ljust_display(s, width):
    return s + ' ' * max(0, width - display_width(s))


def is_table_line(line):
    s = line.strip()
    return s.startswith('|') and s.endswith('|') and len(s) > 1


def parse_cells(line):
    return [c.strip() for c in line.strip()[1:-1].split('|')]


def is_separator(cells):
    return all(re.fullmatch(r':?-+:?', c) for c in cells if c)


def format_file(path):
    with open(path, encoding='utf-8') as f:
        content = f.read()

    trailing_newline = content.endswith('\n')
    lines = content.rstrip('\n').split('\n')

    out = []
    i = 0
    while i < len(lines):
        if not is_table_line(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        block = []
        while i < len(lines) and is_table_line(lines[i]):
            block.append(lines[i])
            i += 1

        rows = [parse_cells(line) for line in block]
        ncols = max(len(r) for r in rows)
        rows = [r + [''] * (ncols - len(r)) for r in rows]

        data_rows = [r for r in rows if not is_separator(r)] or rows
        widths = [max(max(display_width(r[c]) for r in data_rows), 3) for c in range(ncols)]

        for row in rows:
            if is_separator(row):
                cells = ['-' * widths[c] for c in range(ncols)]
            else:
                cells = [ljust_display(row[c], widths[c]) for c in range(ncols)]
            out.append('| ' + ' | '.join(cells) + ' |')

    result = '\n'.join(out)
    if trailing_newline:
        result += '\n'

    with open(path, 'w', encoding='utf-8') as f:
        f.write(result)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} <file.md>')
        sys.exit(1)
    format_file(sys.argv[1])
