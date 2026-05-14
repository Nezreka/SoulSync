"""Discography lookup helpers for metadata API."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.metadata import registry as metadata_registry
from core.metadata.album_tracks import get_artist_albums_for_source
from core.metadata.lookup import MetadataLookupOptions
from core.metadata.types import Album
from utils.logging_config import get_logger

logger = get_logger("metadata.discography")


# Per-source typed converter dispatch — same registry pattern as
# ``core/metadata/album_tracks.py`` and ``core/imports/resolution.py``.
# Discography release builders dispatch through the typed Album
# converter when the active source is known. Falls back to legacy
# duck-typed extraction below on unknown source / converter error.
_TYPED_ALBUM_CONVERTERS: Dict[str, Callable[[Dict[str, Any]], Album]] = {
    'spotify': Album.from_spotify_dict,
    'itunes': Album.from_itunes_dict,
    'deezer': Album.from_deezer_dict,
    'discogs': Album.from_discogs_dict,
    'musicbrainz': Album.from_musicbrainz_dict,
    'hydrabase': Album.from_hydrabase_dict,
    'qobuz': Album.from_qobuz_dict,
}


def _typed_album_for_source(release: Any, source: Optional[str]) -> Optional[Album]:
    """Return a typed Album when source maps to a registered converter
    and the converter succeeds. ``None`` means the caller should fall
    back to the legacy duck-typed extraction.
    """
    if not source or not isinstance(release, dict):
        return None
    converter = _TYPED_ALBUM_CONVERTERS.get(source.strip().lower())
    if converter is None:
        return None
    try:
        return converter(release)
    except Exception as exc:
        logger.debug(
            "Typed album converter failed for source %s in discography "
            "build, falling back to legacy: %s", source, exc,
        )
        return None


def _extract_lookup_value(value: Any, *names: str, default: Any = None) -> Any:
    if value is None:
        return default

    for name in names:
        if isinstance(value, dict):
            if name in value and value[name] is not None:
                return value[name]
        else:
            candidate = getattr(value, name, None)
            if candidate is not None:
                return candidate
    return default


def _get_source_chain_for_lookup(options: MetadataLookupOptions) -> List[str]:
    primary_source = metadata_registry.get_primary_source()
    source_chain = list(metadata_registry.get_source_priority(primary_source))
    override = (options.source_override or '').strip().lower()

    if override:
        source_chain = [override] + [source for source in source_chain if source != override]

    if not options.allow_fallback:
        source_chain = source_chain[:1]

    return source_chain


def _normalize_artist_name(value: Any) -> str:
    return (value or '').strip().casefold()


def _search_artists_for_source(source: str, client: Any, artist_name: str, limit: int = 5) -> List[Any]:
    if not client or not hasattr(client, 'search_artists'):
        return []

    try:
        kwargs = {'limit': limit}
        if source == 'spotify':
            kwargs['allow_fallback'] = False
        return client.search_artists(artist_name, **kwargs) or []
    except Exception as exc:
        logger.debug("Could not search %s for %s: %s", source, artist_name, exc)
        return []


def _search_albums_for_source(source: str, client: Any, query: str, limit: int = 5) -> List[Any]:
    if not client or not hasattr(client, 'search_albums'):
        return []

    try:
        kwargs = {'limit': limit}
        if source == 'spotify':
            kwargs['allow_fallback'] = False
        return client.search_albums(query, **kwargs) or []
    except Exception as exc:
        logger.debug("Could not search %s for %s: %s", source, query, exc)
        return []


def _pick_best_artist_match(search_results: List[Any], artist_name: str) -> Optional[Any]:
    """Prefer an exact artist-name match, otherwise use the first result."""
    if not search_results:
        return None

    target_name = _normalize_artist_name(artist_name)
    for artist in search_results:
        candidate_name = _normalize_artist_name(
            _extract_lookup_value(artist, 'name', 'artist_name', 'title')
        )
        if candidate_name == target_name:
            return artist

    return search_results[0]


def _build_discography_release_dict(release: Any, artist_id: str,
                                    source: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Build a normalized discography release dict.

    When ``source`` is provided AND maps to a registered typed Album
    converter, routes through ``Album.from_<source>_dict()`` and pulls
    canonical fields off the typed Album. Falls back to the legacy
    duck-typed extraction on unknown source / non-dict input / typed
    converter error.
    """
    typed_album = _typed_album_for_source(release, source)
    if typed_album is not None:
        if not typed_album.id:
            return None
        artist_name = typed_album.artists[0] if typed_album.artists else ''
        return {
            'id': typed_album.id,
            'name': typed_album.name or typed_album.id,
            'artist_name': artist_name,
            'release_date': typed_album.release_date or None,
            'album_type': typed_album.album_type or 'album',
            'image_url': typed_album.image_url,
            'total_tracks': typed_album.total_tracks or 0,
            'external_urls': typed_album.external_urls or {},
            'explicit': typed_album.explicit,
        }

    release_id = _extract_lookup_value(release, 'id', 'album_id', 'release_id')
    if not release_id:
        return None

    album_type = _extract_lookup_value(release, 'album_type', default='album') or 'album'
    release_date = _extract_lookup_value(release, 'release_date')

    return {
        'id': release_id,
        'name': _extract_lookup_value(release, 'name', 'title', default=release_id),
        'artist_name': _extract_release_artist_name(release),
        'release_date': release_date,
        'album_type': album_type,
        'image_url': _extract_lookup_value(release, 'image_url', 'thumb_url', 'cover_image'),
        'total_tracks': _extract_lookup_value(release, 'total_tracks', default=0) or 0,
        'external_urls': _extract_lookup_value(release, 'external_urls', default={}) or {},
        'explicit': _extract_lookup_value(release, 'explicit'),
    }


