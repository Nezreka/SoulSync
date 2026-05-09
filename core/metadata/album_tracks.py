"""Album-track lookup helpers for metadata API."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional

from core.metadata import registry as metadata_registry
from core.metadata.lookup import MetadataLookupOptions
from core.metadata.types import Album
from utils.logging_config import get_logger

logger = get_logger("metadata.album_tracks")


# Per-source typed converter dispatch. Powers the typed path inside
# ``_build_album_info`` — when the caller knows which provider the raw
# response came from, route through the canonical Album converter
# instead of duck-typing every field. Sources missing from this map
# fall through to the legacy duck-typed path.
_TYPED_ALBUM_CONVERTERS: Dict[str, Callable[[Dict[str, Any]], Album]] = {
    'spotify': Album.from_spotify_dict,
    'itunes': Album.from_itunes_dict,
    'deezer': Album.from_deezer_dict,
    'discogs': Album.from_discogs_dict,
    'musicbrainz': Album.from_musicbrainz_dict,
    'hydrabase': Album.from_hydrabase_dict,
    'qobuz': Album.from_qobuz_dict,
}

__all__ = [
    "get_album_for_source",
    "get_album_tracks_for_source",
    "get_artist_album_tracks",
    "get_artist_albums_for_source",
    "resolve_album_reference",
]


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


def _get_source_chain_for_lookup(options: MetadataLookupOptions) -> List[str]:
    primary_source = metadata_registry.get_primary_source()
    source_chain = list(metadata_registry.get_source_priority(primary_source))
    override = (options.source_override or '').strip().lower()

    if override:
        source_chain = [override] + [source for source in source_chain if source != override]

    if not options.allow_fallback:
        source_chain = source_chain[:1]

    return source_chain


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


def _normalize_context_artists(artists: Any) -> List[Dict[str, Any]]:
    if not artists:
        return []

    if isinstance(artists, (str, bytes)):
        artists = [artists]
    elif isinstance(artists, dict):
        artists = [artists]
    else:
        try:
            artists = list(artists)
        except TypeError:
            artists = [artists]

    normalized: List[Dict[str, Any]] = []
    for artist in artists:
        if isinstance(artist, dict):
            name = _extract_lookup_value(artist, 'name', 'artist_name', 'title', default='') or ''
            artist_id = _extract_lookup_value(artist, 'id', 'artist_id', default='') or ''
            entry: Dict[str, Any] = {}
            if name:
                entry['name'] = str(name)
            if artist_id:
                entry['id'] = str(artist_id)
            genres = _extract_lookup_value(artist, 'genres', default=None)
            if genres is not None:
                entry['genres'] = genres
            if entry:
                normalized.append(entry)
            continue

        name = str(artist).strip()
        if name:
            normalized.append({'name': name})

    return normalized


def _build_album_info(album_data: Any, album_id: str, album_name: str = '',
                      artist_name: str = '', source: str = '') -> Dict[str, Any]:
    """Build the canonical SoulSync internal album-info dict.

    When ``source`` is provided AND maps to a known typed converter,
    routes through the canonical ``Album.from_<source>_dict()`` path —
    that single converter is the source of truth for that provider's
    wire shape. Falls back to the legacy duck-typed extraction when
    source is empty/unknown OR when the typed converter raises (so a
    converter bug can't break album resolution).

    See ``docs/metadata-types-migration.md`` for the broader plan.
    """
    typed_path_succeeded = None
    if source and isinstance(album_data, dict):
        converter = _TYPED_ALBUM_CONVERTERS.get(source.lower())
        if converter is not None:
            try:
                typed_path_succeeded = _build_album_info_typed(
                    album_data, album_id, album_name, artist_name, converter,
                )
            except Exception as exc:
                logger.debug(
                    "Typed album_info converter failed for source %s, falling "
                    "back to legacy path: %s", source, exc,
                )
    if typed_path_succeeded is not None:
        return typed_path_succeeded

    return _build_album_info_legacy(album_data, album_id, album_name, artist_name)


def _build_album_info_typed(album_data: Dict[str, Any], album_id: str,
                            album_name: str, artist_name: str,
                            converter: Callable[[Dict[str, Any]], Album]) -> Dict[str, Any]:
    """Typed path: convert raw → Album, apply caller fallbacks for
    fields the converter couldn't fill, return canonical dict."""
    album = converter(album_data)

    # Apply caller-provided fallbacks when the converter produced
    # empty values. The legacy path treated `album_id` / `album_name`
    # / `artist_name` as last-resort defaults.
    if not album.id:
        album = replace(album, id=album_id)
    if not album.name:
        album = replace(album, name=album_name or album_id)
    if (not album.artists or album.artists == ['Unknown Artist']) and artist_name:
        album = replace(album, artists=[artist_name])

    ctx = album.to_context_dict()

    # Preserve original `images` list shape from the raw input — the
    # legacy path passed the source's full multi-resolution images
    # array through verbatim. Some downstream consumers iterate the
    # full list to pick a different size.
    raw_images = album_data.get('images')
    if isinstance(raw_images, list) and raw_images:
        ctx['images'] = raw_images
        # Legacy path also derived image_url from the first images entry
        # when the source-specific cover field wasn't populated. Match
        # that fallback so callers with Spotify-shaped raw images keep
        # getting an image_url out of providers whose typed converter
        # only checks source-native cover fields.
        if not ctx.get('image_url'):
            first = raw_images[0]
            if isinstance(first, dict):
                ctx['image_url'] = first.get('url') or ctx.get('image_url')

    return ctx


def _build_album_info_legacy(album_data: Any, album_id: str,
                             album_name: str, artist_name: str) -> Dict[str, Any]:
    """Original duck-typed extraction. Kept as the fallback when the
    typed path can't apply (unknown source, non-dict input, converter
    error). Tracked for removal once every caller passes a recognized
    source — see migration plan."""
    images = _extract_lookup_value(album_data, 'images', default=[]) or []
    if not isinstance(images, list):
        images = list(images) if images else []

    artists = _normalize_context_artists(_extract_lookup_value(album_data, 'artists', default=[]))
    if not artists and artist_name:
        artists = [{'name': artist_name}]

    primary_artist = artists[0] if artists else {}
    resolved_artist_name = (
        _extract_lookup_value(primary_artist, 'name', default='')
        or artist_name
        or _extract_lookup_value(album_data, 'artist_name', 'artist', default='')
        or ''
    )
    resolved_artist_id = str(
        _extract_lookup_value(primary_artist, 'id', default='')
        or _extract_lookup_value(album_data, 'artist_id', default='')
        or ''
    ).strip()

    image_url = None
    if images:
        image_url = _extract_lookup_value(images[0], 'url')
    if not image_url:
        image_url = _extract_lookup_value(album_data, 'image_url', 'thumb_url')

    return {
        'id': _extract_lookup_value(album_data, 'id', 'album_id', 'collectionId', 'release_id', default=album_id) or album_id,
        'name': _extract_lookup_value(album_data, 'name', 'title', default=album_name or album_id) or album_name or album_id,
        'artist': resolved_artist_name or '',
        'artist_name': resolved_artist_name or '',
        'artist_id': resolved_artist_id,
        'artists': artists,
        'image_url': image_url,
        'images': images,
        'release_date': _extract_lookup_value(album_data, 'release_date', default='') or '',
        'album_type': _extract_lookup_value(album_data, 'album_type', default='album') or 'album',
        'total_tracks': _extract_lookup_value(album_data, 'total_tracks', 'track_count', default=0) or 0,
    }


def _build_album_track_entry(track_item: Any, album_info: Dict[str, Any], source: str) -> Dict[str, Any]:
    explicit_value = _extract_lookup_value(track_item, 'explicit', 'trackExplicitness', default=False)
    if isinstance(explicit_value, str):
        explicit_value = explicit_value.lower() == 'explicit'

    # Per-recording exact identifiers — drive the auto-import matcher's
    # fast paths (`core.imports.album_matching.find_exact_id_matches`).
    # Spotify/Deezer typically expose ISRC inside `external_ids.isrc`;
    # iTunes uses top-level `isrc`. MusicBrainz-aware sources expose MBID
    # similarly. Stripping these used to be invisible — until the matcher
    # learned to use them, then it became "fast paths never trigger in
    # production even though the unit tests pass" — pinned by the
    # production-shape test in test_album_matching_exact_id.py.
    external_ids = _extract_lookup_value(track_item, 'external_ids', default=None) or {}
    isrc = (
        _extract_lookup_value(track_item, 'isrc', default='') or ''
        or (external_ids.get('isrc') if isinstance(external_ids, dict) else '')
        or ''
    )
    mbid = (
        _extract_lookup_value(track_item, 'musicbrainz_id', 'mbid', default='') or ''
        or (external_ids.get('mbid') if isinstance(external_ids, dict) else '')
        or (external_ids.get('musicbrainz') if isinstance(external_ids, dict) else '')
        or ''
    )

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
        'external_ids': external_ids if isinstance(external_ids, dict) else {},
        'isrc': str(isrc) if isrc else '',
        'musicbrainz_id': str(mbid) if mbid else '',
        'uri': _extract_lookup_value(track_item, 'uri', default='') or '',
        'album': album_info,
        'source': source,
        'provider': source,
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
    album_info = _build_album_info(
        album_data, album_id,
        album_name=album_name, artist_name=artist_name, source=source,
    )
    album_info['source'] = source
    album_info['_source'] = source
    album_info['provider'] = source
    track_items = _extract_album_track_items(album_data, tracks_data)
    tracks = [_build_album_track_entry(track, album_info, source) for track in track_items]

    return {
        'success': bool(tracks),
        'album': album_info,
        'tracks': tracks,
        'source': source,
    }


def get_album_tracks_for_source(source: str, album_id: str):
    """Get album tracks for an exact source."""
    client = metadata_registry.get_client_for_source(source)
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
    """Get album metadata for an exact source."""
    client = metadata_registry.get_client_for_source(source)
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
    """Get artist albums for an exact source."""
    client = metadata_registry.get_client_for_source(source)
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

            source_chain = list(metadata_registry.get_source_priority(preferred_source or metadata_registry.get_primary_source()))
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
                    client = metadata_registry.get_client_for_source(source)
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
                client = metadata_registry.get_client_for_source(source)
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
        client = metadata_registry.get_client_for_source(source)
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
            client = metadata_registry.get_client_for_source(source)
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
