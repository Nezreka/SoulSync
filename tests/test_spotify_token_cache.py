"""Database-backed Spotify token cache (wolf39us daily-deauth).

The token used to live in config/.spotify_cache — gone on every container
recreation unless the user's compose maps /app/config explicitly. The
DatabaseTokenCache stores it in the config store (which demonstrably
survives recreation — the user's settings did while his tokens died), and
imports the legacy file once on upgrade.
"""

from __future__ import annotations

import json

from core.spotify_token_cache import DatabaseTokenCache


class _Cfg:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value


TOKEN = {"access_token": "at", "refresh_token": "rt", "expires_at": 123}


def test_save_then_get_roundtrip(tmp_path):
    cache = DatabaseTokenCache(_Cfg(), legacy_path=str(tmp_path / "nope"))
    assert cache.get_cached_token() is None
    cache.save_token_to_cache(TOKEN)
    assert cache.get_cached_token() == TOKEN


def test_json_string_value_tolerated(tmp_path):
    cfg = _Cfg()
    cfg.store["spotify.token_info"] = json.dumps(TOKEN)  # stored serialized
    cache = DatabaseTokenCache(cfg, legacy_path=str(tmp_path / "nope"))
    assert cache.get_cached_token() == TOKEN


def test_legacy_file_imported_once(tmp_path):
    legacy = tmp_path / ".spotify_cache"
    legacy.write_text(json.dumps(TOKEN))
    cfg = _Cfg()
    cache = DatabaseTokenCache(cfg, legacy_path=str(legacy))

    assert cache.get_cached_token() == TOKEN          # imported
    assert cfg.store["spotify.token_info"] == TOKEN   # persisted to the store
    # Subsequent reads come from the store even if the file vanishes.
    legacy.unlink()
    assert cache.get_cached_token() == TOKEN


def test_garbage_legacy_file_ignored(tmp_path):
    legacy = tmp_path / ".spotify_cache"
    legacy.write_text("not json{{{")
    cache = DatabaseTokenCache(_Cfg(), legacy_path=str(legacy))
    assert cache.get_cached_token() is None


def test_clear_drops_store_and_file(tmp_path):
    legacy = tmp_path / ".spotify_cache"
    legacy.write_text(json.dumps(TOKEN))
    cfg = _Cfg()
    cache = DatabaseTokenCache(cfg, legacy_path=str(legacy))
    cache.save_token_to_cache(TOKEN)

    cache.clear()

    assert cfg.store["spotify.token_info"] is None
    assert not legacy.exists()
    assert cache.get_cached_token() is None


def test_write_failure_never_raises(tmp_path):
    class _Broken(_Cfg):
        def set(self, key, value):
            raise RuntimeError("db down")

    cache = DatabaseTokenCache(_Broken(), legacy_path=str(tmp_path / "nope"))
    cache.save_token_to_cache(TOKEN)  # must not raise — spotipy calls this mid-request
