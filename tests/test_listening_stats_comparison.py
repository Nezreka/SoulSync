"""A listening stat needs something to be measured against (stats P1).

The stats page printed ten totals and no comparisons — "1,247 plays" with
nothing to say whether that is a lot. A total with no reference point is
trivia; the delta is what makes it a signal.

The two windows share ONE query body (`_listening_overview`) on purpose. Two
hand-written aggregates for one concept is how a "vs last month" delta ends up
comparing subtly different things and lying with confidence.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    database = MusicDatabase(str(tmp_path / 'stats.db'))
    yield database


def _play(db, *, title, artist, days_ago, duration_ms=180000, album='A'):
    """Insert one play N days in the past."""
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO listening_history (track_id, title, artist, album, played_at, duration_ms) "
        f"VALUES (?, ?, ?, ?, datetime('now', '-{days_ago} days'), ?)",
        (f'{title}-{days_ago}', title, artist, album, duration_ms))
    conn.commit()
    conn.close()


# ── the window itself ────────────────────────────────────────────────────────

def test_the_previous_window_is_the_period_before_this_one(db):
    """7d compares against days 7-14, NOT days 0-14 — an overlapping window
    would count this week's plays on both sides and flatten every delta."""
    _play(db, title='now', artist='A', days_ago=2)      # inside 7d
    _play(db, title='prev', artist='B', days_ago=10)    # inside the previous 7d
    _play(db, title='old', artist='C', days_ago=40)     # outside both

    current = db.get_listening_stats('7d')
    previous = db.get_listening_stats_previous('7d')

    assert current['total_plays'] == 1
    assert previous['total_plays'] == 1, 'the previous window missed its own play'


def test_the_windows_do_not_overlap(db):
    """The boundary case: a play at 8 days old belongs to the PREVIOUS window
    only. Counting it twice is the easy mistake and it inflates nothing
    visibly — the delta just quietly becomes wrong."""
    _play(db, title='boundary', artist='A', days_ago=8)

    assert db.get_listening_stats('7d')['total_plays'] == 0
    assert db.get_listening_stats_previous('7d')['total_plays'] == 1


@pytest.mark.parametrize('time_range', ['7d', '30d', '12m'])
def test_every_bounded_range_has_a_previous_window(db, time_range):
    assert db.get_listening_stats_previous(time_range) is not None


def test_all_has_no_previous_window(db):
    """There is no period before everything. Returning None makes the UI omit
    the comparison; returning zeros would render '↑100%' against nothing."""
    _play(db, title='x', artist='A', days_ago=3)

    assert db.get_listening_stats_previous('all') is None


def test_an_unknown_range_has_no_previous_window(db):
    assert db.get_listening_stats_previous('bogus') is None


# ── the shape stays identical to the current window ──────────────────────────

def test_previous_carries_every_field_the_current_window_does(db):
    """The UI pairs these key by key. A missing field would render a delta for
    some tiles and not others, with no obvious reason why."""
    _play(db, title='a', artist='A', days_ago=10)

    current = db.get_listening_stats('7d')
    previous = db.get_listening_stats_previous('7d')

    assert set(previous) == set(current)


def test_an_empty_previous_window_is_zeros_not_none(db):
    """No plays last week is a real answer — 'up from nothing' — and different
    from 'there is no last week'. Only the latter is None."""
    _play(db, title='only-now', artist='A', days_ago=1)

    previous = db.get_listening_stats_previous('7d')

    assert previous is not None
    assert previous['total_plays'] == 0


# ── the aggregate itself is the same computation on both sides ───────────────

def test_both_windows_count_the_same_way(db):
    """Same plays, shifted in time, must produce identical aggregates. This is
    what the shared query body buys: if the two ever diverge, every delta on
    the page is wrong by that difference."""
    for days in (1, 2, 3):
        _play(db, title=f't{days}', artist=f'Artist{days}', days_ago=days,
              duration_ms=120000, album=f'Album{days}')
    for days in (8, 9, 10):
        _play(db, title=f't{days}', artist=f'Artist{days}', days_ago=days,
              duration_ms=120000, album=f'Album{days}')

    current = db.get_listening_stats('7d')
    previous = db.get_listening_stats_previous('7d')

    assert current == previous, f'the two windows disagree: {current} vs {previous}'


def test_duration_and_uniques_are_summed_for_the_previous_window_too(db):
    _play(db, title='a', artist='Same', days_ago=9, duration_ms=100, album='X')
    _play(db, title='b', artist='Same', days_ago=10, duration_ms=200, album='X')

    previous = db.get_listening_stats_previous('7d')

    assert previous['total_plays'] == 2
    assert previous['total_time_ms'] == 300
    assert previous['unique_artists'] == 1
    assert previous['unique_albums'] == 1
    assert previous['unique_tracks'] == 2
