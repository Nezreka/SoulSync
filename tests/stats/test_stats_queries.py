"""Tests for core/stats/queries.py — lifted from web_server.py /api/stats/* routes."""

from __future__ import annotations

import json

import pytest

from core.stats import queries
from database.music_database import MusicDatabase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture
def fix_url():
    """Image-url fixer stub: prefixes inputs to make calls observable."""
    return lambda u: f"FIXED::{u}" if u else None


_id_counter = {'n': 0}


def _next_id():
    """A legacy id. The stats rows still speak this space (docs §50.4.4.13).

    A bare numeric entity id is legacy by contract — the artist-detail page
    resolves it against the legacy table — so the seeds keep one on every lib2
    row and the assertions below compare against it, even though the row the
    query reads is native.
    """
    _id_counter['n'] += 1
    return 1000 + _id_counter['n']


def _lib2(db, sql, params=()):
    conn = db._get_connection()
    try:
        row_id = conn.execute(sql, params).lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def _seed_artist(db, name, thumb=None, lastfm_listeners=None, lastfm_playcount=None,
                 soul_id=None, legacy_id=...):
    from core.library2.importer import normalize_name

    legacy = _next_id() if legacy_id is ... else legacy_id
    enrichment = {}
    if lastfm_listeners is not None or lastfm_playcount is not None:
        enrichment['lastfm'] = {}
        if lastfm_listeners is not None:
            enrichment['lastfm']['listeners'] = lastfm_listeners
        if lastfm_playcount is not None:
            enrichment['lastfm']['playcount'] = lastfm_playcount
    _lib2(
        db,
        "INSERT INTO lib2_artists (name, name_key, sort_name, image_url, enrichment, "
        "soul_id, legacy_artist_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, normalize_name(name), name, thumb, json.dumps(enrichment),
         soul_id, legacy),
    )
    return legacy


def _seed_album(db, artist_legacy_id, title, thumb=None):
    legacy = _next_id()
    _lib2(
        db,
        "INSERT INTO lib2_albums (primary_artist_id, title, image_url, legacy_album_id) "
        "SELECT id, ?, ?, ? FROM lib2_artists WHERE legacy_artist_id = ?",
        (title, thumb, legacy, artist_legacy_id),
    )
    return legacy


def _seed_track(db, album_legacy_id, artist_legacy_id, title, file_path=None,
                bitrate=None, duration=None):
    legacy = _next_id()
    _lib2(
        db,
        "INSERT INTO lib2_tracks (album_id, title, duration, legacy_track_id) "
        "SELECT id, ?, ?, ? FROM lib2_albums WHERE legacy_album_id = ?",
        (title, duration, legacy, album_legacy_id),
    )
    if file_path is not None:
        # The file half is a separate row in lib2 (ADR-03): a track's path and
        # bitrate live on lib2_track_files, and "no file" is the absence of one.
        _lib2(
            db,
            "INSERT INTO lib2_track_files (track_id, path, bitrate, is_primary) "
            "SELECT id, ?, ?, 1 FROM lib2_tracks WHERE legacy_track_id = ?",
            (file_path, bitrate, legacy),
        )
    return legacy


def _seed_history(db, title, artist, album, played_at, duration_ms=180000):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO listening_history (title, artist, album, played_at, duration_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, artist, album, played_at, duration_ms),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_metadata(db, key, value):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_recent_tracks
# ---------------------------------------------------------------------------

def test_get_recent_tracks_orders_by_played_at_desc(db):
    _seed_history(db, "Old", "A", "Album", "2026-01-01 00:00:00")
    _seed_history(db, "Newest", "A", "Album", "2026-04-01 00:00:00")
    _seed_history(db, "Mid", "A", "Album", "2026-02-15 00:00:00")

    rows = queries.get_recent_tracks(db, limit=10)
    titles = [r['title'] for r in rows]

    assert titles == ["Newest", "Mid", "Old"]


def test_get_recent_tracks_respects_limit(db):
    for i in range(5):
        _seed_history(db, f"T{i}", "A", "Album", f"2026-04-0{i + 1} 00:00:00")
    rows = queries.get_recent_tracks(db, limit=2)
    assert len(rows) == 2


