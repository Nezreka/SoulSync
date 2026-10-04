"""#1504 part 2 — repair re-downloads must wishlist to the OWNING profile.

Regression tests pinning that every repair fix path passes the owning user
profile's id to ``add_to_wishlist`` (instead of silently defaulting to
profile 1), and that the fallback stays byte-for-byte today's behavior
(profile 1) whenever the owner can't be determined.
"""

from __future__ import annotations

import sqlite3
import types

import pytest

from core.repair_worker import RepairWorker


def _memdb(with_owner_column=True):
    """In-memory library DB. ``with_owner_column=False`` simulates a DB that
    predates the owner_profile_id migration."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    owner_col = ", owner_profile_id INTEGER DEFAULT NULL" if with_owner_column else ""
    conn.execute(f"CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT{owner_col})")
    conn.execute(
        "CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT,"
        " spotify_album_id TEXT, record_type TEXT, track_count INTEGER,"
        f" year INTEGER, thumb_url TEXT{owner_col})"
    )
    conn.execute(
        "CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT, track_number INTEGER,"
        " duration INTEGER, artist_id INTEGER, album_id INTEGER,"
        " spotify_track_id TEXT, itunes_track_id TEXT, deezer_id TEXT"
        f"{owner_col})"
    )
    conn.commit()
    return conn


class _FakeDB:
    """Minimal repair-worker DB double: owner lookups hit the sqlite conn,
    wishlist calls are captured."""

    def __init__(self, conn):
        self._conn = conn
        self.wishlist_calls = []  # list of (args, kwargs)

    def _get_connection(self):
        return self._conn

    def add_to_wishlist(self, *args, **kwargs):
        self.wishlist_calls.append((args, kwargs))
        return True


def _worker(db, **attrs):
    worker = object.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = ''
    worker._config_manager = None
    for key, value in attrs.items():
        setattr(worker, key, value)
    return worker


def _seed_track(conn, track_id=3, owner=2):
    conn.execute("INSERT INTO artists (id, name, owner_profile_id) VALUES (1, 'Artist A', ?)", (owner,))
    conn.execute("INSERT INTO albums (id, title, owner_profile_id) VALUES (1, 'Album X', ?)", (owner,))
    conn.execute(
        "INSERT INTO tracks (id, title, track_number, duration, artist_id, album_id,"
        " spotify_track_id, owner_profile_id)"
        " VALUES (?, 'Cutoff Song', 4, 210000, 1, 1, 'sp1', ?)",
        (track_id, owner),
    )
    conn.commit()


# --- identity resolver surfaces the owner -----------------------------------

def test_identity_surfaces_owner_profile_id():
    conn = _memdb()
    _seed_track(conn, track_id=3, owner=2)
    db = _FakeDB(conn)

    data = _worker(db)._track_identity_for_redownload(
        3, {'track_title': 'Cutoff Song', 'artist': 'Artist A'})

    assert data is not None
    assert data['_owner_profile_id'] == 2


def test_identity_owner_none_when_column_predates_migration():
    """A DB without the owner_profile_id column must not break identity
    resolution — the owner is just unknown (falls back to profile 1)."""
    conn = _memdb(with_owner_column=False)
    conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Artist A')")
    conn.execute("INSERT INTO tracks (id, title, artist_id) VALUES (3, 'Cutoff Song', 1)")
    conn.commit()
    db = _FakeDB(conn)

    data = _worker(db)._track_identity_for_redownload(
        3, {'track_title': 'Cutoff Song', 'artist': 'Artist A'})

    assert data is not None
    assert data['name'] == 'Cutoff Song'
    assert data['_owner_profile_id'] is None


def test_identity_owner_none_for_orphaned_finding():
    """The track row is gone (DB refresh) — identity still resolves from the
    finding's details, owner unknown."""
    conn = _memdb()
    db = _FakeDB(conn)

    data = _worker(db)._track_identity_for_redownload(
        4242, {'track_title': 'Cutoff Song', 'artist': 'Artist A'})

    assert data is not None
    assert data['_owner_profile_id'] is None


# --- dead file ---------------------------------------------------------------

def test_dead_file_redownload_uses_owning_profile():
    conn = _memdb()
    _seed_track(conn, track_id=3, owner=2)
    db = _FakeDB(conn)

    res = _worker(db)._fix_dead_file('track', 3, None,
                                     {'track_title': 'Cutoff Song', 'artist': 'Artist A'})

    assert res['success'] is True
    assert len(db.wishlist_calls) == 1
    args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2
    # The private owner key must not leak into the wishlist's stored JSON.
    assert '_owner_profile_id' not in args[0]


def test_dead_file_falls_back_to_profile_1_when_owner_null():
    conn = _memdb()
    _seed_track(conn, track_id=3, owner=None)  # legacy row, owner unknown
    db = _FakeDB(conn)

    res = _worker(db)._fix_dead_file('track', 3, None,
                                     {'track_title': 'Cutoff Song', 'artist': 'Artist A'})

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 1


