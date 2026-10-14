"""#1455: global "wishlist missing tracks on sync" toggle + per-click override.

Covers:
- `_resolve_pipeline_skip_wishlist`: explicit per-click body value wins;
  otherwise the `playlist_sync.wishlist_missing_tracks` setting decides
  (default True = today's behavior).
- `auto_playlist_pipeline` adapter: toggle OFF forces skip on scheduled
  runs; toggle ON leaves the automation's own checkbox alone.
- The pipeline/run endpoint: thread receives the resolved skip_wishlist.
"""
from __future__ import annotations

import pytest

from core.playlists.pipeline import resolve_pipeline_skip_wishlist
from core.automation.handlers import playlist_pipeline as pp_adapter


class _FakeConfigManager:
    def __init__(self, settings):
        self._settings = settings

    def get(self, key, default=None):
        return self._settings.get(key, default)


# --- _resolve_pipeline_skip_wishlist ---------------------------------------

def test_explicit_true_wins_over_toggle_on():
    cm = _FakeConfigManager({"playlist_sync.wishlist_missing_tracks": True})
    assert resolve_pipeline_skip_wishlist({"skip_wishlist": True}, cm) is True


def test_explicit_false_wins_over_toggle_off():
    cm = _FakeConfigManager({"playlist_sync.wishlist_missing_tracks": False})
    assert resolve_pipeline_skip_wishlist({"skip_wishlist": False}, cm) is False


def test_no_body_key_toggle_on_wishlists():
    cm = _FakeConfigManager({"playlist_sync.wishlist_missing_tracks": True})
    assert resolve_pipeline_skip_wishlist({}, cm) is False


def test_no_body_key_toggle_off_skips():
    cm = _FakeConfigManager({"playlist_sync.wishlist_missing_tracks": False})
    assert resolve_pipeline_skip_wishlist({}, cm) is True


def test_no_body_key_missing_setting_defaults_to_today():
    cm = _FakeConfigManager({})
    assert resolve_pipeline_skip_wishlist({}, cm) is False


def test_no_config_manager_defaults_to_today():
    assert resolve_pipeline_skip_wishlist({}) is False


# --- automation adapter ----------------------------------------------------

class _FakeDeps:
    def __init__(self, settings):
        self.config_manager = _FakeConfigManager(settings)


def _capture_pipeline(monkeypatch):
    seen = {}

    def fake_run(config, deps, **kwargs):
        seen["config"] = dict(config)
        return {"status": "completed"}

    monkeypatch.setattr(pp_adapter, "run_mirrored_playlist_pipeline", fake_run)
    return seen


def test_adapter_toggle_off_forces_skip(monkeypatch):
    seen = _capture_pipeline(monkeypatch)
    deps = _FakeDeps({"playlist_sync.wishlist_missing_tracks": False})
    pp_adapter.auto_playlist_pipeline({"playlist_id": "3"}, deps)
    assert seen["config"]["skip_wishlist"] is True


def test_adapter_toggle_off_overrides_automation_checkbox(monkeypatch):
    seen = _capture_pipeline(monkeypatch)
    deps = _FakeDeps({"playlist_sync.wishlist_missing_tracks": False})
    pp_adapter.auto_playlist_pipeline(
        {"playlist_id": "3", "skip_wishlist": False}, deps)
    assert seen["config"]["skip_wishlist"] is True


def test_adapter_toggle_on_keeps_automation_checkbox_true(monkeypatch):
    seen = _capture_pipeline(monkeypatch)
    deps = _FakeDeps({"playlist_sync.wishlist_missing_tracks": True})
    pp_adapter.auto_playlist_pipeline(
        {"playlist_id": "3", "skip_wishlist": True}, deps)
    assert seen["config"]["skip_wishlist"] is True


def test_adapter_toggle_on_without_checkbox_wishlists(monkeypatch):
    seen = _capture_pipeline(monkeypatch)
    deps = _FakeDeps({"playlist_sync.wishlist_missing_tracks": True})
    pp_adapter.auto_playlist_pipeline({"playlist_id": "3"}, deps)
    assert "skip_wishlist" not in seen["config"]


def test_adapter_does_not_mutate_caller_config(monkeypatch):
    seen = _capture_pipeline(monkeypatch)
    deps = _FakeDeps({"playlist_sync.wishlist_missing_tracks": False})
    original = {"playlist_id": "3"}
    pp_adapter.auto_playlist_pipeline(original, deps)
    assert original == {"playlist_id": "3"}
    assert seen["config"]["skip_wishlist"] is True


# --- pipeline hands the resolved skip to each sync (#1689) -----------------

