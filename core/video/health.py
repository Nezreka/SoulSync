"""Video system health — one aggregated strip instead of hunting per-page.

Sonarr's health-check idea: cheap, local, no network probes (server/source
connectivity surfaces through the scan flows that actually use them — a
health endpoint that pings Plex on every dashboard load would be its own
problem). Each check returns ok | warning | error; the collection's overall
status is the worst individual one.
"""

from __future__ import annotations

import os

from utils.logging_config import get_logger

logger = get_logger("video.health")

_ROOTS = (("movies_path", "Movie library"), ("tv_path", "TV library"),
          ("youtube_path", "YouTube library"))


def _check(cid, label, status, detail) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail}


def collect(db) -> dict:
    """{status, checks: [...]} — every check always present, worst-first sort."""
    checks = []

    # 1) library folders: set-but-unreachable = a down mount (error); unset is fine
    from core.video import organization
    settings = organization.load(db)
    for key, label in _ROOTS:
        path = str(db.get_setting(key) or "").strip()
        if not path:
            continue
        if not os.path.isdir(path):
            checks.append(_check(key, label, "error",
                                 f"{path} is unreachable — a drive or mount may be down"))
        else:
            from core.video.disk_guard import free_gb
            free = free_gb(path)
            floor = 0
            try:
                floor = float(settings.get("min_free_disk_gb") or 0)
            except (TypeError, ValueError):
                pass
            if free is not None and floor and free < floor:
                checks.append(_check(key + "_space", label, "warning",
                                     "%.1f GB free — under your %.0f GB minimum; new grabs are paused"
                                     % (free, floor)))
            elif free is not None and free < 2:
                checks.append(_check(key + "_space", label, "warning",
                                     "%.1f GB free — the drive is nearly full" % free))

    # 2) recycle override folder (auto per-library folders create themselves)
    override = str(settings.get("recycle_path") or "").strip()
    if settings.get("recycle_deletes", True) and override and not os.path.isdir(override):
        checks.append(_check("recycle_path", "Recycle bin", "warning",
                             f"custom folder {override} doesn't exist — deletes fall back per-library"))

    # 3) maintenance jobs that errored on their last run this process
    try:
        from core.video.repair.worker import get_video_repair_worker
        snap = get_video_repair_worker(db).progress_snapshot() or {}
        bad = [s.get("display_name") or j for j, s in snap.items() if s.get("status") == "error"]
        if bad:
            checks.append(_check("repair_errors", "Library Maintenance", "warning",
                                 "job(s) errored on their last run: " + ", ".join(sorted(bad))))
    except Exception:   # noqa: BLE001 - health must never 500 over one probe
        logger.debug("repair health probe failed", exc_info=True)

    # 4) downloads in flight with no monitor thread (a restart raced the queue)
    try:
        from core.video import download_monitor as mon
        active = db.get_active_video_downloads() or []
        slskd_active = [d for d in active if str(d.get("source") or "").lower() != "youtube"]
        if slskd_active and not mon._started:
            checks.append(_check("monitor", "Download monitor", "warning",
                                 f"{len(slskd_active)} download(s) in flight but the monitor "
                                 "isn't running — restart or re-trigger a download"))
    except Exception:   # noqa: BLE001
        logger.debug("monitor health probe failed", exc_info=True)

    order = {"error": 0, "warning": 1, "ok": 2}
    checks.sort(key=lambda c: order.get(c["status"], 3))
    overall = "ok"
    if any(c["status"] == "error" for c in checks):
        overall = "error"
    elif any(c["status"] == "warning" for c in checks):
        overall = "warning"
    return {"status": overall, "checks": checks}
