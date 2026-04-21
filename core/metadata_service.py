"""
Metadata Service - Centralized metadata source selection

ALL metadata source decisions flow through this module. Other files import
get_primary_source() and get_primary_client() instead of reimplementing
the logic. This prevents bugs where different files have different defaults,
auth checks, or source-fallback behavior.
"""

import threading
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Literal
from core.spotify_client import SpotifyClient
from core.itunes_client import iTunesClient
from utils.logging_config import get_logger

logger = get_logger("metadata_service")

MetadataProvider = Literal["spotify", "itunes", "auto"]

# Ordered by fallback preference. Higher-priority sources appear earlier.
METADATA_SOURCE_PRIORITY = ('deezer', 'itunes', 'spotify', 'discogs', 'hydrabase')

_client_cache_lock = threading.RLock()
_client_cache: Dict[str, Any] = {}


@dataclass(frozen=True)
class MetadataLookupOptions:
    """Generic metadata lookup policy shared by metadata services."""

    source_override: Optional[str] = None
    allow_fallback: bool = True
    skip_cache: bool = False
    max_pages: int = 0
    limit: int = 50
    artist_source_ids: Optional[Dict[str, str]] = None


# =============================================================================
# CANONICAL SOURCE SELECTION — all code should use these two functions
# =============================================================================

def get_primary_source() -> str:
    """Get the user's configured primary metadata source.

    Returns 'spotify', 'deezer', 'itunes', 'discogs', or 'hydrabase'.
    If the user selected Spotify but it's not authenticated, falls back to 'deezer'.

    This is THE single source of truth for "which metadata source should I use?"
    All other modules should import this function instead of reading config directly.
    """
    try:
        from config.settings import config_manager
        source = config_manager.get('metadata.fallback_source', 'deezer') or 'deezer'
    except Exception:
        return 'deezer'

    # Validate Spotify selection — can't use it if not authenticated
    if source == 'spotify':
        try:
            import importlib
            ws = importlib.import_module('web_server')
            sc = getattr(ws, 'spotify_client', None)
            if not sc or not sc.is_spotify_authenticated():
                return 'deezer'
        except Exception:
            return 'deezer'

    return source


def get_primary_client():
    """Get the client object for the user's configured primary metadata source.

    Returns a SpotifyClient, DeezerClient, iTunesClient, DiscogsClient,
    or HydrabaseClient instance.

    This is THE single source of truth for "which client should I call?"
    """
    return get_client_for_source(get_primary_source())


def get_source_priority(preferred_source: str):
    """Return supported sources with the preferred source first."""
    ordered = []

    if preferred_source in METADATA_SOURCE_PRIORITY:
        ordered.append(preferred_source)

    for source in METADATA_SOURCE_PRIORITY:
        if source not in ordered:
            ordered.append(source)

    return ordered


def get_client_for_source(source: str):
    """Get the client object for an exact metadata source.

    Returns the matching client or None if that source is unavailable.
    No fallback swaps.
    """
    if source == 'spotify':
        try:
            import importlib
            ws = importlib.import_module('web_server')
            sc = getattr(ws, 'spotify_client', None)
            if sc and sc.is_spotify_authenticated():
                return sc
        except Exception:
            pass
        return None

    if source == 'deezer':
        return get_deezer_client()

    if source == 'discogs':
        return get_discogs_client()

    if source == 'hydrabase':
        return get_hydrabase_client(allow_fallback=False)

    if source == 'itunes':
        return get_itunes_client()

    return None


def get_album_tracks_for_source(source: str, album_id: str):
    """Get album tracks for an exact source.

    Returns Spotify-compatible dict/list data or None.
    No fallback swaps.
    """
    client = get_client_for_source(source)
    if not client:
        return None

    try:
        fetch = getattr(client, 'get_album_tracks_dict', None) if source == 'hydrabase' else getattr(client, 'get_album_tracks', None)
        if not fetch:
            return None
        if source == 'spotify':
            return fetch(album_id, allow_fallback=False)
        return fetch(album_id)
    except Exception:
        return None


def get_album_for_source(source: str, album_id: str):
    """Get album metadata for an exact source.

    Returns a provider-normalized album dict or None.
    No fallback swaps.
    """
    client = get_client_for_source(source)
    if not client or not hasattr(client, 'get_album'):
        return None

    try:
        if source == 'spotify':
            return client.get_album(album_id, allow_fallback=False)
        return client.get_album(album_id)
    except Exception:
        return None


