"""Tests for the enrichment-worker yield policy (downloads/discovery contention)."""

from __future__ import annotations

import pytest

from core.enrichment.yield_policy import (
    ALL_YIELD_WORKERS,
    API_CONTENTION_WORKERS,
    SERVICE_YIELD_NAMES,
    discovery_state_active,
    worker_yield_reason,
    yield_name_for_service,
)


def test_downloads_pause_everything():
    for name in ALL_YIELD_WORKERS:
        assert worker_yield_reason(name, downloading=True, discovering=False) == 'downloads'


def test_discovery_pauses_only_the_contention_five():
    for name in ALL_YIELD_WORKERS:
        reason = worker_yield_reason(name, downloading=False, discovering=True)
        if name in API_CONTENTION_WORKERS:
            assert reason == 'discovery', name
        else:
            assert reason is None, name


def test_downloads_outrank_discovery_for_the_label():
    assert worker_yield_reason('spotify-enrichment', True, True) == 'downloads'


def test_idle_pauses_nothing():
    for name in ALL_YIELD_WORKERS:
        assert worker_yield_reason(name, False, False) is None


def test_unknown_and_excluded_workers_never_yield():
    # listening-stats (local media server only) and repair (user-scheduled job
    # runner) intentionally keep running through downloads.
    for name in ('listening-stats', 'repair', 'definitely-not-a-worker'):
        assert worker_yield_reason(name, True, True) is None


def test_musicbrainz_yields_for_downloads_not_discovery():
    # The case that motivated all of this: the MB worker starving the import
    # pipeline's per-track lookups (~4m15s/track measured). It must yield to
    # downloads — but keep running during discovery, which doesn't use MB.
    assert worker_yield_reason('musicbrainz', True, False) == 'downloads'
    assert worker_yield_reason('musicbrainz', False, True) is None


def test_discovery_state_active_phases():
    assert discovery_state_active({'phase': 'discovering'})
    assert discovery_state_active({'phase': 'DISCOVERING'})
    assert not discovery_state_active({'phase': 'idle'})
    assert not discovery_state_active({'phase': ''})
    assert not discovery_state_active({'phase': 'discovered'})
    assert not discovery_state_active({'phase': 'error'})
    assert not discovery_state_active({'phase': 'cancelled'})
    assert not discovery_state_active({})
    assert not discovery_state_active(None)


@pytest.mark.parametrize('phase', [
    'fresh', 'parsed', 'syncing', 'sync_complete', 'downloading', 'download_complete',
    'complete', 'done', 'queued', 'Matching tracks...',
])
def test_phases_that_sit_in_memory_dont_pause_workers(phase):
    """#1612 (cremonies): a playlist left in fresh (identifications cleared),
    sync_complete (synced, never downloaded) or download_complete kept
    "discovery" active forever and deezer, spotify, itunes, discogs and
    hydrabase stayed paused with nothing running. only discovering is live
    matching work; a real download yields through its own batch."""
    assert not discovery_state_active({'phase': phase})


def test_every_yield_worker_has_a_service_yield_name():
    # a ui resume can only stick for a worker the override set can name
    assert set(SERVICE_YIELD_NAMES.values()) == set(ALL_YIELD_WORKERS)


def test_yield_name_for_service():
    assert yield_name_for_service('deezer') == 'deezer'
    assert yield_name_for_service('itunes') == 'itunes-enrichment'
    assert yield_name_for_service('spotify', 'spotify-enrichment') == 'spotify-enrichment'
    assert yield_name_for_service('listening-stats') is None
