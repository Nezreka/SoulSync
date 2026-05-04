"""Tests for ``core/metadata/album_mbid_cache.py``.

The persistent MBID cache is the root-cause fix for "tracks of one
album get different MUSICBRAINZ_ALBUMID tags after the in-memory cache
evicts or after a server restart." Strict additive design: every
public function degrades to None / no-op on any database error so the
existing in-memory cache + MusicBrainz lookup remains the
authoritative fallback.

These tests pin: round-trip lookup/record, idempotent re-record,
clear_all behavior, defensive None-on-empty-input, and the graceful
degradation path when the database accessor fails.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.metadata import album_mbid_cache
from database.music_database import get_database


@pytest.fixture(autouse=True)
def _wipe_cache():
    """Each test starts with a clean persistent cache so rows from
    earlier tests don't leak. Wipe AFTER too so other test files
    aren't affected."""
    album_mbid_cache.clear_all()
    yield
    album_mbid_cache.clear_all()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_lookup_returns_none_for_missing_key() -> None:
    assert album_mbid_cache.lookup('nonexistent', 'nobody') is None


def test_record_then_lookup_roundtrip() -> None:
    assert album_mbid_cache.record('gnx', 'kendrick lamar', 'release-mbid-abc-123') is True
    assert album_mbid_cache.lookup('gnx', 'kendrick lamar') == 'release-mbid-abc-123'


def test_record_is_idempotent() -> None:
    """Re-recording the same key with the same MBID succeeds and the
    lookup still returns the value. Schema uses INSERT OR REPLACE."""
    assert album_mbid_cache.record('gnx', 'kendrick lamar', 'mbid-1') is True
    assert album_mbid_cache.record('gnx', 'kendrick lamar', 'mbid-1') is True
    assert album_mbid_cache.lookup('gnx', 'kendrick lamar') == 'mbid-1'


def test_record_overwrites_with_new_mbid() -> None:
    """If the same (album, artist) pair gets re-recorded with a
    different MBID, the new value wins (last-write-wins). Surfaces
    the case where MusicBrainz reorganized a release id."""
    album_mbid_cache.record('gnx', 'kendrick lamar', 'old-mbid')
    album_mbid_cache.record('gnx', 'kendrick lamar', 'new-mbid')
    assert album_mbid_cache.lookup('gnx', 'kendrick lamar') == 'new-mbid'


def test_clear_all_wipes_all_entries() -> None:
    album_mbid_cache.record('a', 'artist1', 'mbid-1')
    album_mbid_cache.record('b', 'artist2', 'mbid-2')
    assert album_mbid_cache.lookup('a', 'artist1') == 'mbid-1'
    assert album_mbid_cache.lookup('b', 'artist2') == 'mbid-2'

    assert album_mbid_cache.clear_all() is True
    assert album_mbid_cache.lookup('a', 'artist1') is None
    assert album_mbid_cache.lookup('b', 'artist2') is None


def test_lookup_preserves_album_artist_independence() -> None:
    """Same album name across different artists must NOT collide.
    Compilation albums titled 'Greatest Hits' would otherwise clobber
    each other across artists."""
    album_mbid_cache.record('greatest hits', 'queen', 'queen-mbid')
    album_mbid_cache.record('greatest hits', 'eminem', 'eminem-mbid')
    assert album_mbid_cache.lookup('greatest hits', 'queen') == 'queen-mbid'
    assert album_mbid_cache.lookup('greatest hits', 'eminem') == 'eminem-mbid'


# ---------------------------------------------------------------------------
# Defensive paths — must NEVER raise to caller
# ---------------------------------------------------------------------------


def test_lookup_returns_none_for_empty_album_key() -> None:
    assert album_mbid_cache.lookup('', 'kendrick') is None


def test_lookup_returns_none_for_empty_artist_key() -> None:
    assert album_mbid_cache.lookup('gnx', '') is None


def test_lookup_returns_none_for_none_inputs() -> None:
    assert album_mbid_cache.lookup(None, 'kendrick') is None  # type: ignore[arg-type]
    assert album_mbid_cache.lookup('gnx', None) is None  # type: ignore[arg-type]


def test_record_returns_false_for_empty_inputs() -> None:
    assert album_mbid_cache.record('', 'artist', 'mbid') is False
    assert album_mbid_cache.record('album', '', 'mbid') is False
    assert album_mbid_cache.record('album', 'artist', '') is False


def test_lookup_degrades_to_none_when_db_unavailable() -> None:
    """Critical defensive path: if `_get_database()` returns None
    (DB module failed to load, accessor raised, etc), lookup MUST
    return None — NOT raise. This is what keeps the enrichment path
    working when this layer breaks."""
    with patch.object(album_mbid_cache, '_get_database', return_value=None):
        assert album_mbid_cache.lookup('gnx', 'kendrick') is None


def test_record_degrades_to_false_when_db_unavailable() -> None:
    """Same defensive contract for record."""
    with patch.object(album_mbid_cache, '_get_database', return_value=None):
        assert album_mbid_cache.record('gnx', 'kendrick', 'mbid') is False


def test_lookup_degrades_to_none_when_query_raises() -> None:
    """If the underlying SQL execute throws (locked DB, schema drift,
    etc), lookup must catch it and return None. No exception escapes."""

    class _ExplodingConn:
        def cursor(self):
            raise RuntimeError("simulated DB explosion")

        def close(self):
            pass

    class _StubDB:
        def _get_connection(self):
            return _ExplodingConn()

    with patch.object(album_mbid_cache, '_get_database', return_value=_StubDB()):
        assert album_mbid_cache.lookup('gnx', 'kendrick') is None


def test_record_degrades_to_false_when_commit_raises() -> None:
    """If commit fails, record returns False — caller (enrichment path)
    just doesn't get the persistent benefit, but downloads continue."""

    class _BadCommitConn:
        def cursor(self):
            class _C:
                def execute(self, *a, **kw):
                    pass
            return _C()

        def commit(self):
            raise RuntimeError("commit failed")

        def close(self):
            pass

    class _StubDB:
        def _get_connection(self):
            return _BadCommitConn()

    with patch.object(album_mbid_cache, '_get_database', return_value=_StubDB()):
        assert album_mbid_cache.record('gnx', 'kendrick', 'mbid') is False


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


def test_table_exists_after_database_init() -> None:
    """The migration in `database/music_database.py` should create the
    `mb_album_release_cache` table on database init."""
    db = get_database()
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='mb_album_release_cache'"
        )
        row = cur.fetchone()
        assert row is not None
    finally:
        conn.close()


def test_release_mbid_index_exists() -> None:
    """Reverse-lookup index (find all albums for a given MBID) helps
    future debug tooling. Pin its existence so a future migration
    refactor doesn't quietly drop it."""
    db = get_database()
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_mb_album_release_mbid'"
        )
        row = cur.fetchone()
        assert row is not None
    finally:
        conn.close()
