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
    """A legacy id, written on every seeded row and expected on none of them.

    §50.4.4.13 read the id contract off the old artist-detail page, which
    resolved a bare numeric id against the legacy table. That page is gone: the
    route redirects `/artist-detail/library/<id>` into Library V2 as
    `?artist=<id>`, and V2 reads a lib2 id (§50.4.4.22). The seeds keep a
    legacy id precisely so a query that still hands one out fails here.
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
    return _lib2(
        db,
        "INSERT INTO lib2_artists (name, name_key, sort_name, image_url, enrichment, "
        "soul_id, legacy_artist_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, normalize_name(name), name, thumb, json.dumps(enrichment),
         soul_id, legacy),
    )


def _seed_album(db, artist_id, title, thumb=None):
    return _lib2(
        db,
        "INSERT INTO lib2_albums (primary_artist_id, title, image_url, legacy_album_id) "
        "VALUES (?, ?, ?, ?)",
        (artist_id, title, thumb, _next_id()),
    )


def _seed_track(db, album_id, artist_id, title, file_path=None,
                bitrate=None, duration=None):
    track_id = _lib2(
        db,
        "INSERT INTO lib2_tracks (album_id, title, duration, legacy_track_id) "
        "VALUES (?, ?, ?, ?)",
        (album_id, title, duration, _next_id()),
    )
    if file_path is not None:
        # The file half is a separate row in lib2 (ADR-03): a track's path and
        # bitrate live on lib2_track_files, and "no file" is the absence of one.
        _lib2(
            db,
            "INSERT INTO lib2_track_files (track_id, path, bitrate, is_primary) "
            "VALUES (?, ?, ?, 1)",
            (track_id, file_path, bitrate),
        )
    return track_id


def _seed_history(db, title, artist, album, played_at, duration_ms=180000,
                  server_source=None, db_track_id=None, lib2_track_id=None):
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO listening_history (title, artist, album, played_at, duration_ms, "
            "server_source, db_track_id, lib2_track_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, artist, album, played_at, duration_ms, server_source,
             db_track_id, lib2_track_id),
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
    _seed_history(db, "Money", "Pink Floyd", "DSOTM", "2026-04-01 00:00:00",
                  duration_ms=383000, server_source="plex")
    rows = queries.get_recent_tracks(db, limit=1)
    assert rows == [{
        'title': "Money",
        'artist': "Pink Floyd",
        'album': "DSOTM",
        'played_at': "2026-04-01 00:00:00",
        'duration_ms': 383000,
        'server_source': "plex",
        # No db_track_id on the row -> the album-art join misses -> None.
        'image_url': None,
        'artist_db_id': None,
    }]


def test_get_recent_tracks_joins_album_art_through_db_track_id(db, fix_url):
    # A play the listening-stats worker matched to a library track carries
    # its album art, run through the image fixer (media-server thumb URLs
    # need auth and die raw in the browser).
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM", thumb="local://thumb.jpg")
    tid = _seed_track(db, alb, aid, "Money", file_path="/music/money.flac",
                      bitrate=1411, duration=383000)
    _seed_history(db, "Money", "Pink Floyd", "DSOTM", "2026-04-01 00:00:00",
                  lib2_track_id=tid)
    rows = queries.get_recent_tracks(db, limit=1, image_url_fixer=fix_url)
    assert rows[0]['image_url'] == "FIXED::local://thumb.jpg"
    assert rows[0]['artist_db_id'] == aid



# ---------------------------------------------------------------------------
# get_listening_events
# ---------------------------------------------------------------------------

def test_get_listening_events_day_bucket_returns_rows(db):
    _seed_history(db, "Morning", "Artist", "Album", "2026-08-22 09:00:00")
    _seed_history(db, "Night", "Artist", "Album", "2026-08-22T22:30:00")
    _seed_history(db, "Other", "Artist", "Album", "2026-08-23 09:00:00")

    out = queries.get_listening_events(
        db,
        None,
        time_range='all',
        filter_type='date',
        date='2026-08-22',
        limit=10,
    )

    assert out['total'] == 2
    assert [item['title'] for item in out['items']] == ["Night", "Morning"]


def test_get_listening_events_month_bucket_returns_rows(db):
    _seed_history(db, "Aug One", "Artist", "Album", "2026-08-01 00:00:00")
    _seed_history(db, "Aug Two", "Artist", "Album", "2026-08-31 23:59:59")
    _seed_history(db, "Sep", "Artist", "Album", "2026-09-01 00:00:00")

    out = queries.get_listening_events(
        db,
        None,
        time_range='all',
        filter_type='date',
        date='2026-08',
        limit=10,
    )

    assert out['total'] == 2
    assert [item['title'] for item in out['items']] == ["Aug Two", "Aug One"]


def test_get_listening_events_weekday_hour_bucket_returns_rows(db):
    _seed_history(db, "Match New", "Artist", "Album", "2026-08-23 16:30:00")
    _seed_history(db, "Match Old", "Artist", "Album", "2026-08-16 16:00:00")
    _seed_history(db, "Wrong Hour", "Artist", "Album", "2026-08-23 15:00:00")

    out = queries.get_listening_events(
        db,
        None,
        time_range='all',
        filter_type='weekday_hour',
        weekday=0,
        hour=16,
        limit=10,
    )

    assert out['total'] == 2
    assert [item['title'] for item in out['items']] == ["Match New", "Match Old"]


def test_get_listening_events_caches_repeated_image_normalization(db):
    artist_id = _seed_artist(db, "Artist")
    album_id = _seed_album(db, artist_id, "Album", thumb='/library/metadata/1/thumb/1')
    track_new = _seed_track(db, album_id, artist_id, "Repeat New")
    track_old = _seed_track(db, album_id, artist_id, "Repeat Old")
    # INT-02: the catalogue link is `lib2_track_id`. Seeding a native id into
    # `db_track_id` (the media server's own id namespace) is the shape the bug
    # had, and pinning it here is what let the chart detail read the wrong
    # column for as long as it did.
    _seed_history(db, "Repeat New", "Artist", "Album", "2026-08-23 16:30:00",
                  lib2_track_id=track_new)
    _seed_history(db, "Repeat Old", "Artist", "Album", "2026-08-23 16:00:00",
                  lib2_track_id=track_old)
    calls = []

    def fixer(url):
        calls.append(url)
        return f'/api/image-proxy?url={url}'

    out = queries.get_listening_events(
        db,
        fixer,
        time_range='all',
        filter_type='weekday_hour',
        weekday=0,
        hour=16,
        limit=10,
    )

    assert out['total'] == 2
    assert calls == ['/library/metadata/1/thumb/1']
    assert {item['image_url'] for item in out['items']} == {'/api/image-proxy?url=/library/metadata/1/thumb/1'}


def test_listening_history_weekday_hour_index_exists(db):
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ('idx_listening_weekday_hour_played_at',),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None

def test_get_listening_events_rejects_unknown_date_bucket(db):
    with pytest.raises(ValueError, match='YYYY-MM-DD or YYYY-MM'):
        queries.get_listening_events(
            db,
            None,
            time_range='all',
            filter_type='date',
            date='2026',
        )

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


def test_get_top_artists_links_a_native_artist_like_any_other(db, fix_url, monkeypatch):
    """A row with no legacy twin used to get artwork and a null id, because the
    id was the legacy one. The link goes to Library V2 now, which knows this
    row — being native is no longer a reason to withhold it (§50.4.4.22)."""
    aid = _seed_artist(db, "Native Only", thumb="local://native.jpg", legacy_id=None)
    monkeypatch.setattr(db, "get_top_artists",
                        lambda tr, lim: [{'name': 'Native Only', 'play_count': 9}])

    result = queries.get_top_artists(db, fix_url, time_range='all', limit=10)
    assert result[0]['image_url'] == 'FIXED::local://native.jpg'
    assert result[0]['id'] == aid


def test_top_rows_never_hand_out_a_legacy_id(db, fix_url, monkeypatch):
    """The one assertion that would have caught the drift: every id in this
    payload is the id `/library?artist=` resolves, and a legacy id there opens
    a different artist or none at all."""
    aid = _seed_artist(db, "Pink Floyd")
    alb = _seed_album(db, aid, "DSOTM", thumb="local://a.jpg")
    _seed_track(db, alb, aid, "Time", file_path="/m/time.flac")
    legacy_ids = set()
    conn = db._get_connection()
    try:
        for table, column in (('lib2_artists', 'legacy_artist_id'),
                              ('lib2_albums', 'legacy_album_id'),
                              ('lib2_tracks', 'legacy_track_id')):
            legacy_ids.update(
                row[0] for row in conn.execute(f"SELECT {column} FROM {table}")
                if row[0] is not None)
    finally:
        conn.close()
    monkeypatch.setattr(db, "get_top_artists",
                        lambda tr, lim: [{'name': 'Pink Floyd', 'play_count': 1}])
    monkeypatch.setattr(db, "get_top_albums",
                        lambda tr, lim: [{'name': 'DSOTM', 'play_count': 1}])
    monkeypatch.setattr(db, "get_top_tracks",
                        lambda tr, lim: [{'name': 'Time', 'artist': 'Pink Floyd',
                                          'play_count': 1}])

    handed_out = set()
    for row in queries.get_top_artists(db, fix_url, 'all', 10):
        handed_out.add(row.get('id'))
    for row in queries.get_top_albums(db, fix_url, 'all', 10):
        handed_out.update((row.get('id'), row.get('artist_id')))
    for row in queries.get_top_tracks(db, fix_url, 'all', 10):
        handed_out.update((row.get('id'), row.get('artist_id')))

    assert legacy_ids, 'the seeds must carry legacy ids for this to prove anything'
    assert handed_out & legacy_ids == set()


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



def test_resolve_track_finds_a_compilation_track_by_its_own_artist(db):
    """INT-03, stats half: the local-playback resolution matched only the
    album's primary artist, so a listening event that correctly named Muse
    could not resolve a Muse track sitting on a Various Artists compilation —
    the file was right there."""
    va = _seed_artist(db, "Various Artists")
    muse = _seed_artist(db, "Muse")
    album_id = _seed_album(db, va, "Compilation")
    track_id = _seed_track(db, album_id, va, "Uprising")
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id, position)"
            " VALUES(?,?,0)", (track_id, muse))
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, is_primary, file_state)"
            " VALUES(?, '/music/uprising.flac', 1, 'active')", (track_id,))
        conn.commit()
    finally:
        conn.close()

    resolved = queries.resolve_track(db, None, "Uprising", "Muse")

    assert resolved is not None
    assert resolved["file_path"] == "/music/uprising.flac"

    # The album artist stays a valid key.
    assert queries.resolve_track(db, None, "Uprising", "Various Artists") is not None
    # And an unrelated artist still does not match.
    assert queries.resolve_track(db, None, "Uprising", "Def Leppard") is None
