"""Tests for core/discovery/sync.py — playlist sync background worker."""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest

from core.discovery import sync as ds


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _FakeSyncResult:
    matched_tracks: int = 5
    failed_tracks: int = 1
    synced_tracks: int = 4
    total_tracks: int = 6
    match_details: list = None

    def __post_init__(self):
        if self.match_details is None:
            self.match_details = []


class _FakeMediaClient:
    def __init__(self, connected=True):
        self._connected = connected

    def is_connected(self):
        return self._connected


class _FakeMediaServerEngine:
    """Stand-in for MediaServerEngine — only the bits SyncDeps needs."""
    def __init__(self, plex=None, jellyfin=None, navidrome=None):
        self._clients = {'plex': plex, 'jellyfin': jellyfin, 'navidrome': navidrome}

    def client(self, name):
        return self._clients.get(name)


class _FakeSyncService:
    def __init__(self, *, media_client=None, server_type='plex',
                 sync_result=None, raise_on_sync=None,
                 spotify_client=True, plex_client=True, jellyfin_client=True):
        self._media_client = media_client
        self._server_type = server_type
        self._sync_result = sync_result or _FakeSyncResult()
        self._raise_on_sync = raise_on_sync
        self.spotify_client = object() if spotify_client else None
        # The sync_service exposes the engine so the discovery worker
        # can introspect per-server clients via self._engine.client(name).
        self._engine = _FakeMediaServerEngine(
            plex=object() if plex_client else None,
            jellyfin=object() if jellyfin_client else None,
        )
        self.progress_callback = None
        self.progress_playlist_name = None
        self.cleared_callbacks = []

    def _get_active_media_client(self):
        return (self._media_client, self._server_type)

    def set_progress_callback(self, cb, playlist_name):
        self.progress_callback = cb
        self.progress_playlist_name = playlist_name

    def clear_progress_callback(self, playlist_name):
        self.cleared_callbacks.append(playlist_name)

    async def sync_playlist(self, playlist, download_missing=False, profile_id=1, sync_mode='replace'):
        if self._raise_on_sync:
            raise self._raise_on_sync
        return self._sync_result

    async def _find_track_in_media_server(self, spotify_track):
        return None, 0.0


class _FakeConfig:
    def __init__(self, server='plex'):
        self._server = server

    def get_active_media_server(self):
        return self._server


class _FakePlex:
    def __init__(self):
        self.image_calls = []

    def set_playlist_image(self, name, url):
        self.image_calls.append((name, url))
        return True


class _FakeJellyfin:
    def __init__(self):
        self.image_calls = []

    def set_playlist_image(self, name, url):
        self.image_calls.append((name, url))
        return True


class _FakeAutomationEngine:
    def __init__(self):
        self.events = []

    def emit(self, event_type, data):
        self.events.append((event_type, data))


