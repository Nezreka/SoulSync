"""Video Library Maintenance API — the music /api/repair surface, video-scoped.

Same route set, same response shapes (see api/repair.py): status/toggle/
pause/resume, jobs list + per-job toggle/settings/run/stop, findings list/
counts/groups/finding-types/fix/resolve/dismiss/reopen/bulk/bulk-fix(-start)/
clear, history, and a progress snapshot for page load (the socket event
'video:repair:progress' keeps it live afterwards). one client, the React
Tools page, drives both sides, so a shape that differs here is a bug there.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.repair")


def _worker():
    from core.video.repair.worker import get_video_repair_worker

    from . import get_video_db
    return get_video_repair_worker(get_video_db())


def _int(val, default):
    try:
        if val is None or str(val).strip().lower() in ("", "undefined", "null", "nan"):
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def finding_poster(item: dict):
    """The poster to show beside a video finding, or None.

    The finding says what it is about: a poster it already carries, or the
    movie or show it names. A bare number with nothing saying which it is
    (movie or show) gets no poster rather than the wrong one.
    """
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    poster = str(details.get("poster_url") or details.get("thumb_url") or "").strip()
    if poster:
        if poster.startswith("http") or poster.startswith("/api/"):
            return poster
        return "https://image.tmdb.org/t/p/w342" + poster

    def _id(value):
        text = str(value if value is not None else "").strip()
        return text if text.isdigit() else None

    if _id(details.get("movie_id")):
        return f"/api/video/poster/movie/{_id(details.get('movie_id'))}"
    if _id(details.get("show_id")):
        return f"/api/video/poster/show/{_id(details.get('show_id'))}"
    kind = str(details.get("kind") or item.get("entity_type") or "").lower()
    library_id = _id(details.get("library_id")) or (
        _id(item.get("entity_id")) if kind in ("movie", "show") else None)
    if library_id and kind in ("movie", "show", "episode", "season"):
        return f"/api/video/poster/{'movie' if kind == 'movie' else 'show'}/{library_id}"
    return None


# how many of a type's newest open findings are read for its posters, and how
# many distinct ones the inbox row shows (the music side's numbers)
_GROUP_ART_SAMPLE = 24
_GROUP_ART_SHOWN = 4


def _with_schedules(jobs: list) -> list:
    """the schedule lives on each job's system automation (in the app db,
    where the shared automation engine keeps its rows). hand the tools page the
    automation id and its interval, so the cadence editor writes there like
    it does for music. without a row the job keeps its own config values."""
    try:
        from core.automation.migrate_repair_jobs import system_job_schedules
        schedules = system_job_schedules(None, "video_run_repair_job")
    except Exception:   # noqa: BLE001 - schedules are a nicety, the list isn't
        logger.debug("could not read video job schedules", exc_info=True)
        return jobs
    for job in jobs:
        sched = schedules.get(job.get("job_id"))
        if not sched:
            continue
        job["automation_id"] = sched.get("automation_id")
        if sched.get("interval_hours"):
            job["interval_hours"] = sched["interval_hours"]
        if sched.get("next_run"):
            job["next_run"] = sched["next_run"]
    return jobs


_VIEW_KEYS = ("job_id", "status", "severity", "finding_type", "q")


def _view_filters(body) -> dict:
    """the filters of the view a selection was made in (``scope`` in the
    body). fix selected and dismiss selected act only on ids that view still
    lists, checked here (review 5 W3-TT, same contract as music)."""
    scope = body.get("scope") if isinstance(body, dict) else None
    if not isinstance(scope, dict):
        scope = {}
    return {key: (scope.get(key) or None) for key in _VIEW_KEYS}


def register_routes(bp):
    @bp.route("/repair/status", methods=["GET"])
    def video_repair_status():
        return jsonify(_worker().get_stats())

    @bp.route("/repair/toggle", methods=["POST"])
    def video_repair_toggle():
        w = _worker()
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", not w.master_enabled()))
        w.set_master(enabled)
        # #1289 item 12: bridge to the automation engine's metadata so
        # scheduled video jobs honor the master toggle.
        try:
            from web_server import get_database
            get_database().set_metadata('automation_master_video_enabled',
                                        '1' if enabled else '0')
        except Exception:
            pass
        return jsonify({"enabled": enabled})

    @bp.route("/repair/pause", methods=["POST"])
    def video_repair_pause():
        _worker().pause()
        return jsonify({"status": "paused"})

    @bp.route("/repair/resume", methods=["POST"])
    def video_repair_resume():
        _worker().resume()
        return jsonify({"status": "running"})

    @bp.route("/repair/jobs", methods=["GET"])
    def video_repair_jobs():
        return jsonify({"jobs": _with_schedules(_worker().get_all_job_info())})

    @bp.route("/repair/jobs/<job_id>/toggle", methods=["POST"])
    def video_repair_job_toggle(job_id):
        w = _worker()
        cfg = w.job_config(job_id)
        if not cfg:
            return jsonify({"error": "unknown job"}), 404
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", not cfg["enabled"]))
        w.set_job_config(job_id, enabled=enabled)
        # #1289 item 12: bridge to the system automation row so the engine's
        # scheduler honors the toggle. Without this a disabled video job keeps
        # running on its schedule.
        try:
            import web_server
            from core.automation.migrate_repair_jobs import set_system_job_enabled
            set_system_job_enabled(web_server.get_database(), web_server.automation_engine,
                                   job_id, enabled, "video_run_repair_job")
        except Exception:
            logger.debug("Could not bridge video job toggle for %s", job_id, exc_info=True)
        return jsonify({"job_id": job_id, "enabled": enabled})

    @bp.route("/repair/jobs/<job_id>/settings", methods=["PUT"])
    def video_repair_job_settings(job_id):
        w = _worker()
        if not w.job_config(job_id):
            return jsonify({"error": "unknown job"}), 404
        body = request.get_json(silent=True) or {}
        w.set_job_config(job_id, interval_hours=body.get("interval_hours"),
                         settings=body.get("settings"))
        return jsonify({"success": True})

    @bp.route("/repair/jobs/<job_id>/run", methods=["POST"])
    def video_repair_job_run(job_id):
        w = _worker()
        w.start()   # ensure the scheduler thread exists (force queue drains even when disabled)
        if not w.run_job_now(job_id):
            return jsonify({"error": "unknown job"}), 404
        return jsonify({"success": True, "job_id": job_id})

    @bp.route("/repair/jobs/<job_id>/stop", methods=["POST"])
    def video_repair_job_stop(job_id):
        res = _worker().stop_current_job(job_id)
        return jsonify({"job_id": job_id, **res})

    @bp.route("/repair/findings", methods=["GET"])
    def video_repair_findings():
        from . import get_video_db
        q = request.args
        result = get_video_db().repair_get_findings(
            job_id=q.get("job_id") or None, status=q.get("status") or None,
            severity=q.get("severity") or None, finding_type=q.get("finding_type") or None,
            sort=q.get("sort") or None, q=q.get("q") or None,
            page=_int(q.get("page"), 0), limit=_int(q.get("limit"), 50))
        # the row's picture, under the name the shared findings list reads
        for item in result.get("items") or []:
            poster = finding_poster(item)
            if poster:
                item["album_thumb_url"] = poster
        return jsonify(result)

    @bp.route("/repair/findings/counts", methods=["GET"])
    def video_repair_findings_counts():
        from . import get_video_db
        return jsonify(get_video_db().repair_counts())

    @bp.route("/repair/findings/groups", methods=["GET"])
    def video_repair_findings_groups():
        # the inbox's job / severity filters: a row counts what its bulk
        # buttons act on (review 5 W3-TT)
        from . import get_video_db
        job_id = request.args.get("job_id") or None
        severity = request.args.get("severity") or None
        groups = _worker().get_finding_groups(job_id=job_id, severity=severity)
        # a few posters per type, off its newest open findings
        for group in groups:
            group["art"] = []
            if not group.get("pending"):
                continue
            try:
                sample = get_video_db().repair_get_findings(
                    job_id=job_id, severity=severity, status="pending",
                    finding_type=group.get("finding_type"), sort="newest",
                    page=0, limit=_GROUP_ART_SAMPLE)
                for item in sample.get("items") or []:
                    poster = finding_poster(item)
                    if poster and poster not in group["art"]:
                        group["art"].append(poster)
                        if len(group["art"]) >= _GROUP_ART_SHOWN:
                            break
            except Exception as exc:  # noqa: BLE001 - posters are not worth a 500
                logger.debug("video group posters unavailable: %s", exc)
        return jsonify({"groups": groups})

    @bp.route("/repair/finding-types", methods=["GET"])
    def video_repair_finding_types():
        return jsonify({"types": _worker().get_finding_type_catalog()})

    @bp.route("/repair/findings/<int:fid>/reopen", methods=["POST"])
    def video_repair_finding_reopen(fid):
        if not _worker().reopen_finding(fid):
            return jsonify({"success": False,
                            "error": "Finding not found or already pending"}), 404
        return jsonify({"success": True})

    @bp.route("/repair/findings/reopen-all", methods=["POST"])
    def video_repair_findings_reopen_all():
        body = request.get_json(silent=True) or {}
        n = _worker().reopen_findings_bulk(job_id=body.get("job_id"),
                                           finding_type=body.get("finding_type"),
                                           severity=body.get("severity") or None)
        return jsonify({"success": True, "reopened": n})

    @bp.route("/repair/findings/<int:fid>/fix", methods=["POST"])
    def video_repair_finding_fix(fid):
        body = request.get_json(silent=True) or {}
        if body.get("bulk"):
            # fix selected: only a row its view still lists (review 5 W3-TT)
            from . import get_video_db
            if not get_video_db().repair_ids_in_view([fid], **_view_filters(body)):
                return jsonify({"success": False, "refused": True,
                                "error": "Not in the list you picked it from any more"}), 409
        res = _worker().fix_finding(fid, body.get("fix_action"))
        return jsonify(res), (200 if res.get("success") else 400)

    @bp.route("/repair/findings/<int:fid>/resolve", methods=["POST"])
    def video_repair_finding_resolve(fid):
        body = request.get_json(silent=True) or {}
        ok = _worker().resolve_finding(fid, body.get("action"))
        return jsonify({"success": ok}), (200 if ok else 404)

    @bp.route("/repair/findings/<int:fid>/dismiss", methods=["POST"])
    def video_repair_finding_dismiss(fid):
        ok = _worker().dismiss_finding(fid)
        return jsonify({"success": ok}), (200 if ok else 404)

    @bp.route("/repair/findings/bulk-fix", methods=["POST"])
    def video_repair_bulk_fix():
        body = request.get_json(silent=True) or {}
        return jsonify(_worker().bulk_fix_findings(
            job_id=body.get("job_id"), severity=body.get("severity"),
            ids=body.get("ids"), fix_action=body.get("fix_action")))

    @bp.route("/repair/findings/bulk-fix-start", methods=["POST"])
    def video_repair_bulk_fix_start():
        """fix all, in the background. returns at once; poll /bulk-fix/status."""
        body = request.get_json(silent=True) or {}
        res = _worker().start_bulk_fix(
            job_id=body.get("job_id") or None, severity=body.get("severity") or None,
            ids=body.get("ids") or None, fix_action=body.get("fix_action") or None,
            finding_type=body.get("finding_type") or None,
            safe_only=bool(body.get("safe_only")))
        # an unscoped fix_action is a caller bug, not "nothing matched"
        return jsonify(res), (400 if res.get("invalid") else 200)

    @bp.route("/repair/bulk-fix/status", methods=["GET"])
    def video_repair_bulk_fix_status():
        return jsonify(_worker().get_bulk_fix_status())

    @bp.route("/repair/bulk-fix/stop", methods=["POST"])
    def video_repair_bulk_fix_stop():
        _worker().stop_bulk_fix()
        return jsonify({"success": True})

    @bp.route("/repair/findings/bulk", methods=["POST"])
    def video_repair_bulk():
        body = request.get_json(silent=True) or {}
        ids, action = body.get("ids") or [], body.get("action")
        finding_type = body.get("finding_type")
        # the inbox's dismiss-all is scoped by TYPE, not by thousands of ids
        if not ids and finding_type:
            if action != "dismiss":
                return jsonify({"error": "Only dismiss is supported for a type-scoped bulk"}), 400
            return jsonify({"success": True,
                            "updated": _worker().dismiss_findings_by_type(
                                finding_type, job_id=body.get("job_id") or None,
                                severity=body.get("severity") or None)})
        if action not in ("dismiss", "resolve") or not ids or not isinstance(ids, list):
            return jsonify({"error": "bad request"}), 400
        # only ids the view they were picked in still lists (review 5 W3-TT)
        from . import get_video_db
        allowed = get_video_db().repair_ids_in_view(ids, **_view_filters(body))
        updated = _worker().bulk_update_findings(allowed, action) if allowed else 0
        return jsonify({"success": True, "updated": updated,
                        "refused": max(0, len({str(i) for i in ids}) - len(allowed))})

    @bp.route("/repair/findings/clear", methods=["POST"])
    def video_repair_clear():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        # every filter the list has, or clear deletes rows that weren't on screen
        deleted = get_video_db().repair_clear_findings(
            job_id=body.get("job_id") or None, status=body.get("status") or None,
            severity=body.get("severity") or None,
            finding_type=body.get("finding_type") or None, q=body.get("q") or None)
        return jsonify({"success": True, "deleted": deleted})

    @bp.route("/repair/history", methods=["GET"])
    def video_repair_history():
        from . import get_video_db
        q = request.args
        return jsonify({"runs": get_video_db().repair_history(
            job_id=q.get("job_id") or None, limit=_int(q.get("limit"), 50))})

    @bp.route("/repair/progress", methods=["GET"])
    def video_repair_progress():
        return jsonify(_worker().progress_snapshot())
