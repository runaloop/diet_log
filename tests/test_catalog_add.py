"""Catalog decisions through the real CLI, always using a temporary database."""
from contextlib import closing
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from catalog_review import warning_lines
import summary


class CatalogAdd(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'catalog.db'
        self.env = dict(os.environ, DIET_DB=str(self.path))
        self.cli('init')

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/db'), *args],
                                env=self.env, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn('Traceback', result.stderr)
        return result.stdout + result.stderr

    def query(self, sql):
        with closing(sqlite3.connect(self.path)) as con:
            return con.execute(sql).fetchall()

    def add(self, *extra, ok=True, name='Пекан'):
        return self.cli('add', name, '--port', '100г', '--k', '690', '--b', '9.17',
                        '--zh', '71.97', '--u', '13.86', *extra, ok=ok)

    def complete(self, *extra, name='Пекан'):
        return self.add('--group', 'орехи=1', '--fat-quality', 'good',
                        *extra, name=name)

    def test_complete_product_is_saved_with_all_decisions(self):
        out = self.complete('--alias', 'орех', '--serving', '20г')
        self.assertIn('орехи=1', out)
        self.assertNotIn('⚠', out)
        self.assertEqual(self.query('SELECT review,fat_quality,serving_g FROM product'), [(0, 'good', 20)])
        self.assertEqual(self.query('SELECT weight FROM product_group'), [(1.0,)])
        self.assertEqual(self.query('SELECT b FROM product'), [(9.17,)])
        self.assertNotIn('профиль', out)

    def test_macros_required_but_missing_annotations_are_accepted(self):
        self.assertIn('--port', self.cli('add', 'Новый', '--alias', 'новинка', ok=False))
        self.assertEqual(self.query('SELECT * FROM product'), [])
        out = self.add()
        self.assertNotIn('⚠', out)
        self.assertEqual(self.query('SELECT review,fat_quality FROM product'), [(0, 'neutral')])
        self.assertEqual(self.query('SELECT field,status FROM product_review ORDER BY field'),
                         [('fat', 'unknown'), ('groups', 'unknown')])

    def test_invalid_choices_never_change_existing_card(self):
        self.complete('--alias', 'старый')
        before = self.query('SELECT * FROM product')
        for args in (('--group', 'нет=1'),
                     ('--group', 'орехи=nan'), ('--group', 'орехи=0'),
                     ('--group', 'орехи=-1'), ('--group', 'орехи=2'),
                     ('--group', 'орехи=1', '--group', 'орехи=0.5'),
                     ('--review-groups', ' '), ('--fat-unknown', ' '),
                     ('--no-groups', ' '), ('--groups-estimate', 'основание'),
                     ('--group', 'орехи=1', '--groups-estimate', ' '),
                     ('--fat-estimate', 'основание'),
                     ('--fat-quality', 'good', '--fat-estimate', ' '),
                     ('--k', 'nan'), ('--zh', 'inf'), ('--b', '-1'), ('--serving', '-20г'),
                     ('--port=-100г',), ('--port', '0г'), ('--port', '100')):
            with self.subTest(args=args):
                self.cli('add', 'Пекан', '--alias', 'не записывать', *args, ok=False)
                self.assertEqual(self.query('SELECT * FROM product'), before)
                self.assertEqual(self.query('SELECT text FROM alias'), [('старый',)])
                self.assertEqual(self.query('SELECT weight FROM product_group'), [(1.0,)])

    def test_unknown_decisions_are_final_and_inspectable(self):
        out = self.add('--review-groups', 'неизвестен рецепт', '--fat-unknown', 'неизвестно масло',
                       '--alias', 'орех')
        self.assertNotIn('⚠', out)
        self.assertEqual(self.query('SELECT review,fat_quality FROM product'), [(0, 'neutral')])
        lines = warning_lines(['орех', 'Пекан'], self.path)
        self.assertEqual(lines, [])
        self.assertIn('требуют проверки: 0/1', self.cli('review'))
        self.assertIn('неизвестно масло', self.cli('review', '--all'))
        self.complete()
        self.assertEqual(warning_lines(['Пекан'], self.path), [])
        self.assertEqual(self.query('SELECT * FROM product_review'), [])

    def test_no_groups_is_explicit_and_remembers_reason(self):
        self.cli('add', 'Вода', '--port', '100г', '--k', '0', '--b', '0', '--zh', '0',
                 '--u', '0', '--no-groups', 'вода не входит в пищевые группы')
        self.assertEqual(warning_lines(['Вода'], self.path), [])
        self.assertEqual(self.query('SELECT status,reason FROM product_review'),
                         [('exempt', 'вода не входит в пищевые группы')])

    def test_partial_updates_keep_macros_groups_and_pending_state(self):
        self.complete('--estimate', '--serving', '20г')
        before = self.query('SELECT * FROM product')
        self.cli('add', 'Пекан', '--alias', 'новый')
        self.assertEqual(self.query('SELECT * FROM product'), before)
        self.assertEqual(self.query('SELECT weight FROM product_group'), [(1.0,)])
        self.cli('add', 'Пекан', '--review-groups', 'проверить состав')
        self.cli('add', 'Пекан', '--alias', 'ещё')
        self.assertEqual(self.query('SELECT review FROM product'), [(0,)])
        self.assertEqual(self.query("SELECT status FROM product_review WHERE field='groups'"), [('unknown',)])

    def test_multiple_groups_replace_old_set_and_concentrated_fish_is_allowed(self):
        self.complete()
        self.cli('add', 'Пекан', '--group', 'рыба=2.5', '--group', 'овощи=0.5')
        self.assertEqual(self.query('SELECT weight FROM product_group ORDER BY weight'), [(0.5,), (2.5,)])

    def test_conflicting_decisions_fail(self):
        self.add('--group', 'орехи=1', '--no-groups', 'нет', ok=False)
        self.add('--fat-quality', 'good', '--fat-unknown', 'нет', ok=False)
        self.assertEqual(self.query('SELECT * FROM product'), [])

    def test_database_failure_rolls_back_product_alias_groups_and_review(self):
        # Fail at the last step, after the product, alias and groups were inserted.
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute("""CREATE TRIGGER reject_review BEFORE INSERT ON product_review
                BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/db'), 'add', 'Пекан',
            '--port', '100г', '--k', '690', '--b', '9', '--zh', '72', '--u', '14',
            '--group', 'орехи=1', '--fat-unknown', 'неизвестно масло',
            '--alias', 'орех'], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        for table in ('product', 'alias', 'product_group', 'product_review'):
            self.assertEqual(self.query(f'SELECT * FROM {table}'), [])

    def test_tag_command_resolves_its_pending_decision(self):
        self.add('--review-groups', 'рецепт', '--fat-quality', 'good')
        self.cli('tag', 'Пекан', 'орехи=1')
        self.assertEqual(warning_lines(['Пекан'], self.path), [])

    def test_legacy_untagged_product_warns_even_without_annotation(self):
        self.complete()
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute('UPDATE product SET review=1')
            con.execute('DELETE FROM product_group')
            con.execute('DROP TABLE product_review')
        self.assertIn('группы ещё не назначены', '\n'.join(warning_lines(['Пекан'], self.path)))

    def test_weekly_group_report_keeps_accepted_unknown_groups_quiet(self):
        self.add('--review-groups', 'неизвестен рецепт', '--fat-quality', 'good')
        ref = date(2026, 11, 4)
        diary = self.root / 'day.md'
        diary.write_text('| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |\n'
                         '|---|---|---|---|---|---|---|\n'
                         '| 12:00 | Пекан 20г | 138 | 2 | 14 | 3 | 2 |\n')
        with patch.object(summary, 'DB_PATH', self.path), patch.object(summary, 'diary_path', return_value=diary):
            out = '\n'.join(summary.group_remainder_lines(ref))
            self.assertNotIn('Учёт групп неполный', out)
            self.assertNotIn('неизвестен рецепт', out)
            servings, unmatched = summary.group_servings(ref, ref)
            self.assertEqual(dict(servings), {})
            self.assertEqual(unmatched, [])

    def test_choices_lists_existing_values(self):
        out = self.cli('choices')
        self.assertIn('орехи', out)
        self.assertIn('good, neutral, bad', out)
        self.assertNotIn('protein', out)

    def test_retired_profile_commands_and_flags_are_rejected(self):
        for args in (('protein', 'Пекан', 'pecan'), ('protein-import', 'missing.json'),
                     ('protein-review',), ('add', 'Пекан', '--protein-profile', 'pecan'),
                     ('add', 'Пекан', '--protein-unknown', 'состав')):
            with self.subTest(args=args):
                self.cli(*args, ok=False)
        self.assertEqual(self.query('SELECT * FROM product'), [])
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE type='table' "
                                    "AND name IN ('protein_profile','product_protein_profile')"), [])

    def test_legacy_cleanup_preserves_food_and_remaining_reviews(self):
        self.add('--review-groups', 'рецепт', '--fat-unknown', 'масло', '--alias', 'орех')
        tables = ('product', 'alias', 'food_group', 'product_group', 'product_review')
        before = {t: self.query(f'SELECT * FROM {t} ORDER BY 1,2') for t in tables}
        with closing(sqlite3.connect(self.path)) as con, con:
            # Simulate the old review constraint and profile tables.
            con.execute('ALTER TABLE product_review RENAME TO old_reviews')
            con.execute('CREATE TABLE product_review (product_id INTEGER, field TEXT, '
                        'status TEXT, reason TEXT, PRIMARY KEY(product_id,field))')
            con.execute('INSERT INTO product_review SELECT * FROM old_reviews')
            con.execute('DROP TABLE old_reviews')
            con.execute("INSERT INTO product_review VALUES (1,'protein','pending','старый профиль')")
            con.execute('CREATE TABLE protein_profile (name TEXT PRIMARY KEY, amino_json TEXT)')
            con.execute('CREATE TABLE product_protein_profile '
                        '(product_id INTEGER REFERENCES product(id), '
                        'profile_name TEXT REFERENCES protein_profile(name))')
            con.execute("INSERT INTO protein_profile VALUES ('pecan','{}')")
            con.execute("INSERT INTO product_protein_profile VALUES (1,'pecan')")
        # Read-only logging must also tolerate a catalog before the migration.
        warnings = '\n'.join(warning_lines(['орех'], self.path))
        self.assertEqual(warnings, '')
        self.assertNotIn('старый профиль', warnings)
        for _ in range(2):  # migration is idempotent
            self.cli('choices')
            self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE type='table' "
                                        "AND name IN ('protein_profile','product_protein_profile')"), [])
            for table in tables:
                self.assertEqual(self.query(f'SELECT * FROM {table} ORDER BY 1,2'), before[table])

    def test_fat_threshold_uses_real_serving_and_calorie_share(self):
        # 6 g/100 g but only 0.6 g/serving, and <30% kcal: neutral default is allowed.
        self.cli('add', 'Порционный', '--port', '100г', '--serving', '10г', '--k', '400',
                 '--b', '0', '--zh', '6', '--u', '80', '--group', 'обработка=1')
        # Significant fat without source becomes accepted unknown.
        self.cli('add', 'Крупный', '--port', '100г', '--k', '400', '--b', '0', '--zh', '6',
                 '--u', '80', '--group', 'обработка=1')
        # Small grams, high share: also requires a decision.
        self.cli('add', 'Жирный', '--port', '10г', '--k', '30', '--b', '0', '--zh', '2',
                 '--u', '3', '--group', 'обработка=1')
        self.assertEqual(self.query("SELECT count(*) FROM product_review WHERE field='fat' AND status='unknown'"), [(2,)])

    def test_logging_unknown_recipe_has_no_warning_and_preserves_catalog(self):
        self.add('--review-groups', 'неизвестен рецепт', '--fat-quality', 'good',
                 '--alias', 'орех')
        diary = self.root / '2026' / '11' / '04.md'
        diary.parent.mkdir(parents=True)
        diary.write_text('# 04-11-2026-W45 | Режим: похудение\n\n## План\n\n'
                         '| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |\n'
                         '|---|---|---|---|---|---|---|\n')
        before = self.query('SELECT * FROM product_review')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/log.py'), str(diary),
                                 'орех:20', '--time', '12:00'],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('неизвестен рецепт', result.stdout)
        self.assertNotIn('разметка групп', result.stdout)
        self.assertIn('Пекан 20г', diary.read_text())
        self.assertEqual(self.query('SELECT * FROM product_review'), before)

    def test_estimates_are_independent_and_partial_updates_preserve_reasons(self):
        self.complete('--groups-estimate', 'типичный состав', '--fat-estimate', 'типичная готовка')
        self.assertEqual(self.query('SELECT estimated,review FROM product'), [(0, 0)])
        self.assertEqual(self.query('SELECT status FROM product_review'), [('estimated',), ('estimated',)])
        before = self.query('SELECT * FROM product_review')
        self.cli('add', 'Пекан', '--alias', 'орешек', '--estimate')
        self.assertEqual(self.query('SELECT * FROM product_review'), before)
        self.assertEqual(self.query('SELECT estimated FROM product'), [(1,)])
        self.cli('add', 'Пекан', '--no-estimate')
        self.assertEqual(self.query('SELECT estimated FROM product'), [(0,)])
        self.assertEqual(self.query('SELECT * FROM product_review'), before)
        self.cli('tag', 'Пекан', 'овощи=0.25')
        self.assertEqual(self.query('SELECT * FROM product_review'), before)
        self.cli('add', 'Пекан', '--group', 'орехи=1')
        self.assertEqual(self.query("SELECT field FROM product_review"), [('fat',)])

    def test_old_pending_schema_migrates_once_preserving_macros_and_reasons(self):
        self.complete('--alias', 'орех')
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute('DROP TABLE product_review')
            con.execute("""CREATE TABLE product_review (
                product_id INTEGER, field TEXT, status TEXT CHECK(status IN ('pending','exempt')),
                reason TEXT, PRIMARY KEY(product_id,field))""")
            con.execute("INSERT INTO product_review VALUES (1,'groups','pending','рецепт неизвестен')")
            con.execute("INSERT INTO product_review VALUES (1,'fat','pending','масло неизвестно')")
            con.execute('UPDATE product SET review=1')
            con.execute('DELETE FROM product_group')
        before = self.query('SELECT k,b,zh,u,estimated FROM product')
        for _ in range(2):
            self.cli('choices')
            self.assertEqual(self.query('SELECT k,b,zh,u,estimated FROM product'), before)
            self.assertEqual(self.query('SELECT review FROM product'), [(0,)])
            self.assertEqual(self.query('SELECT status,reason FROM product_review ORDER BY field'),
                             [('unknown', 'масло неизвестно'), ('unknown', 'рецепт неизвестен')])
            self.assertEqual(warning_lines(['орех'], self.path), [])

    def test_estimated_groups_contribute_without_routine_reminders(self):
        self.cli('add', 'Суп', '--port', '100г', '--k', '47', '--b', '2', '--zh', '3', '--u', '3',
                 '--group', 'птица=0.05', '--group', 'овощи=0.25',
                 '--groups-estimate', 'типичный жидкий суп', '--fat-unknown', 'масло неизвестно')
        ref = date(2026, 11, 4)
        diary = self.root / 'day.md'
        diary.write_text('| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |\n'
                         '|---|---|---|---|---|---|---|\n'
                         '| 12:00 | Суп 300г | 141 | 6 | 9 | 9 | 0 |\n')
        with patch.object(summary, 'DB_PATH', self.path), patch.object(summary, 'diary_path', return_value=diary):
            servings, _ = summary.group_servings(ref, ref)
            self.assertAlmostEqual(servings['птица'], 0.15)
            self.assertAlmostEqual(servings['овощи'], 0.25)
            out = '\n'.join(summary.group_remainder_lines(ref))
            self.assertNotIn('оценена приблизительно', out)
            self.assertNotIn('типичный жидкий суп', out)
            self.assertNotIn('Жиры с неизвестным источником', out)


if __name__ == '__main__':
    unittest.main()
