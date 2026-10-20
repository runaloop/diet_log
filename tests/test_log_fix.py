"""log.py --fix / --delete: a row is picked by label substring, optionally pinned
by a leading time; anything but exactly one match must leave the diary alone.

Run: python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from log import table_rows, match_rows  # noqa: E402

DIARY = """# 19-10-2026-W43 (2026-10-19..2026-10-25) | Режим: похудение

## План

| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |
|---|---|---|---|---|---|---|
| 09:33 | Экспонента 250г | 150 | 30 | 0 | 6 | 0 |
| 09:33 | Банан 100г | 89 | 1 | 0 | 23 | 3 |
| 12:53 | Плавание интервалы Z4 60 мин | -481 | - | - | - | - |
| 13:19 | Экспонента 250г | 150 | 30 | 0 | 6 | 0 |
| 18:12 | Мороженое с чёрной смородиной 100г | 287 | 4 | 17 | 30 | 0 |

**Потреблено:** К676 | Б65 | Ж17 | У65 | Клет3
""".split('\n')


class MatchRows(unittest.TestCase):
    def setUp(self):
        self.rows = table_rows(DIARY)

    def labels(self, query):
        return [(t, label) for _, t, label in match_rows(self.rows, query)]

    def test_table_rows_skip_header_and_footer(self):
        self.assertEqual(len(self.rows), 5)
        self.assertEqual(DIARY[self.rows[0][0]].split('|')[2].strip(), 'Экспонента 250г')

    def test_case_insensitive_substring(self):
        self.assertEqual(self.labels('банан'), [('09:33', 'Банан 100г')])

    def test_activity_row(self):
        self.assertEqual(self.labels('плавание'), [('12:53', 'Плавание интервалы Z4 60 мин')])

    def test_duplicates_need_time(self):
        self.assertEqual(len(self.labels('Экспонента')), 2)
        self.assertEqual(self.labels('13:19 Экспонента'), [('13:19', 'Экспонента 250г')])

    def test_yo_equals_ye(self):
        self.assertEqual(len(self.labels('черной смородиной')), 1)

    def test_no_match(self):
        self.assertEqual(self.labels('борщ'), [])
        self.assertEqual(self.labels('10:00 Банан'), [])


if __name__ == '__main__':
    unittest.main()
