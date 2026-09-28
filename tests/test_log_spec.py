"""log.py product specs: every spelling of a weight the agent writes must parse.

"Фреш пай:75г" used to fall through to a catalog lookup of the whole string and
fail as «продукт не найден» — the colon form accepted no unit.

Run: python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from log import split_spec  # noqa: E402


class SplitSpec(unittest.TestCase):
    def test_weight_spellings(self):
        for spec in ('Фреш пай:75', 'Фреш пай:75г', 'Фреш пай: 75 гр', 'Фреш пай:75G',
                     'Фреш пай 75', 'Фреш пай 75г', '75г Фреш пай'):
            with self.subTest(spec=spec):
                self.assertEqual(split_spec(spec), ('Фреш пай', 75.0))

    def test_decimal_comma(self):
        self.assertEqual(split_spec('Молоко:120,5г'), ('Молоко', 120.5))

    def test_no_weight(self):
        self.assertEqual(split_spec('Экспонента'), ('Экспонента', None))

    def test_digits_inside_name_stay_in_name(self):
        self.assertEqual(split_spec('Молоко 2.5%'), ('Молоко 2.5%', None))


if __name__ == '__main__':
    unittest.main()
