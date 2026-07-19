"""Torrent seeding lifecycle (arr-parity P5).

The importer COPIES a finished torrent's file into the library, so the client
keeps seeding — and until now nothing ever let go: every grab seeded forever
(or until the user cleaned the client by hand). Radarr manages the tail: seed
until the ratio/time goals are met, then remove the torrent from the client.

This sweep does exactly that for completed video torrent grabs:

  · goals live in the video download config (``seed_ratio_goal`` /
    ``seed_time_goal_hours``) — BOTH default 0, which means the sweep is OFF
    and behavior is unchanged; managing someone's torrent client is opt-in
  · ratio/seeding-time come from the client (qBittorrent reports both); when
    a client doesn't, the time goal falls back to the download row's
    ``completed_at`` age — a conservative floor (import time < seed time)
  · goals met → remove the torrent from the client (``seed_remove_data``,
    default on, also deletes the CLIENT'S copy — the library copy is separate
    and never touched) and mark the row ``seed_released``
  · a torrent the client no longer knows is marked released (nothing to manage)

Usenet never seeds; slskd has no concept of it — torrent rows only.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video.seeding")

_running = False
_lock = threading.Lock()


def is_running() -> bool:
    return _running


def _completed_age_hours(dl: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    raw = dl.get("completed_at") or dl.get("updated_at")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 3600.0)


def goals_met(status: Any, dl: Dict[str, Any], cfg: Dict[str, Any],
              now: Optional[datetime] = None) -> Optional[str]:
    """Why this torrent may be released, or None to keep seeding. Pure."""
    ratio_goal = float(cfg.get("seed_ratio_goal") or 0)
    time_goal_h = int(cfg.get("seed_time_goal_hours") or 0)
    if ratio_goal and getattr(status, "ratio", None) is not None \
            and status.ratio >= ratio_goal:
        return "ratio %.2f reached the %.2f goal" % (status.ratio, ratio_goal)
    if time_goal_h:
        st = getattr(status, "seeding_time", None)
        if st is not None and st >= time_goal_h * 3600:
            return "seeded %dh (goal %dh)" % (st // 3600, time_goal_h)
        if st is None:
            age = _completed_age_hours(dl, now)
            if age is not None and age >= time_goal_h:
                return "imported %dh ago (goal %dh; client reports no seed time)" % (age, time_goal_h)
    return None


def sweep() -> Dict[str, Any]:
    """One pass over completed torrent grabs. Returns
    {status, checked, released, seeding, skipped?}."""
    global _running
    with _lock:
        if _running:
            return {"status": "skipped", "reason": "already_running"}
        _running = True
    try:
        return _sweep_inner()
    finally:
        with _lock:
            _running = False


def _sweep_inner() -> Dict[str, Any]:
    from api.video import get_video_db
    from core.video import download_config
    from core.video.client_download import _get_status
    db = get_video_db()
    cfg = download_config.load(db)
    if not cfg.get("seed_ratio_goal") and not cfg.get("seed_time_goal_hours"):
        return {"status": "skipped", "reason": "no_goals_set", "checked": 0, "released": 0}

    # Client mode (arr-style): hand the ratio/time goal to the torrent client so
    # IT enforces, then release the row. If the push fails or the client can't
    # take share limits (non-qBit), fall through to SoulSync's own management so
    # the goal still gets enforced — never leave a grab unmanaged.
    client_mode = cfg.get("seed_mode") == "client"
    adapter = None
    push_seed_goal = None
    if client_mode:
        from core.torrent_clients import get_active_adapter
        from core.torrent_clients.share_limits import push_seed_goal as _psg
        adapter = get_active_adapter()
        push_seed_goal = _psg

    rows = db.torrents_awaiting_seed_release()
    released = seeding = 0
    for dl in rows:
        ref = str(dl["client_ref"])

        if client_mode and push_seed_goal(adapter, ref, cfg.get("seed_ratio_goal"),
                                          cfg.get("seed_time_goal_hours")):
            db.update_video_download(dl["id"], seed_released=1)
            released += 1
            logger.info("seeding: handed '%s' to the torrent client (client mode)", dl.get("title"))
            continue
        # soulsync mode, OR client-mode push failed → SoulSync polls + removes.

        status = _get_status("torrent", ref)
        if status is None:
            # client forgot it (user removed by hand, or a restart lost it) —
            # nothing left to manage
            db.update_video_download(dl["id"], seed_released=1)
            released += 1
            continue
        reason = goals_met(status, dl, cfg)
        if not reason:
            seeding += 1
            continue
        if _remove(ref, bool(cfg.get("seed_remove_data", True))):
            db.update_video_download(dl["id"], seed_released=1)
            released += 1
            logger.info("seeding: released '%s' — %s", dl.get("title"), reason)
        else:
            seeding += 1   # removal failed → try again next sweep
    return {"status": "completed", "checked": len(rows),
            "released": released, "seeding": seeding}


def _remove(ref: str, delete_files: bool) -> bool:
    """Remove one torrent from the shared client. The delete only ever touches
    the CLIENT'S download copy — the imported library file is a separate copy."""
    try:
        from core.torrent_clients import get_active_adapter
        from core.video.client_download import _run
        adapter = get_active_adapter()
        if adapter is None:
            return False
        return bool(_run(adapter.remove(ref, delete_files=delete_files)))
    except Exception:   # noqa: BLE001 - a failed removal retries next sweep
        logger.warning("seeding: removal failed for %s", ref, exc_info=True)
        return False
