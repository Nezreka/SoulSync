"""Video Library Maintenance API — the music /api/repair surface, video-scoped.

Same route set, same response shapes (see the music table in web_server.py):
status/toggle/pause/resume, jobs list + per-job toggle/settings/run/stop,
findings list/counts/fix/resolve/dismiss/bulk-fix/bulk/clear, history, and a
progress snapshot for page load (the socket event 'video:repair:progress'
keeps it live afterwards).
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.repair")


def _worker():
    from core.video.repair.worker import get_video_repair_worker

    from . import get_video_db
    return get_video_repair_worker(get_video_db())


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
        return jsonify({"jobs": _worker().get_all_job_info()})

    @bp.route("/repair/jobs/<job_id>/toggle", methods=["POST"])
    def video_repair_job_toggle(job_id):
        w = _worker()
        cfg = w.job_config(job_id)
        if not cfg:
            return jsonify({"error": "unknown job"}), 404
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", not cfg["enabled"]))
        w.set_job_config(job_id, enabled=enabled)
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
            severity=q.get("severity") or None,
            page=q.get("page", 1), limit=q.get("limit", 50)))

    @bp.route("/repair/findings/counts", methods=["GET"])
    def video_repair_findings_counts():
        from . import get_video_db
        return jsonify(get_video_db().repair_counts())

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

    @bp.route("/repair/findings/bulk", methods=["POST"])
    def video_repair_bulk():
        body = request.get_json(silent=True) or {}
        ids, action = body.get("ids") or [], body.get("action")
        if action not in ("dismiss", "resolve") or not ids:
            return jsonify({"error": "bad request"}), 400
        return jsonify({"success": True,
                        "updated": _worker().bulk_update_findings(ids, action)})

    @bp.route("/repair/findings/clear", methods=["POST"])
    def video_repair_clear():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        deleted = get_video_db().repair_clear_findings(
            job_id=body.get("job_id"), status=body.get("status"))
        return jsonify({"success": True, "deleted": deleted})

    @bp.route("/repair/history", methods=["GET"])
    def video_repair_history():
        from . import get_video_db
        q = request.args
        return jsonify({"runs": get_video_db().repair_history(
            job_id=q.get("job_id") or None, limit=int(q.get("limit", 50)))})

    @bp.route("/repair/progress", methods=["GET"])
    def video_repair_progress():
        return jsonify(_worker().progress_snapshot())