def test_get_recent_tracks_empty_returns_empty(db):
    rows = queries.get_recent_tracks(db, limit=10)
    assert rows == []


def test_get_recent_tracks_returns_full_shape(db):
    _seed_history(db, "Money", "Pink Floyd", "DSOTM", "2026-04-01 00:00:00", duration_ms=383000)
    rows = queries.get_recent_tracks(db, limit=1)
    assert rows == [{
        'title': "Money",
        'artist': "Pink Floyd",
        'album': "DSOTM",
        'played_at': "2026-04-01 00:00:00",
        'duration_ms': 383000,
    }]


# ---------------------------------------------------------------------------
# resolve_track
# ---------------------------------------------------------------------------

def test_resolve_track_returns_full_metadata(db, fix_url):
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM", thumb="local://thumb.jpg")
    _seed_track(db, alb, aid, "Money", file_path="/music/money.flac", bitrate=1411, duration=383000)

    result = queries.resolve_track(db, fix_url, "Money", "Pink Floyd")
    assert result['title'] == "Money"
    assert result['file_path'] == "/music/money.flac"
    assert result['bitrate'] == 1411
    assert result['duration'] == 383000
    assert result['artist_name'] == "Pink Floyd"
    assert result['album_title'] == "DSOTM"
    assert result['image_url'] == "FIXED::local://thumb.jpg"
    assert result['album_id'] == alb
    assert result['artist_id'] == aid


def test_resolve_track_case_insensitive_match(db, fix_url):
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM")
    _seed_track(db, alb, aid, "Money", file_path="/music/x.flac")

    result = queries.resolve_track(db, fix_url, "money", "pink floyd")
    assert result is not None
    assert result['title'] == "Money"


def test_resolve_track_returns_none_when_no_file_path(db, fix_url):
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM")
    _seed_track(db, alb, aid, "Money", file_path=None)

    result = queries.resolve_track(db, fix_url, "Money", "Pink Floyd")
    assert result is None


def test_resolve_track_returns_none_when_file_path_empty(db, fix_url):
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM")
    _seed_track(db, alb, aid, "Money", file_path="")

    result = queries.resolve_track(db, fix_url, "Money", "Pink Floyd")
    assert result is None


def test_resolve_track_strips_whitespace(db, fix_url):
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM")
    _seed_track(db, alb, aid, "Money", file_path="/x.flac")

    result = queries.resolve_track(db, fix_url, "  Money  ", "  Pink Floyd  ")
    assert result is not None


# ---------------------------------------------------------------------------
# get_top_artists / get_top_albums / get_top_tracks — enrichment
# ---------------------------------------------------------------------------

def test_get_top_artists_enriches_from_the_native_catalogue(db, fix_url, monkeypatch):
    aid = _seed_artist(
        db, "Pink Floyd", thumb="local://pf.jpg",
        lastfm_listeners=5000000, lastfm_playcount=100000000, soul_id="soul-pf",
    )

    monkeypatch.setattr(db, "get_top_artists", lambda tr, lim: [{'name': 'Pink Floyd', 'play_count': 42}])

    result = queries.get_top_artists(db, fix_url, time_range='all', limit=10)
    assert result[0]['name'] == 'Pink Floyd'
    assert result[0]['image_url'] == 'FIXED::local://pf.jpg'
    assert result[0]['id'] == aid
    assert result[0]['global_listeners'] == 5000000
    assert result[0]['global_playcount'] == 100000000
    assert result[0]['soul_id'] == 'soul-pf'


def test_get_top_artists_no_match_leaves_record_unenriched(db, fix_url, monkeypatch):
    monkeypatch.setattr(db, "get_top_artists", lambda tr, lim: [{'name': 'Unknown', 'play_count': 1}])
    result = queries.get_top_artists(db, fix_url, time_range='all', limit=10)
    assert result == [{'name': 'Unknown', 'play_count': 1}]


