#!/usr/bin/env python3
"""Commit and push everything — the whole git step in one call.

  git_sync.py [-m "message"] [--dry-run]

There is no decision in it, which is why it is a script and not a procedure:
what to add is everything not ignored, the message is templated on the day
today.md points at, push is unconditional. No `.git`, nothing to commit, no
remote and no network are ordinary outcomes — reported, not errors. Only a
refused commit is worth a human's attention, and it is the only non-zero exit.

Called by `итоги дня` and any ad-hoc commit; `new_day.py` uses run() at the
day close.
"""
import subprocess
import sys
from datetime import date

from paths import ROOT

TODAY_LINK = ROOT / 'today.md'


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)


def default_message():
    """day <date today.md points at>: update — falls back to today."""
    try:
        t = TODAY_LINK.resolve()
        d = date(int(t.parent.parent.name), int(t.parent.name), int(t.stem))
    except (ValueError, OSError):
        d = date.today()
    return f'day {d}: update'


def run(message=None, dry=False):
    """The whole block. Returns (report lines, commit refused)."""
    msg = message or default_message()
    if not (ROOT / '.git').exists():
        if dry:
            return [f'Git: (dry-run) git init, затем коммит «{msg}»'], False
        git('init')
    if dry:
        dirty = [l for l in git('status', '--porcelain').stdout.split('\n') if l.strip()]
        return [f'Git: (dry-run) закоммитил бы {len(dirty)} файлов — «{msg}»'], False

    git('add', '.')
    failed = False
    if git('diff', '--cached', '--quiet').returncode == 0:
        report = ['Git: коммитить нечего']
    else:
        r = git('commit', '-m', msg)
        if r.returncode == 0:
            report = [f'Git: «{msg}»']
        else:
            report = [f'Git: коммит не прошёл — {(r.stderr or r.stdout).strip()}']
            failed = True
    if not git('remote').stdout.strip():
        report.append('  remote нет — пушить некуда')
        return report, failed
    r = git('push')
    tail = (r.stderr or r.stdout).strip().split('\n')[-1] if (r.stderr or r.stdout).strip() else ''
    report.append('  push ok' if r.returncode == 0 else f'  push не прошёл — {tail}')
    return report, failed


def main(argv):
    msg, dry, i = None, False, 0
    while i < len(argv):
        a = argv[i]
        if a in ('-m', '--message'):
            i += 1
            if i >= len(argv):
                sys.exit(f'{a}: требуется значение')
            msg = argv[i]
        elif a == '--dry-run':
            dry = True
        else:
            sys.exit(f'неизвестный аргумент {a}')
        i += 1
    report, failed = run(msg, dry)
    print('\n'.join(report))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
