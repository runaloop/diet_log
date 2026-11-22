"""Rolling report boundaries, using only a copied engine and synthetic data."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NewDayWindow(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copytree(ROOT / 'scripts', self.root / 'scripts',
                        ignore=shutil.ignore_patterns('__pycache__'))
        (self.root / 'data').mkdir()
        config = self.root / 'config'
        config.mkdir()
        (config / 'goals.md').write_text('- Дефицит: ~300-500 ккал\n')
        (config / 'cycle.md').write_text('- Глобальный режим: похудение\n')
        (config / 'user.md').write_text('- Эффективный базовый расход: 2200 ккал\n'
                                      '| 2026-01-01 | 80 | | | | |\n')
        self.env = dict(os.environ, DIET_DB=str(self.root / 'data/diet.db'),
                        PYTHONDONTWRITEBYTECODE='1')
        self.env.pop('PYTHONPATH', None)
        self.command('db', 'init')
        for name, group, quality in [('Good', 'молочка', 'good'),
                                      ('Bad', 'добавки', 'bad')]:
            self.command('db', 'add', name, '--port', '100г', '--k', '100',
                         '--b', '0', '--zh', '10', '--u', '2.5',
                         '--group', group + '=1', '--fat-quality', quality)

    def run_python(self, args):
        result = subprocess.run([sys.executable, *args], cwd=self.root,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def command(self, script, *args):
        return self.run_python([str(self.root / 'scripts' / script), *map(str, args)])

    def evaluate(self, code):
        return self.run_python(['-c',
            "import sys; from pathlib import Path; from datetime import date, timedelta; "
            "sys.path.insert(0, str(Path.cwd()/'scripts')); " + code])

    def diary(self, day):
        return self.root / 'diaries' / day.strftime('%Y/%m/%d.md')

    def make_day(self, day, food=None):
        path = self.diary(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'# {day} | Режим: похудение\n\n## План\n\n'
                        '| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |\n'
                        '|---|---|---|---|---|---|---|\n')
        if food:
            self.command('log.py', path, food, '--time', '12:00')

    def prepare(self, day=date(2026, 11, 16), populated=False):
        # Only two of the preceding seven days exist: never backfill D-8.
        self.make_day(day - timedelta(days=8), 'Bad:800')
        self.make_day(day - timedelta(days=7), 'Good:300')
        self.make_day(day - timedelta(days=1), 'Bad:100')
        if populated:
            self.make_day(day, 'Bad:600')
        (self.root / 'today.md').symlink_to(
            self.diary(day - timedelta(days=1)).relative_to(self.root))
        return day

    def new_day(self, day, dry=False):
        args = ['--date', day.isoformat(), '--no-commit']
        if dry:
            args.append('--dry-run')
        return self.evaluate(
            "import paths, garmin_sync, new_day; "
            "assert paths.ROOT == Path.cwd(); "
            "assert paths.DB_PATH == Path.cwd()/'data/diet.db'; "
            "garmin_sync.TOKEN_PATH=Path.cwd()/'missing-test-tokens.json'; "
            "assert not garmin_sync.TOKEN_PATH.exists(); "
            "sys.exit(new_day.main(" + repr(args) + '))')

    def dashboard(self, day, previous=False, groups=True):
        return self.evaluate(
            "import summary; d=date.fromisoformat(" + repr(day.isoformat()) + '); '
            f'print(summary.weektrend(d, with_groups={groups}'
            + (', rolling_end=d-timedelta(days=1)' if previous else '') + '))')

    def assert_window(self, text, day, previous=True, populated=False):
        end = day - timedelta(days=int(previous))
        dates = f'{end - timedelta(days=6)}..{end}'
        self.assertIn(f'Остаток групп (7 дней {dates})', text)
        self.assertIn(f'Качество жира (7 дней {dates})', text)
        self.assertRegex(text, r'молочка:\s+\S+ ' + ('1.0/' if previous else '0.0/'))
        sweets = '2.0/' if populated and not previous else '1.0/'
        self.assertRegex(text, r'добавки:\s+\S+ ' + sweets)
        self.assertRegex(text, r'bad: \S+ ' + ('25%' if previous else '100%'))

    def snapshot(self):
        return {str(p.relative_to(self.root)):
                ('link', os.readlink(p)) if p.is_symlink() else ('file', p.read_bytes())
                for p in self.root.rglob('*') if p.is_file() or p.is_symlink()}

    def test_explicit_and_default_endpoints(self):
        day = self.prepare(populated=True)
        before = self.snapshot()
        self.assert_window(self.dashboard(day, previous=True), day)
        self.assert_window(self.dashboard(day), day, previous=False, populated=True)
        self.assertEqual(before, self.snapshot())

    def test_no_groups_keeps_heading_and_trend(self):
        day = self.prepare()
        normal = self.dashboard(day, groups=False)
        self.assertEqual(normal, self.dashboard(day, previous=True, groups=False))
        self.assertIn('# 16-11-2026-W47', normal)
        self.assertIn('Первый день недели, тренда пока нет', normal)
        self.assertNotIn('Остаток групп', normal)
        self.assertNotIn('Качество жира', normal)

    def test_new_day_monday_and_ordinary_empty_dashboard(self):
        day = self.prepare()
        report = self.new_day(day)
        self.assert_window(report, day)
        self.assertIn('# 16-11-2026-W47', report)
        self.assertIn('Первый день недели, тренда пока нет', report)
        content = self.diary(day).read_text()
        self.assertIn('# 16-11-2026-W47', content)
        self.assertNotIn('Остаток групп', content)
        self.assertNotIn('Качество жира', content)
        self.assertEqual((self.root / 'today.md').resolve(), self.diary(day))
        self.assert_window(self.command('summary.py', 'weektrend', day),
                           day, previous=False)

    def test_existing_target_is_preserved_and_excluded(self):
        day = self.prepare(populated=True)
        before = self.diary(day).read_bytes()
        self.assert_window(self.new_day(day), day)
        self.assertEqual(before, self.diary(day).read_bytes())
        self.assert_window(self.command('summary.py', 'weektrend', day),
                           day, previous=False, populated=True)

    def test_year_boundary_with_missing_diaries(self):
        day = self.prepare(date(2027, 1, 1))
        self.assert_window(self.new_day(day), day)

    def test_dry_run_does_not_write(self):
        day = self.prepare()
        before = self.snapshot()
        self.assert_window(self.new_day(day, dry=True), day)
        self.assertEqual(before, self.snapshot())

    def test_noop_does_not_write_or_report_window(self):
        day = self.prepare()
        self.new_day(day)
        before = self.snapshot()
        report = self.new_day(day)
        self.assertIn('закрывать нечего', report)
        self.assertNotIn('Остаток групп', report)
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main()
