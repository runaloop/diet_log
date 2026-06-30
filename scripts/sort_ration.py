#!/usr/bin/env python3
"""Sort ration.md table: unchecked (🔲) rows first, checked (✅) rows to the bottom.

Stable: relative order within each group is preserved. Only the first markdown
table in the file is reordered; the `·` status column drives the split.
"""
import sys


def is_table_line(line):
    s = line.strip()
    return s.startswith('|') and s.endswith('|') and len(s) > 1


def parse_cells(line):
    return [c.strip() for c in line.strip()[1:-1].split('|')]


def sort_file(path):
    with open(path, encoding='utf-8') as f:
        content = f.read()

    trailing_newline = content.endswith('\n')
    lines = content.rstrip('\n').split('\n')

    out = []
    i = 0
    done = False
    while i < len(lines):
        if done or not is_table_line(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        block = []
        while i < len(lines) and is_table_line(lines[i]):
            block.append(lines[i])
            i += 1

        header, data = block[:2], block[2:]  # title row + separator stay on top
        unchecked = [r for r in data if '✅' not in parse_cells(r)[0]]
        checked = [r for r in data if '✅' in parse_cells(r)[0]]
        out.extend(header + unchecked + checked)
        done = True  # only sort the first table

    result = '\n'.join(out)
    if trailing_newline:
        result += '\n'

    with open(path, 'w', encoding='utf-8') as f:
        f.write(result)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(f'Usage: {sys.argv[0]} <ration.md>')
        sys.exit(1)
    sort_file(sys.argv[1])
