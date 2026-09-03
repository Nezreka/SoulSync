"""Video system health — one aggregated strip instead of hunting per-page.

Sonarr's health-check idea: cheap, local, no network probes (server/source
connectivity surfaces through the scan flows that actually use them — a
health endpoint that pings Plex on every dashboard load would be its own
problem). Each check returns ok | warning | error; the collection's overall
status is the worst individual one.
"""

from __future__ import annotations

import os
import tempfile

from utils.logging_config import get_logger

logger = get_logger("video.health")

_ROOTS = (("movies_path", "Movie library"), ("tv_path", "TV library"),
          ("youtube_path", "YouTube library"))


def _check(cid, label, status, detail) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail}


def _fmt_gb(v) -> str:
    return "unknown" if v is None else "%.1f GB" % float(v)


def _worse(a: str, b: str) -> str:
    order = {"ok": 0, "warning": 1, "error": 2}
    return b if order.get(b, 0) > order.get(a, 0) else a


def _youtube_cookie_status() -> tuple[str, str]:
    try:
        from core.settings import config_manager
        mode = str(config_manager.get("youtube.cookies_browser", "") or "").strip()
        cookiefile = str(config_manager.get("youtube.cookies_file", "") or "").strip()
    except Exception:   # noqa: BLE001
        logger.debug("youtube cookie health probe failed", exc_info=True)
        return "warning", "cookies: config unreadable"
    if mode == "custom":
        if not cookiefile:
            return "warning", "cookies: custom file not set"
        if not os.path.isfile(cookiefile):
            return "warning", "cookies: file missing"
        return "ok", "cookies: pasted file"
    if mode:
        return "ok", f"cookies: {mode} browser"
    return "ok", "cookies: none"


def _youtube_health_check(db, settings: dict) -> dict:
    """One user-facing YouTube readiness tile: version, auth, disk, recent blockers."""
    status = "ok"
    bits = []

    try:
        from core.ytdlp_update import installed_version
        ver = installed_version()
    except Exception:   # noqa: BLE001
        logger.debug("yt-dlp version health probe failed", exc_info=True)
        ver = None
    if ver:
        bits.append(f"yt-dlp {ver}")
    else:
        status = _worse(status, "error")
        bits.append("yt-dlp unavailable")

    c_status, c_detail = _youtube_cookie_status()
    status = _worse(status, c_status)
    bits.append(c_detail)

    try:
        from core.video.disk_guard import free_gb
        temp_free = free_gb(tempfile.gettempdir())
        bits.append(f"temp: {_fmt_gb(temp_free)} free")
        if temp_free is not None and temp_free < 2:
            status = _worse(status, "warning")

        out = str(db.get_setting("youtube_path") or "").strip()
        if out:
            out_free = free_gb(out)
            if not os.path.isdir(out):
                status = _worse(status, "error")
                bits.append("output: unreachable")
            else:
                bits.append(f"output: {_fmt_gb(out_free)} free")
                try:
                    floor = float(settings.get("min_free_disk_gb") or 0)
                except (TypeError, ValueError):
                    floor = 0
                if out_free is not None and ((floor and out_free < floor) or out_free < 2):
                    status = _worse(status, "warning")
        else:
            bits.append("output: not configured")
    except Exception:   # noqa: BLE001
        logger.debug("youtube disk health probe failed", exc_info=True)
        status = _worse(status, "warning")
        bits.append("disk: probe failed")

    try:
        from core.youtube_errors import failure_summary
        errors = db.youtube_recent_failure_errors(days=3) or []
        if errors:
            summary = failure_summary(errors)
            top = str(summary["dominant"]).replace("_", " ")
            # A download that SUCCEEDED after the last failure settles the question:
            # whatever it was, it is over. Reporting it as a live fault is how a
            # cleared disk kept demanding to be cleared.
            if db.youtube_download_recovered(days=3):
                bits.append("%d recent YouTube failure(s), mostly %s - downloads working again"
                            % (summary["total"], top))
            else:
                # A standing problem the operator has to fix (no disk, no ffmpeg,
                # no cookies) is an ERROR, not the same shade as a few timeouts:
                # it will not clear on its own.
                status = _worse(status, "error" if summary["needs_user_action"] else "warning")
                reason = summary["reason"] or "see YouTube download history"
                bits.append("%d recent YouTube failure(s), mostly %s: %s"
                            % (summary["total"], top, reason))
        else:
            bits.append("recent failures: 0")
    except Exception:   # noqa: BLE001
        logger.debug("youtube failure health probe failed", exc_info=True)
        status = _worse(status, "warning")
        bits.append("recent failures: unreadable")

    return _check("youtube_health", "YouTube", status, "; ".join(bits))


# What each source is called in a snapshot, and how to say it out loud.
_SOURCE_LABELS = {
    "torrent": "Torrent indexers (Prowlarr / EXT.to)",
    "usenet": "Usenet",
    "soulseek": "Soulseek (slskd)",
}


