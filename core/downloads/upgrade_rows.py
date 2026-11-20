"""Quality upgrades on the Downloads page and the library badges.

A task that replaces a file the library already has carries ``upgrade_of``:
what that file is now (format, bitrate, bit depth, sample rate, and a short
label like "MP3 320"). The Downloads row reads "MP3 320 → FLAC", the
Upgrades filter picks these rows out, and the artist page marks a track
whose upgrade is already on its way as "upgrade queued".
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from utils.logging_config import get_logger

logger = get_logger("downloads.upgrade_rows")

TASK_KEY = "upgrade_of"
_LIVE = {"pending", "queued", "searching", "downloading", "post_processing"}
_LOSSLESS = {"flac", "alac", "wav", "aiff", "aif", "ape", "wv", "dsf"}


def short_label(fmt: Any, bitrate: Any = None, bit_depth: Any = None,
                sample_rate: Any = None) -> str:
    """'MP3 320', 'AAC 256', 'FLAC', 'FLAC 24-bit'. Empty for no format."""
    name = str(fmt or "").strip().upper()
    if not name or name == "UNKNOWN":
        return ""
    if name.lower() in _LOSSLESS:
        try:
            depth = int(bit_depth or 0)
        except (TypeError, ValueError):
            depth = 0
        return f"{name} {depth}-bit" if depth and depth != 16 else name
    try:
        kbps = float(bitrate or 0)
    except (TypeError, ValueError):
        kbps = 0
    if kbps > 10000:            # a media server's bps
        kbps = kbps / 1000
    return f"{name} {int(round(kbps))}" if kbps else name


def upgrade_of_file(file_row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """``upgrade_of`` for a lib2 file row, or None when there is no file."""
    if not file_row or not file_row.get("format"):
        return None
    bitrate = file_row.get("bitrate")
    try:
        if bitrate and float(bitrate) > 10000:
            bitrate = int(round(float(bitrate) / 1000))
    except (TypeError, ValueError):
        bitrate = None
    out = {
        "format": str(file_row.get("format") or "").lower(),
        "bitrate": bitrate,
        "bit_depth": file_row.get("bit_depth"),
        "sample_rate": file_row.get("sample_rate"),
    }
    out["label"] = short_label(out["format"], out["bitrate"], out["bit_depth"])
    return out


def upgrade_of_tracks(database: Any, track_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """``{lib2 track id: upgrade_of}`` for tracks that have a primary file."""
    ids = sorted({int(t) for t in track_ids if t})
    if not ids:
        return {}
    from core.library2.track_files import primary_file_rows
    conn = database._get_connection()
    try:
        rows = primary_file_rows(conn, ids)
    finally:
        conn.close()
    out = {}
    for tid, row in (rows or {}).items():
        found = upgrade_of_file(dict(row) if row is not None else None)
        if found:
            out[int(tid)] = found
    return out


def stamp_tasks(database: Any, pairs: List[Tuple[str, int]]) -> int:
    """Give each new upgrade task its ``upgrade_of``. ``pairs`` is
    ``(task_id, lib2 track id)``. The file read happens off tasks_lock."""
    if not pairs:
        return 0
    try:
        found = upgrade_of_tracks(database, [tid for _task, tid in pairs])
    except Exception as e:  # noqa: BLE001 - decoration only, never fails a batch
        logger.debug("upgrade rows: file lookup failed: %s", e)
        return 0
    from core.runtime_state import download_tasks, tasks_lock
    stamped = 0
    with tasks_lock:
        for task_id, track_id in pairs:
            task = download_tasks.get(task_id)
            info = found.get(int(track_id))
            if task is not None and info:
                task[TASK_KEY] = dict(info, lib2_track_id=int(track_id))
                stamped += 1
    return stamped


def queued_upgrade_track_ids() -> Set[int]:
    """lib2 track ids with an upgrade download in flight right now."""
    from core.imports.upgrade_intent import CONTEXT_KEY, is_upgrade_intent
    from core.runtime_state import download_tasks, tasks_lock
    out: Set[int] = set()
    with tasks_lock:
        for task in download_tasks.values():
            if task.get("status") not in _LIVE:
                continue
            intent = task.get(CONTEXT_KEY)
            if is_upgrade_intent(intent):
                out.add(int(intent.track_id))
                continue
            info = task.get(TASK_KEY)
            if isinstance(info, dict) and info.get("lib2_track_id"):
                out.add(int(info["lib2_track_id"]))
    return out
