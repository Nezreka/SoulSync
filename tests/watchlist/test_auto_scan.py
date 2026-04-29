"""Tests for core/watchlist/auto_scan.py — auto-scan orchestrator."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from core.watchlist import auto_scan as autosc


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeApp:
    @contextmanager
    def app_context(self):
        yield


class _FakeSpotify:
    def __init__(self, authenticated=True):
        self._authenticated = authenticated

    def is_authenticated(self):
        return self._authenticated


class _FakeAutomationEngine:
    def __init__(self):
        self.events = []

    def emit(self, event_type, data):
        self.events.append((event_type, data))


@dataclass
class _ScanResult:
    success: bool = True
    new_tracks_found: int = 0
    tracks_added_to_wishlist: int = 0


class _FakeScanner:
    def __init__(self, results=None):
        self._results = results or []
        self.scan_calls = []
        self.discovery_calls = []
        self.lastfm_called = False
        self.cache_calls = []
        self.cutoff_cleared = False

    def backfill_watchlist_artist_images(self, profile_id):
        return 0

    def scan_watchlist_artists(self, artists, *, scan_state, progress_callback, cancel_check):
        self.scan_calls.append((artists, scan_state))
        return self._results

    def populate_discovery_pool(self, profile_id, progress_callback=None):
        self.discovery_calls.append(profile_id)

    def _generate_lastfm_radio_playlists(self):
        self.lastfm_called = True

    def sync_spotify_library_cache(self, profile_id):
        self.cache_calls.append(profile_id)

    def _clear_rescan_cutoff(self):
        self.cutoff_cleared = True


class _FakeDB:
    def __init__(self, profiles=None, watchlist_count=0, watchlist_artists=None,
                 lb_profiles=None):
        self._profiles = profiles or [{'id': 1}]
        self._watchlist_count = watchlist_count
        self._watchlist_artists = watchlist_artists or []
        self._lb_profiles = lb_profiles or []
        self.database_path = '/tmp/test.db'

    def get_all_profiles(self):
        return self._profiles

    def get_watchlist_count(self, profile_id=1):
        return self._watchlist_count

    def get_watchlist_artists(self, profile_id=1):
        return self._watchlist_artists

    def get_profiles_with_listenbrainz(self):
        return self._lb_profiles


def _build_deps(
    *,
    actually_scanning=False,
    flag_set=False,
    spotify_auth=True,
    progress_log=None,
    activity_log=None,
):
    progress_log = progress_log if progress_log is not None else []
    activity_log = activity_log if activity_log is not None else []
    auto_flag = [flag_set]
    auto_ts = [0.0]
    state_ref = [{}]

    deps = autosc.WatchlistAutoScanDeps(
        app=_FakeApp(),
        spotify_client=_FakeSpotify(authenticated=spotify_auth),
        automation_engine=_FakeAutomationEngine(),
        watchlist_timer_lock=threading.Lock(),
        is_watchlist_actually_scanning=lambda: actually_scanning,
        pause_enrichment_workers=lambda label: {'paused': True},
        resume_enrichment_workers=lambda state, label: None,
        update_automation_progress=lambda *a, **kw: progress_log.append((a, kw)),
        add_activity_item=lambda *a, **kw: activity_log.append((a, kw)),
        _get_auto_scanning=lambda: auto_flag[0],
        _set_auto_scanning=lambda v: auto_flag.__setitem__(0, v),
        _get_auto_scanning_timestamp=lambda: auto_ts[0],
        _set_auto_scanning_timestamp=lambda v: auto_ts.__setitem__(0, v),
        _get_watchlist_scan_state=lambda: state_ref[0],
        _set_watchlist_scan_state=lambda v: state_ref.__setitem__(0, v),
    )
    deps._auto_flag = auto_flag
    deps._state_ref = state_ref
    deps._progress_log = progress_log
    deps._activity_log = activity_log
    return deps


@pytest.fixture
def patched_modules(monkeypatch):
    """Stub out core.watchlist_scanner.get_watchlist_scanner + database access."""
    scanner = _FakeScanner(results=[_ScanResult(success=True, new_tracks_found=2,
                                                 tracks_added_to_wishlist=1)])
    db = _FakeDB(watchlist_count=3,
                 watchlist_artists=[{'id': 1, 'name': 'A1'}, {'id': 2, 'name': 'A2'}])
    import core.watchlist_scanner as ws_mod
    monkeypatch.setattr(ws_mod, 'get_watchlist_scanner', lambda spotify: scanner)
    monkeypatch.setattr('database.music_database.get_database', lambda: db)
    # Stub seasonal + listenbrainz so post-scan steps don't crash trying to import real impl
    import core.seasonal_discovery as seasonal_mod
    seasonal_service = type('S', (), {
        'get_current_season': lambda self: None,
        'should_populate_seasonal_content': lambda self, s, days_threshold: False,
        'populate_seasonal_content': lambda self, s: None,
        'curate_seasonal_playlist': lambda self, s: None,
    })()
    monkeypatch.setattr(seasonal_mod, 'get_seasonal_discovery_service',
                        lambda spotify, db: seasonal_service)
    return scanner, db


# ---------------------------------------------------------------------------
# Stuck-detection guard
# ---------------------------------------------------------------------------

def test_already_scanning_returns_immediately(patched_modules):
    """is_watchlist_actually_scanning() True → bail before doing anything."""
    deps = _build_deps(actually_scanning=True)

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    # No state initialized, no scanner called, no flag set
    assert deps._state_ref[0] == {}
    assert deps._auto_flag[0] is False


def test_race_check_inside_lock(patched_modules):
    """If get_auto_scanning_flag returns True after the smart-detect, bail."""
    deps = _build_deps(actually_scanning=False, flag_set=True)

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    # Should have bailed at the lock-internal check; scan didn't run.
    scanner, _ = patched_modules
    assert scanner.scan_calls == []


# ---------------------------------------------------------------------------
# No watchlist artists
# ---------------------------------------------------------------------------

def test_zero_watchlist_count_clears_flag_and_returns(patched_modules, monkeypatch):
    """When watchlist count is 0, function clears flag and returns."""
    scanner, _ = patched_modules
    monkeypatch.setattr('database.music_database.get_database',
                        lambda: _FakeDB(watchlist_count=0))
    deps = _build_deps()

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    # Flag was set then cleared
    assert deps._auto_flag[0] is False
    assert scanner.scan_calls == []


# ---------------------------------------------------------------------------
# Spotify auth gate
# ---------------------------------------------------------------------------

def test_unauthenticated_spotify_clears_flag(patched_modules):
    """Spotify not authenticated → clear flag, return without scanning."""
    scanner, _ = patched_modules
    deps = _build_deps(spotify_auth=False)

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    assert deps._auto_flag[0] is False
    assert scanner.scan_calls == []


# ---------------------------------------------------------------------------
# Successful scan
# ---------------------------------------------------------------------------

def test_successful_scan_runs_post_steps(patched_modules):
    """Scan completes → discovery pool + lastfm + library sync all run."""
    scanner, db = patched_modules
    deps = _build_deps()

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    # Scanner was called with the watchlist
    assert len(scanner.scan_calls) == 1
    # Post-scan steps fired
    assert scanner.discovery_calls == [1]
    assert scanner.lastfm_called is True
    assert scanner.cache_calls == [1]
    # State has summary
    assert deps._state_ref[0]['status'] == 'completed'
    assert deps._state_ref[0]['summary']['new_tracks_found'] == 2


def test_completion_emits_automation_event(patched_modules):
    """Successful scan emits 'watchlist_scan_completed' on automation_engine."""
    scanner, _ = patched_modules
    deps = _build_deps()

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    assert any(name == 'watchlist_scan_completed' for name, _ in deps.automation_engine.events)


def test_activity_feed_logged_when_tracks_added(patched_modules):
    """Successful scan adding > 0 tracks logs an activity feed entry."""
    scanner, _ = patched_modules
    deps = _build_deps()

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    assert deps._activity_log  # at least one activity fired


# ---------------------------------------------------------------------------
# Cancellation mid-scan
# ---------------------------------------------------------------------------

def test_cancelled_scan_skips_post_steps(patched_modules, monkeypatch):
    """If scanner sets cancel_requested mid-flight, post-scan steps skipped."""
    scanner, _ = patched_modules

    def cancel_during_scan(artists, *, scan_state, progress_callback, cancel_check):
        scan_state['cancel_requested'] = True
        return []

    scanner.scan_watchlist_artists = cancel_during_scan
    deps = _build_deps()

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    # No post-scan steps ran
    assert scanner.discovery_calls == []
    assert scanner.lastfm_called is False


# ---------------------------------------------------------------------------
# Profile-scoped trigger
# ---------------------------------------------------------------------------

def test_profile_scoped_trigger_only_scans_that_profile(patched_modules, monkeypatch):
    """When profile_id is provided, only that profile's watchlist is scanned."""
    db = _FakeDB(profiles=[{'id': 1}, {'id': 2}],
                 watchlist_count=3,
                 watchlist_artists=[{'id': 99, 'name': 'X'}])
    monkeypatch.setattr('database.music_database.get_database', lambda: db)
    deps = _build_deps()

    autosc.process_watchlist_scan_automatically(automation_id='a1', profile_id=2, deps=deps)

    scanner, _ = patched_modules
    assert len(scanner.scan_calls) == 1


# ---------------------------------------------------------------------------
# Cleanup runs in finally
# ---------------------------------------------------------------------------

def test_finally_resets_auto_scanning_flag(patched_modules):
    """Even after a successful scan, the auto_scanning flag is reset."""
    deps = _build_deps()

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    assert deps._auto_flag[0] is False


def test_finally_clears_rescan_cutoff(patched_modules):
    """scanner._clear_rescan_cutoff() called via finally."""
    scanner, _ = patched_modules
    deps = _build_deps()

    autosc.process_watchlist_scan_automatically(automation_id='a1', deps=deps)

    assert scanner.cutoff_cleared is True
