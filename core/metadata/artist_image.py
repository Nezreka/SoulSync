"""Artist image lookup helpers for metadata API."""

from __future__ import annotations

from typing import Any, Optional

from core.metadata import registry as metadata_registry
from core.metadata.discography import _extract_lookup_value
from utils.logging_config import get_logger

logger = get_logger("metadata.artist_image")

__all__ = [
    "get_artist_image_url",
]


def _extract_artist_image_url(artist_data: Any) -> Optional[str]:
    if not artist_data:
        return None

    images = _extract_lookup_value(artist_data, 'images', default=[]) or []
    if not isinstance(images, list):
        try:
            images = list(images)
        except TypeError:
            images = []

    if images:
        first_image = images[0]
        image_url = _extract_lookup_value(first_image, 'url')
        if image_url:
            return image_url

    return _extract_lookup_value(
        artist_data,
        'image_url',
        'thumb_url',
        'cover_image',
        'picture_xl',
        'picture_big',
        'picture_medium',
    )


def _get_artist_image_from_source(source: str, artist_id: str) -> Optional[str]:
    client = metadata_registry.get_client_for_source(source)
    if not client:
        return None

    try:
        if source == 'spotify':
            artist_data = client.get_artist(artist_id, allow_fallback=False)
        else:
            artist_data = client.get_artist(artist_id)
    except Exception as exc:
        logger.debug("Could not fetch artist image for %s on %s: %s", artist_id, source, exc)
        artist_data = None

    image_url = _extract_artist_image_url(artist_data)
    if image_url:
        return image_url

    if hasattr(client, '_get_artist_image_from_albums'):
        try:
            return client._get_artist_image_from_albums(artist_id)
        except Exception as exc:
            logger.debug("Could not fetch artist album art for %s on %s: %s", artist_id, source, exc)

    return None


def _lookup_artist_image_by_name(name: str) -> Optional[str]:
    """Look up an artist image by name across fallback sources."""
    name = (name or '').strip()
    if not name:
        return None

    skip_sources = {'musicbrainz', 'soulseek', 'youtube_videos', 'hydrabase'}
    for source in metadata_registry.get_source_priority(metadata_registry.get_primary_source()):
        if source in skip_sources:
            continue
        client = metadata_registry.get_client_for_source(source)
        if not client or not hasattr(client, 'search_artists'):
            continue
        try:
            results = client.search_artists(name, limit=1) or []
            if results:
                top = results[0]
                image_url = getattr(top, 'image_url', None) or (
                    top.get('image_url') if isinstance(top, dict) else None
                )
                if image_url:
                    return image_url
        except Exception as exc:
            logger.debug("Artist image lookup by name failed on %s for %r: %s", source, name, exc)
            continue

    return None


def get_artist_image_url(
    artist_id: str,
    source_override: Optional[str] = None,
    plugin: Optional[str] = None,
    artist_name: Optional[str] = None,
) -> Optional[str]:
    """Resolve an artist image URL using the configured source priority."""
    if not artist_id:
        return None

    if artist_id.startswith('soul_'):
        return None

    source_override = (source_override or '').strip().lower()
    plugin = (plugin or '').strip().lower()

    if source_override == 'hydrabase':
        if plugin in ('deezer', 'itunes'):
            return _get_artist_image_from_source(plugin, artist_id)
        if artist_id.isdigit():
            return _get_artist_image_from_source('itunes', artist_id)
        return None

    if source_override == 'musicbrainz':
        if not artist_name:
            return None
        return _lookup_artist_image_by_name(artist_name)

    if source_override:
        return _get_artist_image_from_source(source_override, artist_id)

    for source in metadata_registry.get_source_priority(metadata_registry.get_primary_source()):
        image_url = _get_artist_image_from_source(source, artist_id)
        if image_url:
            return image_url

    return None
