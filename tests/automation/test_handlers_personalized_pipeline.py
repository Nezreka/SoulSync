"""Boundary tests for the personalized playlist pipeline handler.

Pin every shape: empty kinds error, refresh_first behaviour, snapshot
load + sync dispatch, missing-tracks skip, exception swallowing,
pipeline_running flag cleanup, sync payload shape passed to
_run_sync_task."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, List

import pytest

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers.personalized_pipeline import (
    auto_personalized_pipeline,
    _build_payloads_for_kinds,
    _track_to_sync_shape,
    _sync_personalized_playlist,
)


class _StubLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _build_deps(**overrides) -> AutomationDeps:
    defaults = dict(
        engine=object(),
        state=AutomationState(),
        config_manager=object(),
        update_progress=lambda *a, **k: None,
        logger=_StubLogger(),
        get_database=lambda: object(),
        spotify_client=None,
        tidal_client=None,
        web_scan_manager=None,
        process_wishlist_automatically=lambda **k: None,
        process_watchlist_scan_automatically=lambda **k: None,
        is_wishlist_actually_processing=lambda: False,
        is_watchlist_actually_scanning=lambda: False,
        get_watchlist_scan_state=lambda: {},
        run_playlist_discovery_worker=lambda *a, **k: None,
        run_sync_task=lambda *a, **k: None,
        load_sync_status_file=lambda: {},
        get_deezer_client=lambda: None,
        parse_youtube_playlist=lambda url: None,
        get_sync_states=lambda: {},
        set_db_update_automation_id=lambda v: None,
        get_db_update_state=lambda: {},
        db_update_lock=threading.Lock(),
        db_update_executor=None,
        run_db_update_task=lambda *a, **k: None,
        run_deep_scan_task=lambda *a, **k: None,
        get_duplicate_cleaner_state=lambda: {},
        duplicate_cleaner_lock=threading.Lock(),
        duplicate_cleaner_executor=None,
        run_duplicate_cleaner=lambda: None,
        get_quality_scanner_state=lambda: {},
        quality_scanner_lock=threading.Lock(),
        quality_scanner_executor=None,
        run_quality_scanner=lambda *a, **k: None,
        download_orchestrator=None,
        run_async=lambda coro: None,
        tasks_lock=threading.Lock(),
        get_download_batches=lambda: {},
        get_download_tasks=lambda: {},
        sweep_empty_download_directories=lambda: 0,
        get_staging_path=lambda: '/staging',
        docker_resolve_path=lambda p: p,
        get_current_profile_id=lambda: 1,
        get_watchlist_scanner=lambda spc: None,
        get_app=lambda: None,
        get_beatport_data_cache=lambda: {'cache_lock': threading.Lock(), 'homepage': {}},
        init_automation_progress=lambda *a, **k: None,
        record_progress_history=lambda *a, **k: None,
        build_personalized_manager=lambda: None,
    )
    defaults.update(overrides)
    return AutomationDeps(**defaults)  # type: ignore[arg-type]


# ─── Track shape converter ───────────────────────────────────────────


class TestTrackToSyncShape:
    def test_basic_shape(self):
        track = SimpleNamespace(
            track_name='Song', artist_name='Artist', album_name='Album',
            spotify_track_id='sp-1', itunes_track_id=None, deezer_track_id=None,
            duration_ms=200000,
        )
        out = _track_to_sync_shape(track)
        assert out == {
            'name': 'Song',
            'artists': [{'name': 'Artist'}],
            'album': {'name': 'Album'},
            'duration_ms': 200000,
            'id': 'sp-1',
        }

    def test_falls_back_through_source_ids(self):
        t1 = SimpleNamespace(track_name='', artist_name='', album_name='',
                              spotify_track_id=None, itunes_track_id='it-1',
                              deezer_track_id=None, duration_ms=0)
        assert _track_to_sync_shape(t1)['id'] == 'it-1'

        t2 = SimpleNamespace(track_name='', artist_name='', album_name='',
                              spotify_track_id=None, itunes_track_id=None,
                              deezer_track_id='dz-1', duration_ms=0)
        assert _track_to_sync_shape(t2)['id'] == 'dz-1'

    def test_no_id_returns_empty_string(self):
        t = SimpleNamespace(track_name='X', artist_name='Y', album_name='Z',
                             spotify_track_id=None, itunes_track_id=None,
                             deezer_track_id=None, duration_ms=0)
        assert _track_to_sync_shape(t)['id'] == ''


# ─── Empty / config validation ──────────────────────────────────────


class TestEmptyConfig:
    def test_no_kinds_returns_error_and_clears_flag(self):
        deps = _build_deps()
        deps.state.set_pipeline_running(True)  # simulate already-running
        result = auto_personalized_pipeline({}, deps)
        assert result['status'] == 'error'
        assert 'No personalized playlist' in result['error']
        assert deps.state.pipeline_running is False

    def test_empty_kinds_list_returns_error(self):
        deps = _build_deps()
        result = auto_personalized_pipeline({'kinds': []}, deps)
        assert result['status'] == 'error'
        assert deps.state.pipeline_running is False

    def test_non_list_kinds_returns_error(self):
        deps = _build_deps()
        result = auto_personalized_pipeline({'kinds': 'not_a_list'}, deps)
        assert result['status'] == 'error'


# ─── Payload building ───────────────────────────────────────────────


class _StubManagerNoTracks:
    def ensure_playlist(self, kind, variant, profile_id):
        return SimpleNamespace(
            id=1, name=f'{kind}-{variant}', kind=kind, variant=variant,
        )

    def refresh_playlist(self, kind, variant, profile_id):
        return self.ensure_playlist(kind, variant, profile_id)

    def get_playlist_tracks(self, playlist_id):
        return []


class _StubManagerWithTracks:
    def __init__(self, tracks_per_kind=None):
        self.tracks_per_kind = tracks_per_kind or {}
        self.refresh_calls: List[tuple] = []
        self.ensure_calls: List[tuple] = []

    def ensure_playlist(self, kind, variant, profile_id):
        self.ensure_calls.append((kind, variant, profile_id))
        return SimpleNamespace(
            id=hash((kind, variant)) % 10000,
            name=f'{kind}-{variant or "S"}', kind=kind, variant=variant,
        )

    def refresh_playlist(self, kind, variant, profile_id):
        self.refresh_calls.append((kind, variant, profile_id))
        # Mirror real manager: refresh returns a record without invoking
        # the public ensure_playlist API path again.
        return SimpleNamespace(
            id=hash((kind, variant)) % 10000,
            name=f'{kind}-{variant or "S"}', kind=kind, variant=variant,
        )

    def get_playlist_tracks(self, playlist_id):
        # Return all tracks regardless of id — tests scope to one playlist at a time.
        for tracks in self.tracks_per_kind.values():
            if tracks:
                return [SimpleNamespace(
                    track_name=t['name'], artist_name=t.get('artist', 'A'),
                    album_name=t.get('album', 'Al'),
                    spotify_track_id=t.get('id'),
                    itunes_track_id=None, deezer_track_id=None,
                    duration_ms=200000,
                ) for t in tracks]
        return []


class TestPayloadBuilding:
    def test_skips_kinds_with_no_tracks(self):
        deps = _build_deps()
        manager = _StubManagerNoTracks()
        payloads = _build_payloads_for_kinds(
            deps, manager,
            [{'kind': 'hidden_gems'}, {'kind': 'discovery_shuffle'}],
            profile_id=1, automation_id=None, refresh_first=False,
        )
        assert payloads == []

    def test_skips_invalid_entries(self):
        deps = _build_deps()
        manager = _StubManagerNoTracks()
        payloads = _build_payloads_for_kinds(
            deps, manager,
            ['not-a-dict', {}, {'variant': 'no-kind'}],  # all invalid
            profile_id=1, automation_id=None, refresh_first=False,
        )
        assert payloads == []

    def test_refresh_first_calls_refresh(self):
        deps = _build_deps()
        manager = _StubManagerWithTracks(
            tracks_per_kind={'hidden_gems': [{'name': 'T', 'id': 'sp-1'}]},
        )
        _build_payloads_for_kinds(
            deps, manager,
            [{'kind': 'hidden_gems'}],
            profile_id=1, automation_id=None, refresh_first=True,
        )
        assert manager.refresh_calls == [('hidden_gems', '', 1)]
        assert manager.ensure_calls == []

    def test_no_refresh_calls_ensure(self):
        deps = _build_deps()
        manager = _StubManagerWithTracks(
            tracks_per_kind={'hidden_gems': [{'name': 'T', 'id': 'sp-1'}]},
        )
        _build_payloads_for_kinds(
            deps, manager,
            [{'kind': 'hidden_gems'}],
            profile_id=1, automation_id=None, refresh_first=False,
        )
        assert manager.ensure_calls == [('hidden_gems', '', 1)]
        assert manager.refresh_calls == []

    def test_payload_shape(self):
        deps = _build_deps()
        manager = _StubManagerWithTracks(
            tracks_per_kind={'hidden_gems': [
                {'name': 'Track1', 'id': 'sp-1'},
                {'name': 'Track2', 'id': 'sp-2'},
            ]},
        )
        payloads = _build_payloads_for_kinds(
            deps, manager,
            [{'kind': 'hidden_gems'}],
            profile_id=1, automation_id=None, refresh_first=False,
        )
        assert len(payloads) == 1
        p = payloads[0]
        assert p['kind'] == 'hidden_gems'
        assert p['variant'] == ''
        assert p['name'] == 'hidden_gems-S'
        assert p['sync_id'].startswith('auto_personalized_hidden_gems_')
        assert len(p['tracks_json']) == 2
        assert p['tracks_json'][0]['id'] == 'sp-1'

    def test_manager_exception_swallowed_continues_to_next(self):
        deps = _build_deps()

        class _ExplodingMgr:
            def __init__(self):
                self.calls = []
            def ensure_playlist(self, kind, variant, profile_id):
                self.calls.append(kind)
                if kind == 'broken':
                    raise RuntimeError('manager boom')
                return SimpleNamespace(id=1, name=kind, kind=kind, variant=variant)
            def get_playlist_tracks(self, _id):
                return []

        mgr = _ExplodingMgr()
        # broken raises, hidden_gems proceeds (just no tracks).
        payloads = _build_payloads_for_kinds(
            deps, mgr,
            [{'kind': 'broken'}, {'kind': 'hidden_gems'}],
            profile_id=1, automation_id=None, refresh_first=False,
        )
        assert mgr.calls == ['broken', 'hidden_gems']
        assert payloads == []  # neither produced tracks


# ─── Sync launch ────────────────────────────────────────────────────


class TestSyncLaunch:
    def test_sync_one_playlist_starts_thread(self):
        captured: List[tuple] = []

        def fake_run_sync_task(*args):
            captured.append(args)

        deps = _build_deps(
            run_sync_task=fake_run_sync_task,
            get_current_profile_id=lambda: 7,
        )
        payload = {
            'sync_id': 'auto_personalized_hidden_gems_',
            'name': 'Hidden Gems',
            'tracks_json': [{'name': 'X', 'id': 'sp-1'}],
            'image_url': '',
        }
        result = _sync_personalized_playlist(deps, payload)
        assert result['status'] == 'started'
        # Wait for thread to invoke fake_run_sync_task.
        for _ in range(100):
            if captured:
                break
            import time
            time.sleep(0.01)
        assert len(captured) == 1
        # Args: (sync_id, name, tracks_json, automation_id, profile_id, image_url)
        assert captured[0][0] == 'auto_personalized_hidden_gems_'
        assert captured[0][1] == 'Hidden Gems'
        assert captured[0][3] is None  # automation_id muted
        assert captured[0][4] == 7  # profile_id


# ─── Full pipeline (with stubbed manager + sync states) ─────────────


class TestPipelineHappyPath:
    def test_pipeline_completes_with_synced_count(self):
        # Stub manager returns one playlist with 2 tracks.
        manager = _StubManagerWithTracks(
            tracks_per_kind={'hidden_gems': [
                {'name': 'A', 'id': 'sp-1'},
                {'name': 'B', 'id': 'sp-2'},
            ]},
        )

        # sync_states populated as if the sync background task finished.
        sync_states_storage = {}

        def fake_run_sync(sync_id, name, tracks, aid, pid, img):
            sync_states_storage[sync_id] = {
                'status': 'finished',
                'result': {'matched_tracks': 2},
            }

        deps = _build_deps(
            build_personalized_manager=lambda: manager,
            run_sync_task=fake_run_sync,
            get_sync_states=lambda: sync_states_storage,
        )
        # Patch time.sleep in shared helper so test doesn't take 2s per iter.
        import core.automation.handlers._pipeline_shared as shared
        orig = shared.time.sleep
        shared.time.sleep = lambda _: None
        try:
            result = auto_personalized_pipeline(
                {'_automation_id': 'auto-1', 'kinds': [{'kind': 'hidden_gems'}]},
                deps,
            )
        finally:
            shared.time.sleep = orig
        assert result['status'] == 'completed'
        assert result['_manages_own_progress'] is True
        # Pipeline-running flag cleaned up.
        assert deps.state.pipeline_running is False
