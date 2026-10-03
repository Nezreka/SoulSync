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
