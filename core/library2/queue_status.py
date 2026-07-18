"""Read-only live download-queue status for Library-v2 rows (docs §73, I6).

Scans the two existing in-flight tracking structures — ``download_tasks``
(batch/wishlist path) and ``matched_downloads_context`` (manual-grab path,
docs §71) — filtered to a caller-supplied set of lib2 track ids. Both already
carry the lib2 track/album id (``track_info.source_info`` /
``lib2_entity``); this module just surfaces it. Terminal/idle tracks are
simply absent from the result — there is no persisted "last known outcome"
here, the existing quality/verification badges already cover completed and
failed results (docs §73.2).

Deliberately dependency-free of ``web_server`` (mirrors ``core/downloads/
status.py``'s ``StatusDeps`` precedent): ``make_context_key`` and
``get_cached_transfer_data`` are injected by the caller.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)

# Raw download_tasks status -> the four buckets shown on a Library-v2 row.
# Terminal statuses (completed/failed/cancelled/not_found/skipped/
# already_owned) are intentionally absent — they map to no badge at all.
_TASK_STATUS_BUCKET = {
    "pending": "queued",
    "queued": "queued",
    "searching": "searching",
    "downloading": "downloading",
    "post_processing": "processing",
}


def _classify_live_state(state: Optional[str]) -> str:
    """Best-effort bucket for a raw slskd/streaming transfer state string.

    Manual grabs (the only caller of this) never go through a 'searching'
    phase — the user already picked the candidate — so anything that isn't
    clearly in-progress falls back to 'queued', not 'searching'.
    """
    s = (state or "").lower()
    if "progress" in s or "downloading" in s:
        return "downloading"
    return "queued"


def get_queue_status(
    track_ids: Iterable[int],
    *,
    make_context_key: Callable[[str, str], str],
    get_cached_transfer_data: Callable[[], Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Live queue status for the given lib2 track ids.

    Returns ``{"tracks": {track_id: {"status", "progress_pct"}}, "albums":
    {album_id: active_track_count}}`` — both keys always present, possibly
    empty. The album roll-up comes from the same in-memory scan (no extra
    DB query) since both tracking structures already carry the album id
    alongside the track id.
    """
    wanted = {int(t) for t in track_ids}
    tracks: Dict[int, Dict[str, Any]] = {}
    albums: Dict[int, int] = {}
    if not wanted:
        return {"tracks": {}, "albums": {}}

    live_transfers: Optional[Dict[str, Any]] = None

    def _live_lookup(username: Any, filename: Any) -> Optional[Dict[str, Any]]:
        nonlocal live_transfers
        if not username or not filename:
            return None
        if live_transfers is None:
            live_transfers = get_cached_transfer_data() or {}
        return live_transfers.get(make_context_key(username, filename))

    def _record(track_id: int, album_id: Any, status: str, progress_pct: int) -> None:
        tracks[track_id] = {"status": status, "progress_pct": progress_pct}
        if album_id is not None:
            albums[int(album_id)] = albums.get(int(album_id), 0) + 1

    with tasks_lock:
        tasks_snapshot = list(download_tasks.values())
    for task in tasks_snapshot:
        track_info = task.get("track_info") or {}
        source_info = track_info.get("source_info") or {}
        raw_track_id = source_info.get("lib2_track_id")
        if raw_track_id is None or int(raw_track_id) not in wanted:
            continue
        track_id = int(raw_track_id)
        bucket = _TASK_STATUS_BUCKET.get(task.get("status"))
        if bucket is None:
            continue  # terminal/unknown — omit entirely

        progress_pct = 0
        if bucket == "processing":
            progress_pct = 95
        elif bucket == "downloading":
            live_info = _live_lookup(
                task.get("username") or track_info.get("username"),
                task.get("filename") or track_info.get("filename"),
            )
            if live_info:
                progress_pct = int(live_info.get("percentComplete") or 0)

        _record(track_id, source_info.get("lib2_album_id"), bucket, progress_pct)

    with matched_context_lock:
        contexts_snapshot = list(matched_downloads_context.values())
    for context in contexts_snapshot:
        lib2_entity = context.get("lib2_entity") or {}
        raw_track_id = lib2_entity.get("track_id")
        if raw_track_id is None:
            continue
        track_id = int(raw_track_id)
        # A batch-task entry for the same track already won (more precise
        # status machine); don't let a shadow manual-grab context override it.
        if track_id not in wanted or track_id in tracks:
            continue

        search_result = context.get("search_result") or {}
        live_info = _live_lookup(search_result.get("username"), search_result.get("filename"))
        bucket = "queued"
        progress_pct = 0
        if live_info:
            bucket = _classify_live_state(live_info.get("state"))
            if bucket == "downloading":
                progress_pct = int(live_info.get("percentComplete") or 0)

        _record(track_id, lib2_entity.get("album_id"), bucket, progress_pct)

    return {"tracks": tracks, "albums": albums}
