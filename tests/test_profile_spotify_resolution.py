"""Per-profile Spotify client resolution falls back safely (shared-app model).

The builder must return the GLOBAL client for admin (profile 1) and for any
non-admin profile that hasn't connected its own Spotify (no token cache) — so
background workers and existing users are unaffected. A per-profile client only
appears once that profile has its own .spotify_cache_profile_<id>.
"""

from __future__ import annotations

import os

from core.metadata import registry


def test_admin_and_none_use_global_client():
    g = registry.get_spotify_client()
    assert registry.get_spotify_client_for_profile(1) is g
    assert registry.get_spotify_client_for_profile(None) is g


def test_unconnected_profile_falls_back_to_global(tmp_path, monkeypatch):
    # A non-admin profile with no token cache must resolve to the global client.
    g = registry.get_spotify_client()
    # ensure no stray cache file for this id
    pid = 987654
    cache = f"config/.spotify_cache_profile_{pid}"
    assert not os.path.exists(cache)
    assert registry.get_spotify_client_for_profile(pid) is g
