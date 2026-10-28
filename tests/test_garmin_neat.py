"""The NEAT top-up row must survive a day summary that trails the workout
upload, and a summary that undercounts the workouts must be reported, not
silently skipped.

Run: python3 -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import garmin_sync  # noqa: E402

DIARY = """# 27-10-2026-W44 (2026-10-26..2026-11-01) | Режим: похудение

## План

| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |
|---|---|---|---|---|---|---|
| 09:00 | Банан 100г | 89 | 1 | 0 | 23 | 3 |
{rows}
**Потреблено:** К89 | Б1 | Ж0 | У23 | Клет3
"""

RIDE = {"activityType": {"typeKey": "indoor_cycling"}, "activityName": "Indoor Cycling",
        "startTimeLocal": "2026-10-27 17:52:00", "duration": 2400.0,
        "calories": 300.0, "bmrCalories": 60.0}          # 240 active kcal, 17:52–18:32
RIDE_ROW = "| 18:40 | Вело Z2 40 мин | -240 | - | - | - | - |"
NEAT_ROW = "| 23:59 | Прочая активность (Garmin) | -{} | - | - | - | - |"


class Neat(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.user = self.dir / 'user.md'
        self.user.write_text('# user\n', encoding='utf-8')
        self.diary = self.dir / '2026-10-27.md'

    def run_apply(self, summary, activities, rows=()):
        self.diary.write_text(DIARY.format(rows=''.join(r + '\n' for r in rows)), encoding='utf-8')
        fixture = self.dir / 'fixture.json'
        fixture.write_text(json.dumps({'summary': summary, 'activities': activities, 'weight': {}}))
        out = []
        with mock.patch('builtins.print', lambda *a, **k: out.append(' '.join(map(str, a)))):
            garmin_sync.apply('2026-10-27', fixture=str(fixture), diary=str(self.diary), user=str(self.user))
        return '\n'.join(out), self.diary.read_text(encoding='utf-8')

    def neat_rows(self, text):
        return [l for l in text.splitlines() if 'Прочая активность' in l]

    def test_complete_tally_subtracts_the_whole_workout(self):
        report, text = self.run_apply(
            {'activeKilocalories': 500.0, 'wellnessEndTimeLocal': '2026-10-28T00:00:00.0'}, [RIDE], [RIDE_ROW])
        self.assertIn('+ Прочая активность (Garmin) −260', report)
        self.assertNotIn('повторить синк', report)
        self.assertEqual(len(self.neat_rows(text)), 1)

    def test_tally_before_the_workout_subtracts_nothing(self):
        report, text = self.run_apply(
            {'activeKilocalories': 250.0, 'wellnessEndTimeLocal': '2026-10-27T17:30:00.0'}, [RIDE], [RIDE_ROW])
        self.assertIn('+ Прочая активность (Garmin) −250', report)
        self.assertIn('часы досинкались до 17:30', report)
        self.assertIn('Вело Z2 40 мин (17:52) ещё не в итоге дня', report)
        self.assertEqual(len(self.neat_rows(text)), 1)

    def test_tally_mid_workout_subtracts_the_covered_part(self):
        report, _ = self.run_apply(
            {'activeKilocalories': 400.0, 'wellnessEndTimeLocal': '2026-10-27T18:12:00.0'}, [RIDE], [RIDE_ROW])
        self.assertIn('+ Прочая активность (Garmin) −280', report)   # 400 − 240·½
        self.assertIn('повторить синк позже', report)

    def test_workout_logged_by_the_same_apply_is_treated_alike(self):
        report, text = self.run_apply(
            {'activeKilocalories': 250.0, 'wellnessEndTimeLocal': '2026-10-27T17:30:00.0'}, [RIDE])
        self.assertIn('+ Вело Z2 40 мин −240', report)
        self.assertIn('+ Прочая активность (Garmin) −250', report)
        self.assertIn('Вело Z2 40 мин', text)

    def test_tally_lower_than_workouts_is_reported(self):
        report, text = self.run_apply(
            {'activeKilocalories': 200.0, 'wellnessEndTimeLocal': '2026-10-28T00:00:00.0'}, [RIDE], [RIDE_ROW])
        self.assertIn('докрутки нет: часы за день 200 ккал ≤ тренировки в дневнике 240', report)
        self.assertIn('выше часов на 40', report)
        self.assertEqual(self.neat_rows(text), [])

    def test_existing_row_removed_when_complete_tally_is_lower(self):
        report, text = self.run_apply(
            {'activeKilocalories': 200.0, 'wellnessEndTimeLocal': '2026-10-28T00:00:00.0'}, [RIDE],
            [RIDE_ROW, NEAT_ROW.format(80)])
        self.assertIn('Прочая активность (Garmin) убрана (было −80)', report)
        self.assertEqual(self.neat_rows(text), [])
        self.assertNotRegex(text, r'--\d')

    def test_existing_row_kept_when_tally_trails_the_workout(self):
        report, text = self.run_apply(
            {'activeKilocalories': 230.0, 'wellnessEndTimeLocal': '2026-10-27T17:30:00.0'}, [RIDE],
            [RIDE_ROW, NEAT_ROW.format(200)])
        self.assertIn('Прочая активность (Garmin) −230 (было −200)', report)
        self.assertEqual(len(self.neat_rows(text)), 1)
        self.assertIn('| -230 |', text)

    def test_manual_fix_row_is_never_touched(self):
        report, text = self.run_apply(
            {'activeKilocalories': 200.0, 'wellnessEndTimeLocal': '2026-10-28T00:00:00.0'}, [RIDE],
            [RIDE_ROW, "| 23:59 | Прочая активность (Garmin), ручной фикс | -150 | - | - | - | - |"])
        self.assertIn('ручной фикс, не трогаю', report)
        self.assertIn('| -150 |', text)

    def test_no_wellness_end_means_complete(self):
        self.assertEqual(garmin_sync.covered_share(RIDE, None), 1.0)
        self.assertEqual(garmin_sync.covered_share(RIDE, '2026-10-27T18:32:00.0'), 1.0)
        self.assertEqual(garmin_sync.covered_share(RIDE, '2026-10-27T17:00:00.0'), 0.0)
        self.assertAlmostEqual(garmin_sync.covered_share(RIDE, '2026-10-27T18:02:00.0'), 0.25)


if __name__ == '__main__':
    unittest.main()
