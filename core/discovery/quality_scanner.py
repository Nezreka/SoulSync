"""Shared metadata match + result-normalization helpers for quality matching.

These were the matching guts of the old auto-acting quality-scanner worker (now
removed — quality scanning is the ``quality_upgrade`` library-maintenance repair
job in ``core/repair_jobs/quality_upgrade.py``). They're kept here as a single
source of truth and imported by that job:

- ``_search_tracks_for_source`` — query one metadata source's ``search_tracks``.
- ``_normalize_track_match`` / ``_normalize_track_album`` / ``_normalize_track_artists``
  — turn a provider track into the wishlist-ready dict (typed Album converters
  with legacy duck-typed fallback).
- ``_track_name`` / ``_track_artist_names`` / ``_extract_lookup_value`` — accessors.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from core.metadata.registry import get_client_for_source
from core.metadata.types import Album
from core.wishlist.payloads import ensure_wishlist_track_format

logger = logging.getLogger(__name__)


# Per-source typed converter dispatch — same registry pattern as
# the metadata builders. Quality-scanner result normalization routes
# the embedded ``track.album`` blob through Album.from_<source>_dict()
# when provider is known. Falls back to legacy duck-typed extraction.
_TYPED_ALBUM_CONVERTERS: Dict[str, Callable[[Dict[str, Any]], Album]] = {
    'spotify': Album.from_spotify_dict,
    'itunes': Album.from_itunes_dict,
    'deezer': Album.from_deezer_dict,
    'discogs': Album.from_discogs_dict,
    'musicbrainz': Album.from_musicbrainz_dict,
    'hydrabase': Album.from_hydrabase_dict,
    'qobuz': Album.from_qobuz_dict,
}




def _extract_lookup_value(value: Any, *names: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (str, bytes)):
        return value

    for name in names:
        if isinstance(value, dict):
            if name in value and value[name] is not None:
                return value[name]
        else:
            candidate = getattr(value, name, None)
            if candidate is not None:
                return candidate
    return default


def _normalize_track_artists(track_item: Any) -> list[dict]:
    artists = _extract_lookup_value(track_item, 'artists', default=[]) or []
    if isinstance(artists, (str, bytes)):
        artists = [artists]
    elif isinstance(artists, dict):
        artists = [artists]
    else:
        try:
            artists = list(artists)
        except TypeError:
            artists = [artists]

    normalized = []
    for artist in artists:
        artist_name = _extract_lookup_value(artist, 'name', 'artist_name', 'title')
        if not artist_name and isinstance(artist, (str, bytes)):
            artist_name = artist
        if artist_name:
            artist_data = {'name': str(artist_name)}
            artist_images = _normalize_image_entries(_extract_lookup_value(artist, 'images', default=[]))
            artist_image_url = _extract_lookup_value(artist, 'image_url', 'artist_image_url', default=None)
            if artist_image_url and not artist_images:
                artist_images = [{'url': str(artist_image_url)}]
            if artist_images:
                artist_data['images'] = artist_images
                artist_data['image_url'] = artist_images[0].get('url')
            normalized.append(artist_data)

    if not normalized:
        normalized.append({'name': 'Unknown Artist'})

    return normalized


def _normalize_image_entries(image_value: Any) -> list[dict]:
    if not image_value:
        return []

    if isinstance(image_value, dict):
        image_value = [image_value]
    elif isinstance(image_value, (str, bytes)):
        image_value = [image_value]
    else:
        try:
            image_value = list(image_value)
        except TypeError:
            return []

    normalized = []
    seen_urls = set()
    for image in image_value:
        if isinstance(image, dict):
            image_url = image.get('url') or image.get('image_url')
            if not image_url:
                continue
            image_dict = dict(image)
            image_dict['url'] = str(image_url)
        elif isinstance(image, (str, bytes)):
            image_dict = {'url': str(image)}
        else:
            continue

        if image_dict['url'] in seen_urls:
            continue

        seen_urls.add(image_dict['url'])
        normalized.append(image_dict)

    return normalized


def _normalize_track_album(track_item: Any, provider: Optional[str] = None) -> dict:
    """Normalize a track's embedded album blob into a flat dict.

    When ``provider`` is provided AND maps to a registered typed Album
    converter, routes through the typed path to seed canonical fields
    on ``album_data`` before legacy fallback chains fill any gaps.
    Falls back to legacy duck-typed extraction on unknown provider /
    non-dict input / typed converter error — same pattern as the
    metadata builders.
    """
    album = _extract_lookup_value(track_item, 'album', default={})
    if isinstance(album, dict):
        album_data = dict(album)
    else:
        album_data = {
            'name': _extract_lookup_value(album, 'name', 'title', default=str(album) if album else '') or '',
            'album_type': _extract_lookup_value(album, 'album_type', default='album') or 'album',
            'total_tracks': _extract_lookup_value(album, 'total_tracks', 'track_count', default=0) or 0,
            'release_date': _extract_lookup_value(album, 'release_date', default='') or '',
        }

    if provider and isinstance(album, dict):
        converter = _TYPED_ALBUM_CONVERTERS.get(provider.strip().lower())
        if converter is not None:
            try:
                typed_album = converter(album)
                if typed_album.name:
                    album_data.setdefault('name', typed_album.name)
                if typed_album.album_type:
                    album_data.setdefault('album_type', typed_album.album_type)
                if typed_album.total_tracks:
                    album_data.setdefault('total_tracks', typed_album.total_tracks)
                if typed_album.release_date:
                    album_data.setdefault('release_date', typed_album.release_date)
                if typed_album.id:
                    album_data.setdefault('id', typed_album.id)
            except Exception as exc:
                logger.debug(
                    "Typed album converter failed for provider %s in quality "
                    "scanner normalize, falling back to legacy: %s", provider, exc,
                )

    album_data.setdefault('name', _extract_lookup_value(track_item, 'album_name', default='Unknown Album') or 'Unknown Album')
    album_data.setdefault('album_type', _extract_lookup_value(track_item, 'album_type', default='album') or 'album')
    album_data.setdefault('total_tracks', _extract_lookup_value(track_item, 'total_tracks', 'track_count', default=0) or 0)
    album_data.setdefault('release_date', _extract_lookup_value(track_item, 'release_date', default='') or '')

    album_images = _normalize_image_entries(album_data.get('images'))
    if not album_images and isinstance(album, dict):
        album_images = _normalize_image_entries(
            album.get('images')
            or album.get('image_url')
            or album.get('album_cover_url')
            or album.get('cover_url')
        )

    if not album_images:
        album_images = _normalize_image_entries(
            _extract_lookup_value(track_item, 'images', default=None)
            or _extract_lookup_value(track_item, 'image_url', default=None)
            or _extract_lookup_value(track_item, 'album_cover_url', default=None)
            or _extract_lookup_value(track_item, 'cover_url', default=None)
        )

    if album_images:
        album_data['images'] = album_images
        album_data.setdefault('image_url', album_images[0].get('url'))
    else:
        album_data['images'] = []

    album_data.setdefault('artists', _normalize_track_artists(track_item))
    return album_data


def _normalize_track_match(track_item: Any, provider: str) -> dict:
    track_data = {
        'id': _extract_lookup_value(track_item, 'id', 'track_id', default='') or '',
        'name': _extract_lookup_value(track_item, 'name', 'title', default='Unknown Track') or 'Unknown Track',
        'artists': _normalize_track_artists(track_item),
        'album': _normalize_track_album(track_item, provider=provider),
        'image_url': _extract_lookup_value(track_item, 'image_url', 'album_cover_url', default=None),
        'duration_ms': _extract_lookup_value(track_item, 'duration_ms', default=0) or 0,
        'track_number': _extract_lookup_value(track_item, 'track_number', default=1) or 1,
        'disc_number': _extract_lookup_value(track_item, 'disc_number', default=1) or 1,
        'preview_url': _extract_lookup_value(track_item, 'preview_url', default=None),
        'external_urls': _extract_lookup_value(track_item, 'external_urls', default={}) or {},
        'popularity': _extract_lookup_value(track_item, 'popularity', default=0) or 0,
        'provider': provider,
        'source': provider,
    }
    if not track_data['image_url']:
        album_images = track_data['album'].get('images') if isinstance(track_data['album'], dict) else []
        if isinstance(album_images, list) and album_images:
            first_image = album_images[0]
            if isinstance(first_image, dict):
                track_data['image_url'] = first_image.get('url')
    return ensure_wishlist_track_format(track_data)


def _track_name(track_item: Any) -> str:
    return str(_extract_lookup_value(track_item, 'name', 'title', default='Unknown Track') or 'Unknown Track')


def _track_artist_names(track_item: Any) -> list[str]:
    artists = _extract_lookup_value(track_item, 'artists', default=[]) or []
    if isinstance(artists, (str, bytes)):
        artists = [artists]
    elif isinstance(artists, dict):
        artists = [artists]
    else:
        try:
            artists = list(artists)
        except TypeError:
            artists = [artists]

    normalized = []
    for artist in artists:
        artist_name = _extract_lookup_value(artist, 'name', 'artist_name', 'title')
        if not artist_name and isinstance(artist, (str, bytes)):
            artist_name = artist
        if artist_name:
            normalized.append(str(artist_name))
    return normalized


def _search_tracks_for_source(source: str, query: str, limit: int = 5, client: Any = None):
    if client is None:
        client = get_client_for_source(source)
    if not client or not hasattr(client, 'search_tracks'):
        return []

    try:
        if source == 'spotify':
            return client.search_tracks(query, limit=limit, allow_fallback=False) or []
        return client.search_tracks(query, limit=limit) or []
    except TypeError:
        try:
            return client.search_tracks(query, limit=limit) or []
        except Exception as exc:
            logger.debug("Could not search %s for %s: %s", source, query, exc)
            return []
    except Exception as exc:
        logger.debug("Could not search %s for %s: %s", source, query, exc)
        return []
