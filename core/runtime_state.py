"""Shared runtime state and tiny helpers for the app."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

matched_context_lock = threading.Lock()
matched_downloads_context: Dict[str, Dict[str, Any]] = {}
tasks_lock = threading.Lock()
download_tasks: Dict[str, Dict[str, Any]] = {}
download_batches: Dict[str, Dict[str, Any]] = {}
processed_download_ids = set()
post_process_locks: Dict[str, threading.Lock] = {}
post_process_locks_lock = threading.Lock()

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
    """Mark a download task as completed.

    Callers must already hold `tasks_lock`.
    """
    task = download_tasks.get(task_id)
    if not task:
        return False

    task["status"] = "completed"
    task["stream_processed"] = True
    task["status_change_time"] = time.time()
    if track_info is not None:
        task["track_info"] = track_info
    return True
