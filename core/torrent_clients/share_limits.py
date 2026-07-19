"""Push a SoulSync seed goal into the torrent client's own share limits.

The "who enforces the seed goal" toggle has two modes:

  * ``soulsync`` — SoulSync's seeding sweep polls the client and removes the
    torrent when the goal is met (nothing written to the client).
  * ``client``  — SoulSync writes the ratio / seeding-time limit straight into
    the torrent client (qBittorrent's ``setShareLimits``) so the CLIENT enforces
    it, exactly like Radarr/Sonarr. It shows up in the client's share-limit
    dialog and keeps working even if SoulSync is off.

This module is the client-mode writer, shared by the music and video sides.
Goals are configured in SoulSync's own units (share ratio + seed-time in HOURS);
qBittorrent wants MINUTES, and uses -1 to mean "no limit", so this converts.
"""

from __future__ import annotations

from typing import Any

from utils.logging_config import get_logger

logger = get_logger("torrent.share_limits")

# qBittorrent sentinel: -1 = no limit on this criterion.
_NO_LIMIT = -1


def _ratio_limit(ratio_goal: Any) -> float:
    try:
        r = float(ratio_goal or 0)
    except (TypeError, ValueError):
        return _NO_LIMIT
    return r if r > 0 else _NO_LIMIT


def _seeding_time_limit_minutes(time_goal_hours: Any) -> int:
    try:
        h = int(float(time_goal_hours or 0))
    except (TypeError, ValueError):
        return _NO_LIMIT
    return h * 60 if h > 0 else _NO_LIMIT


def push_seed_goal(adapter: Any, torrent_hash: str,
                   ratio_goal: Any, time_goal_hours: Any) -> bool:
    """Write the seed goal into the client as per-torrent share limits.

    Returns True if the client accepted the limits (it now enforces the goal
    itself). Returns False if there's nothing to push, the client doesn't
    support share limits, or the call failed — in which case the caller should
    fall back to recording the grab for SoulSync's own sweep so the goal still
    gets enforced.
    """
    if not adapter or not torrent_hash:
        return False
    fn = getattr(adapter, 'set_share_limits', None)
    if fn is None:
        return False  # this client can't take share limits — fall back to sweep

    ratio_limit = _ratio_limit(ratio_goal)
    seeding_time_limit = _seeding_time_limit_minutes(time_goal_hours)
    if ratio_limit == _NO_LIMIT and seeding_time_limit == _NO_LIMIT:
        return False  # no goal set — nothing to enforce

    try:
        from utils.async_helpers import run_async
        ok = bool(run_async(fn(torrent_hash, ratio_limit, seeding_time_limit)))
        if ok:
            logger.info("seed goal pushed to client for %s (ratio=%s, seed_time_min=%s)",
                        torrent_hash[:8], ratio_limit, seeding_time_limit)
        else:
            logger.warning("client rejected share limits for %s; caller should sweep instead",
                           torrent_hash[:8])
        return ok
    except Exception:   # noqa: BLE001 - fall back to the sweep on any client error
        logger.warning("set_share_limits failed for %s; caller should sweep instead",
                       torrent_hash[:8], exc_info=True)
        return False
