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


def _make_db_stub(search_returns=None, raise_on_search=None) -> MagicMock:
    """MusicDatabase stub mirroring the contract the helper relies on:
    - _normalize_for_comparison returns a lower-cased key
    - search_tracks returns a list (or raises)
    """
    db = MagicMock()
    db._normalize_for_comparison.side_effect = lambda s: s.lower().strip()
    if raise_on_search is not None:
        db.search_tracks.side_effect = raise_on_search
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


# ---------------------------------------------------------------------------
# Lazy population
# ---------------------------------------------------------------------------

def test_first_call_for_artist_runs_search_and_caches():
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(search_returns=['t1', 't2'])
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    assert result == ['t1', 't2']
    assert pool == {'drake': ['t1', 't2']}
    db.search_tracks.assert_called_once_with(
        artist='Drake', limit=10000, server_source='plex',
    )


def test_second_call_for_same_artist_reuses_cache():
    """Once an artist's pool is populated, subsequent lookups must not
    re-fetch — that's the whole perf point of the pool."""
    svc = _make_service()
    pool = {'drake': ['cached']}
    db = _make_db_stub(search_returns=['fresh'])
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    assert result == ['cached']
    db.search_tracks.assert_not_called()


def test_empty_result_is_still_cached():
    """Artist not in library → empty list cached. Next call short-circuits
    via check_track_exists' batched path without firing SQL."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(search_returns=[])
    result = svc._get_or_fetch_artist_candidates(pool, db, 'Obscure', 'plex')
    assert result == []
    assert pool == {'obscure': []}


def test_none_return_normalized_to_empty_list():
    """Defensive — if search_tracks ever returns None, helper must coerce
    to [] so the cached value is still a valid iterable for the matcher."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub()
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
    db = _make_db_stub(search_returns=['t'])
    svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    db.search_tracks.reset_mock()
    result = svc._get_or_fetch_artist_candidates(pool, db, 'DRAKE', 'plex')
    assert result == ['t']
    db.search_tracks.assert_not_called()


def test_different_artists_get_separate_pool_entries():
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub()
    db.search_tracks.side_effect = [['drake-track'], ['sza-track']]
    svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'plex')
    svc._get_or_fetch_artist_candidates(pool, db, 'SZA', 'plex')
    assert pool == {'drake': ['drake-track'], 'sza': ['sza-track']}
    assert db.search_tracks.call_count == 2


# ---------------------------------------------------------------------------
# Server source plumbing
# ---------------------------------------------------------------------------

def test_active_server_is_passed_through_to_search_tracks():
    """Misrouting server_source would make the pool include tracks from
    the wrong server (e.g. Plex tracks in a Jellyfin sync) — verify it
    survives the trip."""
    svc = _make_service()
    pool: dict = {}
    db = _make_db_stub(search_returns=['t'])
    svc._get_or_fetch_artist_candidates(pool, db, 'Drake', 'jellyfin')
    db.search_tracks.assert_called_once_with(
        artist='Drake', limit=10000, server_source='jellyfin',
    )