# --- quality upgrade -----------------------------------------------------------

def test_quality_upgrade_prematched_data_uses_track_row_owner():
    """Pre-matched finding data carries no owner — it is looked up from the
    entity row. quality_profile_id (the QUALITY profile) still passes through
    untouched."""
    conn = _memdb()
    _seed_track(conn, track_id=3, owner=3)
    db = _FakeDB(conn)

    details = {
        'matched_track_data': {'id': 'sp9', 'name': 'Cutoff Song',
                               'album': {'name': 'Album X'}},
        'quality_profile_id': 7, 'quality_profile_name': 'Strict FLAC',
    }
    res = _worker(db)._fix_quality_upgrade('track', 3, '/music/c.flac', details)

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 3
    assert kwargs['quality_profile_id'] == 7


def test_quality_upgrade_identity_path_uses_row_owner():
    conn = _memdb()
    _seed_track(conn, track_id=3, owner=2)
    db = _FakeDB(conn)

    res = _worker(db)._fix_quality_upgrade(
        'track', 3, '/music/c.flac',
        {'quality_issue': 'below_profile',
         'expected_title': 'Cutoff Song', 'expected_artist': 'Artist A'})

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2


def test_quality_upgrade_orphaned_finding_falls_back_to_profile_1():
    conn = _memdb()  # no track rows — orphaned finding
    db = _FakeDB(conn)

    res = _worker(db)._fix_quality_upgrade(
        'track', 4242, '/music/gone.flac',
        {'quality_issue': 'below_profile',
         'expected_title': 'Cutoff Song', 'expected_artist': 'Artist A'})

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 1


def test_quality_upgrade_unmatched_file_falls_back_to_profile_1():
    """entity_id=None (unmatched file, entity_type='file') has no owner."""
    conn = _memdb()
    db = _FakeDB(conn)

    res = _worker(db)._fix_quality_upgrade(
        'file', None, '/music/stray.flac',
        {'quality_issue': 'below_profile',
         'expected_title': 'Stray Song', 'expected_artist': 'Artist A'})

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 1


# --- preview clip / corrupt audio / fake lossless ------------------------------

def test_short_preview_track_redownload_uses_owning_profile(tmp_path):
    conn = _memdb()
    _seed_track(conn, track_id=5, owner=2)
    db = _FakeDB(conn)
    worker = _worker(db, transfer_folder=str(tmp_path))

    res = worker._fix_short_preview_track(
        'track', 5, None,
        {'track_title': 'Cutoff Song', 'artist': 'Artist A'})

    assert res['success'] is True
    args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2
    assert '_owner_profile_id' not in args[0]


def test_corrupt_audio_redownload_uses_owning_profile(tmp_path):
    conn = _memdb()
    _seed_track(conn, track_id=5, owner=2)
    db = _FakeDB(conn)
    worker = _worker(db, transfer_folder=str(tmp_path))

    res = worker._fix_corrupt_audio(
        'track', 5, None,
        {'track_title': 'Cutoff Song', 'artist': 'Artist A'})

    assert res['success'] is True
    args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2
    assert '_owner_profile_id' not in args[0]


def test_fake_lossless_redownload_uses_owning_profile(tmp_path):
    conn = _memdb()
    _seed_track(conn, track_id=5, owner=2)
    db = _FakeDB(conn)
    worker = _worker(db, transfer_folder=str(tmp_path))

    res = worker._fix_fake_lossless(
        'track', 5, None,
        {'track_title': 'Cutoff Song', 'artist': 'Artist A',
         'detected_cutoff_khz': 16})

    assert res['success'] is True
    args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2
    assert '_owner_profile_id' not in args[0]


# --- AcoustID mismatch -----------------------------------------------------------

def test_acoustid_redownload_uses_track_row_owner():
    conn = _memdb()
    _seed_track(conn, track_id=3, owner=2)
    db = _FakeDB(conn)

    res = _worker(db)._fix_acoustid_mismatch(
        'track', 3, None,
        {'_fix_action': 'redownload',
         'expected_title': 'Real Song', 'expected_artist': 'Artist A'})

    assert res['success'] is True
    assert len(db.wishlist_calls) == 1
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2


def test_acoustid_redownload_falls_back_to_profile_1_when_track_gone():
    conn = _memdb()
    db = _FakeDB(conn)

    res = _worker(db)._fix_acoustid_mismatch(
        'track', 4242, None,
        {'_fix_action': 'redownload',
         'expected_title': 'Real Song', 'expected_artist': 'Artist A'})

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 1


# --- incomplete album (Phase 4 wishlist fallback) ---------------------------------

class _AlbumTrack:
    def __init__(self, id, file_path, bitrate, track_number, album_id):
        self.id = id
        self.file_path = file_path
        self.bitrate = bitrate
        self.track_number = track_number
        self.album_id = album_id


