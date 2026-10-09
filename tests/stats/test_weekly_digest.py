"""Tests for core.stats.queries.get_weekly_digest — the dashboard banner."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.stats import queries
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _seed_play(db, title, artist, played_at, duration_ms=180_000, profile_id=1):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO listening_history (title, artist, played_at, duration_ms, profile_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, artist, played_at, duration_ms, profile_id),
        )
        conn.commit()
    finally:
        conn.close()


def _at(days_ago, hour=12):
    return (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')


def test_empty_db_returns_zero_shape(db):
    digest = queries.get_weekly_digest(db)
    assert digest['tracks_played'] == 0
    assert digest['time_ms'] == 0
    assert digest['top_artist'] is None
    assert digest['discoveries'] == 0
    assert digest['streak_days'] == 0
    assert len(digest['daily']) == 7
    assert all(d['hours'] == 0 for d in digest['daily'])
    assert digest['start_date'] < digest['end_date']


def test_totals_top_artist_and_daily_buckets(db):
    _seed_play(db, 'Song A', 'Tame Impala', _at(1), duration_ms=3_600_000)
    _seed_play(db, 'Song B', 'Tame Impala', _at(1), duration_ms=1_800_000)
    _seed_play(db, 'Song C', 'Daft Punk', _at(3), duration_ms=3_600_000)
    # outside the window: invisible to the digest
    _seed_play(db, 'Oldie', 'Pink Floyd', _at(30), duration_ms=3_600_000)

    digest = queries.get_weekly_digest(db)
    assert digest['tracks_played'] == 3
    assert digest['time_ms'] == 9_000_000
    assert digest['top_artist'] == 'Tame Impala'
    # 1 day ago -> index 5 of the oldest-first 7-day list
    assert digest['daily'][5]['hours'] == 1.5
    assert digest['daily'][3]['hours'] == 1.0
    assert digest['daily'][0]['hours'] == 0


def test_discoveries_counts_first_heard_in_window(db):
    # heard long ago: not a discovery
    _seed_play(db, 'Oldie', 'Pink Floyd', _at(60))
    _seed_play(db, 'Oldie 2', 'Pink Floyd', _at(1))
    # first heard inside the window: discoveries
    _seed_play(db, 'New 1', 'Tame Impala', _at(2))
    _seed_play(db, 'New 2', 'Tame Impala', _at(1))
    _seed_play(db, 'New 3', 'Daft Punk', _at(1))

    digest = queries.get_weekly_digest(db)
    assert digest['discoveries'] == 2


def test_streak_counts_consecutive_days(db):
    _seed_play(db, 'A', 'X', _at(0))
    _seed_play(db, 'B', 'X', _at(1))
    _seed_play(db, 'C', 'X', _at(2))
    assert queries.get_weekly_digest(db)['streak_days'] == 3


def test_streak_survives_today_without_plays_yet(db):
    _seed_play(db, 'A', 'X', _at(1))
    _seed_play(db, 'B', 'X', _at(2))
    assert queries.get_weekly_digest(db)['streak_days'] == 2


def test_streak_breaks_on_gap(db):
    _seed_play(db, 'A', 'X', _at(0))
    _seed_play(db, 'B', 'X', _at(3))
    assert queries.get_weekly_digest(db)['streak_days'] == 1


def test_owner_scoping(db):
    _seed_play(db, 'Mine', 'X', _at(1), profile_id=1)
    _seed_play(db, 'Theirs', 'Y', _at(1), profile_id=2)
    digest = queries.get_weekly_digest(db, profile_id=None)
    assert digest['tracks_played'] == 1
    assert digest['top_artist'] == 'X'


# ---------------------------------------------------------------------------
# 689 plays, 45 minutes: only web-player plays store a duration. plex,
# last.fm and listenbrainz plays come in with 0, so every listening total
# summed almost nothing. they fall back to the linked library track's length.
# ---------------------------------------------------------------------------

def _seed_track(db, track_id, duration):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO artists (id, name) VALUES ('a1', 'A') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES ('al1', 'a1', 'B') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, duration) VALUES (?, 'al1', 'a1', 'T', ?)",
                     (str(track_id), duration))
        conn.commit()
    finally:
        conn.close()


def _seed_linked_play(db, played_at, db_track_id, duration_ms=0, source='plex'):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO listening_history (title, artist, played_at, duration_ms, server_source, db_track_id, profile_id) "
            "VALUES ('Firework', 'Katy Perry', ?, ?, ?, ?, 1)",
            (played_at, duration_ms, source, db_track_id))
        conn.commit()
    finally:
        conn.close()


def test_plays_without_a_duration_use_the_library_track(db):
    _seed_track(db, 42, 240_000)
    _seed_linked_play(db, _at(1), 42)                       # plex: no duration
    _seed_linked_play(db, _at(1), 42, duration_ms=200_000, source='soulsync_web')  # own duration wins
    _seed_linked_play(db, _at(1), None)                     # not in the library: counts 0
    digest = queries.get_weekly_digest(db)
    assert digest['tracks_played'] == 3
    assert digest['time_ms'] == 440_000
    assert digest['daily'][5]['hours'] == round(440_000 / 3_600_000, 1)


def test_stats_page_and_year_count_the_same_time(db):
    _seed_track(db, 7, 180_000)
    for _ in range(3):
        _seed_linked_play(db, _at(2), 7)
    assert db.get_listening_stats('7d', profile_id=1)['total_time_ms'] == 540_000
    year = db.get_year_in_listening(profile_id=1)
    assert year['totals']['minutes'] == 9
