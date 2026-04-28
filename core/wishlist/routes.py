"""Wishlist controller helpers for Flask-style endpoints."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict

from core.wishlist.reporting import build_wishlist_stats_payload
from core.wishlist.selection import prepare_wishlist_tracks_for_display
from core.wishlist.state import get_wishlist_cycle as _get_wishlist_cycle
from core.wishlist.state import set_wishlist_cycle as _set_wishlist_cycle


@dataclass
class WishlistRouteRuntime:
    """Dependencies needed to service wishlist HTTP endpoints outside the controller."""

    get_wishlist_service: Callable[[], Any]
    get_music_database: Callable[[], Any]
    get_current_profile_id: Callable[[], int]
    download_batches: Dict[str, Dict[str, Any]]
    download_tasks: Dict[str, Dict[str, Any]]
    tasks_lock: Any
    is_wishlist_auto_processing_flag: Callable[[], bool]
    is_wishlist_actually_processing: Callable[[], bool]
    reset_wishlist_processing_state: Callable[[], None]
    add_activity_item: Callable[[Any, Any, Any, Any], Any]
    logger: Any
    active_server: str
    get_next_run_seconds: Callable[[str], int] | None = None
    thread_factory: Callable[..., Any] = threading.Thread


def _build_album_images(album: Dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(album.get("images"), list) and album.get("images"):
        return list(album["images"])
    if album.get("image_url"):
        return [{"url": album["image_url"], "height": 640, "width": 640}]
    return []


def _build_spotify_track_data(track: Dict[str, Any], album: Dict[str, Any]) -> Dict[str, Any]:
    album_images = _build_album_images(album)
    return {
        "id": track.get("id"),
        "name": track.get("name"),
        "artists": track.get("artists", []),
        "album": {
            "id": album.get("id"),
            "name": album.get("name"),
            "artists": album.get("artists", []),
            "images": album_images,
            "album_type": album.get("album_type", "album"),
            "release_date": album.get("release_date", ""),
            "total_tracks": album.get("total_tracks", 1),
        },
        "duration_ms": track.get("duration_ms", 0),
        "track_number": track.get("track_number", 1),
        "disc_number": track.get("disc_number", 1),
        "explicit": track.get("explicit", False),
        "popularity": track.get("popularity", 0),
        "preview_url": track.get("preview_url"),
        "external_urls": track.get("external_urls", {}),
    }


def _load_track_spotify_data(track: Dict[str, Any]) -> Dict[str, Any]:
    spotify_data = track.get("spotify_data", {})
    if isinstance(spotify_data, str):
        try:
            spotify_data = json.loads(spotify_data)
        except Exception:
            spotify_data = {}
    if not isinstance(spotify_data, dict):
        spotify_data = {}
    return spotify_data


def _album_lookup_id(spotify_data: Dict[str, Any]) -> tuple[str | None, Dict[str, Any]]:
    album_data = spotify_data.get("album") or {}
    if not isinstance(album_data, dict):
        album_data = {}

    track_album_id = album_data.get("id")
    if not track_album_id:
        album_name = album_data.get("name", "Unknown Album")
        artists = spotify_data.get("artists", [])
        if isinstance(artists, list) and artists and isinstance(artists[0], dict):
            artist_name = artists[0].get("name", "Unknown Artist")
        elif isinstance(artists, list) and artists and isinstance(artists[0], str):
            artist_name = artists[0]
        else:
            artist_name = "Unknown Artist"
        custom_id = f"{album_name}_{artist_name}"
        track_album_id = re.sub(r"[^a-zA-Z0-9\s_-]", "", custom_id)
        track_album_id = re.sub(r"\s+", "_", track_album_id).lower()

    return track_album_id, album_data


def process_wishlist_api(
    runtime: WishlistRouteRuntime,
    *,
    start_processing: Callable[[], None],
) -> tuple[Dict[str, Any], int]:
    """Trigger wishlist processing in the background."""
    try:
        if runtime.is_wishlist_auto_processing_flag():
            return {"success": False, "error": "Wishlist processing already in progress"}, 409

        thread = runtime.thread_factory(target=start_processing, daemon=True)
        thread.start()
        return {"success": True, "message": "Wishlist processing started"}, 200
    except Exception as exc:
        runtime.logger.error("Error starting wishlist processing: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def get_wishlist_count(runtime: WishlistRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Return the current wishlist count for the active profile."""
    try:
        wishlist_service = runtime.get_wishlist_service()
        count = wishlist_service.get_wishlist_count(profile_id=runtime.get_current_profile_id())
        return {"count": count}, 200
    except Exception as exc:
        runtime.logger.error("Error getting wishlist count: %s", exc)
        return {"error": str(exc)}, 500


