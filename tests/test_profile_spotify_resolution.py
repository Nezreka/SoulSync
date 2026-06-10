"""Per-profile Spotify client resolution falls back safely (shared-app model).

The builder must return the GLOBAL client for admin (profile 1) and for any
non-admin profile that hasn't connected its own Spotify (no token cache, no own
app creds) — so background workers and existing users are unaffected. A
per-profile client only appears once that profile has its own app creds OR its
own .spotify_cache_profile_<id>.

We monkeypatch get_spotify_client to a sentinel so "fell back to global" is an
exact, order-independent identity check (the real global client isn't a stable
singleton across the suite).
"""

from __future__ import annotations

import os

from core.metadata import registry


def test_admin_and_none_use_global_client(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(registry, "get_spotify_client", lambda *a, **k: sentinel)
    registry.register_profile_spotify_credentials_provider(lambda pid: None)
    assert registry.get_spotify_client_for_profile(1) is sentinel
    assert registry.get_spotify_client_for_profile(None) is sentinel


def test_unconnected_profile_falls_back_to_global(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(registry, "get_spotify_client", lambda *a, **k: sentinel)
    # No own app creds for this profile, and no token cache → must use global.
    registry.register_profile_spotify_credentials_provider(lambda pid: None)
    pid = 987654
    assert not os.path.exists(f"config/.spotify_cache_profile_{pid}")
    assert registry.get_spotify_client_for_profile(pid) is sentinel
