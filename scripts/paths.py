"""Central path resolver for diet_log scripts.

All data lives outside scripts/. Scripts compute paths from the repo ROOT
(scripts/'s parent), never from their own dir, so moving a script never
re-points its data. Import from here instead of recomputing BASE locally.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config'
DATA = ROOT / 'data'
DIARIES = ROOT / 'diaries'

DB_PATH = DATA / 'diet.db'
PROFILE_PATH = DATA / 'profile.json'
GOALS = CONFIG / 'goals.md'
CYCLE = CONFIG / 'cycle.md'
USER = CONFIG / 'user.md'
TRAINING_TYPES = CONFIG / 'training_types.md'
MEDICATIONS = CONFIG / 'medications.md'


def diary_path(d):
    """Diary file path for a date: diaries/YYYY/MM/DD.md."""
    return DIARIES / str(d.year) / f'{d.month:02d}' / f'{d.day:02d}.md'