def get_wishlist_stats(runtime: WishlistRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Return wishlist statistics for the UI."""
    try:
        wishlist_service = runtime.get_wishlist_service()
        raw_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=runtime.get_current_profile_id())
        next_run_in_seconds = runtime.get_next_run_seconds("process_wishlist") if runtime.get_next_run_seconds else 0
        is_processing = runtime.is_wishlist_actually_processing()
        current_cycle = _get_wishlist_cycle(runtime.get_music_database)

        payload = build_wishlist_stats_payload(
            raw_tracks,
            next_run_in_seconds=next_run_in_seconds,
            is_auto_processing=is_processing,
            current_cycle=current_cycle,
        )
        return payload, 200
    except Exception as exc:
        runtime.logger.error("Error getting wishlist stats: %s", exc)
        return {"error": str(exc)}, 500


def get_wishlist_cycle(runtime: WishlistRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Return the current wishlist cycle."""
    try:
        cycle = _get_wishlist_cycle(runtime.get_music_database)
        return {"cycle": cycle}, 200
    except Exception as exc:
        runtime.logger.error("Error getting wishlist cycle: %s", exc)
        return {"error": str(exc)}, 500


def set_wishlist_cycle(runtime: WishlistRouteRuntime, cycle: str) -> tuple[Dict[str, Any], int]:
    """Persist the wishlist cycle."""
    try:
        if cycle not in ["albums", "singles"]:
            return {"error": "Invalid cycle. Must be 'albums' or 'singles'"}, 400

        _set_wishlist_cycle(runtime.get_music_database, cycle)
        runtime.logger.info("Wishlist cycle set to: %s", cycle)
        return {"success": True, "cycle": cycle}, 200
    except Exception as exc:
        runtime.logger.error("Error setting wishlist cycle: %s", exc)
        return {"error": str(exc)}, 500


def get_wishlist_tracks(
    runtime: WishlistRouteRuntime,
    *,
    category: str | None = None,
    limit: int | None = None,
) -> tuple[Dict[str, Any], int]:
    """Return wishlist tracks for the modal UI."""
    try:
        wishlist_service = runtime.get_wishlist_service()
        db = runtime.get_music_database()

        with runtime.tasks_lock:
            wishlist_batch_active = any(
                batch.get("playlist_id") == "wishlist" and batch.get("phase") in ["analysis", "downloading"]
                for batch in runtime.download_batches.values()
            )

        if not wishlist_batch_active:
            duplicates_removed = db.remove_wishlist_duplicates(profile_id=runtime.get_current_profile_id())
            if duplicates_removed > 0:
                runtime.logger.warning("Cleaned %s duplicate tracks from wishlist", duplicates_removed)
        else:
            runtime.logger.warning("Skipping wishlist duplicate cleanup - download in progress")

        raw_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=runtime.get_current_profile_id())
        prepared = prepare_wishlist_tracks_for_display(raw_tracks, category=category, limit=limit)

        if prepared["duplicates_found"] > 0:
            runtime.logger.warning(
                "[API-Wishlist-Tracks] Found and removed %s duplicate tracks during sanitization",
                prepared["duplicates_found"],
            )

        if category:
            runtime.logger.info(
                "Wishlist filter: %s/%s tracks in '%s' category (limit: %s)",
                len(prepared["tracks"]),
                prepared["total"],
                category,
                limit or "none",
            )
            return {"tracks": prepared["tracks"], "category": category, "total": prepared["total"]}, 200

        return {"tracks": prepared["tracks"], "total": prepared["total"]}, 200
    except Exception as exc:
        runtime.logger.error("Error getting wishlist tracks: %s", exc)
        return {"error": str(exc)}, 500


