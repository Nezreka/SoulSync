"""Shared artist album-list cache helpers for metadata clients."""

from __future__ import annotations

from typing import Any, Optional


def make_artist_album_cache_key(
    artist_id: str,
    album_type: str = 'album,single',
    limit: int = 200,
    *,
    include_limit: bool = True,
) -> str:
    """Return the metadata-cache key for an artist album-list query."""
    safe_album_type = str(album_type or 'album,single').replace(',', '_')
    base_key = f"{artist_id}_albums_{safe_album_type}"
    return f"{base_key}_{limit}" if include_limit else base_key


def get_cached_artist_album_items(
    cache: Any,
    source: str,
    artist_id: str,
    *,
    album_type: str = 'album,single',
    limit: int = 200,
    include_limit: bool = True,
    items_field: str = '_albums',
) -> Optional[list[dict[str, Any]]]:
    """Return cached raw artist album-list items, or None on miss/invalid shape."""
    cached = cache.get_entity(
        source,
        'artist',
        make_artist_album_cache_key(artist_id, album_type, limit, include_limit=include_limit),
    )
    if not isinstance(cached, dict):
        return None

    items = cached.get(items_field)
    return items if isinstance(items, list) and items else None


def get_cached_artist_album_payload(
    cache: Any,
    source: str,
    artist_id: str,
    *,
    album_type: str = 'album,single',
    limit: int = 200,
    include_limit: bool = True,
) -> Optional[dict[str, Any]]:
    """Return the raw cached artist album-list payload, or None on miss."""
    cached = cache.get_entity(
        source,
        'artist',
        make_artist_album_cache_key(artist_id, album_type, limit, include_limit=include_limit),
    )
    return cached if isinstance(cached, dict) else None


def store_artist_album_items(
    cache: Any,
    source: str,
    artist_id: str,
    items: list[dict[str, Any]],
    *,
    album_type: str = 'album,single',
    limit: int = 200,
    include_limit: bool = True,
    items_field: str = '_albums',
    extra_fields: Optional[dict[str, Any]] = None,
) -> None:
    """Store raw artist album-list items in the metadata cache."""
    if not items:
        return

    payload: dict[str, Any] = {
        'name': f'albums_{artist_id}',
        items_field: items,
    }
    if extra_fields:
        payload.update(extra_fields)

    cache.store_entity(
        source,
        'artist',
        make_artist_album_cache_key(artist_id, album_type, limit, include_limit=include_limit),
        payload,
    )
