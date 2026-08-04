"""get_client_for_source('spotify') must resolve for the no-auth free source.

The strict-discography orchestrator resolves its provider clients through
``get_client_for_source()``. That resolver's old gate was
``is_spotify_authenticated()``, so every 'Spotify (no auth)' user got a 503
"provider is unavailable" from artist discography — even though the whole
downstream stack (provider_access's auth gate, the adapter's free search, the
client's own ``_free_active`` routing) is free-aware. The resolver now asks
``is_spotify_metadata_available()``: officially authed OR free-served.

For a plain-Spotify user the two checks are identical (metadata availability
degenerates to authentication when free isn't selected), so these tests pin
both sides of the gate.
"""

from __future__ import annotations

import pytest

from core.metadata import registry as metadata_registry


class _SpotifyClient:
    def __init__(self, *, authed: bool, metadata_available: bool):
        self.sp = object()
        self._authed = authed
        self._metadata_available = metadata_available

    def is_spotify_authenticated(self) -> bool:
        return self._authed

    def is_spotify_metadata_available(self) -> bool:
        return self._metadata_available


def _resolve(client, monkeypatch, *, boot=False):
    # The resolver prefers the app-registered runtime client — pin it so the
    # test controls exactly what get_spotify_client() hands back. Boot phase
    # defaults to True until web_server's import pass completes, so pin it
    # too: these tests target the steady-state availability gate.
    monkeypatch.setattr(
        metadata_registry, "get_registered_runtime_client",
        lambda name: client if name == "spotify" else None,
    )
    monkeypatch.setattr("core.boot_phase.is_boot_phase", lambda: boot)
    return metadata_registry.get_client_for_source("spotify")


def test_free_served_client_resolves_without_auth(monkeypatch):
    """A 'Spotify (no auth)' user: not authenticated, but the free source can
    serve — the resolver must hand the client back, or artist discography
    dies with 'provider is unavailable'."""
    client = _SpotifyClient(authed=False, metadata_available=True)
    assert _resolve(client, monkeypatch) is client


def test_authenticated_client_still_resolves(monkeypatch):
    client = _SpotifyClient(authed=True, metadata_available=True)
    assert _resolve(client, monkeypatch) is client


def test_no_auth_no_free_still_returns_none(monkeypatch):
    """A plain-Spotify user with no token gets None exactly as before —
    metadata availability degenerates to authentication when free isn't
    selected/installed."""
    client = _SpotifyClient(authed=False, metadata_available=False)
    assert _resolve(client, monkeypatch) is None


def test_availability_probe_failure_returns_none(monkeypatch):
    class _Broken(_SpotifyClient):
        def is_spotify_metadata_available(self) -> bool:
            raise RuntimeError("probe blew up")

    client = _Broken(authed=False, metadata_available=True)
    assert _resolve(client, monkeypatch) is None


@pytest.mark.parametrize("authed", [True, False])
def test_boot_phase_keeps_the_cheap_sp_gate(monkeypatch, authed):
    """During boot the resolver must not run availability probes at all — the
    cheap `sp` attribute check stands in (the startup-hang guard)."""
    calls: list[str] = []

    class _Probing(_SpotifyClient):
        def is_spotify_authenticated(self) -> bool:
            calls.append("auth")
            return super().is_spotify_authenticated()

        def is_spotify_metadata_available(self) -> bool:
            calls.append("available")
            return super().is_spotify_metadata_available()

    client = _Probing(authed=authed, metadata_available=True)
    assert _resolve(client, monkeypatch, boot=True) is client
    assert calls == []
