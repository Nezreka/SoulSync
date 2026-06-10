"""Metadata client registry and source selection.

Owns shared metadata client singletons, runtime client registration, and
canonical source selection. Package-internal code should use this module
instead of importing `web_server`.
"""

from __future__ import annotations

import threading
import hashlib
import time
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("metadata.registry")

MetadataClientFactory = Callable[[], Any]

METADATA_SOURCE_PRIORITY = ("deezer", "itunes", "spotify", "discogs", "hydrabase", "musicbrainz")
METADATA_SOURCE_LABELS = {
    "spotify": "Spotify",
    "itunes": "iTunes",
    "deezer": "Deezer",
    "discogs": "Discogs",
    "hydrabase": "Hydrabase",
    "musicbrainz": "MusicBrainz",
}

_UNSET = object()
_client_cache_lock = threading.RLock()
_client_cache: Dict[str, Any] = {}

_runtime_clients_lock = threading.RLock()
_runtime_clients: Dict[str, Any] = {
    "spotify": None,
    "hydrabase": None,
}
_dev_mode_enabled_provider: Callable[[], bool] = lambda: False
_profile_spotify_credentials_provider: Callable[[int], Any] = lambda profile_id: None


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


def register_profile_spotify_credentials_provider(
    provider: Optional[Callable[[int], Any]] = _UNSET,
) -> None:
    """Register a callable that returns per-profile Spotify credentials."""
    global _profile_spotify_credentials_provider
    with _runtime_clients_lock:
        if provider is not _UNSET:
            _profile_spotify_credentials_provider = provider or (lambda profile_id: None)


def get_registered_runtime_client(name: str) -> Any:
    with _runtime_clients_lock:
        return _runtime_clients.get(name)


def clear_cached_metadata_clients() -> None:
    """Clear lazily-created client singletons.

    Runtime clients registered by the host app stay in place.
    """
    with _client_cache_lock:
        _client_cache.clear()


def clear_cached_metadata_client(cache_key: str) -> None:
    """Clear one lazily-created client singleton by cache key."""
    with _client_cache_lock:
        _client_cache.pop(cache_key, None)


def clear_cached_profile_spotify_client(profile_id: int) -> None:
    """Clear any cached Spotify client for a specific profile."""
    prefix = f"spotify_profile::{profile_id}::"
    with _client_cache_lock:
        for key in [key for key in _client_cache if key.startswith(prefix)]:
            _client_cache.pop(key, None)


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


def _get_amazon_factory(client_factory: Optional[MetadataClientFactory]) -> MetadataClientFactory:
    if client_factory is not None:
        return client_factory
    from core.amazon_client import AmazonClient

    return AmazonClient


def _get_musicbrainz_factory(client_factory: Optional[MetadataClientFactory]) -> MetadataClientFactory:
    if client_factory is not None:
        return client_factory
    from core.musicbrainz_search import MusicBrainzSearchClient

    return MusicBrainzSearchClient


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


