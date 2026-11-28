"""REST API for Media Analysis Phase 2, 3, and 4:
- Music analysis (waveform, Sweet Fades transitions, BPM, key/Camelot)
- Sonic similarity (Track Radio) and Sonic Adventure paths
- Video Trickplay seek thumbnail manifests and sprite JPEGs
"""

from __future__ import annotations

from typing import Optional

from flask import Blueprint, jsonify, request, send_file

from core.analysis import music_analysis, sonic
from core.permissions import requires_any
from core.profile_context import admin_only
from core.video import trickplay
from utils.logging_config import get_logger

logger = get_logger("api.media_analysis")

bp = Blueprint("media_analysis_api", __name__)

VIEWER_SECTIONS = ("movies.access", "tv.access", "music.access")


def _music_db():
    from database.music_database import get_database
    return get_database()


def _video_db():
    from api.video import get_video_db
    return get_video_db()


def _track_file_path(db, track_id: int) -> Optional[str]:
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT path FROM lib2_track_files WHERE track_id = ? AND file_state = 'active' "
            "ORDER BY is_primary DESC, id ASC LIMIT 1",
            (int(track_id),),
        ).fetchone()
    return str(row["path"]) if row and row["path"] else None


def _video_file_path(kind: str, item_id: int) -> Optional[str]:
    vdb = _video_db()
    with vdb._get_connection() as conn:
        if kind == "movie":
            row = conn.execute("SELECT file_path FROM movies WHERE id = ?", (int(item_id),)).fetchone()
        elif kind == "episode":
            row = conn.execute("SELECT file_path FROM episodes WHERE id = ?", (int(item_id),)).fetchone()
        else:
            return None
    return str(row["file_path"]) if row and row["file_path"] else None


# ── music analysis (waveform, transitions, BPM, key) ─────────────────────

@bp.route("/api/analysis/track/<int:track_id>", methods=["GET"])
@requires_any(*VIEWER_SECTIONS)
def get_track_analysis(track_id: int):
    data = music_analysis.get_track_analysis(_music_db(), track_id)
    if not data:
        return jsonify({"success": True, "analyzed": False, "analysis": None})
    return jsonify({"success": True, "analyzed": True, "analysis": data})


@bp.route("/api/analysis/track/<int:track_id>", methods=["POST"])
@admin_only
def run_track_analysis(track_id: int):
    body = request.get_json(silent=True) or {}
    db = _music_db()
    path = _track_file_path(db, track_id)
    if not path:
        return jsonify({"success": False, "error": "Track file not found."}), 404
    try:
        res = music_analysis.analyze_track_file(
            db, track_id, path, write_tags=bool(body.get("write_tags", False))
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True, "analysis": res})


# ── sonic similarity & sonic adventure ───────────────────────────────────

@bp.route("/api/analysis/track/<int:track_id>/similar", methods=["GET"])
@requires_any(*VIEWER_SECTIONS)
def get_similar_tracks(track_id: int):
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    items = sonic.similar_tracks(_music_db(), track_id, limit=limit)
    return jsonify({"success": True, "track_id": track_id, "similar": items})


@bp.route("/api/analysis/sonic-adventure", methods=["GET"])
@requires_any(*VIEWER_SECTIONS)
def get_sonic_adventure():
    try:
        start_id = int(request.args.get("from", 0))
        end_id = int(request.args.get("to", 0))
        steps = int(request.args.get("steps", 8))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid track ids."}), 400
    if not start_id or not end_id:
        return jsonify({"success": False, "error": "from and to track ids are required."}), 400
    path = sonic.sonic_adventure(_music_db(), start_id, end_id, steps=steps)
    return jsonify({"success": True, "path": path})


# ── video trickplay seek thumbnails ──────────────────────────────────────

@bp.route("/api/analysis/video/<kind>/<int:item_id>/trickplay", methods=["GET"])
@requires_any(*VIEWER_SECTIONS)
def get_trickplay(kind: str, item_id: int):
    vpath = _video_file_path(kind, item_id)
    if not vpath:
        return jsonify({"success": True, "available": False, "manifest": None})
    manifest = trickplay.get_trickplay_manifest(vpath)
    if not manifest:
        return jsonify({"success": True, "available": False, "manifest": None})
    public = {k: v for k, v in manifest.items() if k != "folder"}
    public["sprite_url_template"] = f"/api/analysis/video/{kind}/{item_id}/trickplay/{{index}}.jpg"
    return jsonify({"success": True, "available": True, "manifest": public})


@bp.route("/api/analysis/video/<kind>/<int:item_id>/trickplay/<int:sprite_idx>.jpg", methods=["GET"])
@requires_any(*VIEWER_SECTIONS)
def get_trickplay_sprite(kind: str, item_id: int, sprite_idx: int):
    vpath = _video_file_path(kind, item_id)
    if not vpath:
        return jsonify({"success": False, "error": "Video not found."}), 404
    fp = trickplay.sprite_file_path(vpath, sprite_idx)
    if not fp:
        return jsonify({"success": False, "error": "Sprite not found."}), 404
    resp = send_file(str(fp), mimetype="image/jpeg", conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.route("/api/analysis/video/<kind>/<int:item_id>/trickplay", methods=["POST"])
@admin_only
def build_trickplay(kind: str, item_id: int):
    vpath = _video_file_path(kind, item_id)
    if not vpath:
        return jsonify({"success": False, "error": "Video not found."}), 404
    body = request.get_json(silent=True) or {}
    try:
        res = trickplay.generate_trickplay(
            vpath,
            interval_s=int(body.get("interval_s", trickplay.DEFAULT_INTERVAL_S) or 10),
            force=bool(body.get("force", False)),
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 500
    public = {k: v for k, v in res.items() if k != "folder"}
    return jsonify({"success": True, "manifest": public})
