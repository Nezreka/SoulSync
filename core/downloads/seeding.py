"""Torrent seeding lifecycle for MUSIC grabs (mirror of ``core/video/seeding.py``).

The importer COPIES a finished torrent's file into the library, so the client
keeps seeding — and nothing ever lets go: every music torrent grab seeds forever
(or until the user cleans the client by hand). Radarr/Sonarr manage the tail:
seed until the ratio/time goals are met, then remove the torrent from the client.
This sweep does exactly that for completed MUSIC torrent grabs.

  · goals live in the music download config under the ``torrent_client`` section
    (``seed_ratio_goal`` / ``seed_time_goal_hours``) — BOTH default 0, which
    means the sweep is OFF and behavior is unchanged; managing someone's torrent
    client is strictly opt-in
  · ratio/seeding-time come from the client (qBittorrent reports both); when a
    client doesn't, the time goal falls back to the recorded grab's
    ``completed_at`` age — a conservative floor (import time < seed time)
  · goals met → remove the torrent from the client (``seed_remove_data``,
    default on, also deletes the CLIENT'S copy — the library copy is a separate
    copy made at import and is never touched) and mark the grab ``seed_released``
  · a torrent the client no longer knows (``get_status`` returns None) is marked
    released — nothing left to manage. A transient client error (an exception,
    not a None) is left alone and retried next sweep, so a flaky/unreachable
    client can never trigger an erroneous release.

Only ever touches torrents recorded in ``torrent_seed_grabs`` (grabs SoulSync
itself completed) — never a torrent the user added, and never the video side's
grabs. Usenet never seeds; slskd has no concept of it — torrent grabs only.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("downloads.seeding")

_running = False
_lock = threading.Lock()


def is_running() -> bool:
    return _running


def _coerce_ratio(v: Any) -> float:
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0


def _coerce_hours(v: Any) -> int:
    try:
        return max(0, int(float(v)))
    except (TypeError, ValueError):
        return 0


def _load_cfg() -> Dict[str, Any]:
    """Read + normalize the seeding goals. A malformed stored value defaults to
    OFF (0) rather than crashing the sweep — the goals are opt-in either way."""
    from config.settings import config_manager
    return {
        "seed_ratio_goal": _coerce_ratio(config_manager.get("torrent_client.seed_ratio_goal", 0)),
        "seed_time_goal_hours": _coerce_hours(config_manager.get("torrent_client.seed_time_goal_hours", 0)),
        "seed_remove_data": bool(config_manager.get("torrent_client.seed_remove_data", True)),
    }


def _completed_age_hours(dl: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    raw = dl.get("completed_at")
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
    """One pass over recorded music torrent grabs. Returns
    {status, checked, released, seeding, ...}."""
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
    from database.music_database import get_database
    db = get_database()
    cfg = _load_cfg()
    if not float(cfg.get("seed_ratio_goal") or 0) and not int(cfg.get("seed_time_goal_hours") or 0):
        return {"status": "skipped", "reason": "no_goals_set", "checked": 0, "released": 0}

    from core.torrent_clients import get_active_adapter
    from utils.async_helpers import run_async
    adapter = get_active_adapter()
    if adapter is None:
        return {"status": "skipped", "reason": "no_torrent_client", "checked": 0, "released": 0}

    rows = db.torrents_awaiting_seed_release()
    released = seeding = 0
    for dl in rows:
        ref = str(dl["torrent_hash"])
        try:
            status = run_async(adapter.get_status(ref))
        except Exception:   # noqa: BLE001 - transient client error: leave it, retry next sweep
            logger.debug("seeding: status check failed for %s, retrying next sweep",
                         ref[:8], exc_info=True)
            seeding += 1
            continue
        if status is None:
            # client genuinely no longer knows it (user removed by hand, or a
            # restart lost it) — nothing left to manage
            db.mark_torrent_seed_released(dl["id"])
            released += 1
            continue
        reason = goals_met(status, dl, cfg)
        if not reason:
            seeding += 1
            continue
        if _remove(adapter, ref, bool(cfg.get("seed_remove_data", True))):
            db.mark_torrent_seed_released(dl["id"])
            released += 1
            logger.info("seeding: released '%s' — %s", dl.get("title") or ref[:8], reason)
        else:
            seeding += 1   # removal failed → try again next sweep
    return {"status": "completed", "checked": len(rows),
            "released": released, "seeding": seeding}


def _remove(adapter: Any, ref: str, delete_files: bool) -> bool:
    """Remove one torrent from the shared client. The delete only ever touches
    the CLIENT'S download copy — the imported library file is a separate copy."""
    from utils.async_helpers import run_async
    try:
        return bool(run_async(adapter.remove(ref, delete_files=delete_files)))
    except Exception:   # noqa: BLE001 - a failed removal retries next sweep
        logger.warning("seeding: removal failed for %s", ref[:8], exc_info=True)
        return False
