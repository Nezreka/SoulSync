"""Monitor a torrent/usenet video download to completion + hand the file to the importer.

The Soulseek path polls slskd transfers; this is the parallel path for torrent/usenet grabs.
``process_client_download`` is PURE (all I/O injected) and returns the SAME patch shape as
``download_monitor.process_download`` — so the monitor's existing progress/failure/completion/
history handling works unchanged. Production wiring reuses the SHARED torrent/usenet client
adapters + ``resolve_reported_save_path`` (music-safe: imported + called, never modified).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional

from core.video.slskd_search import _is_video
from utils.logging_config import get_logger

logger = get_logger("video.client_download")

_FAILED_STATES = {"error", "failed"}
_COMPLETE_STATES = {"seeding", "completed", "complete", "succeeded", "finished"}


def _norm_state(status: Any) -> str:
    st = str(getattr(status, "state", "") or "").lower()
    if st in _FAILED_STATES:
        return "failed"
    if st in _COMPLETE_STATES:
        return "completed"
    return "downloading"


def process_client_download(dl: dict, *, get_status: Callable[[str, str], Any],
                            resolve_path: Callable[[Any], Any],
                            find_video: Callable[[Any], Any],
                            organizer: Optional[Callable] = None) -> dict:
    """Next-state patch for a torrent/usenet download. ``get_status(source, ref)`` returns the
    client's status object (or None if it forgot the job), ``resolve_path`` maps its reported
    save_path to a locally-readable one, ``find_video`` returns the main video file under it."""
    ref = dl.get("client_ref")
    if not ref:
        return {"_missing": True}
    status = get_status(str(dl.get("source") or ""), str(ref))
    if status is None:
        # Client no longer knows the job — could be done+cleared. If we already placed the
        # file, finish; otherwise treat as missing (the monitor decides when to give up).
        if dl.get("dest_path"):
            return {"status": "completed", "progress": 100.0, "dest_path": dl.get("dest_path")}
        return {"_missing": True}
    state = _norm_state(status)
    if state == "failed":
        return {"status": "failed", "error": getattr(status, "error", None) or "Download client reported an error"}
    if state != "completed":
        pct = max(0.0, min(100.0, float(getattr(status, "progress", 0) or 0) * 100.0))
        return {"status": "downloading", "progress": pct}
    # Completed → locate the finished video file in the client's save folder, then import.
    reported = getattr(status, "save_path", None) or getattr(status, "incomplete_path", None)
    save = resolve_path(reported)
    src = find_video(save) if save else None
    if not src:
        if dl.get("dest_path"):
            return {"status": "completed", "progress": 100.0, "dest_path": dl.get("dest_path")}
        return {"progress": 100.0}   # complete but the file isn't visible yet — keep polling
    if organizer is not None:
        return organizer(dl, src)
    return {"status": "completed", "progress": 100.0, "dest_path": src}


# ── production seams ──────────────────────────────────────────────────────────
def _run(coro):
    return asyncio.run(coro)


def _get_status(source: str, ref: str):
    """Poll the SHARED torrent/usenet client for a job's status (or None)."""
    try:
        if source == "torrent":
            from core.torrent_clients import get_active_adapter
        else:
            from core.usenet_clients import get_active_adapter
        adapter = get_active_adapter()
        if adapter is None:
            return None
        return _run(adapter.get_status(ref))
    except Exception:   # noqa: BLE001 - a poll hiccup = 'unknown this tick', not a failure
        logger.debug("client status poll failed for %s %s", source, ref, exc_info=True)
        return None


def _resolve_path(reported):
    try:
        from core.download_plugins.album_bundle import resolve_reported_save_path
        return resolve_reported_save_path(reported)
    except Exception:   # noqa: BLE001 - fall back to the raw path if the resolver isn't usable
        return reported


def find_video_file(root) -> Optional[str]:
    """The largest non-sample video file under ``root`` — the main movie/episode. Accepts a
    directory (walks it) or a single file path."""
    if not root:
        return None
    if os.path.isfile(root):
        return str(root) if _is_video(str(root)) else None
    if not os.path.isdir(root):
        return None
    best, best_size = None, -1
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if not _is_video(f) or "sample" in f.lower():
                continue
            p = os.path.join(dirpath, f)
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = 0
            if sz > best_size:
                best, best_size = p, sz
    return best


def process_active_client_download(dl: dict, organizer=None) -> dict:
    """Production entry: poll the real client + resolve + find the video for one torrent/usenet row."""
    return process_client_download(dl, get_status=_get_status, resolve_path=_resolve_path,
                                   find_video=find_video_file, organizer=organizer)
