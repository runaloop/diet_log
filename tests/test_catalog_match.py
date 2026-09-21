"""Fuzzy catalog lookup: the corpus is real diary phrasing, typos included.

Two halves matter equally. The first says the search finally reaches products
it used to miss. The second says it still refuses to guess — a silently wrong
product corrupts a diary quietly, while a refusal is visible and cheap.

Run: python3 -m unittest discover -s tests
"""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import catalog_match  # noqa: E402
from paths import DB_PATH  # noqa: E402


def live_entries():
    """The real catalog, read-only — thresholds are only meaningful against it."""
    con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    strings = {}
    for name, alias in con.execute(
            """SELECT p.name, a.text FROM product p
               LEFT JOIN alias a ON a.product_id = p.id"""):
        bucket = strings.setdefault(name, [name])
        if alias:
            bucket.append(alias)
    con.close()
    return list(strings.items())


class TokenScore(unittest.TestCase):
    def test_identical_words_score_one(self):
        self.assertEqual(catalog_match.token_score('овсянка', 'овсянка'), 1.0)

    def test_measured_gap_between_typos_and_different_words(self):
        """The threshold sits in a gap that was measured, not picked."""
        typos = [('бострв', 'быстров'), ('эксненту', 'экспонента'),
                 ('авсянка', 'овсянка'), ('грени', 'гренни'), ('чери', 'черри')]
        others = [('грени', 'греча'), ('смит', 'сметана'), ('чери', 'творог')]
        worst_typo = min(catalog_match.token_score(a, b) for a, b in typos)
        best_other = max(catalog_match.token_score(a, b) for a, b in others)
        self.assertGreaterEqual(worst_typo, catalog_match.TOKEN_THRESHOLD)
        self.assertLess(best_other, catalog_match.TOKEN_THRESHOLD)
        self.assertGreater(worst_typo - best_other, 0.1)

    def test_numbers_must_match_exactly(self):
        """'20г белка' and '25г белка' are different products, not a typo."""
        self.assertEqual(catalog_match.token_score('20г', '25г'), 0.0)
        self.assertEqual(catalog_match.token_score('2026', '2025'), 0.0)

    def test_short_words_must_match_exactly(self):
        self.assertEqual(catalog_match.token_score('чиа', 'чай'), 0.0)
        self.assertEqual(catalog_match.token_score('чиа', 'чиа'), 1.0)


class Tokenize(unittest.TestCase):
    def test_splits_on_punctuation_and_lowercases(self):
        self.assertEqual(catalog_match.tokenize('Exponenta High-Pro 20г белка'),
                         ['exponenta', 'high', 'pro', '20г', 'белка'])

    def test_empty_input(self):
        self.assertEqual(catalog_match.tokenize(''), [])
        self.assertEqual(catalog_match.tokenize(None), [])


class FindsWhatItUsedToMiss(unittest.TestCase):
    """Every phrase here returned nothing under plain substring search."""

    def setUp(self):
        self.entries = live_entries()

    def assertResolves(self, query, expected):
        name, ties = catalog_match.best(query, self.entries)
        self.assertEqual(ties, [], f'{query!r} came back ambiguous')
        self.assertEqual(name, expected, f'{query!r} resolved to {name!r}')

    def test_typo_in_a_single_word(self):
        """Three catalog products carry the word «Экспонента», so similarity
        alone cannot choose: the matched word is identical in all of them.
        Coverage decides, and it picks the one whose whole name is that word
        — which is also what AGENTS.md's «экспонента» shorthand already means
        (a 250 g portion of product #10)."""
        self.assertResolves('эксненту', 'Экспонента')

    def test_typos_in_every_word(self):
        self.assertResolves('авсянка бострв', 'Овсянка по-новому Быстров')

    def test_word_order_alone(self):
        """No typo at all — the catalog name is «Овсянка по-новому Быстров»."""
        self.assertResolves('овсянка быстров', 'Овсянка по-новому Быстров')

    def test_typo_inside_a_multiword_name(self):
        self.assertResolves('яблоко грени смит', 'Яблоко Гренни Смит')

    def test_doubled_letter_dropped(self):
        self.assertResolves('помидоры чери', 'Помидоры черри')


class CoverageBreaksIdenticalWordTies(unittest.TestCase):
    """When the matched word is the same, prefer the product the query names
    in full over one where it is a fragment of a longer name."""

    def test_full_name_beats_a_longer_one(self):
        entries = [('Экспонента', ['экспонента']),
                   ('Экспонента Био Скир', ['экспонента био скир'])]
        name, ties = catalog_match.best('экспонента', entries)
        self.assertEqual((name, ties), ('Экспонента', []))

    def test_equal_coverage_stays_ambiguous(self):
        entries = [('Стейк тунца', ['стейк тунца']),
                   ('Стейк форели', ['стейк форели'])]
        name, ties = catalog_match.best('стейк', entries)
        self.assertIsNone(name)
        self.assertEqual(ties, ['Стейк тунца', 'Стейк форели'])


class RefusesToGuess(unittest.TestCase):
    """A wrong pick is silent; a refusal is not. Keep it that way."""

    def setUp(self):
        self.entries = live_entries()

    def test_generic_word_matching_several_products_is_ambiguous(self):
        name, ties = catalog_match.best('чипсы', self.entries)
        self.assertIsNone(name)
        self.assertGreater(len(ties), 1)

    def test_generic_word_stays_ambiguous_for_steak(self):
        name, ties = catalog_match.best('стейк', self.entries)
        self.assertIsNone(name)
        self.assertGreater(len(ties), 1)

    def test_nonsense_matches_nothing(self):
        name, ties = catalog_match.best('щщщщщ', self.entries)
        self.assertIsNone(name)
        self.assertEqual(ties, [])

    def test_empty_query_matches_nothing(self):
        self.assertEqual(catalog_match.best('', self.entries), (None, []))

    def test_every_query_word_must_land(self):
        """One good word does not carry an unrelated one along."""
        name, ties = catalog_match.best('овсянка вертолёт', self.entries)
        self.assertIsNone(name)
        self.assertEqual(ties, [])

    def test_near_tie_is_reported_rather_than_broken(self):
        entries = [('Творог 5%', ['творог 5%']), ('Творог 9%', ['творог 9%'])]
        name, ties = catalog_match.best('творог', entries)
        self.assertIsNone(name)
        self.assertEqual(ties, ['Творог 5%', 'Творог 9%'])

    def test_clear_winner_is_returned(self):
        entries = [('Овсянка по-новому Быстров', ['овсянка по-новому быстров']),
                   ('Овсянка сырая', ['овсянка сырая'])]
        name, ties = catalog_match.best('овсянка быстров', entries)
        self.assertEqual((name, ties), ('Овсянка по-новому Быстров', []))


class AliasesAreSearchedSeparately(unittest.TestCase):
    def test_words_cannot_be_pooled_across_unrelated_aliases(self):
        """Each string is scored on its own, so two aliases cannot combine."""
        entries = [('Штука', ['совершенно другое', 'яблоко'])]
        name, ties = catalog_match.best('другое яблоко', entries)
        self.assertIsNone(name)
        self.assertEqual(ties, [])


if __name__ == '__main__':
    unittest.main()
