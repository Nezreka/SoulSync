"""Wishlist resolution and removal helpers."""

from __future__ import annotations

from typing import Any, Dict, List

from core.imports.context import (
    get_import_original_search,
    get_import_search_result,
    get_import_source,
    get_import_source_ids,
    get_import_track_info,
)
from core.wishlist.service import get_wishlist_service
from database.music_database import get_database
from utils.logging_config import get_logger


logger = get_logger("imports.side_effects")


def _primary_track_artist_name(track_info: Dict[str, Any]) -> str:
    artists = (track_info or {}).get("artists", [])
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            return str(first.get("name", "") or "")
        return str(first or "")
    if isinstance(artists, str):
        return artists
    return str((track_info or {}).get("artist", "") or "")


def _all_profile_wishlist_tracks(wishlist_service, database=None) -> List[Dict[str, Any]]:
    database = database or get_database()
    all_profiles = database.get_all_profiles()
    wishlist_tracks: List[Dict[str, Any]] = []
    for profile in all_profiles:
        wishlist_tracks.extend(wishlist_service.get_wishlist_tracks_for_download(profile_id=profile["id"]))
    return wishlist_tracks


def check_and_remove_from_wishlist(context: Dict[str, Any], wishlist_service=None, database=None) -> None:
    """Check whether a successful download should be removed from the wishlist."""
    try:
        wishlist_service = wishlist_service or get_wishlist_service()
        source = get_import_source(context)
        source_ids = get_import_source_ids(context)
        source_label = {
            "spotify": "Spotify",
            "itunes": "iTunes",
            "deezer": "Deezer",
            "discogs": "Discogs",
            "hydrabase": "Hydrabase",
        }.get(source, "Source")
        track_info = get_import_track_info(context) or get_import_search_result(context)
        search_result = get_import_original_search(context) or get_import_search_result(context)
        track_id = None

        if source == "spotify":
            track_id = source_ids.get("track_id") or None
            if track_id:
                logger.info("[Wishlist] Found %s track ID from source_ids: %s", source_label, track_id)
        elif "wishlist_id" in track_info:
            wishlist_id = track_info["wishlist_id"]
            logger.info("[Wishlist] Found wishlist_id in context: %s", wishlist_id)
            wishlist_tracks = _all_profile_wishlist_tracks(wishlist_service, database=database)
            for wishlist_track in wishlist_tracks:
                if wishlist_track.get("wishlist_id") == wishlist_id:
                    track_id = wishlist_track.get("spotify_track_id") or wishlist_track.get("id")
                    logger.info("[Wishlist] Found track ID from wishlist entry: %s", track_id)
                    break

        if not track_id:
            track_name = track_info.get("name") or search_result.get("title", "")
            artist_name = _primary_track_artist_name(track_info) or _primary_track_artist_name(search_result)

            if track_name and artist_name:
                logger.warning(
                    "[Wishlist] No track ID found, checking for fuzzy match: '%s' by '%s'",
                    track_name,
                    artist_name,
                )

                wishlist_tracks = _all_profile_wishlist_tracks(wishlist_service, database=database)
                for wishlist_track in wishlist_tracks:
                    wl_name = wishlist_track.get("name", "").lower()
                    wl_artists = wishlist_track.get("artists", [])
                    wl_artist_name = ""
                    if wl_artists:
                        if isinstance(wl_artists[0], dict):
                            wl_artist_name = wl_artists[0].get("name", "").lower()
                        else:
                            wl_artist_name = str(wl_artists[0]).lower()
                    if wl_name == track_name.lower() and wl_artist_name == artist_name.lower():
                        track_id = wishlist_track.get("spotify_track_id") or wishlist_track.get("id")
                        logger.info("[Wishlist] Found fuzzy match - track ID: %s", track_id)
                        break

        if track_id:
            logger.info("[Wishlist] Attempting to remove track from wishlist: %s", track_id)
            removed = wishlist_service.mark_track_download_result(track_id, success=True)
            if removed:
                logger.info("[Wishlist] Successfully removed track from wishlist: %s", track_id)
            else:
                logger.warning("ℹ️ [Wishlist] Track not found in wishlist or already removed: %s", track_id)
        else:
            logger.warning("ℹ️ [Wishlist] No track ID found for wishlist removal check")
    except Exception as exc:
        logger.error("[Wishlist] Error in wishlist removal check: %s", exc)


def check_and_remove_track_from_wishlist_by_metadata(
    track_data: Dict[str, Any],
    wishlist_service=None,
    database=None,
) -> bool:
    """Remove a wishlist track by metadata after a database/library match."""
    try:
        wishlist_service = wishlist_service or get_wishlist_service()
        track_name = track_data.get("name", "")
        track_id = track_data.get("id", "")
        artists = track_data.get("artists", [])

        logger.info("[Analysis] Checking if track should be removed from wishlist: '%s' (ID: %s)", track_name, track_id)

        if track_id:
            removed = wishlist_service.mark_track_download_result(track_id, success=True)
            if removed:
                logger.info("[Analysis] Removed track from wishlist via direct ID match: %s", track_id)
                return True

        if track_name and artists:
            primary_artist = _primary_track_artist_name(track_data)
            if primary_artist:
                logger.warning(
                    "[Analysis] No direct ID match, trying fuzzy match: '%s' by '%s'",
                    track_name,
                    primary_artist,
                )

                wishlist_tracks = _all_profile_wishlist_tracks(wishlist_service, database=database)
                for wishlist_track in wishlist_tracks:
                    wl_name = wishlist_track.get("name", "").lower()
                    wl_artists = wishlist_track.get("artists", [])
                    wl_artist_name = ""

                    if wl_artists:
                        if isinstance(wl_artists[0], dict):
                            wl_artist_name = wl_artists[0].get("name", "").lower()
                        else:
                            wl_artist_name = str(wl_artists[0]).lower()

                    if wl_name == track_name.lower() and wl_artist_name == primary_artist.lower():
                        spotify_track_id = wishlist_track.get("spotify_track_id") or wishlist_track.get("id")
                        if spotify_track_id:
                            removed = wishlist_service.mark_track_download_result(spotify_track_id, success=True)
                            if removed:
                                logger.info("[Analysis] Removed track from wishlist via fuzzy match: %s", spotify_track_id)
                                return True

        logger.warning("ℹ️ [Analysis] Track not found in wishlist or already removed: '%s'", track_name)
        return False

    except Exception as e:
        logger.error("[Analysis] Error checking wishlist removal by metadata: %s", e)
        import traceback

        traceback.print_exc()
        return False


__all__ = ["check_and_remove_from_wishlist", "check_and_remove_track_from_wishlist_by_metadata"]
