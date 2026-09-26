"""Put audiobook downloads on the existing Downloads page.

SoulSync already has a live downloads page with progress cards, and podcasts
already proved you can appear on it without joining the music pipeline: a
podcast writes into the SHARED ``download_tasks`` / ``download_batches`` runtime
state under its own batch id, flagged so the music side skips it.

``core/downloads/lifecycle.py:is_music_batch`` returns False for a batch with
``is_music: False`` or ``managed_externally: True``. That is an existing guard
this module simply satisfies — the music worker pool, the batch healer and the
music wishlist failure processor all consult it, so setting those flags is the
whole isolation contract and NO music file needs to change for audiobooks to
show up.

What audiobooks do differently from podcasts: a podcast episode is one file this
app downloads itself, while an audiobook is a release handed to an external
torrent or usenet client. So the progress written here comes from polling that
client rather than from our own byte counter, and a book stays on the page as
one card even though it is a folder of chapters underneath.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.runtime_state import download_batches, download_tasks, tasks_lock
from utils.logging_config import get_logger

logger = get_logger("audiobook_download_state")

# The batch every audiobook download belongs to. One batch, not one per book:
# the page groups by batch, and a card per book inside a single "Audiobooks"
# group is how podcasts read too.
BATCH_ID = "audiobooks"

# The flags that keep the music engine's hands off. Both are checked by
# is_music_batch(); either alone is enough, and both are set deliberately so a
# future refactor of one does not silently re-enlist audiobooks.
_ISOLATION_FLAGS: Dict[str, Any] = {
    "is_music": False,
    "managed_externally": True,
    "batch_type": "audiobook",
    "source_page": "Audiobooks",
}


def _ensure_batch() -> Dict[str, Any]:
    """The audiobooks batch, created on first use. Caller holds tasks_lock."""
    batch = download_batches.get(BATCH_ID)
    if batch is None:
        batch = {
            "queue": [],
            "active_count": 0,
            "max_concurrent": 3,
            "queue_index": 0,
            "playlist_id": BATCH_ID,
            "playlist_name": "Audiobooks",
            "phase": "downloading",
        }
        download_batches[BATCH_ID] = batch
    # Re-stamped every time: a batch that lost these flags would be picked up by
    # the music workers on the next pass.
    batch.update(_ISOLATION_FLAGS)
    return batch


def register_download(
    task_id: str,
    title: str,
    author: str = "",
    series: str = "",
    artwork_url: str = "",
    protocol: str = "",
    size_bytes: int = 0,
    only_if_missing: bool = False,
    status: str = "queued",
    username: str = "",
    release_title: str = "",
) -> bool:
    """Put one grabbed or searching audiobook on the Downloads page.

    ``task_id`` is the download client's own reference (a qBittorrent info-hash,
    a SABnzbd nzo_id) or a temporary ASIN-derived id while searching.

    The track_info shape is the music one because the existing cards read it —
    title, artist, album, artwork. For a book that reads as title / author /
    series, which is the closest honest mapping and what the card renders well.
    """
    task_id = str(task_id or "").strip()
    if not task_id or not title:
        return False

    with tasks_lock:
        if only_if_missing and task_id in download_tasks:
            return False
        batch = _ensure_batch()
        if task_id not in batch["queue"]:
            batch["queue"].append(task_id)
        batch["phase"] = "downloading"

        download_tasks[task_id] = {
            "status": status,
            "track_info": {
                "title": title,
                "name": title,
                "track_name": title,
                "artist": author or "Audiobook",
                "artist_name": author or "Audiobook",
                "album": series or "Audiobooks",
                "album_name": series or "Audiobooks",
                "artwork_url": artwork_url,
            },
            "playlist_id": BATCH_ID,
            "batch_id": BATCH_ID,
            "track_index": max(0, len(batch["queue"]) - 1),
            "download_source": f"Audiobook ({protocol})" if protocol else "Audiobook",
            "quality": protocol or "",
            "progress": 0.0,
            "speed": 0.0,
            "bytes_transferred": 0,
            "size": int(size_bytes or 0),
            "status_change_time": time.time(),
            "cancel_requested": False,
            "error_message": None,
            "username": username,
            "release_title": release_title,
        }
    logger.info("Audiobook download on the downloads page: %s (%s, status=%s)", title, task_id, status)
    return True


def promote_search_task(
    temp_task_id: str,
    real_task_id: str,
    protocol: str = "",
    size_bytes: int = 0,
    username: str = "",
    release_title: str = "",
) -> bool:
    """Transition a searching task to a grabbed client handle."""
    temp_task_id = str(temp_task_id or "").strip()
    real_task_id = str(real_task_id or "").strip()
    if not temp_task_id or not real_task_id:
        return False
    with tasks_lock:
        task = download_tasks.get(temp_task_id)
        if not task:
            return False
        if temp_task_id != real_task_id:
            download_tasks.pop(temp_task_id, None)
            download_tasks[real_task_id] = task
            batch = download_batches.get(BATCH_ID)
            if batch and "queue" in batch:
                if temp_task_id in batch["queue"]:
                    idx = batch["queue"].index(temp_task_id)
                    batch["queue"][idx] = real_task_id
                elif real_task_id not in batch["queue"]:
                    batch["queue"].append(real_task_id)
        task["status"] = "queued"
        task["task_id"] = real_task_id
        if protocol:
            task["download_source"] = f"Audiobook ({protocol})"
            task["quality"] = protocol
        if size_bytes:
            task["size"] = int(size_bytes)
        if username:
            task["username"] = username
        if release_title:
            task["release_title"] = release_title
        task["status_change_time"] = time.time()
    return True


def update_progress(
    task_id: str,
    percent: Optional[float] = None,
    bytes_done: Optional[int] = None,
    bytes_total: Optional[int] = None,
    speed: Optional[float] = None,
) -> None:
    """Push a poll result onto the card. Missing values are left alone."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return
        if speed is not None:
            task["speed"] = max(0.0, float(speed))
        if percent is not None:
            task["progress"] = max(0.0, min(100.0, float(percent)))
        if bytes_done is not None:
            task["bytes_transferred"] = int(bytes_done)
        if bytes_total:
            task["size"] = int(bytes_total)