def clear_wishlist(runtime: WishlistRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Clear the wishlist and cancel active wishlist batches."""
    try:
        wishlist_service = runtime.get_wishlist_service()
        success = wishlist_service.clear_wishlist(profile_id=runtime.get_current_profile_id())

        if success:
            cancelled_count = 0
            with runtime.tasks_lock:
                for _batch_id, batch_data in runtime.download_batches.items():
                    if batch_data.get("playlist_id") == "wishlist" and batch_data.get("phase") not in (
                        "complete",
                        "error",
                        "cancelled",
                    ):
                        batch_data["phase"] = "cancelled"
                        for task_id in batch_data.get("queue", []):
                            if task_id in runtime.download_tasks and runtime.download_tasks[task_id]["status"] not in (
                                "completed",
                                "failed",
                                "not_found",
                                "cancelled",
                            ):
                                runtime.download_tasks[task_id]["status"] = "cancelled"
                                cancelled_count += 1

            runtime.reset_wishlist_processing_state()

            if cancelled_count > 0:
                runtime.logger.warning("[Wishlist Clear] Cancelled %s active wishlist downloads", cancelled_count)
                runtime.add_activity_item("", "Wishlist Cleared", f"Wishlist cleared and {cancelled_count} downloads cancelled", "Now")

            return {
                "success": True,
                "message": "Wishlist cleared successfully",
                "cancelled_downloads": cancelled_count,
            }, 200

        return {"success": False, "error": "Failed to clear wishlist"}, 500
    except Exception as exc:
        runtime.logger.error("Error clearing wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def remove_track_from_wishlist(
    runtime: WishlistRouteRuntime,
    spotify_track_id: str | None,
) -> tuple[Dict[str, Any], int]:
    """Remove a single track from the wishlist."""
    try:
        if not spotify_track_id:
            return {"success": False, "error": "No spotify_track_id provided"}, 400

        wishlist_service = runtime.get_wishlist_service()
        success = wishlist_service.remove_track_from_wishlist(
            spotify_track_id,
            profile_id=runtime.get_current_profile_id(),
        )

        if success:
            runtime.logger.info("Successfully removed track from wishlist: %s", spotify_track_id)
            return {"success": True, "message": "Track removed from wishlist"}, 200

        runtime.logger.warning("Failed to remove track from wishlist: %s", spotify_track_id)
        return {"success": False, "error": "Track not found in wishlist"}, 404
    except Exception as exc:
        runtime.logger.error("Error removing track from wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def remove_album_from_wishlist(
    runtime: WishlistRouteRuntime,
    *,
    album_id: str | None = None,
    album_name_filter: str | None = None,
) -> tuple[Dict[str, Any], int]:
    """Remove every wishlist track that belongs to the selected album."""
    try:
        if not album_id and not album_name_filter:
            return {"success": False, "error": "No album_id or album_name provided"}, 400

        wishlist_service = runtime.get_wishlist_service()
        all_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=runtime.get_current_profile_id())

        tracks_to_remove = []
        for track in all_tracks:
            spotify_data = _load_track_spotify_data(track)
            track_album_id, album_data = _album_lookup_id(spotify_data)

            matched = False
            if album_id and track_album_id == album_id:
                matched = True
            elif album_name_filter:
                track_album_name = album_data.get("name", "")
                if isinstance(spotify_data.get("album"), str):
                    track_album_name = spotify_data["album"]
                if track_album_name and track_album_name.lower().strip() == album_name_filter.lower().strip():
                    matched = True

            if matched:
                spotify_track_id = track.get("spotify_track_id") or track.get("id")
                if spotify_track_id:
                    tracks_to_remove.append(spotify_track_id)

        removed_count = 0
        album_remove_pid = runtime.get_current_profile_id()
        for spotify_track_id in tracks_to_remove:
            if wishlist_service.remove_track_from_wishlist(spotify_track_id, profile_id=album_remove_pid):
                removed_count += 1

        if removed_count > 0:
            runtime.logger.info("Successfully removed %s tracks from album %s", removed_count, album_id)
            return {
                "success": True,
                "message": f"Removed {removed_count} track(s) from wishlist",
                "removed_count": removed_count,
            }, 200

        runtime.logger.warning("No tracks found for album %s", album_id)
        return {"success": False, "error": "No tracks found for this album"}, 404
    except Exception as exc:
        runtime.logger.error("Error removing album from wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def remove_batch_from_wishlist(
    runtime: WishlistRouteRuntime,
    spotify_track_ids,
) -> tuple[Dict[str, Any], int]:
    """Remove a batch of tracks from the wishlist."""
    try:
        if not spotify_track_ids or not isinstance(spotify_track_ids, list):
            return {"success": False, "error": "Missing or invalid spotify_track_ids"}, 400

        wishlist_service = runtime.get_wishlist_service()
        removed = 0
        pid = runtime.get_current_profile_id()
        for track_id in spotify_track_ids:
            if wishlist_service.remove_track_from_wishlist(track_id, profile_id=pid):
                removed += 1

        runtime.logger.info("Batch removed %s track(s) from wishlist", removed)
        return {
            "success": True,
            "removed": removed,
            "message": f"Removed {removed} track{'s' if removed != 1 else ''} from wishlist",
        }, 200
    except Exception as exc:
        runtime.logger.error("Error batch removing from wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def add_album_track_to_wishlist(
    runtime: WishlistRouteRuntime,
    *,
    track: Dict[str, Any] | None,
    artist: Dict[str, Any] | None,
    album: Dict[str, Any] | None,
    source_type: str = "album",
    source_context: Dict[str, Any] | None = None,
) -> tuple[Dict[str, Any], int]:
    """Add a single album track to the wishlist."""
    try:
        if not track or not artist or not album:
            return {"success": False, "error": "Missing required fields: track, artist, album"}, 400

        spotify_track_data = _build_spotify_track_data(track, album)

        enhanced_source_context = {
            **(source_context or {}),
            "artist_id": artist.get("id"),
            "artist_name": artist.get("name"),
            "album_id": album.get("id"),
            "album_name": album.get("name"),
            "added_via": "library_wishlist_modal",
        }

        wishlist_service = runtime.get_wishlist_service()
        success = wishlist_service.add_spotify_track_to_wishlist(
            spotify_track_data=spotify_track_data,
            failure_reason="Added from library (incomplete album)",
            source_type=source_type,
            source_context=enhanced_source_context,
            profile_id=runtime.get_current_profile_id(),
        )

        if success:
            runtime.logger.info("Added track '%s' by '%s' to wishlist", track.get("name"), artist.get("name"))
            return {"success": True, "message": f"Added '{track.get('name')}' to wishlist"}, 200

        runtime.logger.error("Failed to add track '%s' to wishlist", track.get("name"))
        return {"success": False, "error": "Failed to add track to wishlist"}, 200
    except Exception as exc:
        runtime.logger.error("Error adding track to wishlist: %s", exc)
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(exc)}, 500


__all__ = [
    "WishlistRouteRuntime",
    "process_wishlist_api",
    "get_wishlist_count",
    "get_wishlist_stats",
    "get_wishlist_cycle",
    "set_wishlist_cycle",
    "get_wishlist_tracks",
    "clear_wishlist",
    "remove_track_from_wishlist",
    "remove_album_from_wishlist",
    "remove_batch_from_wishlist",
    "add_album_track_to_wishlist",
]
