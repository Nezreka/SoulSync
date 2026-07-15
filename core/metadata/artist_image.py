"""Artist image lookup helpers for metadata API."""

from __future__ import annotations

import re
from typing import Any, Optional

from core.metadata import registry as metadata_registry
from core.metadata.discography import _extract_lookup_value
from utils.logging_config import get_logger

logger = get_logger("metadata.artist_image")

__all__ = [
    "get_artist_image_url",
    "gather_artist_image_candidates",
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


# mbid -> (fetched_at, url|None). The MB lookup is a real API call behind a
# 1 rps limiter; the search page lazy-loads several cards at once and re-runs
# on every search, so repeats must be free. None results cache too (an artist
# with no relations shouldn't be re-asked every render).
_MB_RELATION_IMAGE_CACHE: dict = {}
_MB_RELATION_IMAGE_TTL_S = 6 * 3600

_MB_URL_REL_PATTERNS = (
    ('deezer', re.compile(r'deezer\.com/(?:[a-z]{2}/)?artist/(\d+)', re.I)),
    ('spotify', re.compile(r'open\.spotify\.com/artist/([A-Za-z0-9]+)', re.I)),
    ('itunes', re.compile(r'music\.apple\.com/.+?/(?:artist/)?(?:[^/]*/)?(\d+)', re.I)),
)


def _image_from_musicbrainz_relations(mbid: str) -> Optional[str]:
    """Resolve an MB artist's image via its url relations (exact per-source
    artist ids), never by name. Cached; returns None when MB has no usable
    streaming relation or the lookup fails."""
    import time as _time
    now = _time.time()
    hit = _MB_RELATION_IMAGE_CACHE.get(mbid)
    if hit and now - hit[0] < _MB_RELATION_IMAGE_TTL_S:
        return hit[1]

    url = None
    try:
        from core.musicbrainz_client import MusicBrainzClient
        artist = MusicBrainzClient("SoulSync", "2").get_artist(mbid, includes=['url-rels'])
        for rel in ((artist or {}).get('relations') or []):
            resource = str(((rel or {}).get('url') or {}).get('resource') or '')
            if not resource:
                continue
            for source, pattern in _MB_URL_REL_PATTERNS:
                m = pattern.search(resource)
                if m:
                    url = _get_artist_image_from_source(source, m.group(1))
                    if url:
                        break
            if url:
                break
    except Exception as exc:
        logger.debug("MB url-relation image lookup failed for %s: %s", mbid, exc)

    _MB_RELATION_IMAGE_CACHE[mbid] = (now, url)
    return url


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
        # MB stores no artist images, but it DOES store url relations to the
        # artist's exact Deezer/Spotify/Apple pages. Resolve through those
        # FIRST: the name fallback takes the first source's top hit for the
        # name, and a same-named artist can hijack the photo (#1036 — the MB
        # "Korn" card wore a Thai pop duo's art while opening the metal
        # band's discography). Only when MB has no usable relation does the
        # name lookup run.
        image_url = _image_from_musicbrainz_relations(artist_id)
        if image_url:
            return image_url
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


# Which artists-table column holds each source's artist id (for direct, exact
# lookups in the candidate gather — beats a name search when we have it).
_SOURCE_ID_COLUMNS = {
    'spotify': 'spotify_artist_id',
    'deezer': 'deezer_id',
    'itunes': 'itunes_artist_id',
    'audiodb': 'audiodb_id',
    'discogs': 'discogs_id',
}

# Sources that can't produce an artist photo (or aren't image services at all).
_CANDIDATE_SKIP_SOURCES = {'musicbrainz', 'soulseek', 'youtube_videos', 'hydrabase'}


def gather_artist_image_candidates(artist_name: str, source_ids: Optional[dict] = None) -> list:
    """One candidate photo per CONNECTED metadata source, for the artist
    image picker (mirrors ``gather_album_art_candidates``).

    For each source in the configured priority chain: use the artist's stored
    per-source id when the library row has one (exact), otherwise search the
    source by name and take its top hit's image. Sources fan out concurrently;
    a failing source contributes nothing. Returns ``[{source, url}, ...]``
    deduped by URL, in chain order.
    """
    name = (artist_name or '').strip()
    ids = source_ids or {}
    sources = [s for s in metadata_registry.get_source_priority(metadata_registry.get_primary_source())
               if s not in _CANDIDATE_SKIP_SOURCES]

    def _one(source: str):
        try:
            client = metadata_registry.get_client_for_source(source)
            if not client:
                return None
            sid = str(ids.get(_SOURCE_ID_COLUMNS.get(source, '')) or '').strip()
            url = _get_artist_image_from_source(source, sid) if sid else None
            if not url and name and hasattr(client, 'search_artists'):
                results = client.search_artists(name, limit=1) or []
                if results:
                    top = results[0]
                    url = getattr(top, 'image_url', None) or (
                        top.get('image_url') if isinstance(top, dict) else None)
            return (source, url) if url else None
        except Exception as exc:
            logger.debug("artist image candidate failed for %s: %s", source, exc)
            return None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(len(sources), 6) or 1) as pool:
        results = list(pool.map(_one, sources))

    candidates, seen = [], set()
    for entry in results:
        if not entry:
            continue
        source, url = entry
        if url in seen:
            continue
        seen.add(url)
        candidates.append({'source': source, 'url': url})
    return candidates
