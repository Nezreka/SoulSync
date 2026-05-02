"""Cached metadata-provider and Spotify status snapshots."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from config.settings import config_manager
from utils.logging_config import get_logger

from core.metadata.registry import get_primary_source_status

logger = get_logger("metadata.status")

METADATA_SOURCE_STATUS_TTL = 120
SPOTIFY_STATUS_TTL_ACTIVE = 15
SPOTIFY_STATUS_TTL_IDLE = 300

_status_lock = threading.RLock()
_metadata_source_status_cache: Dict[str, Any] = {
    "source": "deezer",
    "connected": False,
    "response_time": 0,
}
_metadata_source_status_timestamp = 0.0
_spotify_status_cache: Dict[str, Any] = {
    "connected": False,
    "authenticated": False,
    "rate_limited": False,
    "rate_limit": None,
    "post_ban_cooldown": None,
}
_spotify_status_timestamp = 0.0


def _get_config_value(key: str, default: Any = None) -> Any:
    try:
        return config_manager.get(key, default)
    except Exception:
        return default


def invalidate_metadata_status_caches() -> None:
    """Mark the cached metadata-source and Spotify status snapshots stale."""
    global _metadata_source_status_timestamp, _spotify_status_timestamp
    with _status_lock:
        _metadata_source_status_timestamp = 0.0
        _spotify_status_timestamp = 0.0


def get_metadata_source_status() -> Dict[str, Any]:
    """Return a cached snapshot for the active primary metadata source."""
    global _metadata_source_status_timestamp

    current_time = time.time()
    with _status_lock:
        if _metadata_source_status_timestamp and current_time - _metadata_source_status_timestamp <= METADATA_SOURCE_STATUS_TTL:
            return dict(_metadata_source_status_cache)

    try:
        status_data = get_primary_source_status()
    except Exception as exc:
        logger.debug("Metadata source status refresh failed: %s", exc)
        status_data = None

    if status_data:
        with _status_lock:
            _metadata_source_status_cache.update(status_data)
            _metadata_source_status_timestamp = current_time

    with _status_lock:
        return dict(_metadata_source_status_cache)


def get_spotify_status(spotify_client: Optional[Any] = None) -> Dict[str, Any]:
    """Return a cached Spotify-specific status snapshot."""
    global _spotify_status_timestamp

    current_time = time.time()
    configured_source = _get_config_value("metadata.fallback_source", "deezer") or "deezer"
    ttl = SPOTIFY_STATUS_TTL_ACTIVE if configured_source == "spotify" else SPOTIFY_STATUS_TTL_IDLE

    with _status_lock:
        if _spotify_status_timestamp and current_time - _spotify_status_timestamp <= ttl:
            return dict(_spotify_status_cache)

    try:
        is_rate_limited = spotify_client.is_rate_limited() if spotify_client else False
        rate_limit_info = spotify_client.get_rate_limit_info() if (spotify_client and is_rate_limited) else None
        cooldown_remaining = spotify_client.get_post_ban_cooldown_remaining() if spotify_client else 0
        authenticated = spotify_client.is_spotify_authenticated() if spotify_client else False

        with _status_lock:
            _spotify_status_cache.update({
                "connected": authenticated,
                "authenticated": authenticated,
                "rate_limited": is_rate_limited,
                "rate_limit": rate_limit_info,
                "post_ban_cooldown": cooldown_remaining if cooldown_remaining > 0 else None,
            })
            _spotify_status_timestamp = current_time
    except Exception as exc:
        logger.debug("Spotify status refresh failed: %s", exc)

    with _status_lock:
        return dict(_spotify_status_cache)


def get_status_snapshot(spotify_client: Optional[Any] = None) -> Dict[str, Any]:
    """Return the combined metadata-provider status snapshot."""
    return {
        "metadata_source": get_metadata_source_status(),
        "spotify": get_spotify_status(spotify_client=spotify_client),
    }