def mark_status(
    task_id: str,
    status: str,
    error: str = "",
    file_path: str = "",
    held_reason: str = "",
    release_title: str = "",
) -> None:
    """Move a card to a terminal state.

    "importing" is an audiobook-specific stop the music side has no equivalent
    for: the download finished but the book is being checked and filed, and a
    card that jumped straight to complete would claim a library entry that does
    not exist yet.
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return
        if task["status"] != status:
            task["status_change_time"] = time.time()
        task["status"] = status
        if status in ("downloading", "queued"):
            task["error_message"] = None
        if status == "completed":
            task["progress"] = 100.0
        if error:
            task["error_message"] = error
        if file_path:
            task["final_file_path"] = file_path
        if held_reason:
            task["held_reason"] = held_reason
        if release_title:
            task["release_title"] = release_title


def set_task_metadata(
    task_id: str,
    username: str = "",
    release_title: str = "",
) -> None:
    """Attach peer or release details to a live task."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return
        if username and not task.get("username"):
            task["username"] = username
        if release_title and not task.get("release_title"):
            task["release_title"] = release_title


def is_cancelled(task_id: str) -> bool:
    """True when the user cancelled the card on the Downloads page."""
    task_id = str(task_id or "").strip()
    if not task_id:
        return False
    with tasks_lock:
        task = download_tasks.get(task_id)
        return bool(task and (task.get("cancel_requested") or task.get("status") == "cancelled"))


def forget(task_id: str) -> None:
    """Drop a finished card's runtime row.

    Only the live view is cleared; the audiobook database keeps the history, the
    same split the rest of the app uses between what is on screen now and what
    happened.
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        return
    with tasks_lock:
        download_tasks.pop(task_id, None)
        batch = download_batches.get(BATCH_ID)
        if batch is None:
            return
        if task_id in batch.get("queue", []):
            batch["queue"].remove(task_id)

        # Drop ids whose task is already gone, so a card removed by any other
        # path cannot leave the queue looking occupied.
        batch["queue"] = [t for t in batch.get("queue", []) if t in download_tasks]

        # The batch goes when its last card does. Nothing else will do it:
        # the music side's batch healer skips this batch by design, because
        # is_music_batch() returns False for it — the same isolation that keeps
        # the music worker pool off our downloads also opts us out of its
        # cleanup, so an emptied batch would sit on the Downloads page as an
        # "Audiobooks" card with nothing in it, forever.
        if not batch["queue"]:
            download_batches.pop(BATCH_ID, None)
