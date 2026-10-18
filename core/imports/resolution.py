"""Single-track import lookup and context-building helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.metadata import registry as metadata_registry
from core.metadata.types import Album
from utils.logging_config import get_logger


# Per-source typed converter dispatch — same registry pattern as
# ``core/metadata/album_tracks.py``. When the embedded ``album`` blob in
# a track response is dispatched through the typed converter for that
# provider, the resulting Album fields drive the album_payload below.
# Falls through to the legacy duck-typed path when source is empty,
# unknown, or the converter raises.
_TYPED_ALBUM_CONVERTERS: Dict[str, Callable[[Dict[str, Any]], Album]] = {
    'spotify': Album.from_spotify_dict,
    'itunes': Album.from_itunes_dict,
    'deezer': Album.from_deezer_dict,
    'discogs': Album.from_discogs_dict,
    'musicbrainz': Album.from_musicbrainz_dict,
    'hydrabase': Album.from_hydrabase_dict,
    'qobuz': Album.from_qobuz_dict,
}


logger = get_logger("imports.resolution")


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


def _get_source_chain_for_lookup(
    source_override: Optional[str] = None,
    allow_fallback: bool = True,
) -> List[str]:
    primary_source = metadata_registry.get_primary_source()
    source_chain = list(metadata_registry.get_source_priority(primary_source))
    override = (source_override or '').strip().lower()

    if override:
        source_chain = [override] + [source for source in source_chain if source != override]

    if not allow_fallback:
        source_chain = source_chain[:1]

    return source_chain


def _build_track_search_query(source: str, title: str, artist: str) -> str:
    base_query = " ".join(part for part in (title, artist) if part).strip()
    if source == 'deezer' and title:
        if artist:
            return f'artist:"{artist}" track:"{title}"'
        return f'track:"{title}"'
    return base_query or title or artist


def _pick_best_track_match(search_results: List[Any], title: str, artist: str = '') -> Optional[Any]:
    if not search_results:
        return None

    target_title = str(title or '').strip().lower()
    target_artist = str(artist or '').strip().lower()

    for candidate in search_results:
        candidate_title = str(_extract_lookup_value(candidate, 'name', 'title', 'track_name', default='') or '').strip().lower()
        if candidate_title != target_title:
            continue

        if not target_artist:
            return candidate

        candidate_artists = _normalize_context_artists(_extract_lookup_value(candidate, 'artists', default=[]))
        candidate_artist_name = candidate_artists[0]['name'].strip().lower() if candidate_artists else ''
        if candidate_artist_name == target_artist:
            return candidate

    return search_results[0]


def search_tracks_for_source(source: str, client: Any, query: str, limit: int = 1) -> List[Any]:
    if not client or not hasattr(client, 'search_tracks'):
        return []

    try:
        kwargs = {'limit': limit}
        if source == 'spotify':
            kwargs['allow_fallback'] = False
        return client.search_tracks(query, **kwargs) or []
    except Exception as exc:
        logger.debug("Could not search %s for %s: %s", source, query, exc)
        return []


def _build_single_import_context_payload(
    track_data: Any,
    source: Optional[str],
    source_priority: List[str],
    requested_title: str = '',
    requested_artist: str = '',
) -> Dict[str, Any]:
    album_data = _extract_lookup_value(track_data, 'album', default=None)

    track_id = str(_extract_lookup_value(track_data, 'id', 'track_id', 'trackId', default='') or '')
    track_name = _extract_lookup_value(track_data, 'name', 'title', 'trackName', default='') or requested_title or 'Unknown Track'
    track_artists = _normalize_context_artists(_extract_lookup_value(track_data, 'artists', default=[]))
    if not track_artists and requested_artist:
        track_artists = [{'name': requested_artist}]

    primary_track_artist = track_artists[0] if track_artists else {}
    primary_artist_name = primary_track_artist.get('name') or requested_artist or 'Unknown Artist'
    primary_artist_id = str(primary_track_artist.get('id', '') or _extract_lookup_value(track_data, 'artist_id', 'artistId', default='') or '')

    album_name = _extract_lookup_value(track_data, 'album_name', 'collectionName', default='') or ''
    album_id = str(_extract_lookup_value(track_data, 'album_id', 'collectionId', 'albumId', default='') or '')
    release_date = str(_extract_lookup_value(track_data, 'release_date', default='') or '')
    album_type = str(_extract_lookup_value(track_data, 'album_type', default='album') or 'album')
    total_tracks = int(_extract_lookup_value(track_data, 'total_tracks', 'track_count', default=0) or 0)
    album_images: List[Dict[str, Any]] = []
    album_image_url = str(_extract_lookup_value(track_data, 'image_url', 'thumb_url', default='') or '')
    album_artists = _normalize_context_artists(_extract_lookup_value(track_data, 'album_artists', 'artists', default=[]))

    if isinstance(album_data, dict):
        # Typed dispatch: when the source is a known provider, route the
        # embedded album blob through Album.from_<source>_dict() and read
        # canonical fields off the typed result. Falls back to the
        # legacy duck-typed extraction below on unknown/missing source
        # OR if the converter raises (so a converter bug can't break
        # import context resolution).
        typed_album: Optional[Album] = None
        source_key = (source or '').strip().lower()
        if source_key:
            converter = _TYPED_ALBUM_CONVERTERS.get(source_key)
            if converter is not None:
                try:
                    typed_album = converter(album_data)
                except Exception as exc:
                    logger.debug(
                        "Typed album converter failed for source %s in import "
                        "context build, falling back to legacy: %s", source, exc,
                    )
                    typed_album = None

        if typed_album is not None:
            if typed_album.name:
                album_name = typed_album.name
            if typed_album.id:
                album_id = typed_album.id
            if typed_album.release_date:
                release_date = typed_album.release_date
            elif album_data.get('release_date'):
                # the itunes and discogs clients hand back an already
                # normalized album, which their raw-shape converters can't
                # read a date from
                release_date = str(album_data['release_date'])
            if typed_album.album_type:
                album_type = typed_album.album_type
            if typed_album.total_tracks:
                total_tracks = typed_album.total_tracks
            # Preserve raw images list verbatim (legacy behavior — some
            # downstream consumers iterate the full multi-resolution
            # array to pick a different size).
            raw_images = album_data.get('images')
            if isinstance(raw_images, list) and raw_images:
                album_images = raw_images
            if not album_image_url:
                album_image_url = typed_album.image_url or ''
                if not album_image_url and album_images:
                    album_image_url = str(_extract_lookup_value(album_images[0], 'url', default='') or '')
            album_artists = _normalize_context_artists(
                [{'name': name} for name in typed_album.artists]
            )
        else:
            album_name = _extract_lookup_value(album_data, 'name', 'title', 'collectionName', default=album_name) or album_name
            album_id = str(_extract_lookup_value(album_data, 'id', 'album_id', 'collectionId', default=album_id) or album_id)
            release_date = str(_extract_lookup_value(album_data, 'release_date', default=release_date) or release_date)
            album_type = str(_extract_lookup_value(album_data, 'album_type', default=album_type) or album_type)
            total_tracks = int(_extract_lookup_value(album_data, 'total_tracks', 'track_count', 'nb_tracks', default=total_tracks) or total_tracks)
            album_images = _extract_lookup_value(album_data, 'images', default=[]) or []
            if not album_image_url:
                album_image_url = str(_extract_lookup_value(album_data, 'image_url', 'thumb_url', default='') or '')
                if not album_image_url and album_images:
                    album_image_url = str(_extract_lookup_value(album_images[0], 'url', default='') or '')
            album_artists = _normalize_context_artists(_extract_lookup_value(album_data, 'artists', default=[]))
    elif album_data:
        album_name = album_name or str(album_data)

    if not album_artists and primary_artist_name:
        album_artists = [{'name': primary_artist_name}]

    if not album_image_url and album_images:
        album_image_url = str(_extract_lookup_value(album_images[0], 'url', default='') or '')

    track_info = {
        'id': track_id,
        'name': track_name,
        'track_number': int(_extract_lookup_value(track_data, 'track_number', 'trackNumber', default=1) or 1),
        'disc_number': int(_extract_lookup_value(track_data, 'disc_number', 'discNumber', default=1) or 1),
        'duration_ms': int(_extract_lookup_value(track_data, 'duration_ms', 'duration', 'trackTimeMillis', default=0) or 0),
        'artists': track_artists or [{'name': primary_artist_name}],
        'uri': str(_extract_lookup_value(track_data, 'uri', default='') or ''),
        'album': album_name,
        'album_id': album_id,
        'album_type': album_type,
        'release_date': release_date,
        # #1536: recording disambiguation ("acoustic", "live") for the
        # single-import filename/tag layers.
        'disambiguation': str(_extract_lookup_value(track_data, 'disambiguation', default='') or '').strip(),
        '_source': source or '',
    }

    album_payload = {
        'id': album_id,
        'name': album_name,
        'release_date': release_date,
        'total_tracks': total_tracks or 1,
        'album_type': album_type,
        'image_url': album_image_url,
        'images': album_images,
        'artists': album_artists,
        '_source': source or '',
    }

    artist_payload = {
        'id': primary_artist_id,
        'name': primary_artist_name,
        'genres': [],
        '_source': source or '',
    }

    original_search = {
        'title': track_name,
        'artist': primary_artist_name,
        'album': album_name,
        'track_number': track_info['track_number'],
        'disc_number': track_info['disc_number'],
        'clean_title': track_name,
        'clean_album': album_name,
        'clean_artist': primary_artist_name,
        'artists': track_info['artists'],
        'duration_ms': track_info['duration_ms'],
        'id': track_id,
        '_source': source or '',
    }

    return {
        'success': bool(track_id or track_name != requested_title or album_name),
        'source': source,
        'source_priority': source_priority,
        'context': {
            'artist': artist_payload,
            'album': album_payload,
            'track_info': track_info,
            'original_search_result': original_search,
            'is_album_download': False,
            'has_clean_metadata': bool(track_id),
            'has_full_metadata': bool(track_id),
            'source': source,
            'source_priority': source_priority,
        },
    }


def _coerce_tag_int(value: Any, default: int) -> int:
    """Tag fields arrive as whatever the file (or the request JSON) carried —
    strings, floats, None. Anything unusable becomes the default instead of
    raising out of the import (int(float('inf')) is an OverflowError, not a
    ValueError, so it needs naming). Tag conventions are honoured: a track
    position written "3/12" means track 3 (the staging reader splits the
    same way), a float-string duration keeps its whole part, and zero or
    negative positions/distances are not real, so they take the default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, str):
        value = value.strip().split('/', 1)[0].strip()
        if not value:
            return default
    result: Optional[int] = None
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        try:
            result = int(float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            return default
    # Zero/negative are not real positions or durations, and anything past
    # 2**62 is poison, not data — it would overflow the SQLite INTEGER
    # columns this context eventually lands in.
    if result is None or result <= 0 or result > 2**62:
        return default
    return result


def _build_single_import_fallback_context(
    requested_title: str,
    requested_artist: str,
    source_priority: List[str],
    *,
    album: str = '',
    track_number: Any = 1,
    disc_number: Any = 1,
    duration_ms: Any = 0,
    manual_metadata: bool = False,
) -> Dict[str, Any]:
    artist_name = (
        requested_artist.strip()
        if isinstance(requested_artist, str) and requested_artist.strip()
        else 'Unknown Artist'
    )
    title = (
        requested_title.strip()
        if isinstance(requested_title, str) and requested_title.strip()
        else 'Unknown Track'
    )
    # A non-string album (list/dict from raw request JSON) is not an album
    # name; stringifying it would file the track under "['x']".
    album_name = album.strip() if isinstance(album, str) else ''
    track_no = _coerce_tag_int(track_number, 1)
    disc_no = _coerce_tag_int(disc_number, 1)
    duration = _coerce_tag_int(duration_ms, 0)
    context: Dict[str, Any] = {
        'artist': {
            'id': '',
            'name': artist_name,
            'genres': [],
            '_source': '',
        },
        'album': {
            'id': '',
            'name': album_name,
            'release_date': '',
            'total_tracks': 1,
            'album_type': 'album',
            'image_url': '',
            'images': [],
            'artists': [{'name': artist_name}] if album_name else [],
            '_source': '',
        },
        'track_info': {
            'id': '',
            'name': title,
            'track_number': track_no,
            'disc_number': disc_no,
            'duration_ms': duration,
            'artists': [{'name': artist_name}],
            'uri': '',
            'album': album_name,
            'album_id': '',
            'album_type': 'album',
            'release_date': '',
            '_source': '',
        },
        'original_search_result': {
            'title': title,
            'artist': artist_name,
            'album': album_name,
            'track_number': track_no,
            'disc_number': disc_no,
            'clean_title': title,
            'clean_album': album_name,
            'clean_artist': artist_name,
            'artists': [{'name': artist_name}],
            'duration_ms': duration,
            'id': '',
            '_source': '',
        },
        'is_album_download': False,
        'has_clean_metadata': False,
        'has_full_metadata': False,
        'source': None,
        'source_priority': source_priority,
    }
    if manual_metadata:
        # The user explicitly chose the file's own tags over every catalogue
        # match. That is hand-tagged metadata (core/metadata/manual.py):
        # automatic correction — source-id lookups, the AcoustID gate, the
        # preferred-art lookup — stands down, so nothing replaces the file's
        # own artist and title with a studio release's, and no fetched cover
        # is written over the file's embedded art.
        # Deliberate consequence, same as a hand-typed download: the import
        # pipeline records a manual lock for the file, which also locks its
        # album row against enrichment until the user unlocks the album
        # (clear_manual_lock, on the artist page). The reporter's tested
        # workaround for #1772 used exactly this flag.
        context['_manual_metadata'] = True
        context['track_info']['_manual_metadata'] = True
    return {
        'success': False,
        'source': None,
        'source_priority': source_priority,
        'context': context,
    }


def get_single_track_import_context(
    title: str,
    artist: str = '',
    override_id: Optional[str] = None,
    override_source: str = 'spotify',
    source_override: Optional[str] = None,
    from_own_tags: bool = False,
    album: str = '',
    track_number: Any = 1,
    disc_number: Any = 1,
    duration_ms: Any = 0,
) -> Dict[str, Any]:
    """Build an import context for singles using source-priority metadata lookup.

    ``from_own_tags`` is the import UI's "No track picked" choice: the user
    declined every catalogue match, so NO source is searched at all — the
    context is built from the file's own tags alone. Without this, a search
    that found no exact match fell through to ``_pick_best_track_match``,
    which returns the first (unrelated) search result (#1772).
    """
    source_priority = _get_source_chain_for_lookup(source_override=source_override, allow_fallback=True)
    # Raw request JSON can carry non-string title/artist; treat those as
    # absent instead of raising AttributeError on .strip().
    title = title.strip() if isinstance(title, str) else ''
    artist = artist.strip() if isinstance(artist, str) else ''

    if from_own_tags and not override_id:
        return _build_single_import_fallback_context(
            title,
            artist,
            source_priority,
            album=album,
            track_number=track_number,
            disc_number=disc_number,
            duration_ms=duration_ms,
            manual_metadata=True,
        )

    if override_id:
        chosen_source = (override_source or 'spotify').strip().lower() or 'spotify'
        client = metadata_registry.get_client_for_source(chosen_source)
        if client and hasattr(client, 'get_track_details'):
            try:
                track_data = client.get_track_details(str(override_id))
                if track_data:
                    payload = _build_single_import_context_payload(
                        track_data,
                        chosen_source,
                        source_priority,
                        requested_title=title,
                        requested_artist=artist,
                    )
                    if payload['context']['artist'].get('id') and hasattr(client, 'get_artist'):
                        try:
                            artist_details = client.get_artist(payload['context']['artist']['id'])
                            if artist_details:
                                payload['context']['artist']['genres'] = _extract_lookup_value(
                                    artist_details,
                                    'genres',
                                    default=[],
                                ) or []
                        except Exception as e:
                            logger.debug("override artist genres: %s", e)
                    return payload
            except Exception as exc:
                logger.debug("Override track lookup failed on %s for %s: %s", chosen_source, override_id, exc)

    if from_own_tags:
        # The override was asked for but did not resolve. The flag is an
        # absolute "never search" promise, so a failed override lands on the
        # own-tags context too instead of falling through to the search loop.
        return _build_single_import_fallback_context(
            title,
            artist,
            source_priority,
            album=album,
            track_number=track_number,
            disc_number=disc_number,
            duration_ms=duration_ms,
            manual_metadata=True,
        )

    for source in source_priority:
        client = metadata_registry.get_client_for_source(source)
        if not client:
            continue

        search_query = _build_track_search_query(source, title, artist)
        if not search_query:
            continue

        search_results = search_tracks_for_source(source, client, search_query, limit=5)
        if not search_results and search_query != title:
            search_results = search_tracks_for_source(source, client, title, limit=5)
        if not search_results and artist and search_query != artist:
            search_results = search_tracks_for_source(source, client, artist, limit=5)

        if not search_results:
            continue

        best_match = _pick_best_track_match(search_results, title or search_query, artist)
        if not best_match:
            continue

        resolved_track_id = str(_extract_lookup_value(best_match, 'id', 'track_id', 'trackId', default='') or '')
        resolved_data = best_match
        if resolved_track_id and hasattr(client, 'get_track_details'):
            try:
                detailed = client.get_track_details(resolved_track_id)
                if detailed:
                    resolved_data = detailed
            except Exception as exc:
                logger.debug("Track detail lookup failed on %s for %s: %s", source, resolved_track_id, exc)

        payload = _build_single_import_context_payload(
            resolved_data,
            source,
            source_priority,
            requested_title=title,
            requested_artist=artist,
        )
        if payload['context']['artist'].get('id') and hasattr(client, 'get_artist'):
            try:
                artist_details = client.get_artist(payload['context']['artist']['id'])
                if artist_details:
                    payload['context']['artist']['genres'] = _extract_lookup_value(
                        artist_details,
                        'genres',
                        default=[],
                    ) or []
            except Exception as e:
                logger.debug("artist genres lookup: %s", e)
        return payload

    # Deliberately NOT the tag fields the caller may have passed: a failed
    # automatic lookup keeps the historical fallback shape exactly. The tag
    # fields only shape the explicit from_own_tags context above (#1772).
    return _build_single_import_fallback_context(title, artist, source_priority)
