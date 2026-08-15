"""When you listen, and whether you keep listening (stats P3).

The page could say how MUCH you listened and never when. These two are the
first genuinely personal charts: the shape of a listening week, and listening
as a habit rather than a total.

TIMEZONE: played_at is stored as LOCAL naive wall-clock — the web player
writes datetime.now().isoformat(), plex_client writes item.viewedAt (also
local). So strftime('%H', played_at) is the hour the user actually listened,
which is what the chart means. These tests insert local-looking timestamps for
exactly that reason.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'clock.db'))


def _play_at(db, when: datetime, *, title='t', artist='A'):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO listening_history (track_id, title, artist, album, played_at, duration_ms) "
        "VALUES (?, ?, ?, 'Al', ?, 1000)",
        (f'{title}-{when.isoformat()}', title, artist, when.isoformat(sep=' ')))
    conn.commit()
    conn.close()


# ── the clock ────────────────────────────────────────────────────────────────

def test_the_grid_is_dense_even_with_no_plays(db):
    """A heatmap needs a value in every cell. Making the UI fill the gaps is
    how an empty hour becomes an undefined square."""
    clock = db.get_listening_clock('all')

    assert len(clock['grid']) == 7
    assert all(len(row) == 24 for row in clock['grid'])
    assert clock['total'] == 0
    assert clock['peak']['plays'] == 0


def test_a_play_lands_in_its_own_weekday_and_hour(db):
    # 2026-08-12 is a Wednesday -> strftime %w == 3.
    _play_at(db, datetime(2026, 8, 12, 21, 30))

    clock = db.get_listening_clock('all')

    assert clock['grid'][3][21] == 1
    assert clock['total'] == 1


def test_the_hour_is_local_wall_clock_not_shifted(db):
    """The whole point of the chart. A 21:00 play must read as 21, not as
    whatever 21:00 becomes in UTC."""
    _play_at(db, datetime(2026, 8, 12, 21, 0))

    clock = db.get_listening_clock('all')

    assert clock['grid'][3][21] == 1
    assert sum(sum(row) for row in clock['grid']) == 1, 'the play landed in two cells'


def test_the_peak_cell_is_the_busiest_one(db):
    for _ in range(3):
        _play_at(db, datetime(2026, 8, 12, 9, 0))
    _play_at(db, datetime(2026, 8, 13, 23, 0))

    peak = db.get_listening_clock('all')['peak']

    assert peak['weekday'] == 3 and peak['hour'] == 9 and peak['plays'] == 3


def test_midnight_and_late_night_are_distinct_cells(db):
    """Hour 0 must not be confused with "missing" — the classic off-by-one in
    a 24-column grid."""
    _play_at(db, datetime(2026, 8, 12, 0, 30))
    _play_at(db, datetime(2026, 8, 12, 23, 30))

    grid = db.get_listening_clock('all')['grid']

    assert grid[3][0] == 1
    assert grid[3][23] == 1


def test_an_unparseable_timestamp_does_not_break_the_grid(db):
    """strftime returns NULL for junk. Letting None index the grid would take
    the whole page down for one bad row."""
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO listening_history (track_id, title, artist, played_at, duration_ms) "
        "VALUES ('x', 'x', 'A', 'not-a-timestamp', 0)")
    conn.commit()
    conn.close()
    _play_at(db, datetime(2026, 8, 12, 10, 0))

    clock = db.get_listening_clock('all')

    assert clock['total'] == 1
    assert clock['grid'][3][10] == 1


# ── the rhythm ───────────────────────────────────────────────────────────────

def test_no_plays_is_all_zeros_not_a_crash(db):
    rhythm = db.get_listening_rhythm('all')

    assert rhythm['current_streak'] == 0
    assert rhythm['longest_streak'] == 0
    assert rhythm['busiest_day']['date'] is None


def test_consecutive_days_make_a_streak(db):
    today = date.today()
    for back in (2, 1, 0):
        _play_at(db, datetime.combine(today - timedelta(days=back), datetime.min.time())
                 + timedelta(hours=12))

    rhythm = db.get_listening_rhythm('all')

    assert rhythm['current_streak'] == 3
    assert rhythm['longest_streak'] == 3
    assert rhythm['active_days'] == 3


def test_a_gap_breaks_the_streak(db):
    today = date.today()
    for back in (10, 9, 8, 7, 1, 0):   # a 4-day run, then a 2-day run
        _play_at(db, datetime.combine(today - timedelta(days=back), datetime.min.time())
                 + timedelta(hours=12))

    rhythm = db.get_listening_rhythm('all')

    assert rhythm['longest_streak'] == 4
    assert rhythm['current_streak'] == 2


def test_today_with_no_plays_yet_does_not_break_a_streak(db):
    """A streak must not read as broken at 9am just because you have not put
    anything on today."""
    today = date.today()
    for back in (2, 1):
        _play_at(db, datetime.combine(today - timedelta(days=back), datetime.min.time())
                 + timedelta(hours=12))

    assert db.get_listening_rhythm('all')['current_streak'] == 2


def test_an_old_unbroken_run_is_not_a_current_streak(db):
    """Five days in a row last month is history, not a streak you are on."""
    today = date.today()
    for back in (34, 33, 32, 31, 30):
        _play_at(db, datetime.combine(today - timedelta(days=back), datetime.min.time())
                 + timedelta(hours=12))

    rhythm = db.get_listening_rhythm('all')

    assert rhythm['longest_streak'] == 5
    assert rhythm['current_streak'] == 0


def test_the_busiest_day_is_the_one_with_most_plays(db):
    today = date.today()
    base = datetime.combine(today - timedelta(days=3), datetime.min.time())
    for i in range(5):
        _play_at(db, base + timedelta(hours=10 + i), title=f'a{i}')
    _play_at(db, datetime.combine(today, datetime.min.time()) + timedelta(hours=10))

    busiest = db.get_listening_rhythm('all')['busiest_day']

    assert busiest['plays'] == 5
    assert busiest['date'] == (today - timedelta(days=3)).isoformat()


def test_many_plays_in_one_day_are_one_active_day(db):
    """Streaks count DAYS, not plays — a binge is one day, not twenty."""
    today = date.today()
    base = datetime.combine(today, datetime.min.time())
    for i in range(20):
        _play_at(db, base + timedelta(minutes=i * 5), title=f'b{i}')

    rhythm = db.get_listening_rhythm('all')

    assert rhythm['active_days'] == 1
    assert rhythm['current_streak'] == 1
