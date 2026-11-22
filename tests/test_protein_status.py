"""Food logging, summaries and day rollover after removing amino-acid checks.

Run actual commands in a temporary repository; never use the user's data or
Garmin credentials. The ordinary daily protein goal remains in every status.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProteinStatus(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copytree(ROOT/'scripts', self.root/'scripts',
                        ignore=shutil.ignore_patterns('__pycache__'))
        (self.root/'data').mkdir()
        config = self.root/'config'
        config.mkdir()
        (config/'goals.md').write_text('- Дефицит: ~300-500 ккал\n')
        (config/'cycle.md').write_text('- Глобальный режим: похудение\n')
        (config/'user.md').write_text('- Эффективный базовый расход: 2200 ккал\n'
                                     '| 2026-11-01 | 80 | | | | |\n')
        self.env = dict(os.environ, DIET_DB=str(self.root/'data/diet.db'))
        self.run_command('db', 'init')
        self.run_command('db', 'add', 'Белковый продукт', '--port', '100г',
                         '--k', '100', '--b', '20', '--zh', '0', '--u', '5',
                         '--group', 'молочка=1', '--alias', 'белок')
        self.diary = self.root/'diaries/2026/11/07.md'
        self.diary.parent.mkdir(parents=True)
        self.diary.write_text('# 07-11-2026-W45 | Режим: похудение\n\n## План\n\n'
            '| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |\n'
            '|---|---|---|---|---|---|---|\n')
        (self.root/'today.md').symlink_to(self.diary.relative_to(self.root))

    def run_command(self, script, *args, ok=True):
        result = subprocess.run([sys.executable, str(self.root/'scripts'/script), *args],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)
        return result.stdout + result.stderr

    def assert_plain_status(self, text):
        self.assertIn('Белок:', text)
        for removed in ('Аминокислот', 'профиль белка', 'На ближайший приём пищи',
                        'базовые ориентиры', 'Добрать белок:'):
            self.assertNotIn(removed, text)

    def test_add_fix_delete_preserve_quantity_without_additional_recommendations(self):
        cases = [(['белок:450'], 90),
                 (['--fix', 'Белковый продукт', 'белок:400'], 80),
                 (['--delete', 'Белковый продукт'], 0)]
        for args, total in cases:
            with self.subTest(args=args):
                text = self.run_command('log.py', str(self.diary), *args, '--time', '12:00')
                self.assert_plain_status(text)
                self.assertIn(f'{total}/144г', text)
                self.assertIn(f'| Б{total} |', self.diary.read_text())
                self.assertNotIn('⚠ validate_diary', text)

    def test_reports_work_without_profiles_and_do_not_modify_diaries(self):
        self.run_command('log.py', str(self.diary), 'белок:750', '--time', '12:00')
        before = self.diary.read_bytes()
        for cmd in ('day', 'week', 'month', 'weektrend', '2026-11-07..2026-11-07', 'since-weight'):
            with self.subTest(cmd=cmd):
                args = [] if '..' in cmd or cmd == 'since-weight' else ['2026-11-07']
                text = self.run_command('summary.py', cmd, *args)
                self.assertNotIn('Аминокислот', text)
                if cmd != 'weektrend':
                    self.assertIn('Белок:', text)
        self.assertEqual(self.diary.read_bytes(), before)
        short = self.run_command('summary.py', 'weektrend', '2026-11-07', '--no-groups')
        self.assertNotIn('Аминокислот', short)

    def test_retired_summary_command_and_flags_fail_cleanly(self):
        for args in (('protein', '2026-11-07T12:00'),
                     ('day', '2026-11-07', '--amino-details'),
                     ('weektrend', '--amino-details', '2026-11-07')):
            with self.subTest(args=args):
                self.run_command('summary.py', *args, ok=False)

    def test_new_day_creates_diary_without_extra_food_advice(self):
        self.run_command('log.py', str(self.diary), 'белок:750', '--time', '12:00')
        # Isolate the only external-service path; all day-changing code is real.
        runner = ("from pathlib import Path; import sys; "
                  "sys.path.insert(0, str(Path.cwd()/'scripts')); "
                  "import garmin_sync; "
                  "garmin_sync.TOKEN_PATH=Path.cwd()/'missing-test-tokens.json'; "
                  "import new_day; "
                  "sys.exit(new_day.main(['--date','2026-11-08','--no-commit']))")
        result = subprocess.run([sys.executable, '-c', runner], cwd=self.root,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Закрыт:', result.stdout)
        self.assertNotIn('Аминокислот', result.stdout)
        self.assertNotIn('На ближайший приём пищи', result.stdout)
        new_path = self.root/'diaries/2026/11/08.md'
        self.assertEqual((self.root/'today.md').resolve(), new_path)
        self.assertIn('Белок: 144г', new_path.read_text())
        self.assertIn('Б0', new_path.read_text())


if __name__ == '__main__':
    unittest.main()
