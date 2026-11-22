"""Accepted assumptions stay available for inspection, not routine reminders."""
import json
import sqlite3
import unittest
from datetime import date, timedelta

import test_new_day_window as window


class QuietCatalog(unittest.TestCase):
    # Reuse the isolated CLI fixture without inheriting its window test cases.
    run_python = window.NewDayWindow.run_python
    command = window.NewDayWindow.command
    evaluate = window.NewDayWindow.evaluate
    diary = window.NewDayWindow.diary
    make_day = window.NewDayWindow.make_day
    new_day = window.NewDayWindow.new_day
    snapshot = window.NewDayWindow.snapshot

    def setUp(self):
        window.NewDayWindow.setUp(self)
        self.day = date(2026, 11, 15)
        self.make_day(self.day)
        (self.root / 'today.md').symlink_to(self.diary(self.day).relative_to(self.root))
        self.command('db', 'add', 'Estimated', '--port', '100г', '--k', '100',
                     '--b', '0', '--zh', '10', '--u', '2.5', '--group', 'птица=0.1',
                     '--groups-estimate', 'assumed chicken fraction',
                     '--fat-quality', 'bad', '--fat-estimate', 'assumed frying')
        self.command('db', 'add', 'Unknown', '--port', '100г', '--k', '100',
                     '--b', '0', '--zh', '10', '--u', '2.5',
                     '--groups-unknown', 'composition unavailable',
                     '--fat-unknown', 'oil unavailable')
        self.logged = self.command('log.py', self.diary(self.day),
                                   'Good:100', 'Estimated:300', 'Unknown:100',
                                   '--time', '12:00')

    def assert_quiet(self, text):
        for phrase in ('Часть вкладов', 'типичному способу приготовления',
                       'Учёт групп неполный', 'Жиры с неизвестным источником',
                       'assumed chicken fraction', 'assumed frying',
                       'composition unavailable', 'oil unavailable',
                       'покажи допущения', '≈'):
            self.assertNotIn(phrase, text)

    def test_routine_output_preserves_numbers_and_records(self):
        before = self.snapshot()
        self.assert_quiet(self.logged)
        report = self.command('summary.py', 'weektrend', self.day)
        self.assert_quiet(report)
        self.assertRegex(report, r'птица:\s+✓ 0.3/4')
        self.assertRegex(report, r'молочка:\s+\S+ 1.0/7')
        self.assertIn('bad: ✗ 60%', report)
        values = json.loads(self.evaluate(
            "import summary, json; d=date(2026,11,15); "
            "print(json.dumps([summary.group_servings(d,d)[0], "
            "summary.fat_quality_grams(d,d)]))"))
        self.assertAlmostEqual(values[0]['птица'], 0.3)
        self.assertEqual(values[0]['молочка'], 1)
        self.assertEqual(values[1], {'good': 10, 'bad': 30, 'neutral': 10})
        self.assertEqual(before, self.snapshot())

    def test_new_day_stays_quiet_with_previous_seven_day_window(self):
        report = self.new_day(self.day + timedelta(days=1))
        self.assert_quiet(report)
        self.assertIn('7 дней 2026-11-09..2026-11-15', report)
        self.assertRegex(report, r'птица:\s+✓ 0.3/4')
        self.assertIn('bad: ✗ 60%', report)

    def test_explicit_inspection_retains_reasons_and_statuses(self):
        before = self.snapshot()
        self.command('summary.py', 'weektrend', self.day)
        report = self.command('db', 'review', '--all')
        for line in ('Estimated: groups / estimated — assumed chicken fraction',
                     'Estimated: fat / estimated — assumed frying',
                     'Unknown: groups / unknown — composition unavailable',
                     'Unknown: fat / unknown — oil unavailable'):
            self.assertIn(line, report)
        self.assertIn('требуют проверки: 0/', report)
        self.assertEqual(before, self.snapshot())

    def test_unresolved_and_partial_diagnostics_survive(self):
        # Synthetic legacy state; no production catalog is opened.
        with sqlite3.connect(self.root / 'data/diet.db') as con:
            con.execute("UPDATE product SET review=1 WHERE name='Good'")
        path = self.diary(self.day)
        path.write_text(path.read_text() + '\n> Неполный день — synthetic fixture\n'
                        '| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |\n'
                        '|---|---|---|---|---|---|---|\n'
                        '| 13:00 | Unmatched 100г | 100 | 0 | 0 | 25 | 0 |\n')
        before = self.snapshot()
        report = self.command('summary.py', 'weektrend', self.day)
        self.assert_quiet(report)
        self.assertIn('Не привязаны к каталогу: 1 записей (Unmatched)', report)
        self.assertIn('⚠ Good: разметка групп не завершена', report)
        self.assertIn('В окне неполные дни (2026-11-15)', report)
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main()
