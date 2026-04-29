"""Completion helpers for metadata lookups."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.metadata import registry as metadata_registry
from core.metadata.album_tracks import get_album_tracks_for_source
from core.metadata.discography import _extract_release_artist_name
from core.metadata.lookup import MetadataLookupOptions
from utils.logging_config import get_logger

logger = get_logger("metadata.completion")

__all__ = [
    "check_album_completion",
    "check_artist_discography_completion",
    "check_single_completion",
    "iter_artist_discography_completion_events",
]


def _extract_track_items(api_tracks: Any) -> List[Dict[str, Any]]:
    if not api_tracks:
        return []
    if isinstance(api_tracks, dict):
        return api_tracks.get('items') or []
    if isinstance(api_tracks, list):
        return api_tracks
    return []


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


def _get_completion_source_chain(source_override: Optional[str] = None) -> List[str]:
    primary_source = metadata_registry.get_primary_source()
    source_chain = list(metadata_registry.get_source_priority(primary_source))

    override = (source_override or '').strip().lower()
    if override:
        source_chain = [override] + [source for source in source_chain if source != override]

    return source_chain


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
    candidate_albums: Optional[List[Any]] = None,
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
        try:
            from config.settings import config_manager

            active_server = config_manager.get_active_media_server()
            db_album, confidence, owned_tracks, expected_tracks, is_complete, formats = db.check_album_exists_with_completeness(
                title=album_name,
                artist=artist_name,
                expected_track_count=total_tracks if total_tracks > 0 else None,
                confidence_threshold=0.7,
                server_source=active_server,
                candidate_albums=candidate_albums,
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
                "formats": [],
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
            "formats": formats,
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
            "formats": [],
        }


def check_single_completion(
    db,
    single_data: Dict[str, Any],
    artist_name: str,
    source_override: Optional[str] = None,
    source_chain: Optional[List[str]] = None,
    candidate_albums: Optional[List[Any]] = None,
    candidate_tracks: Optional[List[Any]] = None,
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
                    server_source=active_server,
                    candidate_albums=candidate_albums,
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
                "formats": formats,
            }
        else:
            try:
                from config.settings import config_manager

                active_server = config_manager.get_active_media_server()
                db_track, confidence = db.check_track_exists(
                    title=single_name,
                    artist=artist_name,
                    confidence_threshold=0.7,
                    server_source=active_server,
                    candidate_tracks=candidate_tracks,
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
                "formats": formats,
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
            "formats": [],
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

    import time as _time_metadata

    candidate_albums = None
    candidate_tracks = None
    try:
        from config.settings import config_manager as _cm_metadata

        _active_server = _cm_metadata.get_active_media_server()
        _t0 = _time_metadata.perf_counter()
        candidate_albums = db.get_candidate_albums_for_artist(resolved_artist_name, server_source=_active_server)
        _t1 = _time_metadata.perf_counter()
        print(f"[artist-completion-stream] Pre-fetched {len(candidate_albums) if candidate_albums is not None else 0} library albums for '{resolved_artist_name}' in {(_t1 - _t0) * 1000:.0f}ms")
        if candidate_albums:
            _t2 = _time_metadata.perf_counter()
            candidate_tracks = db.get_candidate_tracks_for_albums([a.id for a in candidate_albums])
            _t3 = _time_metadata.perf_counter()
            print(f"[artist-completion-stream] Pre-fetched {len(candidate_tracks) if candidate_tracks is not None else 0} library tracks in {(_t3 - _t2) * 1000:.0f}ms")
    except Exception as _pre_err:
        print(f"[artist-completion-stream] Failed to pre-fetch candidates for '{resolved_artist_name}': {_pre_err}")
        candidate_albums = None
        candidate_tracks = None

    yield {
        'type': 'start',
        'total_items': total_items,
        'artist_name': resolved_artist_name,
    }

    _loop_start = _time_metadata.perf_counter()
    for album in albums:
        try:
            completion_data = check_album_completion(
                db,
                album,
                resolved_artist_name,
                source_override=source_override,
                source_chain=source_chain,
                candidate_albums=candidate_albums,
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
                candidate_albums=candidate_albums,
                candidate_tracks=candidate_tracks,
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

    _loop_elapsed = _time_metadata.perf_counter() - _loop_start
    print(f"[artist-completion-stream] Processed {total_items} items for '{resolved_artist_name}' in {_loop_elapsed * 1000:.0f}ms")

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
