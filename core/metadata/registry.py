"""Metadata client registry and source selection.

Owns shared metadata client singletons, runtime client registration, and
canonical source selection. Package-internal code should use this module
instead of importing `web_server`.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("metadata.registry")

MetadataClientFactory = Callable[[], Any]

METADATA_SOURCE_PRIORITY = ("deezer", "itunes", "spotify", "discogs", "hydrabase")

_UNSET = object()
_client_cache_lock = threading.RLock()
_client_cache: Dict[str, Any] = {}

_runtime_clients_lock = threading.RLock()
_runtime_clients: Dict[str, Any] = {
    "spotify": None,
    "hydrabase": None,
}
_dev_mode_enabled_provider: Callable[[], bool] = lambda: False


def register_runtime_clients(
    *,
    spotify_client: Any = _UNSET,
    hydrabase_client: Any = _UNSET,
    dev_mode_enabled_provider: Optional[Callable[[], bool]] = _UNSET,
) -> None:
    """Register app-owned runtime clients.

    `None` is a valid value and clears the registered client. Omitted
    arguments leave the current registration unchanged.
    """
    global _dev_mode_enabled_provider
    with _runtime_clients_lock:
        if spotify_client is not _UNSET:
            _runtime_clients["spotify"] = spotify_client
        if hydrabase_client is not _UNSET:
            _runtime_clients["hydrabase"] = hydrabase_client
        if dev_mode_enabled_provider is not _UNSET:
            _dev_mode_enabled_provider = dev_mode_enabled_provider or (lambda: False)


def get_registered_runtime_client(name: str) -> Any:
    with _runtime_clients_lock:
        return _runtime_clients.get(name)


def clear_cached_metadata_clients() -> None:
    """Clear lazily-created client singletons.

    Runtime clients registered by the host app stay in place.
    """
    with _client_cache_lock:
        _client_cache.clear()


def _get_config_value(key: str, default: Any = None) -> Any:
    try:
        from config.settings import config_manager

        return config_manager.get(key, default)
    except Exception:
        return default


def _get_spotify_factory(client_factory: Optional[MetadataClientFactory]) -> MetadataClientFactory:
    if client_factory is not None:
        return client_factory
    from core.spotify_client import SpotifyClient

    return SpotifyClient


def _get_itunes_factory(client_factory: Optional[MetadataClientFactory]) -> MetadataClientFactory:
    if client_factory is not None:
        return client_factory
    from core.itunes_client import iTunesClient

    return iTunesClient


def _get_deezer_factory(client_factory: Optional[MetadataClientFactory]) -> MetadataClientFactory:
    if client_factory is not None:
        return client_factory
    from core.deezer_client import DeezerClient

    return DeezerClient


def _get_discogs_factory(client_factory: Optional[MetadataClientFactory]) -> MetadataClientFactory:
    if client_factory is not None:
        return client_factory
    from core.discogs_client import DiscogsClient

    return DiscogsClient


def get_spotify_client(client_factory: Optional[MetadataClientFactory] = None):
    """Get shared Spotify client.

    Prefers the app-registered runtime client. Falls back to a lazily
    cached singleton if no runtime client was registered.
    """
    runtime_client = get_registered_runtime_client("spotify")
    if runtime_client is not None:
        return runtime_client

    cache_key = "spotify"
    factory = _get_spotify_factory(client_factory)
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = factory()
            _client_cache[cache_key] = client
        return client


def get_deezer_client(client_factory: Optional[MetadataClientFactory] = None):
    """Get cached Deezer client keyed by current access token."""
    current_token = _get_config_value("deezer.access_token", None)
    cache_key = f"deezer::{current_token or ''}"
    factory = _get_deezer_factory(client_factory)
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = factory()
            _client_cache[cache_key] = client
        return client


def get_itunes_client(client_factory: Optional[MetadataClientFactory] = None):
    """Get cached iTunes client."""
    cache_key = "itunes"
    factory = _get_itunes_factory(client_factory)
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = factory()
            _client_cache[cache_key] = client
        return client


def get_discogs_client(
    token: Optional[str] = None,
    client_factory: Optional[MetadataClientFactory] = None,
):
    """Get cached Discogs client keyed by token."""
    if token is None:
        current_token = _get_config_value("discogs.token", "") or ""
    else:
        current_token = token or ""

    cache_key = f"discogs::{current_token}"
    factory = _get_discogs_factory(client_factory)
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = factory(token=current_token or None)  # type: ignore[misc]
            _client_cache[cache_key] = client
        return client


def is_hydrabase_enabled() -> bool:
    """Return True when Hydrabase is connected and app-enabled."""
    try:
        client = get_registered_runtime_client("hydrabase")
        if not client or not client.is_connected():
            return False
        return bool(_dev_mode_enabled_provider())
    except Exception:
        return False


def get_hydrabase_client(allow_fallback: bool = True, require_enabled: bool = True):
    """Return registered Hydrabase client or iTunes fallback."""
    try:
        client = get_registered_runtime_client("hydrabase")
        if client and client.is_connected():
            if not require_enabled or bool(_dev_mode_enabled_provider()):
                return client
    except Exception:
        pass

    if allow_fallback:
        return get_itunes_client()
    return None


def get_primary_source(spotify_client_factory: Optional[MetadataClientFactory] = None) -> str:
    """Return configured primary metadata source."""
    source = _get_config_value("metadata.fallback_source", "deezer") or "deezer"

    if source == "spotify":
        try:
            spotify = get_spotify_client(client_factory=spotify_client_factory)
            if not spotify or not spotify.is_spotify_authenticated():
                return "deezer"
        except Exception:
            return "deezer"

    return source


def get_source_priority(preferred_source: str):
    """Return source priority with preferred source first."""
    ordered = []
    if preferred_source in METADATA_SOURCE_PRIORITY:
        ordered.append(preferred_source)

    for source in METADATA_SOURCE_PRIORITY:
        if source not in ordered:
            ordered.append(source)
    return ordered


def get_primary_client(
    *,
    spotify_client_factory: Optional[MetadataClientFactory] = None,
    itunes_client_factory: Optional[MetadataClientFactory] = None,
    deezer_client_factory: Optional[MetadataClientFactory] = None,
    discogs_client_factory: Optional[MetadataClientFactory] = None,
):
    """Return client for configured primary source."""
    return get_client_for_source(
        get_primary_source(spotify_client_factory=spotify_client_factory),
        spotify_client_factory=spotify_client_factory,
        itunes_client_factory=itunes_client_factory,
        deezer_client_factory=deezer_client_factory,
        discogs_client_factory=discogs_client_factory,
    )


def get_client_for_source(
    source: str,
    *,
    spotify_client_factory: Optional[MetadataClientFactory] = None,
    itunes_client_factory: Optional[MetadataClientFactory] = None,
    deezer_client_factory: Optional[MetadataClientFactory] = None,
    discogs_client_factory: Optional[MetadataClientFactory] = None,
):
    """Return exact client for a source, or None if unavailable."""
    if source == "spotify":
        try:
            client = get_spotify_client(client_factory=spotify_client_factory)
            if client and client.is_spotify_authenticated():
                return client
        except Exception:
            pass
        return None

    if source == "deezer":
        return get_deezer_client(client_factory=deezer_client_factory)

    if source == "discogs":
        return get_discogs_client(client_factory=discogs_client_factory)

    if source == "hydrabase":
        return get_hydrabase_client(allow_fallback=False)

    if source == "itunes":
        return get_itunes_client(client_factory=itunes_client_factory)

    return None
