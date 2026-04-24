"""Shared runtime state and tiny helpers for import/post-processing code."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from core.import_context import (
    build_import_album_info,
    extract_artist_name,
    get_import_clean_artist,
    get_import_context_album,
    get_import_original_search,
    get_import_track_info,
    normalize_import_context,
)

matched_context_lock = threading.Lock()
matched_downloads_context: Dict[str, Dict[str, Any]] = {}
tasks_lock = threading.Lock()
download_tasks: Dict[str, Dict[str, Any]] = {}
download_batches: Dict[str, Dict[str, Any]] = {}
_processed_download_ids = set()
_post_process_locks: Dict[str, threading.Lock] = {}
_post_process_locks_lock = threading.Lock()

activity_feed = []
activity_feed_lock = threading.Lock()
_activity_toast_emitter = None


def set_activity_toast_emitter(emitter) -> None:
    """Set the WebSocket-style emitter used by add_activity_item."""
    global _activity_toast_emitter
    _activity_toast_emitter = emitter


def add_activity_item(icon, title, subtitle, time_ago="Now", show_toast=True):
    """Append an activity item and emit a toast if an emitter is configured."""
    activity_item = {
        "icon": icon,
        "title": title,
        "subtitle": subtitle,
        "time": time_ago,
        "timestamp": time.time(),
        "show_toast": show_toast,
    }
    with activity_feed_lock:
        activity_feed.append(activity_item)
        if len(activity_feed) > 20:
            activity_feed.pop(0)

    if show_toast and _activity_toast_emitter is not None:
        try:
            _activity_toast_emitter("dashboard:toast", activity_item)
        except Exception:
            pass

    return activity_item


def mark_task_completed(task_id: str, track_info: Optional[Dict[str, Any]] = None) -> bool:
    """Mark a download task as completed in the shared task registry."""
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return False

        task["status"] = "completed"
        task["stream_processed"] = True
        task["status_change_time"] = time.time()
        if track_info is not None:
            task["track_info"] = track_info
        return True


def detect_album_info_web(context, artist_context=None):
    """Best-effort album detection for single-track downloads."""
    context = normalize_import_context(context)
    if artist_context is None:
        artist_context = context.get("artist") or {}

    album_info = build_import_album_info(context)
    if album_info.get("is_album"):
        return album_info

    album_ctx = get_import_context_album(context)
    track_info = get_import_track_info(context)
    original_search = get_import_original_search(context)

    album_name = (
        album_ctx.get("name")
        or track_info.get("album")
        or original_search.get("album")
        or ""
    )
    track_name = (
        track_info.get("name")
        or original_search.get("title")
        or ""
    )
    artist_name = extract_artist_name(artist_context) or get_import_clean_artist(context, default="")

    if album_name and track_name and album_name.strip().lower() not in {
        track_name.strip().lower(),
        artist_name.strip().lower(),
    }:
        return build_import_album_info(
            context,
            album_info={
                "album_name": album_name,
                "track_number": track_info.get("track_number", 1),
                "disc_number": track_info.get("disc_number", 1),
                "album_image_url": album_ctx.get("image_url", ""),
                "confidence": 0.5,
            },
            force_album=True,
        )

    return None
