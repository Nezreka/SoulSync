"""The import-search 'primary source' label must name the user's CONFIGURED source.

Bug #922: a Spotify Free (no-auth) user saw "Showing Discogs results - not from your
primary source (Deezer)" on the manual album-import search. Root cause: get_primary_source()
deliberately downgrades an unauthenticated Spotify to the working fallback (deezer) so
client routing always yields a usable client — and the import payload reused that
FUNCTIONAL value for the LABEL. The free source has no album-name search (SpotifyFree
.search_albums() returns []), so falling back for results is correct; only the label was
wrong. get_primary_source_label() preserves the configured intent (Spotify Free reads as
'spotify') without touching client routing, and the import route returns the label.
"""

from __future__ import annotations

import core.metadata.registry as registry
from core.imports.routes import ImportRouteRuntime, search_albums


class _AuthedSpotify:
    def is_spotify_authenticated(self):
        return True


class _UnauthedSpotify:
    """No-auth Spotify (free tier): officially unauthenticated."""

    def is_spotify_authenticated(self):
        return False


def _patch_cfg(monkeypatch, cfg, *, client=None):
    monkeypatch.setattr(registry, "_get_config_value", lambda k, d=None: cfg.get(k, d))
    monkeypatch.setattr(registry, "get_spotify_client", lambda client_factory=None: client)


# --- get_primary_source_label seam -------------------------------------------

def test_label_spotify_free_reads_as_spotify(monkeypatch):
    """THE FIX: no-auth Spotify Free is labelled 'spotify', not the deezer fallback."""
    _patch_cfg(
        monkeypatch,
        {"metadata.fallback_source": "spotify", "metadata.spotify_free": True},
        client=_UnauthedSpotify(),
    )
    assert registry.get_primary_source_label() == "spotify"


def test_label_spotify_authed_reads_as_spotify(monkeypatch):
    _patch_cfg(
        monkeypatch,
        {"metadata.fallback_source": "spotify", "metadata.spotify_free": False},
        client=_AuthedSpotify(),
    )
    assert registry.get_primary_source_label() == "spotify"


def test_label_spotify_unauthed_no_free_downgrades(monkeypatch):
    """Spotify configured but neither authed nor free → genuinely on the fallback,
    so the label honestly reports the working default (not a misleading 'spotify')."""
    _patch_cfg(
        monkeypatch,
        {"metadata.fallback_source": "spotify", "metadata.spotify_free": False},
        client=_UnauthedSpotify(),
    )
    label = registry.get_primary_source_label()
    assert label == registry.METADATA_SOURCE_PRIORITY[0]  # deezer default
    assert label != "spotify"


def test_label_non_spotify_source_unchanged(monkeypatch):
    _patch_cfg(
        monkeypatch,
        {"metadata.fallback_source": "deezer", "metadata.spotify_free": False},
    )
    assert registry.get_primary_source_label() == "deezer"


# --- import route regression: label decoupled from functional source ----------

def test_search_albums_payload_uses_label_not_functional_source():
    """REGRESSION (#922): the payload's primary_source is the LABEL ('spotify'),
    even though the functional source the search chain used downgraded to 'deezer'."""
    runtime = ImportRouteRuntime(
        get_primary_source=lambda: "deezer",          # functional (downgraded)
        get_primary_source_label=lambda: "spotify",   # configured intent
        search_import_albums=lambda q, limit=12: [{"name": "X", "source": "discogs"}],
        hydrabase_worker=None,
        dev_mode_enabled=False,
    )
    payload, status = search_albums(runtime, "some album")
    assert status == 200
    assert payload["primary_source"] == "spotify"
    # The functional source is still free to differ (the chain genuinely used a fallback).
    assert runtime.get_primary_source() == "deezer"