def _extract_release_artist_name(release: Any) -> str:
    artist_name = _extract_lookup_value(release, 'artist_name', 'artist', default='') or ''
    artist_name = str(artist_name).strip()
    if artist_name:
        return artist_name

    artists = _extract_lookup_value(release, 'artists', default=[]) or []
    if isinstance(artists, (str, bytes)):
        return str(artists).strip()
    if isinstance(artists, dict):
        return str(_extract_lookup_value(artists, 'name', 'artist_name', 'title', default='') or '').strip()

    try:
        artists = list(artists)
    except TypeError:
        artists = [artists]

    if not artists:
        return ''

    first_artist = artists[0]
    inferred_name = _extract_lookup_value(first_artist, 'name', 'artist_name', 'title')
    if not inferred_name and isinstance(first_artist, str):
        inferred_name = first_artist

    return str(inferred_name).strip() if inferred_name else ''


def _sort_discography_releases(releases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def get_release_year(item):
        if item.get('release_date'):
            try:
                return int(str(item['release_date'])[:4])
            except (ValueError, IndexError, TypeError):
                return 0
        return 0

    return sorted(releases, key=get_release_year, reverse=True)


def _dedup_variant_releases(releases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse obvious edition variants into a single canonical release card.

    This keeps a clean UI while still preserving distinct releases when the
    cleaned titles diverge enough that they are likely not variants.
    """
    if not releases:
        return []

    import re
    from difflib import SequenceMatcher

    variant_suffix_pattern = re.compile(
        r'\s*[\(\[][^()\[\]]*\b(?:edition|editions|deluxe|remaster|remastered|'
        r'explicit|clean|version|anniversary|collector|expanded|redux)\b[^()\[\]]*[\)\]]\s*$',
        re.IGNORECASE,
    )
    legacy_suffix_pattern = re.compile(
        r'\s*-\s*(explicit|clean|deluxe edition|single)\s*$',
        re.IGNORECASE,
    )
    variant_keyword_pattern = re.compile(
        r'\b(?:edition|editions|deluxe|remaster|remastered|explicit|clean|version|'
        r'anniversary|collector|expanded|redux)\b',
        re.IGNORECASE,
    )

    def _clean_title(title: Any) -> str:
        cleaned = str(title or '').strip().lower()
        while True:
            new_cleaned = variant_suffix_pattern.sub('', cleaned).strip()
            new_cleaned = legacy_suffix_pattern.sub('', new_cleaned).strip()
            if new_cleaned == cleaned:
                break
            cleaned = new_cleaned
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned

    def _has_variant_suffix(title: Any) -> bool:
        raw = str(title or '').strip()
        return bool(re.search(r'[\(\[][^\)\]]*' + variant_keyword_pattern.pattern + r'[^\)\]]*[\)\]]\s*$', raw, flags=re.IGNORECASE))

    def _is_compilation(release: Dict[str, Any]) -> bool:
        title = str(_extract_lookup_value(release, 'name', 'title', default='') or '').lower()
        album_type = str(_extract_lookup_value(release, 'album_type', default='') or '').lower()
        return (
            album_type == 'compilation'
            or 'best of' in title
            or 'greatest hits' in title
            or 'collection' in title
            or 'anthology' in title
            or 'essential' in title
        )

    def _variant_score(release: Dict[str, Any]) -> tuple:
        title = str(_extract_lookup_value(release, 'name', 'title', default='') or '').lower()
        has_explicit = 'explicit' in title
        has_clean = 'clean' in title and not has_explicit
        track_count = int(_extract_lookup_value(release, 'track_count', 'total_tracks', default=0) or 0)
        release_date = str(_extract_lookup_value(release, 'release_date', default='') or '')
        has_variant_suffix = _has_variant_suffix(title)

        # Higher is better.
        return (
            1 if not _is_compilation(release) else 0,
            1 if not has_variant_suffix else 0,
            2 if has_explicit else (1 if not has_clean else 0),
            track_count,
            release_date,
        )

    grouped: Dict[tuple, Dict[str, Any]] = {}
    ordered_keys: List[tuple] = []

    for release in releases:
        title = _extract_lookup_value(release, 'name', 'title', default='') or ''
        release_date = _extract_lookup_value(release, 'release_date')
        year = _extract_lookup_value(release, 'year')
        if not year and release_date:
            year = str(release_date)[:4]
        year = str(year) if year is not None else ''

        cleaned_title = _clean_title(title) or str(title).strip().lower()
        key = (cleaned_title, year)

        existing = grouped.get(key)
        if existing is None:
            grouped[key] = release
            ordered_keys.append(key)
            continue

        # If the cleaned titles are still materially different, keep both.
        existing_clean = _clean_title(_extract_lookup_value(existing, 'name', 'title', default='') or '')
        if SequenceMatcher(None, cleaned_title, existing_clean).ratio() < 0.85:
            alt_key = (str(title).strip().lower(), year)
            if alt_key not in grouped:
                grouped[alt_key] = release
                ordered_keys.append(alt_key)
            continue

        if _variant_score(release) > _variant_score(existing):
            grouped[key] = release

    return [grouped[key] for key in ordered_keys]


def get_artist_discography(
    artist_id: str,
    artist_name: str = '',
    options: Optional[MetadataLookupOptions] = None,
) -> Dict[str, Any]:
    """Get a normalized artist discography with source resolution and fallback.

    Each provider uses the same lookup flow:
    1. try the requested artist ID
    2. if that misses, search by artist name
    3. retry with the provider-specific artist ID from the search result
    """
    options = options or MetadataLookupOptions()
    source_priority = _get_source_chain_for_lookup(options)
    source_artist_ids = options.artist_source_ids or {}

    albums: List[Any] = []
    active_source: Optional[str] = None

    if not albums:
        for source in source_priority:
            client = metadata_registry.get_client_for_source(source)
            if not client:
                continue

            source_artist_id = (source_artist_ids.get(source) or '').strip()
            lookup_artist_id = source_artist_id if source_artist_id else (artist_id if not source_artist_ids else '')
            if source_artist_id:
                logger.debug("Using %s artist id %s for discography lookup", source, source_artist_id)

            try:
                albums = get_artist_albums_for_source(
                    source,
                    lookup_artist_id,
                    artist_name=artist_name,
                    limit=options.limit,
                    skip_cache=options.skip_cache,
                    max_pages=options.max_pages,
                ) or []
            except Exception as exc:
                logger.debug("%s direct lookup failed for artist %s: %s", source, artist_id, exc)
                albums = []

            if albums:
                active_source = source
                logger.info("Got %s albums from %s for artist %s", len(albums), source, artist_id)
                break

    album_list: List[Dict[str, Any]] = []
    singles_list: List[Dict[str, Any]] = []
    seen_albums = set()

    for release in albums or []:
        release_data = _build_discography_release_dict(release, artist_id, source=active_source)
        if not release_data:
            continue

        release_id = release_data['id']
        if release_id in seen_albums:
            continue
        seen_albums.add(release_id)

        album_type = release_data.get('album_type') or 'album'
        if album_type in ['single', 'ep']:
            singles_list.append(release_data)
        else:
            album_list.append(release_data)

    album_list = _sort_discography_releases(album_list)
    singles_list = _sort_discography_releases(singles_list)

    logger.debug(
        "Total albums returned for artist %s: %s (source=%s)",
        artist_id,
        len(album_list) + len(singles_list),
        active_source,
    )

    return {
        'albums': album_list,
        'singles': singles_list,
        'source': active_source or (source_priority[0] if source_priority else 'unknown'),
        'source_priority': source_priority,
    }


def _build_artist_detail_release_card(release: Dict[str, Any],
                                      source: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Build an artist-detail release card.

    NOTE on inputs: this function may receive EITHER raw provider
    release dicts (when called directly during a fresh discography
    lookup) OR pre-built canonical release dicts produced by
    ``_build_discography_release_dict`` (the more common case via
    ``get_artist_detail_discography``). Pre-built dicts already carry
    canonical keys, so the typed dispatch is a no-op for them — and
    the legacy duck-typed path also handles them correctly. The typed
    dispatch only kicks in when the caller passes a known source AND
    the release dict matches a provider's wire shape.
    """
    typed_album = _typed_album_for_source(release, source)
    if typed_album is not None and typed_album.id:
        release_year = None
        if typed_album.release_date:
            try:
                release_year = str(typed_album.release_date)[:4]
            except Exception:
                release_year = None

        card = {
            'id': typed_album.id,
            'name': typed_album.name or typed_album.id,
            'title': typed_album.name or typed_album.id,
            'album_type': (typed_album.album_type or 'album').lower(),
            'image_url': typed_album.image_url,
            'year': release_year,
            'track_count': typed_album.total_tracks or 0,
            'owned': None,
            'track_completion': 'checking',
            'explicit': typed_album.explicit,
        }
        if typed_album.release_date:
            card['release_date'] = typed_album.release_date
        elif release_year:
            card['release_date'] = f"{release_year}-01-01"
        return card

    release_id = _extract_lookup_value(release, 'id', 'album_id', 'release_id')
    if not release_id:
        return None

    album_type = (_extract_lookup_value(release, 'album_type', default='album') or 'album').lower()
    release_date = _extract_lookup_value(release, 'release_date')
    release_year = None
    if release_date:
        try:
            release_year = str(release_date)[:4]
        except Exception:
            release_year = None
    if not release_year:
        release_year = _extract_lookup_value(release, 'year')
        if release_year is not None:
            release_year = str(release_year)

    card = {
        'id': release_id,
        'name': _extract_lookup_value(release, 'name', 'title', default=release_id),
        'title': _extract_lookup_value(release, 'name', 'title', default=release_id),
        'album_type': album_type,
        'image_url': _extract_lookup_value(release, 'image_url', 'thumb_url', 'cover_image'),
        'year': release_year,
        'track_count': _extract_lookup_value(release, 'track_count', 'total_tracks', default=0) or 0,
        'owned': None,
        'track_completion': 'checking',
        'explicit': _extract_lookup_value(release, 'explicit'),
    }

    if release_date:
        card['release_date'] = release_date
    elif release_year:
        card['release_date'] = f"{release_year}-01-01"

    return card


def get_artist_detail_discography(
    artist_id: str,
    artist_name: str = '',
    options: Optional[MetadataLookupOptions] = None,
) -> Dict[str, Any]:
    """Get artist-detail-ready discography cards from the source-priority lookup flow."""
    source_discography = get_artist_discography(
        artist_id,
        artist_name=artist_name,
        options=options,
    )

    albums: List[Dict[str, Any]] = []
    eps: List[Dict[str, Any]] = []
    singles: List[Dict[str, Any]] = []
    seen_ids = set()

    for release in list(source_discography.get('albums', []) or []) + list(source_discography.get('singles', []) or []):
        card = _build_artist_detail_release_card(release)
        if not card:
            continue

        release_id = card['id']
        if release_id in seen_ids:
            continue
        seen_ids.add(release_id)

        album_type = (card.get('album_type') or 'album').lower()
        if album_type == 'ep':
            eps.append(card)
        elif album_type == 'single':
            singles.append(card)
        else:
            albums.append(card)

    if options is None or options.dedup_variants:
        albums = _dedup_variant_releases(albums)
        eps = _dedup_variant_releases(eps)
        singles = _dedup_variant_releases(singles)

    albums = _sort_discography_releases(albums)
    eps = _sort_discography_releases(eps)
    singles = _sort_discography_releases(singles)

    has_releases = bool(albums or eps or singles)
    return {
        'success': has_releases,
        'albums': albums,
        'eps': eps,
        'singles': singles,
        'source': source_discography.get('source', 'unknown'),
        'source_priority': source_discography.get('source_priority', []),
        'error': None if has_releases else f'No releases found for artist "{artist_name or artist_id}"',
    }
