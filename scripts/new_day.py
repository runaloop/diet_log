#!/usr/bin/env python3
"""Close the day today.md points at, open the next one — in one call.

  new_day.py [--date YYYY-MM-DD] [--back N] [--dry-run] [--no-commit]

The procedure is deterministic, so it lives here instead of being described
across AGENTS.md:

  1. D = the date today.md points at, N = --date or today.
     N == D → nothing to close: report and exit 0 without touching anything.
  2. Close D: garmin_sync.apply(D, back=N, with_base=True) — watch workouts,
     the NEAT top-up, the weigh-in and the base expenditure. A machine with no
     tokens skips it silently. План/format/validate for D run unconditionally
     afterwards: apply recomputes only what it changed, and nothing at all
     without Garmin.
  3. Pills: sync (the leaving day, before pills.md is overwritten) → build N →
     sync again. config/medications.md is only read, never written.
  4. Diary N: weektrend header (--no-groups) + the План block, then today.md is
     repointed at it. An existing file is left alone; ration.md is not touched.
  5. Git: add, commit "day D: close, start N", push. Nothing to commit and no
     remote are both fine. --no-commit skips the step.
  6. A compact report, then the full weektrend N (with groups) for the chat.

Exit code is non-zero only where a human has to step in: a broken symlink, an
expired Garmin token, a diary that fails validation.
"""
import contextlib
import io
import subprocess
import sys
from datetime import date
from pathlib import Path

from paths import ROOT, MEDICATIONS, diary_path
from format_tables import format_file
from validate_diary import validate
import garmin_sync
import pills
import recalc_plan
import summary

TODAY_LINK = ROOT / 'today.md'


def rel(path):
    return str(Path(path).relative_to(ROOT))


def link_date():
    """(date today.md points at, its path). Exits when the symlink is unusable."""
    if not TODAY_LINK.is_symlink():
        sys.exit(f'today.md не симлинк — почини руками: '
                 f'ln -sf diaries/YYYY/MM/DD.md today.md')
    target = TODAY_LINK.resolve()
    try:
        d = date(int(target.parent.parent.name), int(target.parent.name),
                 int(target.stem))
    except ValueError:
        sys.exit(f'today.md → {target}: не похоже на diaries/YYYY/MM/DD.md')
    if not target.exists():
        sys.exit(f'today.md → {rel(target)}: файла нет')
    return d, target


def close_day(d, path, back, dry):
    """Garmin sync for the leaving day, then План/format/validate. (report, needs_human)"""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            garmin_sync.apply(d.isoformat(), back=back, with_base=True, dry_run=dry)
    except garmin_sync.GarminError as e:
        return [f'Garmin: {e}', '  → выполни: python3 scripts/garmin_sync.py login'], True
    said = [l for l in out.getvalue().rstrip('\n').split('\n')
            if l.strip() and not l.startswith('(dry-run')]
    report = ['Garmin:'] + [f'  {l}' for l in said] if said else \
             ['Garmin: токенов нет — пропуск (это не ошибка)']
    if dry:
        return report, False

    lines = path.read_text(encoding='utf-8').rstrip('\n').split('\n')
    lines = recalc_plan.apply(lines, recalc_plan.compute(lines, d))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    format_file(path)
    errs = validate(path)
    report.append(f'Закрыт: {rel(path)}' if not errs else
                  f'Закрыт: {rel(path)} — проверка не прошла:')
    report += [f'  ✗ {e}' for e in errs]
    return report, bool(errs)


def run_pills(n, dry):
    """sync the leaving day → build N → sync N. Never writes medications.md."""
    if dry:
        return ['Таблетки: (dry-run) sync → build → sync']
    report = []
    day, got = pills.sync()
    report.append(f'  {day} → история (принято {got})' if day
                  else '  уходящий день: pills.md пуст — сохранять нечего')
    if not MEDICATIONS.exists():
        report.append(f'  {rel(MEDICATIONS)} нет — чеклист не собран')
        return ['Таблетки:'] + report
    try:
        _, count = pills.build(n)
    except SystemExit as e:
        report.append(f'  {e}')
        return ['Таблетки:'] + report
    if count:
        report.append(f'  чеклист на {n} — {count} строк')
        day, got = pills.sync(n)
        if day:
            report.append(f'  {day} → история (принято {got})')
    else:
        report.append(f'  на {n} приёмов нет — pills.md не создаю')
    return ['Таблетки:'] + report


