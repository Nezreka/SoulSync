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

from core.downloads import seed_rules

from utils.logging_config import get_logger

logger = get_logger("video.seeding")

# How many awaiting rows to read, and how many of those we'll actually poll the
# client about in one pass. The gap between them is headroom for exempt rows.
MAX_ROWS_PER_SWEEP = 1000
MAX_POLLS_PER_SWEEP = 100

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


def is_pack(dl: Dict[str, Any]) -> bool:
    """True for a season pack grab. Sonarr keeps packs seeding longer than
    single episodes, so they get their own goal.

    Delegates to season_pack.is_pack_download rather than re-deciding it here.
    A second definition drifted immediately: it missed scope 'pack' and wrongly
    counted scope 'show', which the real one settles off ``kind`` instead."""
    from core.video.season_pack import is_pack_download
    return bool(is_pack_download(dl or {}))


def goal_for(dl: Dict[str, Any], cfg: Dict[str, Any]):
    """The (ratio, hours) goal this grab is judged against. Pure."""
    return seed_rules.effective_goal(cfg, (dl or {}).get("indexer_id"), is_pack=is_pack(dl))


def goals_met(status: Any, dl: Dict[str, Any], cfg: Dict[str, Any],
              now: Optional[datetime] = None) -> Optional[str]:
    """Why this torrent may be released, or None to keep seeding. Pure."""
    ratio_goal, time_goal_h = goal_for(dl, cfg)
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
    """One pass over completed torrent grabs. Every return carries the full
    {status, checked, released, seeding} key set (plus reason when skipped) —
    one shape, so callers may index directly. A concurrent sweep left in
    flight (a background automation thread) used to return a keyless skip
    dict and KeyError any caller that did."""
    global _running
    with _lock:
        if _running:
            return {"status": "skipped", "reason": "already_running",
                    "checked": 0, "released": 0, "seeding": 0}
        _running = True
    try:
        return _sweep_inner()
    finally:
        with _lock:
            _running = False


def _status(ref: str):
    """The client's word on one torrent, as ``(status, answered)``.

    ``answered`` is False when the CLIENT never answered: no client configured,
    a refused connection, a timeout, bad credentials. That is emphatically NOT
    "the torrent is gone", and the difference decides whether a row is marked
    ``seed_released`` — which is terminal, the awaiting query filters released
    rows out for good.

    Going through ``client_download._get_status`` collapsed both cases into
    None, so a qBittorrent that was merely down would silently abandon seed
    management for every row this sweep touched, forever. The music sweep has
    guarded exactly this since it shipped; this side had not.
    """
    from core.torrent_clients import get_active_adapter
    from utils.async_helpers import run_async
    adapter = get_active_adapter()
    if adapter is None:
        return None, False
    try:
        return run_async(adapter.get_status(ref)), True
    except Exception:   # noqa: BLE001 - client hiccup: keep managing, retry next sweep
        logger.debug("seeding: status poll failed for %s, retrying next sweep",
                     ref[:8], exc_info=True)
        return None, False


def _sweep_inner() -> Dict[str, Any]:
    from api.video import get_video_db
    from core.video import download_config
    db = get_video_db()
    cfg = download_config.load(db)
    # a user who set rules on one tracker and left the globals blank still
    # wants a sweep. checking only the globals meant it never ran for them.
    if not seed_rules.any_goal_set(cfg):
        return {"status": "skipped", "reason": "no_goals_set",
                "checked": 0, "released": 0, "seeding": 0}

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

    # Fetch wide, poll narrow. An exempt row (its indexer has no goal) is never
    # released, so it sits at the front of an `ORDER BY id LIMIT n` window
    # forever — collect enough of them and no newer torrent is ever swept
    # again. Exempt rows are free to skip, so only the ones we actually poll
    # count against the budget.
    rows = db.torrents_awaiting_seed_release(limit=MAX_ROWS_PER_SWEEP)
    released = seeding = exempt = deferred = 0
    polled = 0
    for dl in rows:
        ref = str(dl["client_ref"])

        row_ratio, row_hours = goal_for(dl, cfg)
        if client_mode and push_seed_goal(adapter, ref, row_ratio, row_hours):
            db.update_video_download(dl["id"], seed_released=1)
            released += 1
            logger.info("seeding: handed '%s' to the torrent client (client mode)", dl.get("title"))
            continue
        # soulsync mode, OR client-mode push failed → SoulSync polls + removes.

        if not row_ratio and not row_hours:
            # this tracker is exempt (or nothing applies to it). leave it
            # seeding forever, that is what "no goal" means. costs no client
            # call, so it doesn't spend the poll budget.
            exempt += 1
            continue

        if polled >= MAX_POLLS_PER_SWEEP:
            deferred += 1
            continue
        polled += 1

        status, answered = _status(ref)
        if not answered:
            # the client did not answer. leave the row exactly as it is and
            # try again next sweep — never read silence as "it's gone".
            seeding += 1
            continue
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
    if deferred:
        # never let a bounded pass read as "checked everything"
        logger.info("seeding: %d torrent(s) deferred to the next sweep (polled %d)",
                    deferred, polled)
    out = {"status": "completed", "checked": len(rows),
           "released": released, "seeding": seeding + exempt}
    if exempt:
        out["exempt"] = exempt
    if deferred:
        out["deferred"] = deferred
    return out


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
