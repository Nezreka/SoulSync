"""Retry backoff for the video wishlist drain.

Boulder's install is the reason this exists: a 1999 film had been searched 959
times and 142 of 172 wished episodes were past 20 attempts, each one a ~20s
blocking Soulseek search repeated every hour. The row recorded
``search_attempts`` and ``last_search_at`` all along; nothing read them.

Two properties matter and both are tested against a REAL sqlite, because the
schedule exists twice - once in Python for humans, once in SQL for the query -
and a silent drift between them is the failure nobody would notice.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.video.wishlist_backoff import (FREE_ATTEMPTS, MAX_DELAY_HOURS,
                                          due_sql, retry_delay_hours)
from database.video_database import VideoDatabase


# ── the schedule itself ──────────────────────────────────────────────────────

def test_the_first_few_attempts_are_free():
    """A release genuinely can show up a day late; don't punish that."""
    for n in range(FREE_ATTEMPTS):
        assert retry_delay_hours(n) == 0


def test_the_wait_doubles_then_stops_at_a_week():
    assert retry_delay_hours(3) == 1
    assert retry_delay_hours(4) == 2
    assert retry_delay_hours(5) == 4
    assert retry_delay_hours(10) == 128
    assert retry_delay_hours(11) == MAX_DELAY_HOURS
    # The real numbers off Boulder's install. Weekly, not hourly.
    assert retry_delay_hours(388) == MAX_DELAY_HOURS
    assert retry_delay_hours(959) == MAX_DELAY_HOURS


@pytest.mark.parametrize("junk", [None, "", "x", -5, [], {}])
def test_junk_attempts_never_block_a_search(junk):
    assert retry_delay_hours(junk) == 0


def test_the_delay_never_goes_backwards():
    seen = [retry_delay_hours(n) for n in range(0, 80)]
    assert seen == sorted(seen)
    assert max(seen) == MAX_DELAY_HOURS


# ── the SQL says the same thing as the Python ────────────────────────────────

def _sql_says_due(attempts: int, hours_since_search: float) -> bool:
    """Run the real WHERE fragment against sqlite for one hypothetical row."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE w (search_attempts INTEGER, last_search_at TEXT)")
    conn.execute("INSERT INTO w VALUES (?, datetime('now', ?))",
                 (attempts, "-%f hours" % hours_since_search))
    row = conn.execute("SELECT 1 FROM w WHERE " + due_sql("w")).fetchone()
    conn.close()
    return row is not None


@pytest.mark.parametrize("attempts", [0, 1, 2, 3, 4, 5, 8, 11, 20, 64, 65, 388, 959])
def test_sql_and_python_agree_on_every_attempt_count(attempts):
    """The drift guard. 64+ matters specifically: sqlite's 1<<n falls to 0 past
    63 bits, which would silently mean 'no backoff' for the very rows that have
    failed the most."""
    wait = retry_delay_hours(attempts)
    if wait:
        assert not _sql_says_due(attempts, wait * 0.5), (
            "SQL let a row through %dh into a %dh wait" % (wait * 0.5, wait))
    assert _sql_says_due(attempts, wait + 1), (
        "SQL held a row back after its %dh wait had passed" % wait)


def test_a_row_never_searched_is_always_due():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE w (search_attempts INTEGER, last_search_at TEXT)")
    conn.execute("INSERT INTO w VALUES (999, NULL)")
    assert conn.execute("SELECT 1 FROM w WHERE " + due_sql("w")).fetchone() is not None
    conn.close()


# ── the drain queries actually use it ────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def _wish(db, kind, tmdb_id, attempts, hours_ago, **extra):
    cols = {"kind": kind, "tmdb_id": tmdb_id, "title": "T%s" % tmdb_id,
            "status": "wanted", "search_attempts": attempts}
    cols.update(extra)
    keys = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO video_wishlist (%s, last_search_at) VALUES (%s, "
                     "datetime('now', ?))" % (keys, marks),
                     list(cols.values()) + ["-%d hours" % hours_ago])
        conn.commit()
    finally:
        conn.close()


def test_the_drain_skips_a_row_still_in_its_backoff(db):
    _wish(db, "movie", 1, attempts=0, hours_ago=0)          # fresh
    _wish(db, "movie", 2, attempts=959, hours_ago=1)        # searched an hour ago
    ids = {m["tmdb_id"] for m in db.movie_wishlist_to_download()}
    assert ids == {1}, "the 959-attempt row was searched again an hour later"


def test_the_drain_picks_it_up_once_the_wait_has_passed(db):
    _wish(db, "movie", 2, attempts=959, hours_ago=MAX_DELAY_HOURS + 1)
    assert {m["tmdb_id"] for m in db.movie_wishlist_to_download()} == {2}


def test_episodes_are_gated_the_same_way(db):
    _wish(db, "episode", 10, attempts=0, hours_ago=0, season_number=1, episode_number=1)
    _wish(db, "episode", 11, attempts=388, hours_ago=2, season_number=1, episode_number=1)
    ids = {e["show_tmdb_id"] for e in db.episode_wishlist_to_download()}
    assert ids == {10}


def test_a_user_asking_for_everything_is_never_gated(db):
    """Backoff paces the hourly tick. It does not overrule a person clicking
    'search now' — that is the only lever they have when a row is stuck."""
    _wish(db, "movie", 2, attempts=959, hours_ago=1)
    _wish(db, "episode", 11, attempts=388, hours_ago=1, season_number=1, episode_number=1)
    assert len(db.movie_wishlist_to_download(due_only=False)) == 1
    assert len(db.episode_wishlist_to_download(due_only=False)) == 1
