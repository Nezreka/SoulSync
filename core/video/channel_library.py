"""a followed YouTube channel as a library you manage.

a channel's downloads never become media_files rows (the show manager's
world): what's on disk is the download ledger, video_download_history
rows with source='youtube', outcome='completed' and no pruned_at. this
builds the channel page's "your library" from it: every file with its
size and where it lives, the totals, the folder, and what needs attention
(files gone from disk, downloads that keep failing, wished videos still
waiting, videos retention will remove next). the actions (delete through
the recycle bin, undo, clean now) keep the ledger's rule: a removed video
stays in it, pruned, so the scans never fetch it again, unless the person
asks to forget it ("get it again").

pure where it can be: everything the disk or db does comes in as a seam.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.video.retention import episode_date, episodes_to_prune, parse_retention

Stat = Callable[[str], Optional[int]]


def file_size(path: str) -> Optional[int]:
    """the file's size, None when it isn't there (a ghost)"""
    try:
        return os.path.getsize(path) if path and os.path.isfile(path) else None
    except OSError:
        return None


def _folder(paths: Iterable[str]) -> Optional[str]:
    """the channel's folder: the common parent of its files' season folders"""
    dirs = [os.path.dirname(p) for p in paths if p]
    if not dirs:
        return None
    try:
        common = os.path.commonpath(dirs)
    except ValueError:      # different drives
        return None
    # files sit in <channel>/Season YYYY/...: one level up when they all share a season
    if len(set(dirs)) == 1 and os.path.basename(common).lower().startswith("season"):
        common = os.path.dirname(common)
    return common or None


def _retention_words(policy: Any) -> str:
    parsed = parse_retention(policy)
    if not parsed:
        return "Keeps every video"
    mode, n = parsed
    if mode == "count":
        return f"Keeps the newest {n}"
    return f"Keeps the last {n} days"


def channel_library(
    files: List[Dict[str, Any]],
    *,
    settings: Optional[Dict[str, Any]] = None,
    wished_ids: Iterable[str] = (),
    retry_state: Optional[Dict[str, Dict[str, Any]]] = None,
    today: str,
    stat: Stat = file_size,
) -> Dict[str, Any]:
    """the library panel's data for one channel.

    ``files``: the ledger rows (youtube_channel_files). ``settings``: the
    channel's (retention, auto_download). ``wished_ids``: its wished videos.
    ``retry_state``: youtube_retry_state(), for wished videos that keep failing.
    """
    settings = settings or {}
    retry_state = retry_state or {}
    out_files = []
    total = 0
    ghosts = []
    for f in files:
        path = f.get("dest_path") or ""
        size = stat(path) if path else None
        exists = size is not None
        if exists:
            total += size or 0
        else:
            ghosts.append(f.get("media_id"))
        out_files.append({
            "history_id": f.get("id"),
            "video_id": f.get("media_id"),
            "title": f.get("title") or f.get("media_id") or "",
            "published": episode_date(f),
            "downloaded_at": str(f.get("completed_at") or "")[:19],
            "path": path,
            "folder": os.path.dirname(path) if path else None,
            "size": size,
            "exists": exists,
            "quality": f.get("quality_label") or f.get("resolution") or None,
        })

    owned = {f.get("media_id") for f in files}
    waiting = [v for v in wished_ids if v not in owned]
    failing = [v for v in waiting if (retry_state.get(v) or {}).get("strikes", 0) > 0]
    gone = [v for v in waiting if (retry_state.get(v) or {}).get("permanent")]

    retention = settings.get("retention")
    due = episodes_to_prune([f for f in files if stat(f.get("dest_path") or "") is not None],
                            retention, today=today) if parse_retention(retention) else []

    quality: Dict[str, int] = {}
    for f in out_files:
        if f["exists"] and f["quality"]:
            quality[f["quality"]] = quality.get(f["quality"], 0) + 1

    attention = []
    if ghosts:
        attention.append({"kind": "missing", "count": len(ghosts), "video_ids": ghosts,
                          "words": "gone from disk"})
    if failing:
        attention.append({"kind": "failing", "count": len(failing), "video_ids": failing,
                          "words": "keep failing to download"})
    if gone:
        attention.append({"kind": "unavailable", "count": len(gone), "video_ids": gone,
                          "words": "were removed from YouTube or made private"})
    if due:
        attention.append({"kind": "retention", "count": len(due),
                          "video_ids": [d.get("media_id") for d in due],
                          "words": "will be removed by retention"})
    if settings.get("auto_download") is False:
        attention.append({"kind": "paused", "count": 0, "video_ids": [],
                          "words": "New uploads aren't downloaded (paused in settings)"})

    return {
        "files": out_files,
        "count": sum(1 for f in out_files if f["exists"]),
        "size": total,
        "folder": _folder(f["path"] for f in out_files if f["exists"]),
        "quality": [{"label": k, "count": v} for k, v in sorted(quality.items(), key=lambda kv: -kv[1])],
        "waiting": len(waiting),
        "retention": {"policy": retention or "all", "words": _retention_words(retention),
                      "due": len(due)},
        "attention": attention,
    }


def remove_files(
    rows: List[Dict[str, Any]],
    *,
    delete_file: Callable[[Dict[str, Any]], Any],
    mark_pruned: Callable[[Any, str], Any],
    forget: Callable[[Any], Any],
    today: str,
    keep_in_ledger: bool = True,
) -> Dict[str, Any]:
    """delete these files (through the recycle bin). ``keep_in_ledger``:
    the row stays, pruned, so no scan fetches the video again (the usual
    delete); False forgets it, so it can download again. a file that won't
    go is left in place and its row untouched."""
    removed, freed, failed, undo = 0, 0, [], []
    for row in rows:
        result = delete_file(row)
        ok, size, trash = (result + (None,))[:3] if isinstance(result, tuple) else (bool(result), 0, None)
        if not ok:
            failed.append(row.get("media_id"))
            continue
        removed += 1
        freed += size or 0
        if keep_in_ledger:
            mark_pruned(row.get("id"), today)
            if trash:
                undo.append({"history_id": row.get("id"), "trash_path": trash})
        else:
            forget(row.get("id"))
    return {"removed": removed, "freed": freed, "failed": failed, "undo": undo}
