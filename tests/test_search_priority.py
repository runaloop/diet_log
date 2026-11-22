"""Search display priority through the real CLI, with isolated catalogs/diaries."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import catalog_match


class MatchPriority(unittest.TestCase):
    def test_priority_orders_equal_matches_without_selecting_one(self):
        entries = [('Йогурт ванильный', ['Йогурт ванильный']),
                   ('Йогурт клубничный', ['Йогурт клубничный'])]
        priorities = {'Йогурт клубничный': 10}
        ranked = catalog_match.rank('йогрт', entries, priorities)
        self.assertEqual([row[2] for row in ranked],
                         ['Йогурт клубничный', 'Йогурт ванильный'])
        self.assertEqual(catalog_match.best('йогрт', entries, priorities),
                         (None, ['Йогурт клубничный', 'Йогурт ванильный']))

    def test_match_quality_and_coverage_precede_priority(self):
        cases = [
            ('Йогрт ванильный', (None, ['Йогурт ванильный', 'Йогрт ванильный'])),
            ('Йогурт ванильный натуральный', ('Йогурт ванильный', [])),
        ]
        for weaker, expected in cases:
            with self.subTest(weaker=weaker):
                entries = [('Йогурт ванильный', ['Йогурт ванильный']),
                           (weaker, [weaker])]
                priorities = {weaker: 100}
                self.assertEqual(catalog_match.rank('йогурт ванильный', entries,
                                                    priorities)[0][2], 'Йогурт ванильный')
                self.assertEqual(catalog_match.best('йогурт ванильный', entries,
                                                    priorities), expected)

    def test_exact_word_ties_remain_ambiguous_in_priority_order(self):
        entries = [('Первый', ['йогурт натуральный']),
                   ('Второй', ['натуральный йогурт'])]
        self.assertEqual(catalog_match.exact_words(
            'натуральный йогурт', entries, {'Первый': 10}),
            (None, ['Первый', 'Второй']))


class SearchPriorityCLI(unittest.TestCase):
    FIRST = 'Йогурт ванильный'
    SECOND = 'Йогурт клубничный'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'catalog.db'
        self.env = dict(os.environ, DIET_DB=str(self.path))
        self.cli('init')
        for name in (self.FIRST, self.SECOND):
            self.cli('add', name, '--port', '100г', '--k', '70', '--b', '8',
                     '--zh', '2', '--u', '5', '--serving', '230г',
                     '--group', 'молочка=1', '--fat-quality', 'neutral')
        self.diary = self.root / 'day.md'
        self.diary.write_text('# Проверка | Режим: похудение\n\n', encoding='utf-8')

    def run_script(self, script, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *args],
                                env=self.env, capture_output=True, text=True)
        output = result.stdout + result.stderr
        if ok:
            self.assertEqual(result.returncode, 0, output)
        else:
            self.assertNotEqual(result.returncode, 0, output)
            self.assertNotIn('Traceback', output)
        return output

    def cli(self, *args, ok=True):
        return self.run_script('db', *args, ok=ok)

    def find_names(self, query):
        return [line.split(' [', 1)[0] for line in self.cli('find', query).splitlines()]

    def prefer_second(self):
        self.cli('search-priority', self.SECOND, '10')

    def test_substring_order_negative_priorities_and_reset(self):
        self.assertEqual(self.find_names('йогурт'), [self.FIRST, self.SECOND])
        self.prefer_second()
        self.assertEqual(self.find_names('йогурт'), [self.SECOND, self.FIRST])
        self.assertIn('приоритет поиска:10', self.cli('find', 'йогурт'))
        self.cli('search-priority', self.SECOND, '-5')
        self.assertEqual(self.find_names('йогурт'), [self.FIRST, self.SECOND])
        self.cli('search-priority', self.SECOND, '0')
        self.assertNotIn('приоритет поиска:', self.cli('find', 'йогурт'))
        self.cli('search-priority', self.FIRST, '10')
        self.prefer_second()
        self.assertEqual(self.find_names('йогурт'), [self.FIRST, self.SECOND])

    def test_add_flag_partial_updates_and_planner_priority_are_independent(self):
        self.cli('add', self.SECOND, '--search-priority', '10', '--priority', '-3')
        self.cli('add', self.SECOND, '--alias', 'ягодный')
        self.cli('priority', self.FIRST, '100')
        rows = self.cli('q', 'select name,priority,search_priority,k,serving_g from product order by name')
        self.assertIn(f'{self.FIRST}\t100\t0\t70.0\t230.0', rows)
        self.assertIn(f'{self.SECOND}\t-3\t10\t70.0\t230.0', rows)
        self.assertEqual(self.find_names('йогурт'), [self.SECOND, self.FIRST])
        self.cli('add', self.SECOND, '--search-priority', '0')
        self.assertEqual(self.find_names('йогурт'), [self.FIRST, self.SECOND])
        self.cli('add', 'Йогурт персиковый', '--port', '100г', '--k', '70',
                 '--b', '8', '--zh', '2', '--u', '5', '--group', 'молочка=1',
                 '--search-priority', '20')
        self.assertEqual(self.find_names('йогурт')[0], 'Йогурт персиковый')

    def test_fuzzy_order_uses_priority_only_after_match_quality(self):
        self.prefer_second()
        self.assertEqual(self.find_names('йогрт'), [self.SECOND, self.FIRST])
        self.assertEqual(self.find_names('ванильный йогурт'), [self.FIRST])
        self.cli('add', 'Йогурт ванильный натуральный', '--port', '100г',
                 '--k', '70', '--b', '8', '--zh', '2', '--u', '5',
                 '--group', 'молочка=1', '--search-priority', '100')
        self.assertEqual(self.find_names('ванильный йогрт')[0], self.FIRST)

    def test_exact_names_and_aliases_keep_precedence(self):
        self.prefer_second()
        self.assertEqual(self.find_names(self.FIRST), [self.FIRST])
        self.cli('add', self.FIRST, '--alias', 'йогурт')
        self.assertEqual(self.find_names('йогурт'), [self.FIRST])
        output = self.run_script('log.py', str(self.diary), 'йогурт', '--time', '12:00')
        self.assertIn(self.FIRST + ' 230г', output)
        self.assertNotIn(self.SECOND, output)
        self.run_script('validate_diary.py', str(self.diary))

    def test_ordered_ambiguity_never_writes_a_diary_or_availability(self):
        self.prefer_second()
        before = self.diary.read_bytes()
        for query in ('йогурт', 'йогрт'):
            with self.subTest(query=query):
                output = self.run_script('log.py', str(self.diary), query, ok=False)
                self.assertIn('неоднозначно', output)
                self.assertLess(output.index(self.SECOND), output.index(self.FIRST))
                self.assertEqual(self.diary.read_bytes(), before)
                output = self.cli('avail', query, 'stock', ok=False)
                self.assertLess(output.index(self.SECOND), output.index(self.FIRST))
        self.assertEqual(self.cli('q', 'select count(*) as n from product where avail is not null'), 'n\n0\n')

    def test_exact_word_ambiguity_in_find_and_log_is_ordered(self):
        self.cli('add', self.FIRST, '--alias', 'йогурт вкусный')
        self.cli('add', self.SECOND, '--alias', 'вкусный йогурт')
        self.prefer_second()
        before = self.diary.read_bytes()
        output = self.cli('find', 'вкусный, йогурт')
        self.assertIn('неоднозначно', output)
        self.assertLess(output.index(self.SECOND), output.index(self.FIRST))
        output = self.run_script('log.py', str(self.diary), 'вкусный, йогурт', ok=False)
        self.assertLess(output.index(self.SECOND), output.index(self.FIRST))
        self.assertEqual(self.diary.read_bytes(), before)

    def test_invalid_priorities_and_missing_products_leave_data_unchanged(self):
        before = self.cli('q', 'select name,priority,search_priority from product order by name')
        for args in (('search-priority', 'Нет такого', '10'),
                     ('search-priority', self.SECOND, '1.5'),
                     ('add', self.SECOND, '--search-priority', 'nan')):
            with self.subTest(args=args):
                self.cli(*args, ok=False)
                self.assertEqual(self.cli('q', 'select name,priority,search_priority from product order by name'), before)

    def test_legacy_catalog_logs_without_migration_then_migrates_idempotently(self):
        self.cli('add', self.FIRST, '--alias', 'любимый')
        self.cli('priority', self.FIRST, '7')
        before = self.cli('q', 'select name,priority,k,serving_g from product order by name')
        # A temporary legacy fixture; readers must not write schema migrations.
        with sqlite3.connect(self.path) as con:
            con.execute('ALTER TABLE product DROP COLUMN search_priority')
        legacy = self.path.read_bytes()
        output = self.run_script('log.py', str(self.diary), 'любимый', '--time', '12:00')
        self.assertIn(self.FIRST + ' 230г', output)
        self.assertEqual(self.path.read_bytes(), legacy)
        for _ in range(2):
            self.cli('init')
            self.assertEqual(self.cli('q', 'select name,priority,k,serving_g from product order by name'), before)
            self.assertEqual(self.cli('q', 'select sum(search_priority) as n from product'), 'n\n0\n')
        self.prefer_second()
        self.assertEqual(self.find_names('йогурт'), [self.SECOND, self.FIRST])


if __name__ == '__main__':
    unittest.main()