def _album_db(conn):
    db = _FakeDB(conn)
    db.get_tracks_by_album = lambda album_id: db._existing_tracks
    db.search_tracks = lambda **kw: []  # no library candidates → Phase 4
    return db


def _run_incomplete_album(tmp_path, album_owner):
    conn = _memdb()
    conn.execute("INSERT INTO albums (id, title, owner_profile_id) VALUES (7, 'Album X', ?)",
                 (album_owner,))
    conn.commit()
    existing_file = tmp_path / '01 - Track One.flac'
    existing_file.write_bytes(b'fake-flac')
    db = _album_db(conn)
    db._existing_tracks = [_AlbumTrack(11, str(existing_file), 900, 1, 7)]
    worker = _worker(db, transfer_folder=str(tmp_path))

    details = {
        'album_id': 7,
        'album_title': 'Album X',
        'artist': 'Artist A',
        'missing_tracks': [
            {'name': 'Track Two', 'track_number': 2, 'disc_number': 1,
             'artists': ['Artist A'], 'source': 'spotify',
             'source_track_id': 'sp_track_2', 'duration_ms': 200000},
        ],
    }
    res = worker._fix_incomplete_album('album', 7, None, details)
    return res, db


def test_incomplete_album_wishlist_uses_album_owner(tmp_path):
    res, db = _run_incomplete_album(tmp_path, album_owner=2)

    assert res['success'] is True
    assert res['wishlisted'] == 1
    assert len(db.wishlist_calls) == 1
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2


def test_incomplete_album_wishlist_falls_back_to_profile_1(tmp_path):
    res, db = _run_incomplete_album(tmp_path, album_owner=None)

    assert res['success'] is True
    assert res['wishlisted'] == 1
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 1


# --- discography backfill ----------------------------------------------------------

def test_discography_backfill_fix_uses_scan_time_owner():
    """New findings carry owner_profile_id from the scan."""
    db = _FakeDB(_memdb())
    details = {
        'track_data': {'id': 'sp1', 'name': 'Missing Song'},
        'artist_name': 'Artist A',
        'owner_profile_id': 2,
    }

    res = _worker(db)._fix_discography_backfill('track', 'sp1', None, details)

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2


def test_discography_backfill_fix_falls_back_to_artist_name_lookup():
    """Old findings predate the scan-time field — the artist-name lookup
    covers them."""
    conn = _memdb()
    conn.execute("INSERT INTO artists (id, name, owner_profile_id) VALUES (1, 'Artist A', 2)")
    conn.commit()
    db = _FakeDB(conn)
    details = {
        'track_data': {'id': 'sp1', 'name': 'Missing Song'},
        'artist_name': 'Artist A',
    }

    res = _worker(db)._fix_discography_backfill('track', 'sp1', None, details)

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 2


def test_discography_backfill_fix_falls_back_to_profile_1():
    db = _FakeDB(_memdb())  # no artist rows at all
    details = {
        'track_data': {'id': 'sp1', 'name': 'Missing Song'},
        'artist_name': 'Unknown Artist',
    }

    res = _worker(db)._fix_discography_backfill('track', 'sp1', None, details)

    assert res['success'] is True
    _args, kwargs = db.wishlist_calls[0]
    assert kwargs['profile_id'] == 1


def test_get_library_artists_selects_owner_profile_id():
    """The scan must surface the artist's owner so findings and auto-adds can
    route to the owning profile."""
    pytest.importorskip("spotipy")  # known env gap: discography_backfill's
    # metadata-provider import chain needs spotipy, which isn't installed here.
    from core.repair_jobs.discography_backfill import DiscographyBackfillJob

    conn = _memdb()
    conn.execute("INSERT INTO artists (id, name, owner_profile_id) VALUES (1, 'Artist A', 2)")
    conn.execute("INSERT INTO albums (id, title, artist_id, owner_profile_id)"
                 " VALUES (1, 'Album X', 1, 2)")
    conn.commit()
    context = types.SimpleNamespace(db=_FakeDB(conn))

    artists = DiscographyBackfillJob()._get_library_artists(context)

    assert len(artists) == 1
    assert artists[0]['owner_profile_id'] == 2


# --- helper units --------------------------------------------------------------------

def test_wishlist_profile_for_owner():
    w = RepairWorker._wishlist_profile_for_owner
    assert w(2) == 2
    assert w('3') == 3
    assert w(None) == 1
    assert w(0) == 1
    assert w('junk') == 1


def test_owner_helpers_are_fail_open():
    db = _FakeDB(_memdb(with_owner_column=False))  # pre-migration DB
    worker = _worker(db)

    assert worker._owner_profile_for_track(None) is None
    assert worker._owner_profile_for_track(999) is None
    assert worker._owner_profile_for_album(None) is None
    assert worker._owner_profile_for_album(999) is None
    assert worker._owner_profile_for_artist_name('') is None
    assert worker._owner_profile_for_artist_name('Nobody') is None
