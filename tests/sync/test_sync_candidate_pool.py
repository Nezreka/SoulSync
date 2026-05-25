"""Tests for the lazy per-artist candidate pool in PlaylistSyncService.

The pool replaces a per-track SQL storm: instead of running ~30
title-variation × artist-variation queries for every playlist track,
sync now fetches each unique artist's library tracks once and feeds the
matcher via the in-memory `candidate_tracks` path. The fetch is *lazy*
— it only fires when a track actually misses the sync_match_cache,
so warm-cache playlists pay zero pool cost.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from services.sync_service import PlaylistSyncService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_service() -> PlaylistSyncService:
    """Bare PlaylistSyncService — pool helper doesn't touch service state."""
    return PlaylistSyncService(
        spotify_client=MagicMock(),
        download_orchestrator=MagicMock(),
        media_server_engine=MagicMock(),
    )


def _make_db_stub(indexed_returns=None, search_returns=None, raise_on_search=None) -> MagicMock:
    """MusicDatabase stub mirroring the contract the helper relies on:
    - get_artist_tracks_indexed is the fast path (indexed artist_id lookup)
    - search_tracks is the slow LIKE-based fallback for recall edge cases

    Pool-key normalization runs through `core.text.normalize` directly,
    not through the db, so no `_normalize_for_comparison` stub is needed.
    """
    db = MagicMock()
    db.get_artist_tracks_indexed.return_value = indexed_returns if indexed_returns is not None else []
    if raise_on_search is not None:
        db.search_tracks.side_effect = raise_on_search
        db.get_artist_tracks_indexed.side_effect = raise_on_search
    else:
        db.search_tracks.return_value = search_returns if search_returns is not None else []
    return db


# ---------------------------------------------------------------------------
# Pooling disabled — legacy fallback
# ---------------------------------------------------------------------------

def test_returns_none_when_pool_disabled():
    """candidate_pool=None signals callers to fall through to the legacy
    per-track SQL loop. Helper must not touch the DB."""
    svc = _make_service()
    db = _make_db_stub()
    result = svc._get_or_fetch_artist_candidates(None, db, 'Drake', 'plex')
    assert result is None
    db.search_tracks.assert_not_called()
    db.get_artist_tracks_indexed.assert_not_called()


# ---------------------------------------------------------------------------
# Lazy population
# ---------------------------------------------------------------------------

def test_indexed_fast_path_hits_skip_the_like_fallback():
    """When the indexed lookup finds tracks, the LIKE-based fallback must
    NOT run — that's the whole perf point of the fast path."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(indexed_returns=['t1', 't2'])
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    assert result == ['t1', 't2']
    assert pool == {'drake': ['t1', 't2']}
    db.get_artist_tracks_indexed.assert_called_once_with(
        'Drake', server_source='plex', limit=10000,
    )
    db.search_tracks.assert_not_called()


def test_like_fallback_runs_when_indexed_returns_empty():
    """Diacritics / featured-artist recall lives in the LIKE path. The
    helper must fall through to search_tracks when the indexed lookup
    finds nothing, otherwise sync regresses on those cases. Note that
    the pool key is accent-folded (`Beyoncé` → `beyonce`) so library
    spellings with/without diacritics share one entry."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(indexed_returns=[], search_returns=['feature-track'])
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Beyoncé', 'plex')
    assert result == ['feature-track']
    assert pool == {'beyonce': ['feature-track']}
    db.get_artist_tracks_indexed.assert_called_once()
    db.search_tracks.assert_called_once_with(
        artist='Beyoncé', limit=10000, server_source='plex',
    )


def test_second_call_for_same_artist_reuses_cache():
    """Once an artist's pool is populated, subsequent lookups must not
    re-fetch — that's the whole perf point of the pool."""
    svc = _make_service()
    pool = {'drake': ['cached']}
    db = _make_db_stub(indexed_returns=['fresh'], search_returns=['stale'])
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    assert result == ['cached']
    db.get_artist_tracks_indexed.assert_not_called()
    db.search_tracks.assert_not_called()


def test_artist_absent_from_library_cached_as_empty_list():
    """Both paths return [] → cache [] so the next call short-circuits
    via check_track_exists' batched path without firing SQL again."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(indexed_returns=[], search_returns=[])
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Obscure', 'plex')
    assert result == []
    assert pool == {'obscure': []}


def test_none_return_normalized_to_empty_list():
    """Defensive — if both paths ever return None, helper must coerce
    to [] so the cached value is still a valid iterable for the matcher."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub()
    db.get_artist_tracks_indexed.return_value = None
    db.search_tracks.return_value = None
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Anyone', 'plex')
    assert result == []
    assert pool == {'anyone': []}


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_fetch_failure_returns_none_and_does_not_cache():
    """A pool fetch exception must not poison the dict — the per-track
    legacy path still has a chance to run for this track, and a later
    track for the same artist can retry the fetch."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(raise_on_search=RuntimeError('DB exploded'))
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    assert result is None
    assert pool == {}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_pool_key_is_normalized_so_casing_variants_share_one_fetch():
    """'Drake' and 'DRAKE' must hash to the same pool entry — otherwise
    a playlist that mixes casing would re-fetch the same artist twice."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(indexed_returns=['t'])
    svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    db.get_artist_tracks_indexed.reset_mock()
    db.search_tracks.reset_mock()
    result = svc._get_or_fetch_artist_candidates(pool, db, 'DRAKE', 'plex')
    assert result == ['t']
    db.get_artist_tracks_indexed.assert_not_called()
    db.search_tracks.assert_not_called()


def test_different_artists_get_separate_pool_entries():
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub()
    db.get_artist_tracks_indexed.side_effect = [['drake-track'], ['sza-track']]
    svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    svc._get_or_fetch_artist_candidates(pool, db, 'SZA', 'plex')
    assert pool == {'drake': ['drake-track'], 'sza': ['sza-track']}
    assert db.get_artist_tracks_indexed.call_count == 2


# ---------------------------------------------------------------------------
# Server source plumbing
# ---------------------------------------------------------------------------

def test_active_server_is_passed_through_to_indexed_path():
    """Misrouting server_source would make the pool include tracks from
    the wrong server (e.g. Plex tracks in a Jellyfin sync) — verify it
    survives the trip on the fast path."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(indexed_returns=['t'])
    svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'jellyfin')
    db.get_artist_tracks_indexed.assert_called_once_with(
        'Drake', server_source='jellyfin', limit=10000,
    )


def test_active_server_is_passed_through_to_like_fallback():
    """Same server_source check for the slow LIKE-based fallback path."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(indexed_returns=[], search_returns=['t'])
    svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'jellyfin')
    db.search_tracks.assert_called_once_with(
        artist='Drake', limit=10000, server_source='jellyfin',
    )
