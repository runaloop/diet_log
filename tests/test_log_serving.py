"""log.py without a weight: a product's serving_g wins over its catalog portion.

The catalog gives macros per 100г for most products; logging «печенье» without
grams must take one real serving, not 100г.

Run: python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from log import scale  # noqa: E402

BASE = {'k': 400.0, 'b': 10.0, 'zh': 20.0, 'u': 50.0, 'fiber': 2.0}


def rec(**kw):
    return {'name': 'Печенье', 'portion_raw': '100г', 'portion_g': 100.0,
            'serving_g': None, **BASE, **kw}


class Serving(unittest.TestCase):
    def test_serving_used_when_no_weight(self):
        label, m = scale(rec(serving_g=25.0), None)
        self.assertEqual(label, 'Печенье 25г')
        self.assertAlmostEqual(m['k'], 100.0)

    def test_explicit_weight_beats_serving(self):
        label, m = scale(rec(serving_g=25.0), 50)
        self.assertEqual(label, 'Печенье 50г')
        self.assertAlmostEqual(m['k'], 200.0)

    def test_no_serving_falls_back_to_catalog_portion(self):
        label, m = scale(rec(), None)
        self.assertEqual(label, 'Печенье 100г')
        self.assertAlmostEqual(m['k'], 400.0)

    def test_serving_ignored_for_piece_portion(self):
        label, m = scale(rec(portion_raw='1 шт', portion_g=None, serving_g=25.0), None)
        self.assertEqual(label, 'Печенье 1 шт')
        self.assertAlmostEqual(m['k'], 400.0)


if __name__ == '__main__':
    unittest.main()
