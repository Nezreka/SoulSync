"""Wishlist payload normalization helpers."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from utils.logging_config import get_logger


logger = get_logger("wishlist.payloads")


def sanitize_track_data_for_processing(track_data):
    """
    Sanitizes track data from wishlist service to ensure consistent format.
    Preserves album dict to retain full metadata (images, id, etc.) and normalizes artist field.
    """
    if not isinstance(track_data, dict):
        logger.info(f"[Sanitize] Unexpected track data type: {type(track_data)}")
        return track_data

    sanitized = track_data.copy()

    raw_album = sanitized.get('album', '')
    if not isinstance(raw_album, (dict, str)):
        sanitized['album'] = str(raw_album)

    raw_artists = sanitized.get('artists', [])
    if isinstance(raw_artists, list):
        processed_artists = []
        for artist in raw_artists:
            if isinstance(artist, str):
                processed_artists.append(artist)
            elif isinstance(artist, dict) and 'name' in artist:
                processed_artists.append(artist['name'])
            else:
                processed_artists.append(str(artist))
        sanitized['artists'] = processed_artists
    else:
        logger.info(f"[Sanitize] Unexpected artists format: {type(raw_artists)}")
        sanitized['artists'] = [str(raw_artists)] if raw_artists else []

    return sanitized


def get_track_artist_name(track_info):
    """Extract artist name from track info, handling different data formats."""
    if not track_info:
        return "Unknown Artist"

    artists = track_info.get('artists', [])
    if artists and len(artists) > 0:
        first_artist = artists[0]
        if isinstance(first_artist, dict) and 'name' in first_artist:
            return first_artist['name']
        if isinstance(first_artist, str):
            return first_artist

    artist = track_info.get('artist')
    if artist:
        return artist

    return "Unknown Artist"


def ensure_wishlist_track_format(track_info):
    """
    Ensure track_info has a consistent wishlist track structure.

    This keeps the legacy Spotify-shaped fields because the download pipeline
    still expects them, but the helper itself is provider-agnostic.
    """
    if not track_info:
        return {}

    if isinstance(track_info.get('artists'), list) and len(track_info.get('artists', [])) > 0:
        first_artist = track_info['artists'][0]
        if isinstance(first_artist, dict) and 'name' in first_artist:
            return track_info

    artists_list = []
    artists = track_info.get('artists', [])
    if artists:
        if isinstance(artists, list):
            for artist in artists:
                if isinstance(artist, dict) and 'name' in artist:
                    artists_list.append({'name': artist['name']})
                elif isinstance(artist, str):
                    artists_list.append({'name': artist})
                else:
                    artists_list.append({'name': str(artist)})
        else:
            artists_list.append({'name': str(artists)})
    else:
        artist = track_info.get('artist')
        if artist:
            artists_list.append({'name': str(artist)})
        else:
            artists_list.append({'name': 'Unknown Artist'})

    album_data = track_info.get('album', {})
    if isinstance(album_data, dict):
        album = dict(album_data)
        album.setdefault('name', 'Unknown Album')
    else:
        album = {
            'name': str(album_data) if album_data else track_info.get('name', 'Unknown Album'),
            'album_type': 'single',
            'total_tracks': 1,
            'release_date': '',
        }
    album.setdefault('images', [])
    album.setdefault('album_type', 'album')
    album.setdefault('total_tracks', 0)

    return {
        'id': track_info.get('id', f"webui_{hash(str(track_info))}"),
        'name': track_info.get('name', 'Unknown Track'),
        'artists': artists_list,
        'album': album,
        'duration_ms': track_info.get('duration_ms', 0),
        'track_number': track_info.get('track_number', 1),
        'disc_number': track_info.get('disc_number', 1),
        'preview_url': track_info.get('preview_url'),
        'external_urls': track_info.get('external_urls', {}),
        'popularity': track_info.get('popularity', 0),
        'source': track_info.get('source', 'webui_modal'),
    }


def ensure_spotify_track_format(track_info):
    """Backward-compatible wrapper for `ensure_wishlist_track_format`."""
    return ensure_wishlist_track_format(track_info)


def build_cancelled_task_wishlist_payload(task, profile_id: int = 1):
    """Build the wishlist payload for a cancelled download task."""
    if not task:
        return {}

    track_info = task.get('track_info', {})
    artists_data = track_info.get('artists', [])
    formatted_artists = []

    for artist in artists_data:
        if isinstance(artist, str):
            formatted_artists.append({'name': artist})
        elif isinstance(artist, dict):
            if 'name' in artist and isinstance(artist['name'], str):
                formatted_artists.append(artist)
            elif 'name' in artist and isinstance(artist['name'], dict) and 'name' in artist['name']:
                formatted_artists.append({'name': artist['name']['name']})
            else:
                formatted_artists.append({'name': str(artist)})
        else:
            formatted_artists.append({'name': str(artist)})

    album_raw = track_info.get('album', {})
    if isinstance(album_raw, dict):
        album_data = dict(album_raw)
        album_data.setdefault('name', 'Unknown Album')
        album_data.setdefault('album_type', track_info.get('album_type', 'album'))
        if 'images' not in album_data and track_info.get('album_image_url'):
            album_data['images'] = [{'url': track_info.get('album_image_url')}]
    else:
        album_data = {
            'name': str(album_raw) if album_raw else 'Unknown Album',
            'album_type': track_info.get('album_type', 'album'),
        }
        if track_info.get('album_image_url'):
            album_data['images'] = [{'url': track_info.get('album_image_url')}]

    track_data = {
        'id': track_info.get('id'),
        'name': track_info.get('name'),
        'artists': formatted_artists,
        'album': album_data,
        'duration_ms': track_info.get('duration_ms'),
    }

    source_context = {
        'playlist_name': task.get('playlist_name', 'Unknown Playlist'),
        'playlist_id': task.get('playlist_id'),
        'added_from': 'modal_cancellation_v2',
    }

    return {
        'spotify_track_data': track_data,
        'track_data': track_data,
        'failure_reason': 'Download cancelled by user (v2)',
        'source_type': 'playlist',
        'source_context': source_context,
        'profile_id': profile_id,
    }


def build_failed_track_wishlist_context(
    track_info,
    *,
    track_index: int = 0,
    retry_count: int = 0,
    failure_reason: str = 'Download failed',
    candidates=None,
):
    """Build the track-info payload used when queue tasks get added back to wishlist."""
    track_info = track_info or {}
    return {
        'download_index': track_index,
        'table_index': track_index,
        'track_name': track_info.get('name', 'Unknown Track'),
        'artist_name': get_track_artist_name(track_info),
        'retry_count': retry_count,
        'spotify_track': ensure_wishlist_track_format(track_info),
        'track_data': ensure_wishlist_track_format(track_info),
        'failure_reason': failure_reason,
        'candidates': list(candidates or []),
    }


def track_object_to_dict(track_object) -> Dict[str, Any]:
    """Convert a track object or TrackResult object to a dictionary."""
    try:
        logger.debug(
            "Converting track object to dict: type=%s has_title=%s has_artist=%s has_id=%s",
            type(track_object),
            hasattr(track_object, "title"),
            hasattr(track_object, "artist"),
            hasattr(track_object, "id"),
        )

        if hasattr(track_object, "title") and hasattr(track_object, "artist") and not hasattr(track_object, "id"):
            logger.debug("Detected TrackResult object, converting")
            album_name = getattr(track_object, "album", "") or getattr(track_object, "title", "Unknown Album")
            result = {
                "id": f"trackresult_{hash(f'{track_object.artist}_{track_object.title}')}",
                "name": getattr(track_object, "title", "Unknown Track"),
                "artists": [{"name": getattr(track_object, "artist", "Unknown Artist")}],
                "album": {"name": album_name, "images": [], "album_type": "single", "total_tracks": 1},
                "duration_ms": 0,
                "preview_url": None,
                "external_urls": {},
                "popularity": 0,
                "source": "trackresult",
            }
            logger.debug(
                "TrackResult converted successfully: name=%s artist=%s",
                result["name"],
                result["artists"][0]["name"],
            )
            return result

        logger.debug("Processing as track object")

        artists_list = []
        raw_artists = getattr(track_object, "artists", [])
        logger.debug("Raw artists: %r (type=%s)", raw_artists, type(raw_artists))

        for artist in raw_artists:
            logger.debug("Processing artist: %r (type=%s)", artist, type(artist))
            if hasattr(artist, "name"):
                artists_list.append({"name": artist.name})
            elif isinstance(artist, str):
                artists_list.append({"name": artist})
            else:
                artists_list.append({"name": str(artist)})

        album_name = "Unknown Album"
        if hasattr(track_object, "album") and track_object.album:
            if hasattr(track_object.album, "name"):
                album_name = track_object.album.name
            else:
                album_name = str(track_object.album)

        result = {
            "id": getattr(track_object, "id", None),
            "name": getattr(track_object, "name", "Unknown Track"),
            "artists": artists_list,
            "album": {"name": album_name},
            "duration_ms": getattr(track_object, "duration_ms", 0),
            "preview_url": getattr(track_object, "preview_url", None),
            "external_urls": getattr(track_object, "external_urls", {}),
            "popularity": getattr(track_object, "popularity", 0),
            "track_number": getattr(track_object, "track_number", 1),
            "disc_number": getattr(track_object, "disc_number", 1),
        }

        logger.debug(
            "Track converted: name=%s artists=%s",
            result["name"],
            [a["name"] for a in result["artists"]],
        )

        try:
            json.dumps(result)
            logger.debug("Conversion result is JSON serializable")
        except Exception as json_error:
            logger.error("Conversion result is NOT JSON serializable: %s", json_error)
            logger.error("Conversion result content: %r", result)
            return {
                "id": f"fallback_{hash(str(track_object))}",
                "name": str(getattr(track_object, "name", "Unknown Track")),
                "artists": [{"name": "Unknown Artist"}],
                "album": {"name": "Unknown Album"},
                "duration_ms": 0,
                "preview_url": None,
                "external_urls": {},
                "popularity": 0,
                "source": "fallback",
            }

        return result
    except Exception as e:
        logger.error(f"Error converting track object to dict: {e}")
        logger.error(f"Object type: {type(track_object)}")
        logger.error(f"Object attributes: {dir(track_object)}")
        return {}


def spotify_track_object_to_dict(spotify_track) -> Dict[str, Any]:
    """Backward-compatible wrapper for `track_object_to_dict`."""
    return track_object_to_dict(spotify_track)


def extract_wishlist_track_from_modal_info(track_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract a track payload from modal track_info structure.
    """
    try:
        if not isinstance(track_info, dict):
            return None

        for key in ("track_data", "track", "metadata_track", "spotify_track"):
            if key not in track_info or not track_info[key]:
                continue
            extracted = track_info[key]
            if hasattr(extracted, "__dict__"):
                return track_object_to_dict(extracted)
            if isinstance(extracted, dict):
                return extracted

        if track_info.get("name") or track_info.get("title"):
            if track_info.get("artists") or track_info.get("artist"):
                return ensure_wishlist_track_format(track_info)

        if "slskd_result" in track_info and track_info["slskd_result"]:
            slskd_result = track_info["slskd_result"]
            if hasattr(slskd_result, "artist") and hasattr(slskd_result, "title"):
                album_name = getattr(slskd_result, "album", "") or getattr(slskd_result, "title", "Unknown Album")
                return {
                    "id": f"reconstructed_{hash(f'{slskd_result.artist}_{slskd_result.title}')}",
                    "name": getattr(slskd_result, "title", "Unknown Track"),
                    "artists": [{"name": getattr(slskd_result, "artist", "Unknown Artist")}],
                    "album": {"name": album_name, "images": [], "album_type": "single", "total_tracks": 1},
                    "duration_ms": 0,
                    "reconstructed": True,
                }

        logger.warning("Could not find track data in modal info, attempting reconstruction")
        return None

    except Exception as e:
        logger.error(f"Error extracting track from modal info: {e}")
        return None


def extract_spotify_track_from_modal_info(track_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Backward-compatible wrapper for `extract_wishlist_track_from_modal_info`."""
    return extract_wishlist_track_from_modal_info(track_info)


__all__ = [
    "sanitize_track_data_for_processing",
    "get_track_artist_name",
    "ensure_wishlist_track_format",
    "ensure_spotify_track_format",
    "build_cancelled_task_wishlist_payload",
    "build_failed_track_wishlist_context",
    "track_object_to_dict",
    "spotify_track_object_to_dict",
    "extract_wishlist_track_from_modal_info",
    "extract_spotify_track_from_modal_info",
]
