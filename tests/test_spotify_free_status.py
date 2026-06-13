"""Spotify-Free (no-auth) must read as a WORKING primary metadata source.

A user who picks 'Spotify Free' (fallback_source='spotify' + metadata.spotify_free)
is officially unauthenticated, so is_spotify_authenticated() is False. The sidebar/
dashboard status dot keys on get_primary_source_status()['connected'], and the
dashboard test button on run_service_test('spotify', ...). Both used to report
disconnected / "Deezer connection successful! (Spotify configured but not
authenticated)" even though Spotify metadata was actually flowing.

Root cause (pinned by test_*_unauthed_free_seen_via_direct_client): get_client_for_source
('spotify') returns None unless officially authed, so the free-availability check in
get_primary_source_status could never fire — the client it probed was always None.
"""

from __future__ import annotations

import core.metadata.registry as registry
import core.connection_test as connection_test


class _FreeClient:
    """No-auth Spotify: not officially authed, but free metadata IS available."""

    def is_spotify_authenticated(self):
        return False

    def is_spotify_metadata_available(self):
        return True


class _NoMetaClient:
    def is_spotify_authenticated(self):
        return False

    def is_spotify_metadata_available(self):
        return False


def _patch_registry(monkeypatch, *, free_selected, client):
    cfg = {
        "metadata.fallback_source": "spotify",
        "metadata.spotify_free": free_selected,
    }
    monkeypatch.setattr(registry, "_get_config_value", lambda k, d=None: cfg.get(k, d))
    # get_client_for_source('spotify') returns None when unauthed; the direct fetch
    # is what the fix relies on, so route both through the fake.
    monkeypatch.setattr(registry, "get_spotify_client", lambda client_factory=None: client)


def test_unauthed_free_seen_via_direct_client(monkeypatch):
    """REGRESSION: free selected + available but not officially authed → connected.
    Before the fix this was False because the probed client was None."""
    _patch_registry(monkeypatch, free_selected=True, client=_FreeClient())
    status = registry.get_primary_source_status()
    assert status["connected"] is True
    assert status["source"] == "spotify_free"


def test_free_not_selected_unauthed_is_disconnected(monkeypatch):
    """Free NOT chosen → an unauthenticated Spotify primary is genuinely down."""
    _patch_registry(monkeypatch, free_selected=False, client=_FreeClient())
    status = registry.get_primary_source_status()
    assert status["connected"] is False
    assert status["source"] == "spotify"


def test_free_selected_but_unavailable_is_disconnected(monkeypatch):
    """Free chosen but the package/path can't serve → not connected (no false green)."""
    _patch_registry(monkeypatch, free_selected=True, client=_NoMetaClient())
    status = registry.get_primary_source_status()
    assert status["connected"] is False


# --- dashboard test button (run_service_test) ---------------------------------


class _FakeConfigManager:
    def __init__(self, store):
        self._store = store

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value):
        self._store[key] = value


def _run_spotify_test(monkeypatch, *, metadata_available, fallback="deezer"):
    fake_client = _FreeClient() if metadata_available else _NoMetaClient()

    class _Client:
        def __init__(self):
            self._d = fake_client

        def is_authenticated(self):
            return True  # free user passes the top-level auth gate

        def is_spotify_authenticated(self):
            return self._d.is_spotify_authenticated()

        def is_spotify_metadata_available(self):
            return self._d.is_spotify_metadata_available()

    monkeypatch.setattr(connection_test, "SpotifyClient", _Client)
    monkeypatch.setattr(
        connection_test,
        "config_manager",
        _FakeConfigManager({"spotify": {"client_id": "x", "client_secret": "y"}}),
    )
    monkeypatch.setattr(connection_test, "_get_metadata_fallback_source", lambda: fallback)
    monkeypatch.setattr(connection_test, "docker_resolve_url", lambda v: v, raising=False)
    return connection_test.run_service_test("spotify", {})


def test_test_button_reports_spotify_free(monkeypatch):
    ok, msg = _run_spotify_test(monkeypatch, metadata_available=True)
    assert ok is True
    assert "Spotify (no-auth)" in msg


def test_test_button_falls_back_when_free_unavailable(monkeypatch):
    """No free path → keep the honest Deezer-fallback message."""
    ok, msg = _run_spotify_test(monkeypatch, metadata_available=False, fallback="deezer")
    assert ok is True
    assert "Deezer connection successful" in msg
    assert "Spotify (no-auth)" not in msg
