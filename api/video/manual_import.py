"""Manual / failed-import resolution API (isolated).

When a finished download can't be auto-placed it's parked as ``import_failed`` with
the file left on disk (``dest_path`` points at it). The Import page surfaces these and
lets the user place them by hand:

  GET  /api/video/import/failed          → the queue of unplaced downloads
  POST /api/video/import/<id>/place      → force-import to the user's chosen identity
  POST /api/video/import/<id>/dismiss    → drop the row (optionally delete the file)

The identity picker on the page reuses the existing /api/video/search (TMDB, with a
``library_id`` annotation for owned titles) — no new search endpoint needed here.
Reads only the video engine + video.db; nothing from the music side.
"""

from __future__ import annotations

import json
import os

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.manual_import")

_KIND_FOR_SCOPE = {"movie": "movie", "episode": "show"}


def _ctx(row):
    try:
        c = json.loads(row.get("search_ctx") or "{}")
        return c if isinstance(c, dict) else {}
    except (ValueError, TypeError):
        return {}


def _failed_view(row):
    """The render-ready shape for one unplaced download."""
    c = _ctx(row)
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "kind": row.get("kind"),
        "year": row.get("year"),
        "reason": row.get("error"),
        "file": row.get("dest_path"),         # where the file is sitting, unplaced
        "release_title": row.get("release_title"),
        "poster_url": row.get("poster_url"),
        "scope": c.get("scope"),
        "season": c.get("season"),
        "episode": c.get("episode"),
    }


def register_routes(bp):
    @bp.route("/import/failed", methods=["GET"])
    def video_import_failed():
        from . import get_video_db
        rows = get_video_db().get_import_failed_video_downloads()
        return jsonify({"success": True, "items": [_failed_view(r) for r in rows]})

    @bp.route("/import/<int:dl_id>/place", methods=["POST"])
    def video_import_place(dl_id):
        """Force-import an unplaced file to the user's chosen identity. Body:
        {scope, title, year, season, episode, episode_title, media_id}."""
        from . import get_video_db
        from core.video import organization
        from core.video.download_pipeline import target_dir_for
        from core.video.importer import real_fs, run_import
        from core.video.mediainfo import probe

        db = get_video_db()
        row = db.get_video_download(dl_id)
        if not row or row.get("status") != "import_failed":
            return jsonify({"success": False, "error": "Not an unplaced import."}), 404
        src = row.get("dest_path")
        if not src or not os.path.exists(src):
            return jsonify({"success": False, "error": "The file is no longer on disk."}), 410

        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "").lower()
        if scope not in ("movie", "episode"):
            return jsonify({"success": False, "error": "Choose a movie or an episode."}), 400

        paths = {k: db.get_setting(k) or "" for k in ("movies_path", "tv_path", "youtube_path")}
        if not paths["movies_path"]:
            paths["movies_path"] = db.get_setting("transfer_path") or ""
        override = {
            "scope": scope,
            "title": body.get("title"),
            "year": body.get("year"),
            "season": body.get("season"),
            "episode": body.get("episode"),
            "episode_title": body.get("episode_title"),
            "media_id": body.get("media_id"),
            "target_dir": target_dir_for(_KIND_FOR_SCOPE[scope], paths),
        }
        settings = organization.load(db)
        prober = probe if settings.get("verify_with_ffprobe", True) else None
        patch = run_import(row, src, fs=real_fs(), prober=prober, settings=settings,
                           force=True, override=override)
        try:
            db.update_video_download(dl_id, **patch)
        except Exception:
            logger.exception("manual place: failed to persist import %s", dl_id)
            return jsonify({"success": False, "error": "Couldn't save the result."}), 500
        ok = patch.get("status") == "completed"
        if ok:
            # Write NFO + artwork sidecars for the chosen identity (best-effort), then
            # refresh the server + DB the same way the auto-download path does
            # (batch-complete → scan chain), so the manually-placed title shows up
            # without waiting for a scheduled scan.
            sidecar_dl = {"kind": _KIND_FOR_SCOPE[scope], "media_source": "tmdb",
                          "media_id": override.get("media_id"),
                          "poster_url": row.get("poster_url"),
                          "search_ctx": json.dumps({"scope": scope, "season": override.get("season"),
                                                    "episode": override.get("episode")})}
            if settings.get("save_artwork") or settings.get("write_nfo"):
                try:
                    from core.video.download_monitor import write_sidecars
                    from core.video.importer import real_fs
                    write_sidecars(db, sidecar_dl, patch["dest_path"], settings, real_fs())
                except Exception:
                    logger.exception("manual place: sidecar write failed for %s", dl_id)
            if settings.get("download_subtitles"):
                try:
                    from core.video.download_monitor import write_subtitles_for
                    from core.video.importer import real_fs
                    write_subtitles_for(db, sidecar_dl, patch["dest_path"], settings, real_fs())
                except Exception:
                    logger.exception("manual place: subtitle fetch failed for %s", dl_id)
            try:
                from core.video.download_events import notify_batch_complete
                notify_batch_complete({"completed": 1, "manual": True})
            except Exception:
                logger.exception("manual place: batch-complete notify failed for %s", dl_id)
        return jsonify({"success": ok, "status": patch.get("status"),
                        "dest_path": patch.get("dest_path"), "error": patch.get("error")})

    @bp.route("/import/<int:dl_id>/dismiss", methods=["POST"])
    def video_import_dismiss(dl_id):
        """Drop a failed-import row. Body {delete_file: bool} optionally removes the
        unplaced file from disk too."""
        from . import get_video_db
        db = get_video_db()
        row = db.get_video_download(dl_id)
        if not row or row.get("status") != "import_failed":
            return jsonify({"success": False, "error": "Not an unplaced import."}), 404
        body = request.get_json(silent=True) or {}
        if body.get("delete_file"):
            src = row.get("dest_path")
            if src and os.path.exists(src):
                try:
                    os.remove(src)
                except OSError:
                    logger.warning("dismiss: could not delete %s", src)
        db.update_video_download(dl_id, status="cancelled",
                                 error="Dismissed from manual import")
        return jsonify({"success": True})
