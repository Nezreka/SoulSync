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
        return jsonify(get_video_db().repair_get_findings(
            job_id=q.get("job_id") or None, status=q.get("status") or None,
            severity=q.get("severity") or None, finding_type=q.get("finding_type") or None,
            sort=q.get("sort") or None, q=q.get("q") or None,
            page=_int(q.get("page"), 0), limit=_int(q.get("limit"), 50)))

    @bp.route("/repair/findings/counts", methods=["GET"])
    def video_repair_findings_counts():
        from . import get_video_db
        return jsonify(get_video_db().repair_counts())

    @bp.route("/repair/findings/groups", methods=["GET"])
    def video_repair_findings_groups():
        return jsonify({"groups": _worker().get_finding_groups()})

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
                                           finding_type=body.get("finding_type"))
        return jsonify({"success": True, "reopened": n})

    @bp.route("/repair/findings/<int:fid>/fix", methods=["POST"])
    def video_repair_finding_fix(fid):
        body = request.get_json(silent=True) or {}
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
                            "updated": _worker().dismiss_findings_by_type(finding_type)})
        if action not in ("dismiss", "resolve") or not ids:
            return jsonify({"error": "bad request"}), 400
        return jsonify({"success": True,
                        "updated": _worker().bulk_update_findings(ids, action)})

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