def open_day(n, dry):
    """Create diary N from the weektrend header and repoint today.md at it."""
    path = diary_path(n)
    if path.exists():
        report = [f'Дневник: {rel(path)} уже есть — не трогаю']
    elif dry:
        return [f'Дневник: создал бы {rel(path)}',
                f'Симлинк: today.md → {rel(path)}']
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        head = summary.weektrend(n, with_groups=False).rstrip('\n')
        lines = head.split('\n')
        lines = recalc_plan.apply(lines, recalc_plan.compute(lines, n))
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        format_file(path)
        report = [f'Дневник: {rel(path)} создан']
    if not dry:
        if TODAY_LINK.is_symlink() or TODAY_LINK.exists():
            TODAY_LINK.unlink()
        TODAY_LINK.symlink_to(path.relative_to(ROOT))
    report.append(f'Симлинк: today.md → {rel(path)}')
    return report


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)


def commit(d, n, dry):
    msg = f'day {d}: close, start {n}'
    if not (ROOT / '.git').exists():
        if dry:
            return [f'Git: (dry-run) git init, затем коммит «{msg}»']
        git('init')
    if dry:
        dirty = [l for l in git('status', '--porcelain').stdout.split('\n') if l.strip()]
        return [f'Git: (dry-run) закоммитил бы {len(dirty)} файлов — «{msg}»']
    git('add', '.')
    if git('diff', '--cached', '--quiet').returncode == 0:
        report = ['Git: коммитить нечего']
    else:
        r = git('commit', '-m', msg)
        report = [f'Git: «{msg}»'] if r.returncode == 0 else \
                 [f'Git: коммит не прошёл — {(r.stderr or r.stdout).strip()}']
    if not git('remote').stdout.strip():
        report.append('  remote нет — пушить некуда')
        return report
    r = git('push')
    tail = (r.stderr or r.stdout).strip().split('\n')[-1] if (r.stderr or r.stdout).strip() else ''
    report.append('  push ok' if r.returncode == 0 else f'  push не прошёл — {tail}')
    return report


def main(argv):
    opts, i = {'back': 2}, 0
    while i < len(argv):
        a = argv[i]
        if a in ('--date', '--back'):
            i += 1
            if i >= len(argv):
                sys.exit(f'{a}: требуется значение')
            opts[a[2:]] = argv[i]
        elif a in ('--dry-run', '--no-commit'):
            opts[a[2:]] = True
        else:
            sys.exit(f'неизвестный аргумент {a}')
        i += 1
    dry = opts.get('dry-run', False)

    d, path = link_date()
    n = date.fromisoformat(opts['date']) if 'date' in opts else date.today()
    if n == d:
        print(f'today.md уже указывает на {n} — закрывать нечего')
        return 0
    if n < d:
        sys.exit(f'today.md указывает на {d}, а запрошен {n} — назад не закрываю')

    report = []
    if dry:
        report.append('(dry-run, ничего не записано)')
    closing, needs_human = close_day(d, path, int(opts['back']), dry)
    report += closing
    gap = (n - d).days - 1
    if gap > 0:
        report.append(f'Пропущено дней между {d} и {n}: {gap} — файлов нет, '
                      f'в средние не входят')
    report += run_pills(n, dry)
    report += open_day(n, dry)
    if opts.get('no-commit'):
        report.append('Git: пропущен (--no-commit)')
    else:
        report += commit(d, n, dry)

    print('\n'.join(report))
    print()
    print(summary.weektrend(n))
    return 1 if needs_human else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
