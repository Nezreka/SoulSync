"""Put downloads that bypass the batch system on the Downloads page.

Some downloads never join a batch. A music video is fetched by its own thread
into its own private dict; a basic-search grab is handed straight to the
orchestrator and the route returns. Both work — and neither appears on the
Downloads page, because that page is built from the shared ``download_tasks``
registry and nothing ever writes a row for them. The user presses Download,
something happens somewhere, and the page that exists to show downloads is
empty.

This is the same trick podcasts and audiobooks already use, generalised: write
into the shared runtime state under an own batch id, flagged so the music side
skips it. ``core/downloads/lifecycle.py:is_music_batch`` returns False for a
batch carrying ``is_music: False`` or ``managed_externally: True``, and the
music worker pool, the batch healer and the wishlist failure processor all
consult it. Satisfying that guard IS the isolation contract; no music file
changes.

``managed_externally`` is the honest flag for these two. The download is already
being run and post-processed by its own path — the row here is for visibility
only, and the music engine must not adopt it and try to run it a second time.

Locking: ``tasks_lock`` is taken briefly and never while calling out. Do not
call these from a callback fired inside a coroutine — that is the deadlock that
wedged every download once already.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.runtime_state import download_batches, download_tasks, tasks_lock
from utils.logging_config import get_logger

logger = get_logger("direct_download_state")

MUSIC_VIDEO_BATCH = "music-videos"
QUICK_BATCH = "quick-downloads"

_BATCH_NAMES = {
    MUSIC_VIDEO_BATCH: "Music Videos",
    QUICK_BATCH: "Quick Downloads",
}

# Checked by is_music_batch(). Both set deliberately: either alone is enough
# today, and a future refactor of one should not silently re-enlist these.
_ISOLATION_FLAGS: Dict[str, Any] = {
    "is_music": False,
    "managed_externally": True,
}


def _ensure_batch(batch_id: str) -> Dict[str, Any]:
    """The batch, created on first use. Caller holds tasks_lock."""
    batch = download_batches.get(batch_id)
    if batch is None:
        batch = {
            "queue": [],
            "active_count": 0,
            "max_concurrent": 3,
            "queue_index": 0,
            "playlist_id": batch_id,
            "playlist_name": _BATCH_NAMES.get(batch_id, batch_id),
            "phase": "downloading",
        }
        download_batches[batch_id] = batch
    # Re-stamped every time: a batch that lost these would be picked up by the
    # music workers on their next pass.
    batch.update(_ISOLATION_FLAGS)
    batch["batch_type"] = batch_id
    batch["source_page"] = _BATCH_NAMES.get(batch_id, batch_id)
    return batch


def register(
    batch_id: str,
    task_id: str,
    title: str,
    artist: str = "",
    album: str = "",
    artwork_url: str = "",
    source_label: str = "",
    size_bytes: int = 0,
) -> bool:
    """Put one download on the Downloads page. Returns False if it cannot."""
    task_id = str(task_id or "").strip()
    title = str(title or "").strip()
    if not task_id or not title:
        return False

    with tasks_lock:
        batch = _ensure_batch(batch_id)
        if task_id not in batch["queue"]:
            batch["queue"].append(task_id)
        batch["phase"] = "downloading"

        # The music track_info shape, because the existing cards read it.
        download_tasks[task_id] = {
            "status": "downloading",
            "track_info": {
                "title": title,
                "name": title,
                "track_name": title,
                "artist": artist or "Unknown",
                "artist_name": artist or "Unknown",
                "album": album or _BATCH_NAMES.get(batch_id, ""),
                "album_name": album or _BATCH_NAMES.get(batch_id, ""),
                "artwork_url": artwork_url,
            },
            "playlist_id": batch_id,
            "batch_id": batch_id,
            "track_index": max(0, len(batch["queue"]) - 1),
            "download_source": source_label or _BATCH_NAMES.get(batch_id, ""),
            "quality": "",
            "progress": 0.0,
            "speed": 0.0,
            "bytes_transferred": 0,
            "size": int(size_bytes or 0),
            "status_change_time": time.time(),
            "cancel_requested": False,
            "error_message": None,
        }
    logger.info("On the downloads page: %s (%s / %s)", title, batch_id, task_id)
    return True


def update_progress(task_id: str, percent: Optional[float] = None,
                    bytes_done: Optional[int] = None,
                    bytes_total: Optional[int] = None) -> None:
    """Push progress onto the card. Missing values are left alone."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return
        if percent is not None:
            task["progress"] = max(0.0, min(100.0, float(percent)))
        if bytes_done is not None:
            task["bytes_transferred"] = int(bytes_done)
        if bytes_total:
            task["size"] = int(bytes_total)


def mark_status(task_id: str, status: str, error: str = "", file_path: str = "") -> None:
    """Move a card to a terminal state."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return
        task["status"] = status
        task["status_change_time"] = time.time()
        if status == "completed":
            task["progress"] = 100.0
        if error:
            task["error_message"] = str(error)
        if file_path:
            task["final_file_path"] = str(file_path)


def is_cancelled(task_id: str) -> bool:
    """True when the user cancelled the card on the Downloads page."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return False
    with tasks_lock:
        task = download_tasks.get(task_id)
        return bool(task and (task.get("cancel_requested") or task.get("status") == "cancelled"))


def forget(task_id: str) -> None:
    """Drop a finished card's runtime row, and the batch with its last card.

    Nothing else will empty the batch: the music batch healer skips it by
    design, because is_music_batch() is False for it. The same isolation that
    keeps the music pool off these downloads also opts them out of its cleanup,
    so an emptied batch would sit on the page forever with nothing in it.
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    with tasks_lock:
        task = download_tasks.pop(task_id, None)
        batch_id = (task or {}).get("batch_id")
        if not batch_id:
            return
        batch = download_batches.get(batch_id)
        if batch is None:
            return
        if task_id in batch.get("queue", []):
            batch["queue"].remove(task_id)
        # Drop ids whose task is already gone, so a card removed by any other
        # path cannot leave the queue looking occupied.
        batch["queue"] = [t for t in batch.get("queue", []) if t in download_tasks]
        if not batch["queue"]:
            download_batches.pop(batch_id, None)
