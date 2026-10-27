"""Garmin being down must not break the day change: the failed days are queued
in a pending file, and the next successful apply retries them and clears it.

Run: python3 -m unittest discover -s tests
"""
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import garmin_sync  # noqa: E402
import new_day  # noqa: E402
import paths  # noqa: E402

DIARY = """# 26-10-2026-W44 (2026-10-26..2026-11-01) | Режим: похудение

## План

| Время | Продукт/Активность | К | Б | Ж | У | Клетчатка |
|---|---|---|---|---|---|---|
| 09:00 | Банан 100г | 89 | 1 | 0 | 23 | 3 |

**Потреблено:** К89 | Б1 | Ж0 | У23 | Клет3
"""


class Down:
    def __init__(self):
        raise garmin_sync.GarminUnavailable('connectapi.garmin.com: timed out')


class PendingQueue(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.pending = self.dir / 'pending.json'
        self.user = self.dir / 'user.md'
        self.user.write_text('# user\n', encoding='utf-8')
        self.fixture = self.dir / 'fixture.json'
        self.fixture.write_text(json.dumps({'summary': {}, 'activities': [], 'weight': {}}))
        token = self.dir / 'tokens.json'
        token.write_text('{}')
        diary_of = lambda d: self.dir / f'{d.isoformat()}.md'
        for d in (date(2026, 10, 25), date(2026, 10, 26)):
            diary_of(d).write_text(DIARY, encoding='utf-8')
        for patch in (mock.patch.object(garmin_sync, 'TOKEN_PATH', token),
                      mock.patch.object(garmin_sync, 'PENDING_PATH', self.pending),
                      mock.patch.object(paths, 'diary_path', diary_of)):
            patch.start()
            self.addCleanup(patch.stop)

    def fail(self, day, back=1):
        with mock.patch.object(garmin_sync, 'Live', Down), \
                self.assertRaises(garmin_sync.GarminUnavailable):
            garmin_sync.apply(day, back=back, user=self.user)

    def test_failure_queues_the_window(self):
        self.fail('2026-10-26')
        self.assertEqual(sorted(garmin_sync.load_pending(self.pending)),
                         ['2026-10-25', '2026-10-26'])

    def test_success_retries_and_clears(self):
        self.fail('2026-10-25', back=0)
        out = []
        with mock.patch('builtins.print', lambda *a, **k: out.append(' '.join(map(str, a)))):
            garmin_sync.apply('2026-10-27', fixture=self.fixture, user=self.user,
                              pending=self.pending)
        self.assertIn('повтор из очереди: 2026-10-25', '\n'.join(out))
        self.assertFalse(self.pending.exists())

    def test_dry_run_keeps_the_queue(self):
        self.fail('2026-10-25', back=0)
        with mock.patch('builtins.print'):
            garmin_sync.apply('2026-10-27', fixture=self.fixture, user=self.user,
                              pending=self.pending, dry_run=True)
        self.assertEqual(list(garmin_sync.load_pending(self.pending)), ['2026-10-25'])

    def test_close_day_survives_garmin_down(self):
        path = self.dir / '2026-10-26.md'
        with mock.patch.object(garmin_sync, 'Live', Down), \
                mock.patch.object(new_day, 'rel', lambda p: Path(p).name):
            report, needs_human = new_day.close_day(date(2026, 10, 26), path, 2, False)
        text = '\n'.join(report)
        self.assertFalse(needs_human, text)
        self.assertIn('Garmin недоступен', text)
        self.assertIn('2026-10-26', text)
        self.assertIn('Базовый расход', path.read_text(encoding='utf-8'))


class Request(unittest.TestCase):
    def test_network_error_is_unavailable(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError('Name or service not known')
        with self.assertRaises(garmin_sync.GarminUnavailable):
            garmin_sync._request(opener, 'GET', 'https://connectapi.garmin.com/x')

    def test_5xx_is_unavailable(self):
        with self.assertRaises(garmin_sync.GarminUnavailable):
            garmin_sync._json_or_die(503, '<html>', 'daily')


if __name__ == '__main__':
    unittest.main()