def _source_health_checks(db) -> list:
    """One check per download source that has actually been used lately.

    The distinction that matters: a source that could not RUN is broken, and a
    source that ran and found nothing is working. Those used to look identical
    from the outside - "no results" - which is how a closed slskd port reads as
    "nothing on Soulseek has what you want" for weeks.

    Sources with no recent searches are omitted rather than reported healthy: an
    untried source has told us nothing, and a green tile for one would be a
    claim we cannot make.
    """
    out = []
    try:
        snap = db.source_health_snapshot(days=7) or {}
    except Exception:   # noqa: BLE001 - health must never 500 over one probe
        logger.debug("source health probe failed", exc_info=True)
        return out

    for src, s in sorted(snap.items()):
        label = _SOURCE_LABELS.get(src, src.title())
        # A source is only in the snapshot because it was searched, so there is
        # no zero case to guard: an untried source simply is not here, which is
        # the point - a green tile for one would be a claim we cannot make.
        searches = int(s.get("searches") or 0)
        ran = int(s.get("ran") or 0)
        cid = "source_" + src
        if ran == 0:
            reason = s.get("reason") or "the search could not run"
            out.append(_check(cid, label, "error",
                              "couldn't run any of the last %d searches - %s" % (searches, reason)))
        elif ran < searches:
            reason = s.get("reason") or "the search could not run"
            out.append(_check(cid, label, "warning",
                              "ran %d of the last %d searches - %s" % (ran, searches, reason)))
        elif not s.get("results"):
            # It ran every time and found nothing. Not broken, but worth saying:
            # an indexer set that returns nothing for a week is misconfigured
            # more often than it is unlucky.
            out.append(_check(cid, label, "warning",
                              "ran %d searches and returned no releases at all" % ran))
        elif not s.get("accepted"):
            # The source works and nothing it returns is being grabbed. Reported
            # healthy this is the most misleading tile on the page - 180 found, 0
            # grabbed, green tick - so it warns. It deliberately does NOT name a
            # cause: on Boulder's install four of six rows read "none were this
            # release" (a MATCHING miss, where the profile never judged anything)
            # and only two were the profile turning down a tier. Guessing between
            # those sends the user to change the wrong setting.
            out.append(_check(cid, label, "warning",
                              "found %d releases across %d searches but none were grabbed - "
                              "check the per-row reasons on the wishlist for whether they "
                              "were the wrong release or turned down by your quality profile"
                              % (s.get("results") or 0, ran)))
        else:
            out.append(_check(cid, label, "ok",
                              "%d releases across %d searches, %d passed your quality profile"
                              % (s.get("results") or 0, ran, s.get("accepted") or 0)))
    return out


def _indexer_health_checks(db) -> list:
    """Name the indexers whose results never survive the quality profile.

    NOT "indexers that returned nothing": these stats are built FROM search
    receipts, so an indexer that returns nothing leaves no sample and cannot
    appear here at all. A check for it could never fire — finding that out
    before shipping it is the only reason this docstring exists. Seeing a truly
    idle indexer needs Prowlarr's configured list, which is a network call, and
    health runs on a page load.

    What the receipts CAN show is an indexer that keeps answering with releases
    none of which are ever usable — a tracker carrying the wrong region, the
    wrong tier, or nothing but packs. That is real, actionable, and invisible in
    the transport-level total.
    """
    out = []
    try:
        stats = db.indexer_health_snapshot(days=14) or []
    except Exception:   # noqa: BLE001 - health must never 500 over one probe
        logger.debug("indexer health probe failed", exc_info=True)
        return out
    # With one indexer there is nothing to compare it against, and a single
    # source having a bad fortnight is the transport check's business.
    if len(stats) < 2:
        return out
    # Enough results to mean something: one unusable hit is luck, twenty is a
    # pattern.
    barren = sorted(s["indexer"] for s in stats
                    if s.get("results", 0) >= 20 and not s.get("accepted"))
    if barren:
        out.append(_check("indexers_barren", "Indexers", "warning",
                          "%d of %d returned releases but never one you could use: %s"
                          % (len(barren), len(stats), ", ".join(barren[:6]))))
    return out


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

    # 5) YouTube readiness: always visible, because an OK tile tells users which
    # moving parts are healthy before they start chasing a failed channel/video.
    checks.append(_youtube_health_check(db, settings))

    # 6) the other download sources, read off the receipts each search already
    # leaves. No probing: health runs on a dashboard load and pinging a source
    # that is down is the exact case that hangs the page.
    checks.extend(_source_health_checks(db))

    # 7) individual indexers that are contributing nothing. Only worth a line
    # when there IS one — a healthy indexer set needs no commentary.
    checks.extend(_indexer_health_checks(db))

    order = {"error": 0, "warning": 1, "ok": 2}
    checks.sort(key=lambda c: order.get(c["status"], 3))
    overall = "ok"
    if any(c["status"] == "error" for c in checks):
        overall = "error"
    elif any(c["status"] == "warning" for c in checks):
        overall = "warning"
    return {"status": overall, "checks": checks}