def _run_async_sync(coro):
    """Run a coroutine to completion using a new event loop."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build_deps(
    *,
    sync_service=None,
    config=None,
    plex=None,
    jellyfin=None,
    automation=None,
    sync_states=None,
    sync_lock=None,
    record_sync_history_start=None,
    update_automation_progress=None,
    update_and_save_sync_status=None,
    run_async=None,
):
    return ds.SyncDeps(
        config_manager=config or _FakeConfig(),
        sync_service=sync_service or _FakeSyncService(media_client=_FakeMediaClient()),
        media_server_engine=_FakeMediaServerEngine(
            plex=plex or _FakePlex(),
            jellyfin=jellyfin or _FakeJellyfin(),
        ),
        automation_engine=automation or _FakeAutomationEngine(),
        run_async=run_async or _run_async_sync,
        record_sync_history_start=record_sync_history_start or (lambda **kw: None),
        update_automation_progress=update_automation_progress or (lambda *a, **kw: None),
        update_and_save_sync_status=update_and_save_sync_status or (lambda *a, **kw: None),
        sync_states=sync_states if sync_states is not None else {},
        sync_lock=sync_lock or threading.Lock(),
    )


def _track(name='Song', artists=None, album='Album', track_id='id1'):
    return {
        'id': track_id,
        'name': name,
        'artists': artists or ['Artist'],
        'album': album,
        'duration_ms': 1000,
    }


@pytest.fixture
def patched_db(monkeypatch):
    """Stubs database access — never hits a real DB."""
    class _StubDB:
        def __init__(self):
            self.completion_calls = []
            self.track_results_calls = []

        def update_sync_history_completion(self, batch_id, matched, synced, failed):
            self.completion_calls.append((batch_id, matched, synced, failed))

        def update_sync_history_track_results(self, batch_id, results_json):
            self.track_results_calls.append((batch_id, results_json))
            return True

        def refresh_sync_history_entry(self, *args):
            pass

        def get_sync_history_entry(self, entry_id):
            return None

        def read_sync_match_cache(self, sp_id, server):
            return None

    stub = _StubDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: stub)
    return stub


# ---------------------------------------------------------------------------
# History recording
# ---------------------------------------------------------------------------

def test_records_sync_history_for_new_sync(patched_db):
    """Non-resync playlist_id triggers record_sync_history_start callback."""
    history_calls = []
    deps = _build_deps(record_sync_history_start=lambda **kw: history_calls.append(kw))

    ds.run_sync_task('p1', 'My Playlist', [_track()], deps=deps)

    assert len(history_calls) == 1
    assert history_calls[0]['playlist_id'] == 'p1'
    assert history_calls[0]['playlist_name'] == 'My Playlist'
    assert history_calls[0]['source_page'] == 'sync'


def test_resync_skips_history_record(patched_db):
    """Re-sync playlist_id (resync_<id>_<ts>) skips record_sync_history_start."""
    history_calls = []
    deps = _build_deps(record_sync_history_start=lambda **kw: history_calls.append(kw))

    ds.run_sync_task('resync_42_1234', 'Replayed', [_track()], deps=deps)

    assert history_calls == []


# ---------------------------------------------------------------------------
# Setup error path
# ---------------------------------------------------------------------------

def test_setup_error_marks_state_error(patched_db, monkeypatch):
    """Exception during track conversion → sync_states[id] = 'error'."""
    states = {}

    # Force SpotifyTrack constructor to raise to trigger setup error path
    class BoomSpotifyTrack:
        def __init__(self, **kw):
            raise ValueError("boom!")

    monkeypatch.setattr(ds, 'SpotifyTrack', BoomSpotifyTrack)
    deps = _build_deps(sync_states=states)

    ds.run_sync_task('pX', 'Playlist X', [_track()], deps=deps)

    assert states['pX']['status'] == 'error'
    assert 'boom!' in states['pX']['error']


def test_setup_error_with_automation_id_updates_progress(patched_db, monkeypatch):
    """Setup error with automation_id calls update_automation_progress with status=error."""
    auto_calls = []

    class BoomSpotifyTrack:
        def __init__(self, **kw):
            raise ValueError("setup boom")

    monkeypatch.setattr(ds, 'SpotifyTrack', BoomSpotifyTrack)
    deps = _build_deps(update_automation_progress=lambda *a, **kw: auto_calls.append((a, kw)))

    ds.run_sync_task('pY', 'PY', [_track()], automation_id='auto-1', deps=deps)

    assert any(kw.get('status') == 'error' for _, kw in auto_calls)


# ---------------------------------------------------------------------------
# Sync service errors
# ---------------------------------------------------------------------------

def test_no_sync_service_marks_error(patched_db):
    """sync_service None → caught by outer except, sync_states marked error."""
    states = {}
    deps = _build_deps(sync_states=states)
    deps.sync_service = None  # explicit override past the default fallback

    ds.run_sync_task('pZ', 'PZ', [_track()], deps=deps)

    assert states['pZ']['status'] == 'error'


def test_sync_playlist_exception_marks_error(patched_db):
    """sync_playlist raising propagates → sync_states marked error."""
    states = {}
    svc = _FakeSyncService(media_client=_FakeMediaClient(),
                           raise_on_sync=RuntimeError("network down"))
    deps = _build_deps(sync_service=svc, sync_states=states)

    ds.run_sync_task('pErr', 'PErr', [_track()], deps=deps)

    assert states['pErr']['status'] == 'error'
    assert 'network down' in states['pErr']['error']


# ---------------------------------------------------------------------------
# Successful sync
# ---------------------------------------------------------------------------

def test_successful_sync_marks_state_finished(patched_db):
    """Successful sync transitions sync_states to 'finished' with result_dict."""
    states = {}
    result = _FakeSyncResult(matched_tracks=10, total_tracks=12, synced_tracks=10, failed_tracks=2)
    svc = _FakeSyncService(media_client=_FakeMediaClient(), sync_result=result)
    deps = _build_deps(sync_service=svc, sync_states=states)

    ds.run_sync_task('pOK', 'POK', [_track()], deps=deps)

    assert states['pOK']['status'] == 'finished'
    assert states['pOK']['progress']['matched_tracks'] == 10


def test_unmatched_tracks_summary_added_to_state(patched_db):
    """match_details with not_found entries → unmatched_tracks summary on result_dict."""
    states = {}
    md = [
        {'name': 'Lost1', 'artist': 'A', 'image_url': '', 'status': 'not_found'},
        {'name': 'Found1', 'artist': 'B', 'status': 'matched'},
        {'name': 'Lost2', 'artist': 'C', 'image_url': '', 'status': 'not_found'},
    ]
    result = _FakeSyncResult(match_details=md)
    svc = _FakeSyncService(media_client=_FakeMediaClient(), sync_result=result)
    deps = _build_deps(sync_service=svc, sync_states=states)

    ds.run_sync_task('pU', 'PU', [_track()], deps=deps)

    unmatched = states['pU']['progress'].get('unmatched_tracks', [])
    assert len(unmatched) == 2
    assert unmatched[0]['name'] == 'Lost1'


# ---------------------------------------------------------------------------
# Playlist image upload
# ---------------------------------------------------------------------------

def test_playlist_image_uploaded_to_plex(patched_db):
    """Plex active server + image_url + synced > 0 → plex_client.set_playlist_image called."""
    plex = _FakePlex()
    cfg = _FakeConfig(server='plex')
    result = _FakeSyncResult(synced_tracks=5)
    svc = _FakeSyncService(media_client=_FakeMediaClient(), sync_result=result)
    deps = _build_deps(sync_service=svc, plex=plex, config=cfg)

    ds.run_sync_task('pImg', 'PImg', [_track()],
                     playlist_image_url='https://img/x.png', deps=deps)

    assert plex.image_calls == [('PImg', 'https://img/x.png')]


def test_playlist_image_uploaded_to_jellyfin(patched_db):
    """Jellyfin/Emby active → jellyfin_client.set_playlist_image."""
    jf = _FakeJellyfin()
    cfg = _FakeConfig(server='jellyfin')
    result = _FakeSyncResult(synced_tracks=3)
    svc = _FakeSyncService(media_client=_FakeMediaClient(), sync_result=result)
    deps = _build_deps(sync_service=svc, jellyfin=jf, config=cfg)

    ds.run_sync_task('pJF', 'PJF', [_track()],
                     playlist_image_url='https://img/y.png', deps=deps)

    assert jf.image_calls == [('PJF', 'https://img/y.png')]


def test_no_image_upload_when_zero_synced(patched_db):
    """synced_tracks == 0 → no playlist image upload."""
    plex = _FakePlex()
    result = _FakeSyncResult(synced_tracks=0)
    svc = _FakeSyncService(media_client=_FakeMediaClient(), sync_result=result)
    deps = _build_deps(sync_service=svc, plex=plex)

    ds.run_sync_task('pNoImg', 'PNoImg', [_track()],
                     playlist_image_url='https://img/z.png', deps=deps)

    assert plex.image_calls == []


# ---------------------------------------------------------------------------
# Automation engine
# ---------------------------------------------------------------------------

def test_automation_engine_emits_playlist_synced(patched_db):
    """Successful sync emits 'playlist_synced' event on automation_engine."""
    ae = _FakeAutomationEngine()
    result = _FakeSyncResult(matched_tracks=7, total_tracks=8, synced_tracks=7, failed_tracks=1)
    svc = _FakeSyncService(media_client=_FakeMediaClient(), sync_result=result)
    deps = _build_deps(sync_service=svc, automation=ae)

    ds.run_sync_task('pE', 'PE', [_track()], deps=deps)

    assert any(evt == 'playlist_synced' for evt, _ in ae.events)


def test_automation_progress_finished_called(patched_db):
    """automation_id provided + sync OK → update_automation_progress called with status=finished."""
    auto_calls = []
    svc = _FakeSyncService(media_client=_FakeMediaClient())
    deps = _build_deps(sync_service=svc,
                       update_automation_progress=lambda *a, **kw: auto_calls.append(kw))

    ds.run_sync_task('pA', 'PA', [_track()], automation_id='auto-99', deps=deps)

    assert any(kw.get('status') == 'finished' for kw in auto_calls)


# ---------------------------------------------------------------------------
# Sync history persistence
# ---------------------------------------------------------------------------

def test_sync_history_completion_saved(patched_db):
    """Successful sync calls update_sync_history_completion on the DB."""
    result = _FakeSyncResult(matched_tracks=4, synced_tracks=4, failed_tracks=0)
    svc = _FakeSyncService(media_client=_FakeMediaClient(), sync_result=result)
    deps = _build_deps(sync_service=svc)

    ds.run_sync_task('pHist', 'PHist', [_track()], deps=deps)

    assert len(patched_db.completion_calls) == 1
    bid, matched, synced, failed = patched_db.completion_calls[0]
    assert matched == 4 and synced == 4 and failed == 0


def test_match_details_persisted_to_track_results(patched_db):
    """match_details on result → update_sync_history_track_results called with JSON."""
    md = [{'name': 'T1', 'status': 'matched'}]
    result = _FakeSyncResult(match_details=md)
    svc = _FakeSyncService(media_client=_FakeMediaClient(), sync_result=result)
    deps = _build_deps(sync_service=svc)

    ds.run_sync_task('pMD', 'PMD', [_track()], deps=deps)

    assert len(patched_db.track_results_calls) == 1


# ---------------------------------------------------------------------------
# Sync status save (smart-skip hash)
# ---------------------------------------------------------------------------

def test_update_and_save_sync_status_called(patched_db):
    """update_and_save_sync_status called with a tracks_hash for smart-skip."""
    save_calls = []
    svc = _FakeSyncService(media_client=_FakeMediaClient())
    deps = _build_deps(sync_service=svc,
                       update_and_save_sync_status=lambda *a, **kw: save_calls.append((a, kw)))

    ds.run_sync_task('pSS', 'PSS', [_track(track_id='abc'), _track(track_id='def')], deps=deps)

    assert len(save_calls) == 1
    args, kwargs = save_calls[0]
    assert kwargs.get('tracks_hash')  # md5 hash present


# ---------------------------------------------------------------------------
# Cleanup (finally)
# ---------------------------------------------------------------------------

def test_finally_clears_progress_callback(patched_db):
    """finally block clears sync_service progress callback."""
    svc = _FakeSyncService(media_client=_FakeMediaClient())
    deps = _build_deps(sync_service=svc)

    ds.run_sync_task('pCB', 'PCB', [_track()], deps=deps)

    # Both the explicit clear (after run_async) and the finally block run
    assert 'PCB' in svc.cleared_callbacks


def test_finally_drops_original_tracks_map(patched_db):
    """finally block deletes _original_tracks_map attribute when present."""
    svc = _FakeSyncService(media_client=_FakeMediaClient())
    deps = _build_deps(sync_service=svc)

    ds.run_sync_task('pTM', 'PTM', [_track()], deps=deps)

    assert not hasattr(svc, '_original_tracks_map')
