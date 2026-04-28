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

    # Create a copy to avoid modifying original data
    sanitized = track_data.copy()

    # Handle album field - preserve dict format to retain full metadata (images, id, etc.)
    # Downstream code already handles both dict and string formats defensively
    raw_album = sanitized.get('album', '')
    if not isinstance(raw_album, (dict, str)):
        sanitized['album'] = str(raw_album)

    # Handle artists field - ensure it's a list of strings
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


def ensure_spotify_track_format(track_info):
    """
    Ensure track_info has proper Spotify track structure for wishlist service.
    Converts webui track format to match sync.py's spotify_track format.
    """
    if not track_info:
        return {}

    # If it already has the proper Spotify structure, return as-is
    if isinstance(track_info.get('artists'), list) and len(track_info.get('artists', [])) > 0:
        first_artist = track_info['artists'][0]
        if isinstance(first_artist, dict) and 'name' in first_artist:
            # Already has proper Spotify format
            return track_info

    # Convert to proper Spotify format
    artists_list = []

    # Handle different artist formats from webui
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
            # Single artist as string
            artists_list.append({'name': str(artists)})
    else:
        # Fallback: try single artist field
        artist = track_info.get('artist')
        if artist:
            artists_list.append({'name': str(artist)})
        else:
            artists_list.append({'name': 'Unknown Artist'})

    # Build album object - preserve ALL fields (id, release_date, total_tracks,
    # album_type, images, etc.) so wishlist tracks retain full album context
    # for correct folder placement, multi-disc support, and classification
    album_data = track_info.get('album', {})
    if isinstance(album_data, dict):
        album = dict(album_data)  # Copy all fields
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

    # Build proper Spotify track structure
    spotify_track = {
        'id': track_info.get('id', f"webui_{hash(str(track_info))}"),
        'name': track_info.get('name', 'Unknown Track'),
        'artists': artists_list,  # Proper Spotify format
        'album': album,
        'duration_ms': track_info.get('duration_ms', 0),
        'track_number': track_info.get('track_number', 1),
        'disc_number': track_info.get('disc_number', 1),
        'preview_url': track_info.get('preview_url'),
        'external_urls': track_info.get('external_urls', {}),
        'popularity': track_info.get('popularity', 0),
        'source': 'webui_modal'  # Mark as coming from webui
    }

    return spotify_track


def build_cancelled_task_wishlist_payload(task, profile_id: int = 1):
    """Build the wishlist payload for a cancelled download task.

    This preserves the current web_server.py behavior while moving the
    data-shaping logic into the wishlist package.
    """
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

    # Build album data - preserve all fields (including artists) for correct folder placement
    album_raw = track_info.get('album', {})
    if isinstance(album_raw, dict):
        album_data = dict(album_raw)  # Copy all fields including artists
        album_data.setdefault('name', 'Unknown Album')
        album_data.setdefault('album_type', track_info.get('album_type', 'album'))
        # Add images fallback if not present
        if 'images' not in album_data and track_info.get('album_image_url'):
            album_data['images'] = [{'url': track_info.get('album_image_url')}]
    else:
        # album is a string (album name)
        album_data = {
            'name': str(album_raw) if album_raw else 'Unknown Album',
            'album_type': track_info.get('album_type', 'album')
        }
        # Add album image if available
        if track_info.get('album_image_url'):
            album_data['images'] = [{'url': track_info.get('album_image_url')}]

    spotify_track_data = {
        'id': track_info.get('id'),
        'name': track_info.get('name'),
        'artists': formatted_artists,
        'album': album_data,
        'duration_ms': track_info.get('duration_ms')
    }

    source_context = {
        'playlist_name': task.get('playlist_name', 'Unknown Playlist'),
        'playlist_id': task.get('playlist_id'),
        'added_from': 'modal_cancellation_v2',
    }

    return {
        'spotify_track_data': spotify_track_data,
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
        'spotify_track': ensure_spotify_track_format(track_info),
        'failure_reason': failure_reason,
        'candidates': list(candidates or []),
    }


