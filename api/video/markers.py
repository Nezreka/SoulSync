"""skip markers, the admin's side: see them, set them by hand, find them now.

GET    /api/video/markers/<movie|episode>/<id>
    every file of the title: the markers the player shows, every source
    each came from, what was removed, and the analysis state
PUT    /api/video/markers/<movie|episode>/<id>
    {kind, start, end, file_id?, scope?: "file" | "season"}  set a marker
    by hand (it beats every other source). season: the same times for every
    episode file of the season
DELETE /api/video/markers/<movie|episode>/<id>?kind=&file_id=
    no marker of that kind: the hand-set one goes and every automatic one
    stays hidden, so a re-detect can't bring it back
POST   /api/video/markers/<movie|episode>/<id>/reset   {kind, file_id?}
    back to what was found automatically
POST   /api/video/markers/find   {show_id, season?} | {movie_id}
    "Find intros now": to the front of the queue, matched again
GET    /api/video/markers/status
    the job: progress, what it's waiting for
POST   /api/video/markers/settings   {enabled?, when?}

admin only for every method (access.ADMIN_ANY). the player reads markers
from /api/player/tracks (api/player_tracks.py).
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.markers")

_ITEM_KINDS = ("movie", "episode")
_MARKER_KINDS = ("intro", "credits", "recap", "preview")


def _db():
    from . import get_video_db
    return get_video_db()


def _profile_id():
    try:
        from core.profile_context import get_current_profile_id
        pid = get_current_profile_id()
        return int(pid) if pid is not None else None
    except Exception:  # noqa: BLE001
        return None


def _file_ids(db, kind, item_id, file_id):
    from core.video.markers import service
    ids = service.item_file_ids(db, kind, item_id)
    if file_id not in (None, ""):
        try:
            fid = int(file_id)
        except (TypeError, ValueError):
            return None
        return [fid] if fid in ids else None
    return ids[:1] or None


def _resync(db, file_ids):
    """the .edl files follow an edit right away"""
    from core.video.markers import service
    for fid in file_ids:
        ctx = service.file_ctx(db, fid)
        if ctx is not None:
            try:
                service.write_sidecar(db, ctx)
            except Exception:  # noqa: BLE001 - the db has it; the file is a copy
                logger.debug("sidecar write after an edit failed for %s", fid, exc_info=True)


def item_view(db, kind, item_id):
    import os

    from core.video.markers import merge, service, store
    from core.video.markers.job import get_job
    files = []
    for fid in service.item_file_ids(db, kind, item_id):
        ctx = service.file_ctx(db, fid)
        if ctx is None:
            continue
        st = store.state(db, fid) or {}
        duration = (st.get("result") or {}).get("duration") or ctx.runtime
        if ctx.local:
            service.read_sidecar(db, ctx, duration)
        rows = store.segments(db, fid)
        hidden = store.suppressed(db, fid)
        shown = merge.best(rows, hidden, duration)
        opts = service.options_for(db, ctx)
        ours = store.written_sidecar(db, fid)
        files.append({
            "file_id": fid,
            "name": os.path.basename(ctx.rel_path.replace("\\", "/")),
            "duration": duration,
            "markers": [merge.public(m) for m in shown],
            "sources": [{**merge.public(m), "extra": m.get("extra") or {}} for m in rows],
            "removed": hidden,
            "analysis": {"status": st.get("status") or "queued", "error": st.get("last_error"),
                         "analyzed_at": st.get("analyzed_at"), "attempts": st.get("attempts") or 0},
            "sidecar": {"writes": bool(opts.get("markers_write")) and not ctx.hands_off,
                        "written": bool(ours), "reachable": bool(ctx.local)},
        })
    return {"success": True, "kind": kind, "id": item_id, "files": files,
            "job": get_job(db).status()}


def register_routes(bp):
    @bp.route("/markers/status", methods=["GET"])
    def video_markers_status():
        from core.video.markers.job import get_job
        return jsonify({"success": True, **get_job(_db()).status()})

    @bp.route("/markers/settings", methods=["POST"])
    def video_markers_settings():
        from core.video.markers.job import ENABLED, WHEN, get_job
        body = request.get_json(silent=True) or {}
        db = _db()
        if "enabled" in body:
            db.set_setting(ENABLED, "1" if body.get("enabled") else "0")
        if "when" in body:
            when = str(body.get("when") or "")
            if when not in ("idle", "night", "always"):
                return jsonify({"success": False, "error": "when is idle, night or always"}), 400
            db.set_setting(WHEN, when)
        job = get_job(db)
        job.poke()
        return jsonify({"success": True, **job.status()})

    @bp.route("/markers/find", methods=["POST"])
    def video_markers_find():
        from core.video.markers import service, store
        from core.video.markers.job import FIND_NOW, get_job
        body = request.get_json(silent=True) or {}
        db = _db()
        try:
            if body.get("movie_id") is not None:
                ids = service.item_file_ids(db, "movie", int(body["movie_id"]))
            elif body.get("episode_id") is not None:
                ids = service.item_file_ids(db, "episode", int(body["episode_id"]))
            elif body.get("show_id") is not None:
                season = body.get("season")
                ids = service.season_file_ids(db, int(body["show_id"]),
                                              None if season in (None, "") else int(season))
            else:
                return jsonify({"success": False, "error": "say which show, season or movie"}), 400
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "bad id"}), 400
        if not ids:
            return jsonify({"success": False, "error": "there are no files to look at"}), 404
        n = store.bump(db, ids, FIND_NOW, redo=True)
        job = get_job(db)
        job.poke()
        return jsonify({"success": True, "queued": n, "files": len(ids), "job": job.status()})

    @bp.route("/markers/<kind>/<int:item_id>", methods=["GET"])
    def video_markers_get(kind, item_id):
        if kind not in _ITEM_KINDS:
            return jsonify({"success": False, "error": "kind is movie or episode"}), 400
        return jsonify(item_view(_db(), kind, item_id))

    @bp.route("/markers/<kind>/<int:item_id>", methods=["PUT"])
    def video_markers_set(kind, item_id):
        from core.video.markers import service, store
        if kind not in _ITEM_KINDS:
            return jsonify({"success": False, "error": "kind is movie or episode"}), 400
        body = request.get_json(silent=True) or {}
        mk = str(body.get("kind") or "")
        if mk not in _MARKER_KINDS:
            return jsonify({"success": False, "error": "the marker is intro, credits, recap or preview"}), 400
        try:
            start, end = float(body.get("start")), float(body.get("end"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "start and end are seconds"}), 400
        if start < 0 or end - start < 1:
            return jsonify({"success": False, "error": "the end has to be at least a second after the start"}), 400
        db = _db()
        ids = _file_ids(db, kind, item_id, body.get("file_id"))
        if not ids:
            return jsonify({"success": False, "error": "no such file"}), 404
        if kind == "episode" and body.get("scope") == "season":
            ctx = service.file_ctx(db, ids[0], resolve=False)
            if ctx and ctx.show_id is not None:
                ids = service.season_file_ids(db, ctx.show_id, ctx.season)
        for fid in ids:
            dur = ((store.state(db, fid) or {}).get("result") or {}).get("duration")
            if dur and start >= dur:
                continue
            store.set_user(db, fid, mk, start, min(end, dur) if dur else end, _profile_id())
        _resync(db, ids)
        return jsonify(item_view(db, kind, item_id))

    @bp.route("/markers/<kind>/<int:item_id>", methods=["DELETE"])
    def video_markers_remove(kind, item_id):
        from core.video.markers import store
        if kind not in _ITEM_KINDS:
            return jsonify({"success": False, "error": "kind is movie or episode"}), 400
        mk = str(request.args.get("kind") or "")
        if mk not in _MARKER_KINDS:
            return jsonify({"success": False, "error": "the marker is intro, credits, recap or preview"}), 400
        db = _db()
        ids = _file_ids(db, kind, item_id, request.args.get("file_id"))
        if not ids:
            return jsonify({"success": False, "error": "no such file"}), 404
        for fid in ids:
            store.remove(db, fid, mk)
        _resync(db, ids)
        return jsonify(item_view(db, kind, item_id))

    @bp.route("/markers/<kind>/<int:item_id>/reset", methods=["POST"])
    def video_markers_reset(kind, item_id):
        from core.video.markers import store
        if kind not in _ITEM_KINDS:
            return jsonify({"success": False, "error": "kind is movie or episode"}), 400
        body = request.get_json(silent=True) or {}
        mk = str(body.get("kind") or "")
        if mk not in _MARKER_KINDS:
            return jsonify({"success": False, "error": "the marker is intro, credits, recap or preview"}), 400
        db = _db()
        ids = _file_ids(db, kind, item_id, body.get("file_id"))
        if not ids:
            return jsonify({"success": False, "error": "no such file"}), 404
        for fid in ids:
            store.reset(db, fid, mk)
        _resync(db, ids)
        return jsonify(item_view(db, kind, item_id))