def _build_profile_spotify_cache_key(profile_id: int, creds: Dict[str, Any]) -> str:
    fingerprint = hashlib.sha256(
        f"{profile_id}:{creds.get('client_id', '')}:{creds.get('client_secret', '')}:{creds.get('redirect_uri', '')}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"spotify_profile::{profile_id}::{fingerprint}"


def get_spotify_client_for_profile(profile_id: Optional[int] = None):
    """Get a profile-specific Spotify client or fall back to the global one.

    Shared-app model: a profile authenticates its OWN Spotify account through
    the GLOBAL app credentials (client_id/secret) and gets its own token cache
    (``.spotify_cache_profile_<id>``). A profile that set its own app creds
    (legacy) still works. A profile that hasn't connected — no token cache —
    falls back to the global/admin client, so nothing changes for them or for
    background workers (which run as profile 1)."""
    if profile_id is None or profile_id == 1:
        return get_spotify_client()

    try:
        creds = _profile_spotify_credentials_provider(profile_id) or {}
    except Exception:
        return get_spotify_client()

    import os
    cache_path = f"config/.spotify_cache_profile_{profile_id}"
    own_app = bool(creds.get("client_id") and creds.get("client_secret"))
    connected = os.path.exists(cache_path)
    # Build a per-profile client when the profile has its OWN app creds (legacy)
    # OR has connected via the shared app (its own token cache exists). A profile
    # with neither uses the global/admin client — so background workers and
    # unconnected users are unaffected.
    if not own_app and not connected:
        return get_spotify_client()

    # Effective OAuth app creds: the profile's own (legacy) else the global app.
    client_id = creds.get("client_id") or _get_config_value("spotify.client_id", "")
    client_secret = creds.get("client_secret") or _get_config_value("spotify.client_secret", "")
    redirect_uri = (creds.get("redirect_uri")
                    or _get_config_value("spotify.redirect_uri", "http://127.0.0.1:8888/callback"))
    if not client_id:
        return get_spotify_client()

    cache_key = _build_profile_spotify_cache_key(
        profile_id, {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri})
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is not None and getattr(client, "sp", None) is not None:
            return client

    try:
        from core.spotify_client import SpotifyClient
        from spotipy.oauth2 import SpotifyOAuth
        import spotipy

        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="user-library-read user-read-private playlist-read-private playlist-read-collaborative user-read-email user-follow-read",
            cache_path=cache_path,
            state=f"profile_{profile_id}",
        )

        profile_client = SpotifyClient()
        profile_client.sp = spotipy.Spotify(auth_manager=auth_manager, retries=0, requests_timeout=15)
        profile_client.user_id = None

        with _client_cache_lock:
            _client_cache[cache_key] = profile_client

        logger.info("Created per-profile Spotify client for profile %s", profile_id)
        return profile_client
    except Exception as e:
        logger.error("Failed to create per-profile Spotify client for profile %s: %s", profile_id, e)
        return get_spotify_client()


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


def get_amazon_client(client_factory: Optional[MetadataClientFactory] = None):
    """Get cached Amazon Music client."""
    cache_key = "amazon"
    factory = _get_amazon_factory(client_factory)
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = factory()
            _client_cache[cache_key] = client
        return client


def get_musicbrainz_client(client_factory: Optional[MetadataClientFactory] = None):
    """Get cached MusicBrainz primary source client."""
    cache_key = "musicbrainz"
    factory = _get_musicbrainz_factory(client_factory)
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = factory()
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
    except Exception as e:
        logger.debug("hydrabase client lookup: %s", e)

    if allow_fallback:
        return get_itunes_client()
    return None


def get_primary_source(spotify_client_factory: Optional[MetadataClientFactory] = None) -> str:
    """Return configured primary metadata source."""
    _default = METADATA_SOURCE_PRIORITY[0]
    source = _get_config_value("metadata.fallback_source", _default) or _default

    if source == "spotify":
        try:
            spotify = get_spotify_client(client_factory=spotify_client_factory)
            if not spotify or not spotify.is_spotify_authenticated():
                return _default
        except Exception:
            return _default

    return source


def get_spotify_disconnect_source(configured_source: Optional[str] = None) -> str:
    """Return the active metadata source after Spotify is disconnected."""
    _default = METADATA_SOURCE_PRIORITY[0]
    source = configured_source if configured_source is not None else _get_config_value("metadata.fallback_source", _default)
    source = source or _default
    return _default if source == "spotify" else source


def get_metadata_source_label(source: str) -> str:
    """Return a human-readable label for a metadata source."""
    return METADATA_SOURCE_LABELS.get(source, "Unmapped")


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
    amazon_client_factory: Optional[MetadataClientFactory] = None,
    musicbrainz_client_factory: Optional[MetadataClientFactory] = None,
):
    """Return client for configured primary source."""
    return get_client_for_source(
        get_primary_source(spotify_client_factory=spotify_client_factory),
        spotify_client_factory=spotify_client_factory,
        itunes_client_factory=itunes_client_factory,
        deezer_client_factory=deezer_client_factory,
        discogs_client_factory=discogs_client_factory,
        amazon_client_factory=amazon_client_factory,
        musicbrainz_client_factory=musicbrainz_client_factory,
    )