def get_artist_albums_for_source(
    source: str,
    artist_id: str,
    artist_name: str = '',
    album_type: str = 'album,single',
    limit: int = 50,
    skip_cache: bool = False,
    max_pages: int = 0,
):
    """Get artist albums for an exact source.

    Returns a provider-native album list or None if the source is unavailable.
    Tries the requested artist ID first, then falls back to artist-name
    search using the same flow for every provider when artist_name is provided.

    Set skip_cache=True only for freshness-sensitive flows that need newly
    released albums to show up immediately.
    """
    client = get_client_for_source(source)
    if not client or not hasattr(client, 'get_artist_albums'):
        return None

    def _fetch_for_artist(target_artist_id: str):
        kwargs = {
            'album_type': album_type,
            'limit': limit,
        }
        if source == 'spotify':
            kwargs['allow_fallback'] = False
            kwargs['skip_cache'] = skip_cache
            kwargs['max_pages'] = max_pages
        return client.get_artist_albums(target_artist_id, **kwargs)

    try:
        if artist_id:
            albums = _fetch_for_artist(artist_id) or []
            if albums:
                return albums
        else:
            albums = []

        if not artist_name:
            return albums

        search_results = _search_artists_for_source(source, client, artist_name, limit=5)
        if not search_results:
            return albums

        best = _pick_best_artist_match(search_results, artist_name)
        if not best:
            return albums

        found_artist_id = _extract_lookup_value(best, 'id', 'artist_id')
        if not found_artist_id:
            return albums

        resolved = _fetch_for_artist(found_artist_id) or []
        if resolved:
            logger.debug("Found %s artist '%s' (id=%s)", source, _extract_lookup_value(best, 'name', 'artist_name', 'title'), found_artist_id)
        return resolved
    except Exception:
        return None


def _get_source_chain_for_lookup(options: MetadataLookupOptions) -> List[str]:
    primary_source = get_primary_source()
    source_chain = list(get_source_priority(primary_source))
    override = (options.source_override or '').strip().lower()

    if override:
        source_chain = [override] + [source for source in source_chain if source != override]

    if not options.allow_fallback:
        source_chain = source_chain[:1]

    return source_chain


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


def _build_discography_release_dict(release: Any, artist_id: str) -> Optional[Dict[str, Any]]:
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
            client = get_client_for_source(source)
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
        release_data = _build_discography_release_dict(release, artist_id)
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


