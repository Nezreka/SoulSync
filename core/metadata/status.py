"""Cached metadata-provider and Spotify status snapshots."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

from core.metadata.registry import get_primary_source_status

logger = get_logger("metadata.status")

METADATA_SOURCE_STATUS_TTL = 120

_UNSET = object()

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
_spotify_status_initialized = False
_spotify_rate_limit_expires_at = 0.0
_spotify_post_ban_cooldown_expires_at = 0.0


def invalidate_metadata_status_caches() -> None:
    """Mark the cached metadata-source snapshot stale."""
    global _metadata_source_status_timestamp
    with _status_lock:
        _metadata_source_status_timestamp = 0.0


def publish_spotify_status(
    *,
    connected: Any = _UNSET,
    authenticated: Any = _UNSET,
    rate_limited: Any = _UNSET,
    rate_limit: Any = _UNSET,
    post_ban_cooldown: Any = _UNSET,
) -> Dict[str, Any]:
    """Update the cached Spotify status snapshot from an event."""
    global _spotify_status_timestamp, _spotify_status_initialized
    global _spotify_rate_limit_expires_at, _spotify_post_ban_cooldown_expires_at

    with _status_lock:
        if connected is not _UNSET:
            _spotify_status_cache["connected"] = connected
        if authenticated is not _UNSET:
            _spotify_status_cache["authenticated"] = authenticated
        if rate_limited is not _UNSET:
            _spotify_status_cache["rate_limited"] = rate_limited
        if rate_limit is not _UNSET:
            _spotify_status_cache["rate_limit"] = rate_limit
            if rate_limit and isinstance(rate_limit, dict):
                _spotify_rate_limit_expires_at = float(rate_limit.get("expires_at") or 0.0)
            else:
                _spotify_rate_limit_expires_at = 0.0
        if post_ban_cooldown is not _UNSET:
            _spotify_status_cache["post_ban_cooldown"] = post_ban_cooldown
            if post_ban_cooldown is not None:
                _spotify_post_ban_cooldown_expires_at = time.time() + max(0, float(post_ban_cooldown))
            else:
                _spotify_post_ban_cooldown_expires_at = 0.0
        _spotify_status_timestamp = time.time()
        _spotify_status_initialized = True
        _normalize_spotify_status_locked(_spotify_status_timestamp)
        return dict(_spotify_status_cache)


def refresh_spotify_status_from_client(spotify_client: Optional[Any]) -> Dict[str, Any]:
    """Probe Spotify once to seed the cache when no event has populated it yet."""
    if spotify_client is None:
        with _status_lock:
            return dict(_spotify_status_cache)

    try:
        is_rate_limited = spotify_client.is_rate_limited() if spotify_client else False
        rate_limit_info = spotify_client.get_rate_limit_info() if (spotify_client and is_rate_limited) else None
        cooldown_remaining = spotify_client.get_post_ban_cooldown_remaining() if spotify_client else 0
        authenticated = spotify_client.is_spotify_authenticated() if spotify_client else False
    except Exception as exc:
        logger.debug("Spotify status probe failed: %s", exc)
        authenticated = False
        is_rate_limited = False
        rate_limit_info = None
        cooldown_remaining = 0

    return publish_spotify_status(
        connected=authenticated,
        authenticated=authenticated,
        rate_limited=is_rate_limited,
        rate_limit=rate_limit_info,
        post_ban_cooldown=cooldown_remaining if cooldown_remaining > 0 else None,
    )


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
    with _status_lock:
        if _spotify_status_initialized:
            _normalize_spotify_status_locked(time.time())
            return dict(_spotify_status_cache)

    return refresh_spotify_status_from_client(spotify_client)


def _normalize_spotify_status_locked(current_time: float) -> None:
    """Update derived Spotify status fields and clear expired ban state."""
    global _spotify_rate_limit_expires_at, _spotify_post_ban_cooldown_expires_at

    rate_limit = _spotify_status_cache.get("rate_limit")
    if _spotify_status_cache.get("rate_limited") and rate_limit and isinstance(rate_limit, dict):
        expires_at = float(rate_limit.get("expires_at") or 0.0)
        if expires_at > 0:
            remaining = int(max(0, expires_at - current_time))
            if remaining > 0:
                _spotify_status_cache["rate_limit"] = {**rate_limit, "remaining_seconds": remaining}
                _spotify_rate_limit_expires_at = expires_at
            else:
                _spotify_status_cache["rate_limited"] = False
                _spotify_status_cache["rate_limit"] = None
                _spotify_rate_limit_expires_at = 0.0
    elif _spotify_rate_limit_expires_at and current_time >= _spotify_rate_limit_expires_at:
        _spotify_status_cache["rate_limited"] = False
        _spotify_status_cache["rate_limit"] = None
        _spotify_rate_limit_expires_at = 0.0

    if _spotify_post_ban_cooldown_expires_at > 0:
        remaining = int(max(0, _spotify_post_ban_cooldown_expires_at - current_time))
        if remaining > 0:
            _spotify_status_cache["post_ban_cooldown"] = remaining
        else:
            _spotify_status_cache["post_ban_cooldown"] = None
            _spotify_post_ban_cooldown_expires_at = 0.0


def get_status_snapshot(spotify_client: Optional[Any] = None) -> Dict[str, Any]:
    """Return the combined metadata-provider status snapshot."""
    return {
        "metadata_source": get_metadata_source_status(),
        "spotify": get_spotify_status(spotify_client=spotify_client),
    }