def test_get_top_artists_matches_on_the_normalized_name_key(db, fix_url, monkeypatch):
    """lib2 indexes ``name_key``; ``LOWER(name)=LOWER(?)`` was a scan and — with
    SQLite's ASCII-only ``lower()`` — missed every non-Latin name (iss29-D13)."""
    aid = _seed_artist(db, "Björk", thumb="local://bjork.jpg")
    monkeypatch.setattr(db, "get_top_artists",
                        lambda tr, lim: [{'name': 'BJÖRK', 'play_count': 3}])

    result = queries.get_top_artists(db, fix_url, time_range='all', limit=10)
    assert result[0]['id'] == aid


def test_get_top_artists_gives_a_native_artist_art_but_no_dead_link(db, fix_url, monkeypatch):
    """A row with no legacy twin has no id navigation can resolve. Its artwork
    is still real, so the record is enriched with everything except the id."""
    _seed_artist(db, "Native Only", thumb="local://native.jpg", legacy_id=None)
    monkeypatch.setattr(db, "get_top_artists",
                        lambda tr, lim: [{'name': 'Native Only', 'play_count': 9}])

    result = queries.get_top_artists(db, fix_url, time_range='all', limit=10)
    assert result[0]['image_url'] == 'FIXED::local://native.jpg'
    assert result[0].get('id') is None


def test_get_top_albums_enriches_with_album_thumb(db, fix_url, monkeypatch):
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM", thumb="local://album.jpg")

    monkeypatch.setattr(db, "get_top_albums", lambda tr, lim: [{'name': 'DSOTM', 'play_count': 5}])

    result = queries.get_top_albums(db, fix_url, time_range='all', limit=10)
    assert result[0]['image_url'] == 'FIXED::local://album.jpg'
    assert result[0]['id'] == alb
    assert result[0]['artist_id'] == aid


def test_get_top_albums_skips_empty_thumb(db, fix_url, monkeypatch):
    aid = _seed_artist(db, "X")
    _seed_album(db, aid, "Empty", thumb="")
    monkeypatch.setattr(db, "get_top_albums", lambda tr, lim: [{'name': 'Empty', 'play_count': 1}])

    result = queries.get_top_albums(db, fix_url, time_range='all', limit=10)
    assert 'image_url' not in result[0]


def test_get_top_tracks_enriches_with_album_thumb(db, fix_url, monkeypatch):
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM", thumb="local://thumb.jpg")
    tid = _seed_track(db, alb, aid, "Money")

    monkeypatch.setattr(db, "get_top_tracks", lambda tr, lim: [{'name': 'Money', 'artist': 'Pink Floyd'}])

    result = queries.get_top_tracks(db, fix_url, time_range='all', limit=10)
    assert result[0]['image_url'] == 'FIXED::local://thumb.jpg'
    assert result[0]['id'] == tid
    assert result[0]['artist_id'] == aid


def test_get_top_tracks_unmatched_record_passed_through(db, fix_url, monkeypatch):
    monkeypatch.setattr(db, "get_top_tracks", lambda tr, lim: [{'name': 'Phantom', 'artist': 'Nobody'}])
    result = queries.get_top_tracks(db, fix_url, time_range='all', limit=10)
    assert result == [{'name': 'Phantom', 'artist': 'Nobody'}]


# ---------------------------------------------------------------------------
# get_cached_stats
# ---------------------------------------------------------------------------

def test_get_cached_stats_reads_three_metadata_keys(db, fix_url):
    _seed_metadata(db, 'stats_cache_7d', {
        'top_artists': [{'name': 'PF', 'image_url': 'local://a.jpg'}],
        'top_albums': [{'name': 'DSOTM'}],
        'top_tracks': [{'name': 'Money', 'image_url': 'local://t.jpg'}],
        'overview': {'plays': 100},
    })
    _seed_metadata(db, 'stats_cache_recent', [{'title': 'Money'}])
    _seed_metadata(db, 'stats_cache_health', {'orphan_tracks': 0})

    result = queries.get_cached_stats(db, fix_url, '7d')

    assert result['cached'] is True
    assert result['top_artists'][0]['image_url'] == 'FIXED::local://a.jpg'
    assert result['top_tracks'][0]['image_url'] == 'FIXED::local://t.jpg'
    assert result['overview'] == {'plays': 100}
    assert result['recent'] == [{'title': 'Money'}]
    assert result['health'] == {'orphan_tracks': 0}


