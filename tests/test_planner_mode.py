"""Planner mode contracts, using copied scripts and wholly synthetic inputs.

No project module is imported in this test process. Every product invocation runs
from a temporary engine root with its own config, catalog, profile and diaries.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUT = 'похудение'
MAINT = 'поддержание'

# Observe real CLI calculations without replacing any calculation or selection.
PROBE = r'''
import contextlib, io, json, runpy, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()/'scripts'))
import paths, summary
assert paths.ROOT == Path.cwd()
for p in (paths.DB_PATH, paths.CYCLE, paths.USER, paths.GOALS,
          paths.PROFILE_PATH, paths.DIARIES, paths.RATION):
    assert p.resolve().is_relative_to(Path.cwd())
budgets, calls = [], []
real_range, real_mode = summary.fat_range, summary.global_mode
def observed_range(*args, **kwargs):
    budgets.append(kwargs.get('budget', args[2] if len(args)>2 else None))
    return real_range(*args, **kwargs)
def observed_mode(*args, **kwargs):
    calls.append(True)
    return real_mode(*args, **kwargs)
summary.fat_range, summary.global_mode = observed_range, observed_mode
output = io.StringIO()
sys.argv = ['scripts/plan_ration.py', *sys.argv[1:]]
with contextlib.redirect_stdout(output):
    ns = runpy.run_path('scripts/plan_ration.py', run_name='__main__')
print(json.dumps({'output': output.getvalue(), 'budgets': budgets,
                  'mode_calls': len(calls), 'plan': ns['plan'],
                  'load': ns['load'], 'mode': ns['mode']}, ensure_ascii=False))
'''


class PlannerMode(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='synthetic-planner-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root/'scripts').mkdir()
        for source in (ROOT/'scripts').iterdir():
            if source.is_file() and (source.suffix == '.py' or source.name == 'db'):
                shutil.copy2(source, self.root/'scripts'/source.name)
        for name in ('config', 'data', 'diaries/2030/01'):
            (self.root/name).mkdir(parents=True)
        (self.root/'config/goals.md').write_text('- Дефицит: ~300-500 ккал\n- Белок: min 0\n')
        (self.root/'config/user.md').write_text('- Эффективный базовый расход: 2000 ккал\n')
        self.env = dict(os.environ, DIET_DB=str(self.root/'data/diet.db'),
                        PYTHONPATH=str(self.root/'scripts'), PYTHONDONTWRITEBYTECODE='1')
        self.diary = self.root/'diaries/2030/01/02.md'
        self.ration = self.root/'ration.md'
        self.set_mode(MAINT)
        self.command('scripts/db', 'init')
        self.command('scripts/db', 'add', 'Synthetic carb', '--port', '100г',
                     '--k', '200', '--b', '0', '--zh', '0', '--u', '50',
                     '--group', 'птица=0.1', '--fat-quality', 'neutral')
        self.command('scripts/db', 'add', 'Synthetic fat', '--port', '100г',
                     '--k', '270', '--b', '0', '--zh', '30', '--u', '0',
                     '--group', 'оливковое=1', '--fat-quality', 'good')
        profile = [dict(name=name, count=5, median_grams=100,
                        per_gram=dict(k=k/100, b=0, zh=zh/100, u=u/100))
                   for name, k, zh, u in [('Synthetic carb', 200, 0, 50),
                                         ('Synthetic fat', 270, 30, 0)]]
        (self.root/'data/profile.json').write_text(json.dumps(profile))
        (self.root/'probe.py').write_text(PROBE)

    def command(self, *args):
        target = (self.root/args[0]).resolve()
        self.assertTrue(target.is_relative_to(self.root))
        result = subprocess.run([sys.executable, str(target), *args[1:]],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def set_mode(self, mode):
        (self.root/'config/cycle.md').write_text(f'- Глобальный режим: {mode}\n')

    def prepare(self, mode, heading, load='low', legacy=False, activity=False):
        self.set_mode(mode)
        label = 'Фаза цикла' if legacy else 'Режим'
        suffix = f' | {label}: {heading}' if heading else ''
        text = f'# 02-01-2030-W01{suffix}\n\n## План\n\n'
        text += '| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |\n'
        text += '|---|---|---|---|---|---|---|\n'
        text += '| 09:00 | Synthetic eaten | 600 | 0 | 60 | 15 | 0 |\n'
        if activity:
            text += '| 10:00 | Synthetic Z3 | -400 | - | - | - | - |\n'
        self.diary.write_text(text)
        self.command('scripts/recalc_plan.py', str(self.diary), '--load', load, '--write')
        # Derive expectations independently through the existing recalculator.
        script = self.root/'expected.py'
        script.write_text("import json,sys\nfrom pathlib import Path\n"
                          "from datetime import date\nimport recalc_plan\n"
                          "c=recalc_plan.compute(Path(sys.argv[1]).read_text().splitlines(),"
                          "date(2030,1,2),load=sys.argv[2])\n"
                          "print(json.dumps({k:c[k] for k in ['ffloor','fcap','can_eat','carb_left','base','spent']}))\n")
        return json.loads(self.command('expected.py', str(self.diary), load))

    def probe(self, *args):
        return json.loads(self.command('probe.py', str(self.diary), *args))

    def assert_consistent(self, mode, heading, load='low', legacy=False, activity=False):
        expected = self.prepare(mode, heading, load, legacy, activity)
        result = self.probe('--load', load)
        budget = expected['base'] + expected['spent'] - (
            -150 if mode == MAINT or load == 'high' else 0 if load == 'mid' else 300)
        # Initial cap, final fat completion and rendered range each use budget.
        self.assertEqual(result['budgets'], [budget, budget, budget])
        self.assertEqual(result['mode_calls'], 1)
        self.assertEqual(result['mode'], mode)
        self.assertEqual(result['load'], load)
        self.assertIn(f"жир: {expected['ffloor']}–{expected['fcap']}г", result['output'])
        self.assertIn(f'Режим: {mode}', result['output'])
        self.assertEqual('Synthetic carb' in result['output'], mode == MAINT or load != 'low')
        self.assertEqual(result['plan']['kcal'], expected['can_eat'])
        self.assertEqual(result['plan']['carb_rem'], expected['carb_left'])
        self.assertEqual(result['plan']['phase'], heading or CUT)
        return result

    def test_matching_modes_and_shared_recalculation(self):
        for mode in (CUT, MAINT):
            for load in ('low', 'mid', 'high'):
                with self.subTest(mode=mode, load=load):
                    self.assert_consistent(mode, mode, load)

    def test_both_mismatch_directions(self):
        for mode, heading in ((MAINT, CUT), (CUT, MAINT)):
            with self.subTest(mode=mode):
                self.assert_consistent(mode, heading)

    def test_missing_and_legacy_headings(self):
        self.assert_consistent(MAINT, None)
        self.assert_consistent(MAINT, CUT, legacy=True)
        # Legacy allowance spelling and ASCII minus are still accepted.
        text = self.diary.read_text().replace('Можно съесть до +150:',
                                             'Можно съесть (чтобы сохранить дефицит -150 ккал):')
        self.diary.write_text(text)
        result = self.probe('--load', 'low')
        self.assertEqual(result['plan']['kcal'], 1550)
        self.assertEqual(result['plan']['phase'], CUT)

    def test_explicit_load_overrides_logged_activity(self):
        self.assert_consistent(MAINT, CUT, 'low', activity=True)
        self.assert_consistent(CUT, MAINT, 'high')

    def test_inferred_load_uses_same_mode(self):
        self.prepare(MAINT, CUT, 'high', activity=True)
        result = self.probe()
        self.assertEqual(result['load'], 'high')
        self.assertEqual(result['budgets'], [2550, 2550, 2550])
        self.assertEqual(result['mode_calls'], 1)

    def test_no_write_and_no_stored_value_refresh(self):
        self.prepare(MAINT, CUT)
        text = self.diary.read_text().replace('1550 ккал', '123 ккал')
        # Deliberately stale numeric fields must still be consumed as before.
        import re
        text = re.sub(r'осталось: \d+', 'осталось: 17', text)
        self.diary.write_text(text)
        self.ration.write_text('# Рекомендуемый рацион 2030-01-02\nexisting checks\n')
        paths = [self.diary, self.ration, self.root/'data/diet.db',
                 self.root/'data/profile.json', self.root/'config/cycle.md']
        before = [p.read_bytes() for p in paths]
        result = self.probe('--load', 'low')
        self.assertEqual(result['plan']['kcal'], 123)
        self.assertEqual(result['plan']['carb_rem'], 17)
        self.assertEqual(before, [p.read_bytes() for p in paths])

    def test_write_overwrite_force_and_empty(self):
        self.prepare(MAINT, CUT)
        marker = '# Рекомендуемый рацион 2030-01-02\nexisting checks\n'
        self.ration.write_text(marker)
        self.probe('--load', 'low', '--write')
        self.assertEqual(self.ration.read_text(), marker)
        self.probe('--load', 'low', '--write', '--force')
        self.assertNotEqual(self.ration.read_text(), marker)
        self.assertIn('Synthetic carb', self.ration.read_text())
        self.ration.unlink()
        self.probe('--load', 'low', '--write')
        self.assertIn('Synthetic carb', self.ration.read_text())
        (self.root/'data/profile.json').write_text('[]')
        self.ration.write_text(marker)
        self.probe('--load', 'low', '--write', '--force')
        self.assertEqual(self.ration.read_text(), marker)
        self.ration.unlink()
        self.probe('--load', 'low', '--write', '--force')
        self.assertFalse(self.ration.exists())

    def test_parser_caller_ration_totals(self):
        self.prepare(MAINT, CUT, legacy=True)
        self.ration.write_text('## Осталось\n| · | Блюдо | К | Б | Ж | У | Съедено |\n'
                              '|---|---|---|---|---|---|---|\n'
                              '| 🔲 | Synthetic | 10 | 0 | 0 | 2 | |\n')
        result = self.command('scripts/ration_totals.py', str(self.ration), str(self.diary))
        self.assertIn('К10 Б0 Ж0 У2', result)


if __name__ == '__main__':
    unittest.main()