def extract_spotify_track_from_modal_info(track_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract Spotify track data from modal track_info structure.

    Handles different formats from sync.py and artists.py modals.
    """
    try:
        # Try to find Spotify track data in various locations within track_info

        # Check if we have direct Spotify track reference
        if "spotify_track" in track_info and track_info["spotify_track"]:
            spotify_track = track_info["spotify_track"]

            # Convert to dictionary if it's an object
            if hasattr(spotify_track, "__dict__"):
                return spotify_track_object_to_dict(spotify_track)
            if isinstance(spotify_track, dict):
                return spotify_track

        # Check if we have slskd_result with embedded metadata
        if "slskd_result" in track_info and track_info["slskd_result"]:
            slskd_result = track_info["slskd_result"]

            # Look for Spotify metadata in the result
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

        # If no Spotify data found, try to reconstruct from available info
        logger.warning("Could not find Spotify track data in modal info, attempting reconstruction")
        return None

    except Exception as e:
        logger.error(f"Error extracting Spotify track from modal info: {e}")
        return None


def spotify_track_object_to_dict(spotify_track) -> Dict[str, Any]:
    """Convert a Spotify track object or TrackResult object to a dictionary."""
    try:
        logger.debug(
            "Converting track object to dict: type=%s has_title=%s has_artist=%s has_id=%s",
            type(spotify_track),
            hasattr(spotify_track, "title"),
            hasattr(spotify_track, "artist"),
            hasattr(spotify_track, "id"),
        )

        # Check if this is a TrackResult object (has title/artist but no id)
        if hasattr(spotify_track, "title") and hasattr(spotify_track, "artist") and not hasattr(spotify_track, "id"):
            logger.debug("Detected TrackResult object, converting")
            # Handle TrackResult objects - these don't have Spotify IDs
            album_name = getattr(spotify_track, "album", "") or getattr(spotify_track, "title", "Unknown Album")
            result = {
                "id": f"trackresult_{hash(f'{spotify_track.artist}_{spotify_track.title}')}",
                "name": getattr(spotify_track, "title", "Unknown Track"),
                "artists": [{"name": getattr(spotify_track, "artist", "Unknown Artist")}],
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

        # Handle regular Spotify Track objects
        logger.debug("Processing as Spotify Track object")

        # Handle artists list carefully to avoid TrackResult serialization issues
        artists_list = []
        raw_artists = getattr(spotify_track, "artists", [])
        logger.debug("Raw artists: %r (type=%s)", raw_artists, type(raw_artists))

        for artist in raw_artists:
            logger.debug("Processing artist: %r (type=%s)", artist, type(artist))
            if hasattr(artist, "name"):
                artists_list.append({"name": artist.name})
            elif isinstance(artist, str):
                artists_list.append({"name": artist})
            else:
                # Convert any complex objects to string to avoid serialization issues
                artists_list.append({"name": str(artist)})

        # Handle album safely
        album_name = "Unknown Album"
        if hasattr(spotify_track, "album") and spotify_track.album:
            if hasattr(spotify_track.album, "name"):
                album_name = spotify_track.album.name
            else:
                album_name = str(spotify_track.album)

        result = {
            "id": getattr(spotify_track, "id", None),
            "name": getattr(spotify_track, "name", "Unknown Track"),
            "artists": artists_list,
            "album": {"name": album_name},
            "duration_ms": getattr(spotify_track, "duration_ms", 0),
            "preview_url": getattr(spotify_track, "preview_url", None),
            "external_urls": getattr(spotify_track, "external_urls", {}),
            "popularity": getattr(spotify_track, "popularity", 0),
            "track_number": getattr(spotify_track, "track_number", 1),
            "disc_number": getattr(spotify_track, "disc_number", 1),
        }

        logger.debug(
            "Spotify Track converted: name=%s artists=%s",
            result["name"],
            [a["name"] for a in result["artists"]],
        )

        # Test JSON serialization before returning to catch any remaining issues
        try:
            json.dumps(result)
            logger.debug("Conversion result is JSON serializable")
        except Exception as json_error:
            logger.error("Conversion result is NOT JSON serializable: %s", json_error)
            logger.error("Conversion result content: %r", result)
            # Return a safe fallback
            return {
                "id": f"fallback_{hash(str(spotify_track))}",
                "name": str(getattr(spotify_track, "name", "Unknown Track")),
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
        logger.error(f"Object type: {type(spotify_track)}")
        logger.error(f"Object attributes: {dir(spotify_track)}")
        return {}


__all__ = [
    "sanitize_track_data_for_processing",
    "get_track_artist_name",
    "ensure_spotify_track_format",
    "build_cancelled_task_wishlist_payload",
    "build_failed_track_wishlist_context",
    "extract_spotify_track_from_modal_info",
    "spotify_track_object_to_dict",
]