def test_get_cached_stats_missing_keys_return_empty_defaults(db, fix_url):
    result = queries.get_cached_stats(db, fix_url, '30d')
    assert result['cached'] is True
    assert result['recent'] == []
    assert result['health'] == {}


def test_get_cached_stats_skips_image_fix_when_no_url(db, fix_url):
    _seed_metadata(db, 'stats_cache_7d', {
        'top_artists': [{'name': 'PF'}],
    })
    result = queries.get_cached_stats(db, fix_url, '7d')
    assert 'image_url' not in result['top_artists'][0]


# ---------------------------------------------------------------------------
# Pass-through helpers — verify they delegate to the right DB method
# ---------------------------------------------------------------------------

def test_get_overview_delegates_to_db(monkeypatch):
    sentinel = object()
    called = {}

    class _DB:
        def get_listening_stats(self, time_range):
            called['arg'] = time_range
            return sentinel

    assert queries.get_overview(_DB(), '7d') is sentinel
    assert called['arg'] == '7d'


def test_get_timeline_delegates_to_db():
    called = {}

    class _DB:
        def get_listening_timeline(self, time_range, granularity):
            called['args'] = (time_range, granularity)
            return ['data']

    assert queries.get_timeline(_DB(), '30d', 'week') == ['data']
    assert called['args'] == ('30d', 'week')


def test_get_genres_delegates_to_db():
    called = {}

    class _DB:
        def get_genre_breakdown(self, time_range):
            called['arg'] = time_range
            return [{'genre': 'rock'}]

    assert queries.get_genres(_DB(), 'all') == [{'genre': 'rock'}]
    assert called['arg'] == 'all'


def test_get_library_health_delegates_to_db():
    class _DB:
        def get_library_health(self):
            return {'orphan_tracks': 5}

    assert queries.get_library_health(_DB()) == {'orphan_tracks': 5}


def test_get_db_storage_delegates_to_db():
    class _DB:
        def get_db_storage_stats(self):
            return {'total_mb': 42}

    assert queries.get_db_storage(_DB()) == {'total_mb': 42}


# ---------------------------------------------------------------------------
# Listening worker glue
# ---------------------------------------------------------------------------

def test_get_listening_status_handles_none_worker():
    result = queries.get_listening_status(None)
    assert result == {
        'enabled': False,
        'running': False,
        'paused': False,
        'idle': False,
        'current_item': None,
        'stats': {},
    }


def test_get_listening_status_delegates_to_worker():
    class _Worker:
        def get_stats(self):
            return {'enabled': True, 'running': True, 'stats': {'polls_completed': 42}}

    result = queries.get_listening_status(_Worker())
    assert result['enabled'] is True
    assert result['stats']['polls_completed'] == 42


def test_trigger_listening_sync_runs_worker_poll_in_thread():
    poll_called = []
    stats_dict = {'polls_completed': 0, 'last_poll': None}

    class _Worker:
        stats = stats_dict

        def _poll(self):
            poll_called.append(True)

    queries.trigger_listening_sync(_Worker())

    # Wait briefly for thread to run
    import time as _time
    for _ in range(50):
        if poll_called:
            break
        _time.sleep(0.01)

    assert poll_called == [True]
    assert stats_dict['polls_completed'] == 1
    assert stats_dict['last_poll'] is not None


def test_trigger_listening_sync_swallows_worker_errors():
    class _BrokenWorker:
        stats = {'polls_completed': 0, 'last_poll': None}

        def _poll(self):
            raise RuntimeError("boom")

    # Should NOT raise — error is caught + logged inside the thread
    queries.trigger_listening_sync(_BrokenWorker())

    import time as _time
    _time.sleep(0.1)  # give thread time to crash
    # Counter not incremented because exception was raised before increment
    assert _BrokenWorker.stats['polls_completed'] == 0
