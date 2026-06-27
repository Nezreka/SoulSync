"""Automation handler: ``video_clean_youtube_episodes`` action.

YouTube retention: for each followed channel that has a per-channel retention policy (set in
the channel's cog modal — default 'keep everything'), delete the episodes that fall outside
the keep window (video file + its ``-thumb``/``.nfo`` sidecars). The history row is KEPT and
flagged pruned, so the scan's download-dedup still excludes it — a cleaned episode is never
re-downloaded. Playlists are mirror-the-whole-thing, so they're out of scope (channels only).

Runs daily. Pure — channel list, per-channel policy, episode list, file deletion + the prune
mark are all injected seams; tests drive it with fakes and never touch a disk or DB.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from core.automation.deps import AutomationDeps
from core.video.retention import episodes_to_prune, parse_retention

logger = logging.getLogger(__name__)


def _default_fetch_channels() -> List[Any]:
    from api.video import get_video_db
    return get_video_db().youtube_channels_with_downloads()


def _default_channel_retention(channel_id: Any) -> Any:
    from api.video import get_video_db
    return (get_video_db().get_channel_settings(channel_id) or {}).get("retention")


def _default_fetch_episodes(channel_id: Any) -> List[Dict[str, Any]]:
    from api.video import get_video_db
    return get_video_db().youtube_channel_episodes(channel_id)


def _default_mark_pruned(history_id: Any, when: str) -> bool:
    from api.video import get_video_db
    return get_video_db().mark_download_pruned(history_id, when)


def _silent_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _default_delete_files(ep: Dict[str, Any]):
    """Delete the episode's video + its sidecars. Returns (ok, freed_bytes). Only ever touches
    the exact recorded ``dest_path`` and its same-stem sidecars — never walks a folder. A file
    already gone counts as success (mark it pruned); a real delete error does NOT (so it retries)."""
    path = ep.get("dest_path")
    if not path:
        return False, 0
    size = 0
    try:
        if os.path.exists(path):
            size = os.path.getsize(path)
            os.remove(path)
    except OSError:
        logger.exception("retention: could not delete %s", path)
        return False, 0
    stem = os.path.splitext(path)[0]
    for sc in (stem + ".nfo", stem + "-thumb.jpg", stem + "-thumb.jpeg",
               stem + "-thumb.png", stem + "-thumb.webp"):
        if os.path.exists(sc):
            _silent_remove(sc)
    return True, size


def _fmt_gb(b: int) -> str:
    gb = (b or 0) / (1024 ** 3)
    return ("%.1f GB" % gb) if gb >= 0.1 else "%d MB" % round((b or 0) / (1024 ** 2))


def auto_video_clean_youtube_episodes(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    fetch_channels: Optional[Callable[[], List[Any]]] = None,
    channel_retention: Optional[Callable[[Any], Any]] = None,
    fetch_episodes: Optional[Callable[[Any], List[Dict[str, Any]]]] = None,
    delete_files: Optional[Callable[[Dict[str, Any]], Any]] = None,
    mark_pruned: Optional[Callable[[Any, str], Any]] = None,
    today_fn: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    """Delete out-of-window episodes for channels with a retention policy. Returns
    ``{'status': 'completed', 'deleted': int, 'channels': int, 'freed_bytes': int, ...}``."""
    fetch_channels = fetch_channels or _default_fetch_channels
    channel_retention = channel_retention or _default_channel_retention
    fetch_episodes = fetch_episodes or _default_fetch_episodes
    delete_files = delete_files or _default_delete_files
    mark_pruned = mark_pruned or _default_mark_pruned
    today_fn = today_fn or (lambda: date.today().isoformat())
    automation_id = config.get("_automation_id")
    try:
        today = today_fn()
        deps.update_progress(automation_id, phase="Checking retention…", progress=10,
                             log_line="Looking for channels with a retention policy", log_type="info")
        deleted = freed = checked = 0
        for cid in (fetch_channels() or []):
            policy = channel_retention(cid)
            if not parse_retention(policy):           # 'keep everything' / unset → skip
                continue
            checked += 1
            prune = episodes_to_prune(fetch_episodes(cid) or [], policy, today=today)
            for ep in prune:
                ok, size = delete_files(ep)
                if not ok:
                    continue
                mark_pruned(ep.get("id"), today)      # keep the row → scan won't re-download it
                deleted += 1
                freed += size or 0
                deps.update_progress(automation_id, log_type="info",
                                     log_line="Removed '%s'" % (ep.get("title") or ep.get("media_id") or "episode"))

        msg = ("Removed %d old episode(s) · freed %s" % (deleted, _fmt_gb(freed))) if deleted \
            else "Nothing to clean — every channel within its retention window"
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=msg, log_type="success")
        return {"status": "completed", "deleted": deleted, "channels": checked,
                "freed_bytes": freed, "_manages_own_progress": True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status="error", phase="Error", log_line=str(e), log_type="error")
        return {"status": "error", "error": str(e), "_manages_own_progress": True}
