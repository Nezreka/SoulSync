"""Tests for the enrichment-worker yield policy (downloads/discovery contention)."""

from __future__ import annotations

from core.enrichment.yield_policy import (
    ALL_YIELD_WORKERS,
    API_CONTENTION_WORKERS,
    discovery_state_active,
    worker_yield_reason,
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
    assert discovery_state_active({'phase': 'Matching tracks...'})
    assert not discovery_state_active({'phase': 'idle'})
    assert not discovery_state_active({'phase': ''})
    assert not discovery_state_active({'phase': 'discovered'})
    assert not discovery_state_active({'phase': 'error'})
    assert not discovery_state_active({'phase': 'cancelled'})
    assert not discovery_state_active({})
    assert not discovery_state_active(None)
