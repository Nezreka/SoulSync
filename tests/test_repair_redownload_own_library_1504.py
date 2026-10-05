"""Regression tests for issue #1504 Phase 3 — repair re-downloads route to the
owning profile's wishlist instead of always landing on profile 1.

The choke point is `_track_identity_for_redownload` (sites 2-6): it must
carry `owner_profile_id` from the track row into the wishlist payload, and
each fix handler must pass it as `profile_id=` to `add_to_wishlist`.
NULL owner maps to 1 explicitly (the wishlist column is NOT NULL).
"""
import sqlite3

import pytest

from core.repair_worker import RepairWorker


def _make_db(owner_profile_id):
    """Minimal tracks/artists/albums schema WITH the owner column."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT,
                    owner_profile_id INTEGER)""")
    conn.execute("""CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT,
                    spotify_album_id TEXT, record_type TEXT, track_count INTEGER,
                    year INTEGER, thumb_url TEXT, owner_profile_id INTEGER)""")
    conn.execute("""CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT,
                    track_number INTEGER, duration INTEGER, artist_id INTEGER,
                    album_id INTEGER, spotify_track_id TEXT, itunes_track_id TEXT,
                    deezer_id TEXT, owner_profile_id INTEGER)""")
    conn.execute("INSERT INTO artists (id, name, owner_profile_id) VALUES (1, 'Artist A', ?)",
                 (owner_profile_id,))
    conn.execute("""INSERT INTO albums (id, title, record_type, track_count, year, owner_profile_id)
                    VALUES (1, 'Album X', 'album', 10, 2020, ?)""",
                 (owner_profile_id,))
    conn.execute("""INSERT INTO tracks (id, title, track_number, duration, artist_id,
                    album_id, owner_profile_id)
                    VALUES (3, 'Cutoff Song', 4, 210000, 1, 1, ?)""",
                 (owner_profile_id,))
    conn.commit()
    return conn


class _DB:
    def __init__(self, conn):
        self._conn = conn
        self.captured = {}

    def _get_connection(self):
        return self._conn

    def add_to_wishlist(self, *args, **kwargs):
        self.captured.update(kwargs)
        if args:
            self.captured['spotify_track_data'] = args[0]
        return True


def _worker_with_db(conn):
    worker = object.__new__(RepairWorker)
    worker.db = _DB(conn)
    return worker


def test_track_identity_carries_owner_profile_id():
    """The choke-point SELECT must include t.owner_profile_id."""
    conn = _make_db(7)
    worker = _worker_with_db(conn)
    try:
        ident = worker._track_identity_for_redownload(3, {})
    finally:
        conn.close()
    assert ident is not None
    assert ident['owner_profile_id'] == 7


def test_track_identity_null_owner_stays_null_for_caller_to_map():
    """NULL owner comes through as None; the call sites map it to 1."""
    conn = _make_db(None)
    worker = _worker_with_db(conn)
    try:
        ident = worker._track_identity_for_redownload(3, {})
    finally:
        conn.close()
    assert ident is not None
    assert ident['owner_profile_id'] is None


def test_quality_upgrade_routes_to_owner_wishlist():
    """Site 2: quality-upgrade redownload passes profile_id= (not just
    quality_profile_id=) from the track row."""
    conn = _make_db(7)
    worker = _worker_with_db(conn)
    details = {
        'quality_issue': 'below_profile',
        'current_format': 'FLAC 16-bit',
        'expected_title': 'Cutoff Song',
        'expected_artist': 'Artist A',
        'album_title': 'Album X',
    }
    try:
        res = worker._fix_quality_upgrade('track', 3, '/music/c.flac', details)
    finally:
        conn.close()
    assert res['success'] is True
    assert worker.db.captured['profile_id'] == 7
    # quality_profile_id is a SEPARATE kwarg and must not be confused.
    assert 'quality_profile_id' in worker.db.captured


def test_quality_upgrade_null_owner_maps_to_profile_1():
    """NULL owner -> profile_id=1 (wishlist column is NOT NULL)."""
    conn = _make_db(None)
    worker = _worker_with_db(conn)
    details = {
        'quality_issue': 'below_profile',
        'current_format': 'FLAC 16-bit',
        'expected_title': 'Cutoff Song',
        'expected_artist': 'Artist A',
        'album_title': 'Album X',
    }
    try:
        res = worker._fix_quality_upgrade('track', 3, '/music/c.flac', details)
    finally:
        conn.close()
    assert res['success'] is True
    assert worker.db.captured['profile_id'] == 1


def test_discography_backfill_fix_reads_owner_from_details():
    """Site 1: _fix_discography_backfill uses details['owner_profile_id']."""
    conn = _make_db(None)
    worker = _worker_with_db(conn)
    details = {
        'track_data': {'id': 'x', 'name': 'Missing Song',
                       'artists': [{'name': 'Artist A'}]},
        'artist_name': 'Artist A',
        'owner_profile_id': 9,
    }
    try:
        res = worker._fix_discography_backfill('track', 'x', None, details)
    finally:
        conn.close()
    assert res['success'] is True
    assert worker.db.captured['profile_id'] == 9


def test_discography_backfill_fix_null_owner_maps_to_profile_1():
    conn = _make_db(None)
    worker = _worker_with_db(conn)
    details = {
        'track_data': {'id': 'x', 'name': 'Missing Song',
                       'artists': [{'name': 'Artist A'}]},
        'artist_name': 'Artist A',
    }
    try:
        res = worker._fix_discography_backfill('track', 'x', None, details)
    finally:
        conn.close()
    assert res['success'] is True
    assert worker.db.captured['profile_id'] == 1