def test_pipeline_hands_skip_wishlist_add_to_each_sync(tmp_path):
    """The wishlist-phase skip alone did not stop wishlist adds: the sync
    itself wishlists every missing track as it goes. The pipeline's
    resolved skip_wishlist must reach each per-playlist sync as
    skip_wishlist_add so a skipped run adds nothing."""
    from core.playlists import pipeline
    from database.music_database import MusicDatabase
    from tests.automation.test_playlist_pipeline_folder_mode import _minimal_deps

    db = MusicDatabase(str(tmp_path / "music.db"))
    db.mirror_playlist(source='deezer', source_playlist_id='1', name='test',
                       tracks=[{'track_name': 'Golden', 'artist_name': 'HUNTR/X',
                                'source_track_id': 'd1'}],
                       profile_id=1)
    pl_id = db.get_mirrored_playlists(1)[0]['id']
    seen = []

    def sync_and_wishlist(deps, automation_id, playlists, *, sync_one_fn, **_k):
        for pl in playlists:
            sync_one_fn(pl)
        return {'synced': 0, 'skipped': 0, 'wishlist_queued': 0}

    for config, expected in (({'skip_wishlist': True}, True), ({}, False)):
        pipeline.run_mirrored_playlist_pipeline(
            {'playlist_id': str(pl_id), 'profile_id': 1,
             '_automation_id': 'mirrored_x', **config},
            _minimal_deps(
                get_database=lambda: db,
                run_playlist_discovery_worker=lambda *a, **k: None,
                update_progress=lambda *_a, **_k: None),
            refresh_fn=lambda cfg, deps: {'refreshed': '1', 'errors': '0'},
            sync_one_fn=lambda cfg, deps: seen.append(cfg.get('skip_wishlist_add')) or {},
            sync_and_wishlist_fn=sync_and_wishlist,
        )
    assert seen == [True, False]


def test_pipeline_skip_reaches_the_real_sync_handler(tmp_path):
    """End-to-end seam (#1689): the real pipeline + the real
    auto_sync_playlist + the real shared sync/wishlist tail — a skipped
    run must hand skip_wishlist_add=True to run_sync_task and never
    trigger wishlist processing."""
    import json as _json

    from core.automation.handlers._pipeline_shared import run_sync_and_wishlist
    from core.automation.handlers.sync_playlist import auto_sync_playlist
    from core.playlists import pipeline
    from tests.automation.test_playlist_pipeline_folder_mode import _minimal_deps

    discovered_track = {
        'extra_data': _json.dumps({
            'discovered': True,
            'matched_data': {
                'id': 'spot-1', 'name': 'Track', 'artists': [{'name': 'X'}],
                'album': {'name': 'Album'}, 'duration_ms': 200000,
            },
        }),
        'artist_name': 'X',
    }

    class _StubDB:
        def __init__(self):
            self._pl = {'id': 1, 'name': 'P', 'profile_id': 1, 'source': 'deezer'}

        def get_mirrored_playlist(self, playlist_id, profile_id=None):
            return dict(self._pl)

        def get_mirrored_playlists(self, *a, **k):
            return [dict(self._pl)]

        def get_mirrored_playlist_tracks(self, playlist_id):
            return [discovered_track]

        def get_mirrored_playlist_status_counts(self, playlist_id):
            return {}

    class _StubConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return 'jellyfin'

    for config, expected in (({'skip_wishlist': True}, True), ({}, False)):
        sync_states = {}
        sync_kwargs = []
        wishlist_calls = []

        def fake_run_sync_task(*args, **kwargs):
            sync_kwargs.append(kwargs)
            sync_states[args[0]] = {
                'status': 'finished',
                'result': {'matched_tracks': 1},
            }

        deps = _minimal_deps(
            get_database=lambda: _StubDB(),
            config_manager=_StubConfigManager(),
            run_sync_task=fake_run_sync_task,
            get_sync_states=lambda: sync_states,
            process_wishlist_automatically=lambda **k: wishlist_calls.append(k),
        )
        import core.automation.handlers._pipeline_shared as shared
        orig = shared.time.sleep
        shared.time.sleep = lambda _: None
        try:
            result = pipeline.run_mirrored_playlist_pipeline(
                {'playlist_id': '1', 'profile_id': 1,
                 '_automation_id': 'mirrored_x', **config},
                deps,
                refresh_fn=lambda cfg, d: {'refreshed': '1', 'errors': '0'},
                sync_one_fn=auto_sync_playlist,
                sync_and_wishlist_fn=run_sync_and_wishlist,
            )
        finally:
            shared.time.sleep = orig
        assert result['status'] == 'completed'
        assert len(sync_kwargs) == 1
        assert sync_kwargs[0].get('skip_wishlist_add') is expected
        assert sync_kwargs[0].get('organize_by_playlist') is False
        if expected:
            assert wishlist_calls == []
        else:
            assert len(wishlist_calls) == 1
