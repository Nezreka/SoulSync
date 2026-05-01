"""MusicMap similar-artist helpers for metadata API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

from core.metadata import registry as metadata_registry
from core.metadata.artist_image import _extract_artist_image_url
from core.metadata.discography import (
    _extract_lookup_value,
    _normalize_artist_name,
    _pick_best_artist_match,
    _search_artists_for_source,
)
from core.metadata.lookup import MetadataLookupOptions
from utils.logging_config import get_logger

logger = get_logger("metadata.similar_artists")

__all__ = [
    "get_musicmap_similar_artists",
    "iter_musicmap_similar_artist_events",
]


def _get_source_chain_for_lookup(options: MetadataLookupOptions) -> List[str]:
    primary_source = metadata_registry.get_primary_source()
    source_chain = list(metadata_registry.get_source_priority(primary_source))
    override = (options.source_override or '').strip().lower()

    if override:
        source_chain = [override] + [source for source in source_chain if source != override]

    if not options.allow_fallback:
        source_chain = source_chain[:1]

    return source_chain


def _fetch_musicmap_similar_artist_names(artist_name: str) -> List[str]:
    """Fetch similar artist names from MusicMap."""
    if not (artist_name or '').strip():
        raise ValueError('Artist name is required')

    from bs4 import BeautifulSoup
    from urllib.parse import quote_plus

    url_artist = quote_plus(artist_name.strip())
    musicmap_url = f'https://www.music-map.com/{url_artist}'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }

    logger.debug("Fetching MusicMap: %s", musicmap_url)
    response = requests.get(musicmap_url, headers=headers, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, 'html.parser')
    gnod_map = soup.find(id='gnodMap')
    if not gnod_map:
        raise ValueError('Could not find artist map on MusicMap')

    searched_artist_lower = _normalize_artist_name(artist_name)
    similar_artist_names: List[str] = []
    seen_names = set()

    for anchor in gnod_map.find_all('a'):
        artist_text = anchor.get_text(strip=True)
        normalized_name = _normalize_artist_name(artist_text)
        if not normalized_name or normalized_name == searched_artist_lower or normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)
        similar_artist_names.append(artist_text)

    logger.debug("Found %s similar artists from MusicMap", len(similar_artist_names))
    return similar_artist_names


def _build_similar_artist_payload(artist_data: Any, source: str) -> Optional[Dict[str, Any]]:
    artist_id = _extract_lookup_value(artist_data, 'id', 'artist_id', 'spotify_id', 'itunes_id', 'deezer_id')
    if not artist_id:
        return None

    if isinstance(artist_data, dict):
        name = artist_data.get('name') or artist_data.get('artist_name') or artist_data.get('title')
        genres = artist_data.get('genres') or []
        popularity = artist_data.get('popularity') or artist_data.get('rank') or 0
    else:
        name = (
            getattr(artist_data, 'name', None)
            or getattr(artist_data, 'artist_name', None)
            or getattr(artist_data, 'title', None)
        )
        genres = getattr(artist_data, 'genres', None) or []
        popularity = getattr(artist_data, 'popularity', None) or getattr(artist_data, 'rank', None) or 0

    if isinstance(genres, str):
        genres = [genres]
    elif not isinstance(genres, list):
        try:
            genres = list(genres)
        except TypeError:
            genres = []

    try:
        popularity = int(popularity or 0)
    except Exception:
        popularity = 0

    return {
        'id': str(artist_id),
        'name': str(name or artist_id),
        'image_url': _extract_artist_image_url(artist_data),
        'genres': genres,
        'popularity': popularity,
        'source': source,
    }


def _resolve_musicmap_artist_source_ids(artist_name: str, source_chain: List[str]) -> Dict[str, Optional[str]]:
    searched_source_ids: Dict[str, Optional[str]] = {}

    for source in source_chain:
        client = metadata_registry.get_client_for_source(source)
        if not client:
            searched_source_ids[source] = None
            continue

        search_results = _search_artists_for_source(source, client, artist_name, limit=1)
        searched_source_ids[source] = _extract_lookup_value(search_results[0], 'id', 'artist_id') if search_results else None

    return searched_source_ids


def _match_musicmap_similar_artist(
    candidate_name: str,
    source_chain: List[str],
    searched_artist_name: str,
    searched_source_ids: Dict[str, Optional[str]],
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    target_name = _normalize_artist_name(candidate_name)
    searched_name = _normalize_artist_name(searched_artist_name)

    for source in source_chain:
        client = metadata_registry.get_client_for_source(source)
        if not client:
            continue

        search_results = _search_artists_for_source(source, client, candidate_name, limit=1)
        if not search_results:
            continue

        matched_artist = _pick_best_artist_match(search_results, candidate_name)
        if not matched_artist:
            continue

        matched_name = _normalize_artist_name(
            _extract_lookup_value(matched_artist, 'name', 'artist_name', 'title')
        )
        if matched_name and matched_name == searched_name:
            continue

        matched_id = _extract_lookup_value(matched_artist, 'id', 'artist_id')
        if not matched_id:
            continue

        if str(matched_id) == str(searched_source_ids.get(source) or ''):
            continue

        payload = _build_similar_artist_payload(matched_artist, source)
        if not payload:
            continue

        if source == 'itunes' and not payload.get('image_url') and hasattr(client, 'get_artist'):
            try:
                full_artist = client.get_artist(str(matched_id))
                image_url = _extract_artist_image_url(full_artist)
                if image_url:
                    payload['image_url'] = image_url
                elif hasattr(client, '_get_artist_image_from_albums'):
                    album_image_url = client._get_artist_image_from_albums(str(matched_id))
                    if album_image_url:
                        payload['image_url'] = album_image_url
            except Exception as exc:
                logger.debug("Could not enrich iTunes image for %s: %s", matched_id, exc)

        if target_name and _normalize_artist_name(payload['name']) == searched_name:
            continue

        return source, payload

    return None, None


def iter_musicmap_similar_artist_events(
    artist_name: str,
    limit: int = 20,
    source_override: Optional[str] = None,
):
    """Yield MusicMap similar-artist events using source priority."""
    try:
        source_chain = _get_source_chain_for_lookup(
            MetadataLookupOptions(source_override=source_override, allow_fallback=True)
        )
        available_sources = [source for source in source_chain if metadata_registry.get_client_for_source(source)]
        if not available_sources:
            yield {
                'type': 'error',
                'error': 'No metadata providers available for similar artist matching',
                'status_code': 503,
            }
            return

        similar_artist_names = _fetch_musicmap_similar_artist_names(artist_name)
        searched_source_ids = _resolve_musicmap_artist_source_ids(artist_name, source_chain)

        yield {
            'type': 'start',
            'artist_name': artist_name,
            'total_found': len(similar_artist_names),
            'source_priority': source_chain,
        }

        matched_count = 0
        seen_names = set()
        seen_ids = set()

        for candidate_name in similar_artist_names[:limit]:
            normalized_candidate = _normalize_artist_name(candidate_name)
            if not normalized_candidate or normalized_candidate in seen_names:
                continue

            source, payload = _match_musicmap_similar_artist(
                candidate_name,
                source_chain,
                artist_name,
                searched_source_ids,
            )
            if not payload:
                continue

            payload_id = str(payload.get('id') or '')
            if payload_id in seen_ids:
                continue

            seen_names.add(normalized_candidate)
            seen_ids.add(payload_id)
            matched_count += 1

            yield {
                'type': 'artist',
                'artist': payload,
                'source': source,
            }

        yield {
            'type': 'complete',
            'complete': True,
            'total': matched_count,
            'total_found': len(similar_artist_names),
            'artist_name': artist_name,
            'source_priority': source_chain,
        }

    except requests.exceptions.RequestException as exc:
        logger.debug("Error fetching MusicMap for %s: %s", artist_name, exc)
        yield {
            'type': 'error',
            'error': f'Failed to fetch from MusicMap: {exc}',
            'status_code': 502,
        }
    except ValueError as exc:
        status_code = 404 if 'Could not find artist map on MusicMap' in str(exc) else 400
        yield {
            'type': 'error',
            'error': str(exc),
            'status_code': status_code,
        }
    except Exception as exc:
        logger.error("Error streaming similar artists for %s: %s", artist_name, exc)
        yield {
            'type': 'error',
            'error': str(exc),
            'status_code': 500,
        }


def get_musicmap_similar_artists(
    artist_name: str,
    limit: int = 20,
    source_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Return matched MusicMap similar artists as a single payload."""
    artists: List[Dict[str, Any]] = []
    total_found = 0
    error_message = None
    status_code = 500
    source_priority: List[str] = []

    for event in iter_musicmap_similar_artist_events(
        artist_name,
        limit=limit,
        source_override=source_override,
    ):
        if event.get('type') == 'start':
            total_found = event.get('total_found', 0)
            source_priority = event.get('source_priority', [])
        elif event.get('type') == 'artist' and event.get('artist'):
            artists.append(event['artist'])
        elif event.get('type') == 'complete':
            total_found = event.get('total_found', total_found)
            source_priority = event.get('source_priority', source_priority)
        elif event.get('type') == 'error':
            error_message = event.get('error', 'Unknown error')
            status_code = int(event.get('status_code') or status_code or 500)
            break

    if error_message:
        return {
            'success': False,
            'error': error_message,
            'status_code': status_code,
            'artist': artist_name,
            'similar_artists': [],
            'total_found': total_found,
            'total_matched': 0,
            'source_priority': source_priority,
        }

    return {
        'success': True,
        'artist': artist_name,
        'similar_artists': artists,
        'total_found': total_found,
        'total_matched': len(artists),
        'source_priority': source_priority,
    }