def get_primary_source_status(
    *,
    spotify_client_factory: Optional[MetadataClientFactory] = None,
    itunes_client_factory: Optional[MetadataClientFactory] = None,
    deezer_client_factory: Optional[MetadataClientFactory] = None,
    discogs_client_factory: Optional[MetadataClientFactory] = None,
    amazon_client_factory: Optional[MetadataClientFactory] = None,
    musicbrainz_client_factory: Optional[MetadataClientFactory] = None,
) -> Dict[str, Any]:
    """Return a generic status snapshot for the active primary metadata source."""
    source = _get_config_value("metadata.fallback_source", "deezer") or "deezer"
    started = time.time()
    connected = False

    try:
        client = get_client_for_source(
            source,
            spotify_client_factory=spotify_client_factory,
            itunes_client_factory=itunes_client_factory,
            deezer_client_factory=deezer_client_factory,
            discogs_client_factory=discogs_client_factory,
            amazon_client_factory=amazon_client_factory,
            musicbrainz_client_factory=musicbrainz_client_factory,
        )
        if source == "spotify":
            connected = bool(client and client.is_spotify_authenticated())
            # No-auth composite (fallback_source='spotify' + metadata.spotify_free):
            # works without authentication, so treat the free path's availability
            # as "connected" too.
            if not connected and _get_config_value("metadata.spotify_free", False):
                try:
                    connected = bool(client and client.is_spotify_metadata_available())
                except Exception:
                    connected = False
        elif source == "hydrabase":
            connected = bool(client and (client.is_connected() if hasattr(client, "is_connected") else client.is_authenticated()))
        elif client is not None and hasattr(client, "is_authenticated"):
            connected = bool(client.is_authenticated())
        else:
            connected = client is not None
    except Exception:
        connected = False

    # Report the composite-aware source for DISPLAY: "Spotify (no auth)" is
    # stored as fallback_source='spotify' + metadata.spotify_free=true. The raw
    # 'spotify' is kept above for the connected/auth checks; consumers that
    # label the source (sidebar, dashboard card) get 'spotify_free' so they
    # stop mislabeling no-auth as plain Spotify.
    display_source = source
    if source == "spotify" and _get_config_value("metadata.spotify_free", False):
        display_source = "spotify_free"

    return {
        "source": display_source,
        "connected": connected,
        "response_time": round((time.time() - started) * 1000, 1),
    }


def get_client_for_source(
    source: str,
    *,
    spotify_client_factory: Optional[MetadataClientFactory] = None,
    itunes_client_factory: Optional[MetadataClientFactory] = None,
    deezer_client_factory: Optional[MetadataClientFactory] = None,
    discogs_client_factory: Optional[MetadataClientFactory] = None,
    amazon_client_factory: Optional[MetadataClientFactory] = None,
    musicbrainz_client_factory: Optional[MetadataClientFactory] = None,
):
    """Return exact client for a source, or None if unavailable."""
    if source == "spotify":
        try:
            client = get_spotify_client(client_factory=spotify_client_factory)
            if client and client.is_spotify_authenticated():
                return client
        except Exception as e:
            logger.debug("spotify client get_for_source: %s", e)
        return None

    if source == "deezer":
        return get_deezer_client(client_factory=deezer_client_factory)

    if source == "discogs":
        return get_discogs_client(client_factory=discogs_client_factory)

    if source == "hydrabase":
        return get_hydrabase_client(allow_fallback=False)

    if source == "itunes":
        return get_itunes_client(client_factory=itunes_client_factory)

    if source == "amazon":
        return get_amazon_client(client_factory=amazon_client_factory)

    if source == "musicbrainz":
        return get_musicbrainz_client(client_factory=musicbrainz_client_factory)

    return None