def _build_artist_detail_release_card(release: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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


def _get_completion_source_chain(source_override: Optional[str] = None) -> List[str]:
    primary_source = get_primary_source()
    source_chain = list(get_source_priority(primary_source))

    override = (source_override or '').strip().lower()
    if override:
        source_chain = [override] + [source for source in source_chain if source != override]

    return source_chain


def _extract_track_items(api_tracks: Any) -> List[Dict[str, Any]]:
    if not api_tracks:
        return []
    if isinstance(api_tracks, dict):
        return api_tracks.get('items') or []
    if isinstance(api_tracks, list):
        return api_tracks
    return []


def _normalize_track_artists(track_item: Any) -> List[str]:
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
        if not artist_name and isinstance(artist, str):
            artist_name = artist
        if artist_name:
            normalized.append(str(artist_name))
    return normalized


def _extract_album_track_items(album_data: Any, tracks_data: Any = None) -> List[Dict[str, Any]]:
    embedded_tracks = _extract_lookup_value(album_data, 'tracks', default=None)
    if isinstance(embedded_tracks, dict):
        items = embedded_tracks.get('items') or []
        if items:
            return items
    elif isinstance(embedded_tracks, list):
        if embedded_tracks:
            return embedded_tracks

    return _extract_track_items(tracks_data)


def _build_album_info(album_data: Any, album_id: str, album_name: str = '', artist_name: str = '') -> Dict[str, Any]:
    images = _extract_lookup_value(album_data, 'images', default=[]) or []
    if not isinstance(images, list):
        images = list(images) if images else []

    image_url = None
    if images:
        image_url = _extract_lookup_value(images[0], 'url')
    if not image_url:
        image_url = _extract_lookup_value(album_data, 'image_url', 'thumb_url')

    return {
        'id': _extract_lookup_value(album_data, 'id', 'album_id', 'collectionId', 'release_id', default=album_id) or album_id,
        'name': _extract_lookup_value(album_data, 'name', 'title', default=album_name or album_id) or album_name or album_id,
        'image_url': image_url,
        'images': images,
        'release_date': _extract_lookup_value(album_data, 'release_date', default='') or '',
        'album_type': _extract_lookup_value(album_data, 'album_type', default='album') or 'album',
        'total_tracks': _extract_lookup_value(album_data, 'total_tracks', 'track_count', default=0) or 0,
        'artist_name': artist_name or _extract_lookup_value(album_data, 'artist_name', default='') or '',
    }


def _build_album_track_entry(track_item: Any, album_info: Dict[str, Any], source: str) -> Dict[str, Any]:
    explicit_value = _extract_lookup_value(track_item, 'explicit', 'trackExplicitness', default=False)
    if isinstance(explicit_value, str):
        explicit_value = explicit_value.lower() == 'explicit'

    return {
        'id': _extract_lookup_value(track_item, 'id', 'track_id', 'trackId', default='') or '',
        'name': _extract_lookup_value(track_item, 'name', 'track_name', 'trackName', default='Unknown Track') or 'Unknown Track',
        'artists': _normalize_track_artists(track_item),
        'duration_ms': _extract_lookup_value(track_item, 'duration_ms', 'trackTimeMillis', default=0) or 0,
        'track_number': _extract_lookup_value(track_item, 'track_number', 'trackNumber', default=0) or 0,
        'disc_number': _extract_lookup_value(track_item, 'disc_number', 'discNumber', default=1) or 1,
        'explicit': bool(explicit_value),
        'preview_url': _extract_lookup_value(track_item, 'preview_url', 'previewUrl'),
        'external_urls': _extract_lookup_value(track_item, 'external_urls', default={}) or {},
        'uri': _extract_lookup_value(track_item, 'uri', default='') or '',
        'album': album_info,
        '_source': source,
    }


def _build_album_tracks_payload(
    album_data: Any,
    tracks_data: Any,
    source: str,
    album_id: str,
    album_name: str = '',
    artist_name: str = '',
) -> Dict[str, Any]:
    album_info = _build_album_info(album_data, album_id, album_name=album_name, artist_name=artist_name)
    track_items = _extract_album_track_items(album_data, tracks_data)
    tracks = [_build_album_track_entry(track, album_info, source) for track in track_items]

    return {
        'success': bool(tracks),
        'album': album_info,
        'tracks': tracks,
        'source': source,
    }


def resolve_album_reference(
    album_id: str,
    preferred_source: Optional[str] = None,
    album_name: str = '',
    artist_name: str = '',
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a local database album ID or name-based reference to a provider ID."""
    try:
        from database.music_database import get_database

        database = get_database()
        with database._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(albums)")
            album_columns = {row[1] for row in cursor.fetchall()}

            source_chain = list(get_source_priority(preferred_source or get_primary_source()))
            override = (preferred_source or '').strip().lower()
            if override:
                source_chain = [override] + [source for source in source_chain if source != override]

            source_columns = {
                'spotify': ('spotify_album_id',),
                'deezer': ('deezer_id', 'deezer_album_id'),
                'itunes': ('itunes_album_id',),
                'discogs': ('discogs_id',),
                'hydrabase': ('soul_id', 'hydrabase_album_id'),
            }

            select_columns = ["a.title", "ar.name as artist_name"]
            for columns in source_columns.values():
                for column in columns:
                    if column in album_columns:
                        select_columns.append(f"a.{column}")

            cursor.execute(
                """
                SELECT {select_columns}
                FROM albums a
                JOIN artists ar ON a.artist_id = ar.id
                WHERE a.id = ?
                """.format(select_columns=", ".join(select_columns)),
                (album_id,),
            )
            row = cursor.fetchone()

            if row:
                for source in source_chain:
                    for column in source_columns.get(source, ()):
                        if column not in row.keys():
                            continue
                        value = row[column]
                        if value:
                            return value, source

                search_title = album_name or row['title']
                search_artist = artist_name or row['artist_name']
                query = f"{search_artist} {search_title}".strip()

                for source in source_chain:
                    client = get_client_for_source(source)
                    if not client:
                        continue
                    results = _search_albums_for_source(source, client, query, limit=5)
                    if results:
                        for album in results:
                            candidate_name = str(_extract_lookup_value(album, 'name', 'title', default='') or '').strip().lower()
                            if candidate_name and candidate_name == str(search_title).strip().lower():
                                return _extract_lookup_value(album, 'id', 'album_id', 'release_id'), source
                        best = results[0]
                        return _extract_lookup_value(best, 'id', 'album_id', 'release_id'), source

            if not album_name and not artist_name:
                return None, None

            query = " ".join(part for part in (artist_name, album_name) if part).strip() or album_id
            for source in source_chain:
                client = get_client_for_source(source)
                if not client:
                    continue
                results = _search_albums_for_source(source, client, query, limit=5)
                if results:
                    for album in results:
                        candidate_name = str(_extract_lookup_value(album, 'name', 'title', default='') or '').strip().lower()
                        if album_name and candidate_name == album_name.strip().lower():
                            return _extract_lookup_value(album, 'id', 'album_id', 'release_id'), source
                    best = results[0]
                    return _extract_lookup_value(best, 'id', 'album_id', 'release_id'), source
    except Exception as e:
        logger.debug("Error resolving album reference %s: %s", album_id, e)

    return None, None


def get_artist_album_tracks(
    album_id: str,
    artist_name: str = '',
    album_name: str = '',
    source_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Get a normalized album-track payload using source-priority lookup."""
    source_chain = _get_source_chain_for_lookup(
        MetadataLookupOptions(source_override=source_override, allow_fallback=True)
    )
    preferred_source = source_chain[0] if source_chain else None

    for source in source_chain:
        client = get_client_for_source(source)
        if not client:
            continue

        album_data = get_album_for_source(source, album_id)
        if not album_data:
            continue

        tracks_data = None
        if not _extract_album_track_items(album_data):
            tracks_data = get_album_tracks_for_source(source, album_id)
        payload = _build_album_tracks_payload(
            album_data,
            tracks_data,
            source,
            album_id,
            album_name=album_name,
            artist_name=artist_name,
        )
        if payload['tracks']:
            payload['success'] = True
            payload['source_priority'] = source_chain
            payload['resolved_album_id'] = album_id
            return payload

    resolved_album_id, resolved_source = resolve_album_reference(
        album_id,
        preferred_source=preferred_source,
        album_name=album_name,
        artist_name=artist_name,
    )

    if resolved_album_id:
        retry_sources = []
        if resolved_source:
            retry_sources.append(resolved_source)
        retry_sources.extend(source for source in source_chain if source not in retry_sources)

        for source in retry_sources:
            client = get_client_for_source(source)
            if not client:
                continue

            album_data = get_album_for_source(source, resolved_album_id)
            if not album_data:
                continue

            tracks_data = None
            if not _extract_album_track_items(album_data):
                tracks_data = get_album_tracks_for_source(source, resolved_album_id)
            payload = _build_album_tracks_payload(
                album_data,
                tracks_data,
                source,
                resolved_album_id,
                album_name=album_name,
                artist_name=artist_name,
            )
            if payload['tracks']:
                payload['success'] = True
                payload['source_priority'] = source_chain
                payload['resolved_album_id'] = resolved_album_id
                return payload

            # Keep trying the remaining sources in case another provider has the track listing.
            continue

    if resolved_album_id:
        return {
            'success': False,
            'error': 'No tracks found for album — it may be region-restricted or unavailable on this metadata source',
            'status_code': 404,
            'source_priority': source_chain,
            'resolved_album_id': resolved_album_id,
            'tracks': [],
            'album': {
                'id': resolved_album_id,
                'name': album_name or resolved_album_id,
                'image_url': None,
                'images': [],
                'release_date': '',
                'album_type': 'album',
                'total_tracks': 0,
            },
        }

    return {
        'success': False,
        'error': 'Album not found',
        'status_code': 404,
        'source_priority': source_chain,
        'resolved_album_id': None,
        'tracks': [],
        'album': {
            'id': album_id,
            'name': album_name or album_id,
            'image_url': None,
            'images': [],
            'release_date': '',
            'album_type': 'album',
            'total_tracks': 0,
        },
    }


def _resolve_completion_artist_name(
    discography: Dict[str, Any],
    artist_name: str,
) -> str:
    resolved_name = (artist_name or '').strip()
    if resolved_name and resolved_name.lower() != 'unknown artist':
        return resolved_name

    release_items = list((discography or {}).get('albums', []) or []) + list((discography or {}).get('singles', []) or [])
    if not release_items:
        return resolved_name or 'Unknown Artist'

    release_artist_name = _extract_release_artist_name(release_items[0])
    if release_artist_name:
        logger.debug("Using release artist metadata '%s' for completion", release_artist_name)
        return release_artist_name

    return resolved_name or 'Unknown Artist'


def _resolve_completion_track_total(release: Dict[str, Any], source_chain: List[str]) -> int:
    total_tracks = _extract_lookup_value(release, 'total_tracks', default=0) or 0
    if total_tracks:
        return int(total_tracks)

    release_id = _extract_lookup_value(release, 'id', 'album_id', 'release_id')
    if not release_id:
        return 0

    for source in source_chain:
        try:
            api_tracks = get_album_tracks_for_source(source, str(release_id))
            items = _extract_track_items(api_tracks)
            if items:
                logger.debug("Resolved track count for release %s from %s", release_id, source)
                return len(items)
        except Exception as exc:
            logger.debug("Could not resolve track count for release %s from %s: %s", release_id, source, exc)

    return 0


def check_album_completion(
    db,
    album_data: Dict[str, Any],
    artist_name: str,
    source_override: Optional[str] = None,
    source_chain: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Check completion status for a single album."""
    try:
        source_chain = source_chain or _get_completion_source_chain(source_override)
        album_name = album_data.get('name', '')
        total_tracks = _resolve_completion_track_total(album_data, source_chain)
        album_id = album_data.get('id', '')

        # If total_tracks is 0 (Discogs masters don't include track counts),
        # try to fetch the real count from the prioritized metadata sources.
        if total_tracks == 0 and album_id:
            logger.debug("No track count found for '%s' (%s)", album_name, album_id)

        logger.debug(f"Checking album: '{album_name}' ({total_tracks} tracks)")

        formats = []
        # Check if album exists in database with completeness info
        try:
            from config.settings import config_manager
            active_server = config_manager.get_active_media_server()
            db_album, confidence, owned_tracks, expected_tracks, is_complete, formats = db.check_album_exists_with_completeness(
                title=album_name,
                artist=artist_name,
                expected_track_count=total_tracks if total_tracks > 0 else None,
                confidence_threshold=0.7,
                server_source=active_server
            )
        except Exception as db_error:
            logger.error(f"Database error for album '{album_name}': {db_error}")
            return {
                "id": album_id,
                "name": album_name,
                "status": "error",
                "owned_tracks": 0,
                "expected_tracks": total_tracks,
                "completion_percentage": 0,
                "confidence": 0.0,
                "found_in_db": False,
                "error_message": str(db_error),
                "formats": []
            }

        if expected_tracks > 0:
            completion_percentage = (owned_tracks / expected_tracks) * 100
        elif total_tracks > 0:
            completion_percentage = (owned_tracks / total_tracks) * 100
        else:
            completion_percentage = 100 if owned_tracks > 0 else 0

        if owned_tracks > 0 and owned_tracks >= (expected_tracks or total_tracks):
            status = "completed"
        elif owned_tracks > 0:
            status = "partial"
        else:
            status = "missing"

        logger.debug(
            "Album completion result: owned=%s expected=%s total=%s completion=%.1f status=%s",
            owned_tracks,
            expected_tracks or total_tracks,
            total_tracks,
            completion_percentage,
            status,
        )

        return {
            "id": album_id,
            "name": album_name,
            "status": status,
            "owned_tracks": owned_tracks,
            "expected_tracks": expected_tracks or total_tracks,
            "completion_percentage": round(completion_percentage, 1),
            "confidence": round(confidence, 2) if confidence else 0.0,
            "found_in_db": db_album is not None,
            "formats": formats
        }

    except Exception as e:
        logger.error(f"Error checking album completion for '{album_data.get('name', 'Unknown')}': {e}")
        return {
            "id": album_data.get('id', ''),
            "name": album_data.get('name', 'Unknown'),
            "status": "error",
            "owned_tracks": 0,
            "expected_tracks": album_data.get('total_tracks', 0),
            "completion_percentage": 0,
            "confidence": 0.0,
            "found_in_db": False,
            "formats": []
        }


def check_single_completion(
    db,
    single_data: Dict[str, Any],
    artist_name: str,
    source_override: Optional[str] = None,
    source_chain: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Check completion status for a single/EP."""
    try:
        source_chain = source_chain or _get_completion_source_chain(source_override)
        single_name = single_data.get('name', '')
        raw_total_tracks = single_data.get('total_tracks', 1)
        total_tracks = raw_total_tracks if raw_total_tracks is not None else 1
        single_id = single_data.get('id', '')
        album_type = single_data.get('album_type', 'single')
        formats = []

        if total_tracks == 0:
            total_tracks = _resolve_completion_track_total(single_data, source_chain) or 1

        logger.debug(
            "Checking %s: name=%r tracks=%s",
            album_type,
            single_name,
            total_tracks,
        )

        if album_type == 'ep' or total_tracks > 1:
            try:
                from config.settings import config_manager
                active_server = config_manager.get_active_media_server()
                db_album, confidence, owned_tracks, expected_tracks, is_complete, formats = db.check_album_exists_with_completeness(
                    title=single_name,
                    artist=artist_name,
                    expected_track_count=total_tracks,
                    confidence_threshold=0.7,
                    server_source=active_server
                )
            except Exception as db_error:
                logger.error(f"Database error for EP '{single_name}': {db_error}")
                owned_tracks, expected_tracks, confidence = 0, total_tracks, 0.0
                db_album = None

            if expected_tracks > 0:
                completion_percentage = (owned_tracks / expected_tracks) * 100
            else:
                completion_percentage = (owned_tracks / total_tracks) * 100

            if owned_tracks > 0 and owned_tracks >= (expected_tracks or total_tracks):
                status = "completed"
            elif owned_tracks > 0:
                status = "partial"
            else:
                status = "missing"

            logger.debug(
                "EP completion result: owned=%s expected=%s total=%s completion=%.1f status=%s",
                owned_tracks,
                expected_tracks or total_tracks,
                total_tracks,
                completion_percentage,
                status,
            )

            return {
                "id": single_id,
                "name": single_name,
                "status": status,
                "owned_tracks": owned_tracks,
                "expected_tracks": expected_tracks or total_tracks,
                "completion_percentage": round(completion_percentage, 1),
                "confidence": round(confidence, 2) if confidence else 0.0,
                "found_in_db": db_album is not None,
                "type": album_type,
                "formats": formats
            }
        else:
            try:
                from config.settings import config_manager
                active_server = config_manager.get_active_media_server()
                db_track, confidence = db.check_track_exists(
                    title=single_name,
                    artist=artist_name,
                    confidence_threshold=0.7,
                    server_source=active_server
                )
            except Exception as db_error:
                logger.error(f"Database error for single '{single_name}': {db_error}")
                db_track, confidence = None, 0.0

            owned_tracks = 1 if db_track else 0
            expected_tracks = 1
            completion_percentage = 100 if db_track else 0
            status = "completed" if db_track else "missing"

            if db_track and db_track.file_path:
                import os
                ext = os.path.splitext(db_track.file_path)[1].lstrip('.').upper()
                if ext == 'MP3' and db_track.bitrate:
                    formats = [f"MP3-{db_track.bitrate}"]
                elif ext:
                    formats = [ext]

            logger.debug(
                "Single completion result: owned=%s expected=1 completion=%.1f status=%s",
                owned_tracks,
                completion_percentage,
                status,
            )

            return {
                "id": single_id,
                "name": single_name,
                "status": status,
                "owned_tracks": owned_tracks,
                "expected_tracks": expected_tracks,
                "completion_percentage": round(completion_percentage, 1),
                "confidence": round(confidence, 2) if confidence else 0.0,
                "found_in_db": db_track is not None,
                "type": album_type,
                "formats": formats
            }

    except Exception as e:
        logger.error(f"Error checking single/EP completion for '{single_data.get('name', 'Unknown')}': {e}")
        return {
            "id": single_data.get('id', ''),
            "name": single_data.get('name', 'Unknown'),
            "status": "error",
            "owned_tracks": 0,
            "expected_tracks": single_data.get('total_tracks', 1),
            "completion_percentage": 0,
            "confidence": 0.0,
            "found_in_db": False,
            "type": single_data.get('album_type', 'single'),
            "formats": []
        }


def iter_artist_discography_completion_events(
    discography: Dict[str, Any],
    artist_name: str = 'Unknown Artist',
    source_override: Optional[str] = None,
    db=None,
):
    """Yield completion-stream events for artist discography ownership checks."""
    if db is None:
        from database.music_database import get_database

        db = get_database()
    source_chain = _get_completion_source_chain(source_override)
    resolved_artist_name = _resolve_completion_artist_name(discography or {}, artist_name)

    albums = list((discography or {}).get('albums', []) or [])
    singles = list((discography or {}).get('singles', []) or [])
    total_items = len(albums) + len(singles)
    processed_count = 0

    yield {
        'type': 'start',
        'total_items': total_items,
        'artist_name': resolved_artist_name,
    }

    for album in albums:
        try:
            completion_data = check_album_completion(
                db,
                album,
                resolved_artist_name,
                source_override=source_override,
                source_chain=source_chain,
            )
            completion_data['type'] = 'album_completion'
            completion_data['container_type'] = 'albums'
            processed_count += 1
            completion_data['progress'] = round((processed_count / total_items) * 100, 1) if total_items else 100
            yield completion_data
        except Exception as e:
            yield {
                'type': 'error',
                'container_type': 'albums',
                'id': album.get('id', ''),
                'name': album.get('name', 'Unknown'),
                'error': str(e),
            }

    for single in singles:
        try:
            completion_data = check_single_completion(
                db,
                single,
                resolved_artist_name,
                source_override=source_override,
                source_chain=source_chain,
            )
            completion_data['type'] = 'single_completion'
            completion_data['container_type'] = 'singles'
            processed_count += 1
            completion_data['progress'] = round((processed_count / total_items) * 100, 1) if total_items else 100
            yield completion_data
        except Exception as e:
            yield {
                'type': 'error',
                'container_type': 'singles',
                'id': single.get('id', ''),
                'name': single.get('name', 'Unknown'),
                'error': str(e),
            }

    yield {
        'type': 'complete',
        'processed_count': processed_count,
        'artist_name': resolved_artist_name,
    }


def check_artist_discography_completion(
    discography: Dict[str, Any],
    artist_name: str = 'Unknown Artist',
    source_override: Optional[str] = None,
    db=None,
) -> Dict[str, Any]:
    """Return completion results for an artist discography without streaming."""
    albums_completion = []
    singles_completion = []

    for event in iter_artist_discography_completion_events(
        discography,
        artist_name=artist_name,
        source_override=source_override,
        db=db,
    ):
        if event.get('type') == 'album_completion':
            albums_completion.append(event)
        elif event.get('type') == 'single_completion':
            singles_completion.append(event)

    return {
        'albums': albums_completion,
        'singles': singles_completion,
    }


def get_deezer_client():
    """Get cached Deezer client.

    Deezer client is safe to reuse across requests because it owns no
    request-specific state beyond the current access token.
    """
    from core.deezer_client import DeezerClient
    try:
        from config.settings import config_manager
        current_token = config_manager.get('deezer.access_token', None)
    except Exception:
        current_token = None

    cache_key = f"deezer::{current_token or ''}"
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            client = DeezerClient()
            _client_cache[cache_key] = client
        return client


def get_itunes_client():
    """Get cached iTunes client."""
    with _client_cache_lock:
        client = _client_cache.get("itunes")
        if client is None:
            client = iTunesClient()
            _client_cache["itunes"] = client
        return client


def get_discogs_client(token: Optional[str] = None):
    """Get cached Discogs client.

    Discogs auth changes are token-driven, so the cache key tracks the
    current configured token.
    """
    if token is None:
        try:
            from config.settings import config_manager
            current_token = config_manager.get('discogs.token', '') or ''
        except Exception:
            current_token = ''
    else:
        current_token = token or ''

    cache_key = f"discogs::{current_token}"
    with _client_cache_lock:
        client = _client_cache.get(cache_key)
        if client is None:
            from core.discogs_client import DiscogsClient
            client = DiscogsClient(token=current_token or None)
            _client_cache[cache_key] = client
        return client


def is_hydrabase_enabled() -> bool:
    """Return True when Hydrabase is connected and allowed for metadata use."""
    try:
        import importlib
        ws = importlib.import_module('web_server')
        client = getattr(ws, 'hydrabase_client', None)
        if not client or not client.is_connected():
            return False
        return bool(getattr(ws, 'dev_mode_enabled', False))
    except Exception:
        return False


def get_hydrabase_client(allow_fallback: bool = True, require_enabled: bool = True):
    """Return current Hydrabase client if connected and enabled.

    If allow_fallback is True, return iTunes fallback when Hydrabase is not
    connected or not enabled. If False, return None instead.
    """
    try:
        import importlib
        ws = importlib.import_module('web_server')
        client = getattr(ws, 'hydrabase_client', None)
        if client and client.is_connected():
            if not require_enabled or bool(getattr(ws, 'dev_mode_enabled', False)):
                return client
    except Exception:
        pass
    if allow_fallback:
        return get_itunes_client()
    return None


def clear_cached_metadata_clients():
    """Clear cached metadata clients.

    Useful for tests and config reload flows.
    """
    with _client_cache_lock:
        _client_cache.clear()


def _get_client_for_source(source: str):
    if source == 'spotify':
        try:
            import importlib
            ws = importlib.import_module('web_server')
            sc = getattr(ws, 'spotify_client', None)
            if sc and sc.is_spotify_authenticated():
                return sc
        except Exception:
            pass
        return get_deezer_client()

    if source == 'deezer':
        return get_deezer_client()

    if source == 'discogs':
        return get_discogs_client()

    if source == 'hydrabase':
        return get_hydrabase_client()

    return get_itunes_client()


# =============================================================================
# LEGACY ALIASES — kept for backward compatibility, delegate to canonical funcs
# =============================================================================

def _get_configured_fallback_source():
    """Legacy alias for get_primary_source(). Use get_primary_source() instead."""
    return get_primary_source()


def _create_fallback_client():
    """Legacy alias for get_primary_client(). Use get_primary_client() instead."""
    return get_primary_client()


class MetadataService:
    """
    Unified metadata service that seamlessly switches between Spotify and
    the configured fallback source (iTunes or Deezer).

    Usage:
        service = MetadataService()
        tracks = service.search_tracks("Radiohead OK Computer")
        # Uses Spotify if authenticated, otherwise configured fallback
    """

    def __init__(self, preferred_provider: MetadataProvider = "auto"):
        """
        Initialize metadata service.

        Args:
            preferred_provider: "spotify", "itunes", or "auto" (default)
                - "auto": Use Spotify if authenticated, else configured fallback
                - "spotify": Always use Spotify (may fail if not authenticated)
                - "itunes": Always use configured fallback source
        """
        self.preferred_provider = preferred_provider
        self.spotify = SpotifyClient()
        self._fallback_source = get_primary_source()
        self.itunes = get_client_for_source(self._fallback_source)

        self._log_initialization()

    def _log_initialization(self):
        """Log initialization status"""
        spotify_status = "Authenticated" if self.spotify.is_spotify_authenticated() else "Not authenticated"
        fallback_status = "Available" if self.itunes.is_authenticated() else "Not available"

        logger.info(f"MetadataService initialized - Spotify: {spotify_status}, {self._fallback_source.capitalize()}: {fallback_status}")
        logger.info(f"Preferred provider: {self.preferred_provider}")

    def get_active_provider(self) -> str:
        """
        Get the currently active metadata provider.

        Returns:
            "spotify" or the configured fallback source name
        """
        if self.preferred_provider == "spotify":
            return "spotify"
        elif self.preferred_provider == "itunes":
            return self._fallback_source
        else:  # auto — use the centralized source selection
            return get_primary_source()

    def _get_client(self):
        """Get the appropriate client based on provider selection"""
        provider = self.get_active_provider()

        if provider == "spotify":
            if not self.spotify.is_spotify_authenticated():
                logger.warning(f"Spotify requested but not authenticated, falling back to {self._fallback_source}")
                return self.itunes
            return self.spotify
        else:
            return self.itunes
    
    # ==================== Search Methods ====================
    
    def search_tracks(self, query: str, limit: int = 20) -> List:
        """
        Search for tracks using active provider.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of Track objects
        """
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Searching tracks with {provider}: '{query}'")
        return client.search_tracks(query, limit)
    
    def search_artists(self, query: str, limit: int = 20) -> List:
        """
        Search for artists using active provider.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of Artist objects
        """
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Searching artists with {provider}: '{query}'")
        return client.search_artists(query, limit)
    
    def search_albums(self, query: str, limit: int = 20) -> List:
        """
        Search for albums using active provider.
        
        Args:
            query: Search query
            limit: Maximum results
            
        Returns:
            List of Album objects
        """
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Searching albums with {provider}: '{query}'")
        return client.search_albums(query, limit)
    
    # ==================== Detail Fetching ====================
    
    def get_track_details(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed track information"""
        client = self._get_client()
        return client.get_track_details(track_id)
    
    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album information"""
        client = self._get_client()
        return client.get_album(album_id)
    
    def get_album_tracks(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get all tracks from an album"""
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Fetching album tracks with {provider}: {album_id}")
        return client.get_album_tracks(album_id)
    
    def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """Get artist information"""
        client = self._get_client()
        return client.get_artist(artist_id)
    
    def get_artist_albums(self, artist_id: str, album_type: str = "album,single", limit: int = 50) -> List:
        """Get artist's albums/discography"""
        client = self._get_client()
        provider = self.get_active_provider()
        logger.debug(f"Fetching artist albums with {provider}: {artist_id}")
        return client.get_artist_albums(artist_id, album_type, limit)
    
    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """
        Get track audio features (Spotify only).
        Returns None for iTunes.
        """
        client = self._get_client()
        return client.get_track_features(track_id)
    
    # ==================== User Library (Spotify only) ====================
    
    def get_user_playlists(self) -> List:
        """Get user playlists (Spotify only)"""
        if self.spotify.is_spotify_authenticated():
            return self.spotify.get_user_playlists()
        logger.warning("User playlists only available with Spotify authentication")
        return []

    def get_saved_tracks(self) -> List:
        """Get user's saved/liked tracks (Spotify only)"""
        if self.spotify.is_spotify_authenticated():
            return self.spotify.get_saved_tracks()
        logger.warning("Saved tracks only available with Spotify authentication")
        return []

    def get_saved_tracks_count(self) -> int:
        """Get count of user's saved tracks (Spotify only)"""
        if self.spotify.is_spotify_authenticated():
            return self.spotify.get_saved_tracks_count()
        return 0

    # ==================== Utility Methods ====================

    def is_authenticated(self) -> bool:
        """Check if any provider is available"""
        return self.spotify.is_spotify_authenticated() or self.itunes.is_authenticated()

    def get_provider_info(self) -> Dict[str, Any]:
        """Get information about available providers"""
        return {
            "active_provider": self.get_active_provider(),
            "spotify_authenticated": self.spotify.is_spotify_authenticated(),
            "itunes_available": self.itunes.is_authenticated(),
            "fallback_source": self._fallback_source,
            "preferred_provider": self.preferred_provider,
            "can_access_user_data": self.spotify.is_spotify_authenticated(),
        }
    
    def reload_config(self):
        """Reload configuration for both clients"""
        logger.info("Reloading metadata service configuration")
        self.spotify.reload_config()
        new_source = get_primary_source()
        self._fallback_source = new_source
        self.itunes = get_client_for_source(new_source)
        self._log_initialization()


# Convenience singleton instance
_metadata_service_instance: Optional[MetadataService] = None


def get_metadata_service() -> MetadataService:
    """
    Get global metadata service instance (singleton pattern).
    
    Returns:
        MetadataService instance
    """
    global _metadata_service_instance
    if _metadata_service_instance is None:
        _metadata_service_instance = MetadataService()
    return _metadata_service_instance
