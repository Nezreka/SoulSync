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
                            find_video: Callable[[Any, Any], Any],
                            organizer: Optional[Callable] = None) -> dict:
    """Next-state patch for a torrent/usenet download. ``get_status(source, ref)`` returns the
    client's status object (or None if it forgot the job), ``resolve_path`` maps its reported
    save_path to a locally-readable one, ``find_video(root, name)`` returns the main video file
    for THIS job — scoped to its own content (``name`` = the torrent/nzb job name), never the
    largest file in a shared download folder."""
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
    pct = max(0.0, min(100.0, float(getattr(status, "progress", 0) or 0) * 100.0))
    # Ready to import once the DOWNLOAD is 100% — the byte progress, NOT the seed/upload state.
    # 'seeding'/'stalledUP'/'uploading'/'pausedUP'/'queuedUP' all mean the download is finished;
    # the adapter lumps queuedUP (done, just queued to seed) in with checking/moving under
    # 'queued', so state alone would leave a completed-but-seed-queued torrent stuck on
    # "Downloading 100%" forever. A file that isn't settled on disk yet is handled below
    # (find_video returns nothing → we keep polling), so treating 100% as done is safe.
    if state != "completed" and pct < 100.0:
        return {"status": "downloading", "progress": pct}
    # Completed → locate THIS job's finished video file, then import. Prefer the client's
    # exact content_path (this torrent's own file/folder) — the reliable anti-cross-attribution
    # signal: the shared save_path DIR holds every concurrent grab, and the torrent NAME often
    # differs from the real on-disk filename (e.g. name 'Love Island S13E42 1080p WEB H264-SKYFiRE'
    # vs file 'love.island.s13e42.1080p.web.h264-skyfire[EZTVx.to].mkv'), so save_path/name misses.
    # content_path points straight at the content. Fall back to save_path + name scoping for
    # clients that don't report it (never the largest file in the shared folder).
    content = getattr(status, "content_path", None)
    if content:
        save = resolve_path(content)
        src = find_video(save, None) if save else None       # already this job's own content
    else:
        reported = getattr(status, "save_path", None) or getattr(status, "incomplete_path", None)
        save = resolve_path(reported)
        name = getattr(status, "name", None)
        src = find_video(save, name) if save else None
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


def _largest_video(path) -> Optional[str]:
    """The largest non-sample video file under ``path`` — accepts a single file or a
    directory to walk. This is the 'main movie/episode' pick WITHIN an already-scoped root."""
    if os.path.isfile(path):
        return str(path) if _is_video(str(path)) else None
    if not os.path.isdir(path):
        return None
    best, best_size = None, -1
    for dirpath, _dirs, files in os.walk(path):
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


def _scoped_content(root, name) -> Optional[str]:
    """The on-disk path of a job's OWN content inside ``root`` — ``root/name`` (a single-file
    torrent's file, or a multi-file torrent's / nzb's folder). Tolerates a case/layout mismatch
    by matching a top-level entry. Returns None when the job's content isn't there — so we never
    fall back to scanning ``root`` itself and picking up a different job's file."""
    direct = os.path.join(root, str(name))
    if os.path.exists(direct):
        return direct
    try:
        for entry in os.listdir(root):
            if entry.lower() == str(name).lower():
                return os.path.join(root, entry)
    except OSError:
        return None
    return None


def find_video_file(root, name=None) -> Optional[str]:
    """The main video file for a download. When ``name`` (the torrent/nzb job name) is given the
    search is SCOPED to that job's own content (``root/name``), so a shared download folder
    holding several concurrent jobs can never leak a neighbour's (often larger) file into this
    import — the cross-attribution bug. With no name we fall back to the largest video in
    ``root`` (single-job folders, e.g. per-job usenet output)."""
    if not root:
        return None
    if name:
        scoped = _scoped_content(root, name)
        return _largest_video(scoped) if scoped else None
    return _largest_video(root)


def process_active_client_download(dl: dict, organizer=None) -> dict:
    """Production entry: poll the real client + resolve + find the video for one torrent/usenet row."""
    return process_client_download(dl, get_status=_get_status, resolve_path=_resolve_path,
                                   find_video=find_video_file, organizer=organizer)
