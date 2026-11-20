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
    """The render-ready shape for one unplaced download — everything the card's
    expand drawer shows (grab provenance, on-disk facts, identity context)."""
    c = _ctx(row)
    path = row.get("dest_path")
    file_size, file_exists = None, False
    if path:
        try:
            file_size = os.path.getsize(path)
            file_exists = True
        except OSError:
            pass
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "kind": row.get("kind"),
        "year": row.get("year"),
        "reason": row.get("error"),
        "file": path,                         # where the file is sitting, unplaced
        "file_exists": file_exists,
        "file_size": file_size,               # actual bytes on disk (None if gone)
        "release_title": row.get("release_title"),
        "poster_url": row.get("poster_url"),
        "quality_label": row.get("quality_label"),
        "size_bytes": row.get("size_bytes"),  # the release's advertised size
        "source": row.get("source"),
        "username": row.get("username"),
        "attempts": row.get("attempts"),
        "grabbed_at": row.get("created_at"),
        "media_id": row.get("media_id"),
        "media_source": row.get("media_source"),
        "scope": c.get("scope"),
        "season": c.get("season"),
        "episode": c.get("episode"),
    }


def _current_file(db, row):
    """The unplaced file's path, or None when it is gone. A torrent/usenet
    row's stored path can go stale: the client moved the file after we read
    it (out of its incomplete folder, #2176). Then the client is asked where
    it is now, and the row is pointed there."""
    path = row.get("dest_path")
    if path and os.path.exists(path):
        return path
    try:
        from core.video.client_download import current_job_file
        found = current_job_file(row)
    except Exception:   # noqa: BLE001 - a client hiccup leaves the old answer
        logger.debug("re-asking the client for download %s failed", row.get("id"), exc_info=True)
        found = None
    if found:
        try:
            db.update_video_download(row["id"], dest_path=found)
        except Exception:   # noqa: BLE001 - the found path still works for this request
            logger.debug("could not store the new path for %s", row.get("id"), exc_info=True)
        row["dest_path"] = found
    return found


def register_routes(bp):
    @bp.route("/import/failed", methods=["GET"])
    def video_import_failed():
        from . import get_video_db
        db = get_video_db()
        rows = db.get_import_failed_video_downloads()
        for r in rows:
            _current_file(db, r)     # a stale client path points at where the file went
        return jsonify({"success": True, "items": [_failed_view(r) for r in rows]})

    @bp.route("/import/<int:dl_id>/place", methods=["POST"])
    def video_import_place(dl_id):
        """Force-import an unplaced file to the user's chosen identity. Body:
        {scope, title, year, season, episode, episode_title, media_id}."""
        from . import get_video_db
        from core.video import organization
        from core.video.importer import real_fs, run_import
        from core.video.mediainfo import probe

        db = get_video_db()
        row = db.get_video_download(dl_id)
        if not row or row.get("status") != "import_failed":
            return jsonify({"success": False, "error": "Not an unplaced import."}), 404
        src = _current_file(db, row)
        if not src:
            return jsonify({"success": False, "error": "The file is no longer on disk."}), 410

        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "").lower()
        if scope not in ("movie", "episode"):
            return jsonify({"success": False, "error": "Choose a movie or an episode."}), 400

        from .downloads import _target_for
        override = {
            "scope": scope,
            "title": body.get("title"),
            "year": body.get("year"),
            "season": body.get("season"),
            "episode": body.get("episode"),
            "episode_title": body.get("episode_title"),
            "media_id": body.get("media_id"),
            # the chosen identity's show decides anime vs TV. The place screen
            # sends the picked result's TMDB id as media_id (video-import.js), so
            # read it as one; an id that isn't resolves to 'standard' -> TV folder.
            # and its quality profile decides main vs the 4K library
            "target_dir": _target_for(
                db, _KIND_FOR_SCOPE[scope],
                {"media_id": body.get("media_id"),
                 "media_source": body.get("media_source") or "tmdb",
                 # and its video library (#2029): one picked here, the
                 # title's own, the rules or the default
                 "video_library_id": body.get("video_library_id"),
                 "title": body.get("title")}),
        }
        settings = organization.load(db)
        prober = probe if settings.get("verify_with_ffprobe", True) else None
        from core.video.recycle import discarder
        from core.video.importer import plan_import
        from core.video.download_monitor import _expected_duration_sec, _reclaim_source
        fs = real_fs()
        # Plan first and persist the destination BEFORE the file moves (same
        # crash-window reasoning as the automatic path in download_monitor).
        try:
            probe_info = prober(src) if prober is not None else None
        except Exception:   # noqa: BLE001 - a probe crash must not block the import
            probe_info = None
        from core.video.download_monitor import absolute_lookup, naming_series_type, template_ids
        # the picked identity's ids (its media_id is a TMDB id), not the grab's
        picked = {"kind": _KIND_FOR_SCOPE[scope], "media_source": "tmdb",
                  "media_id": override.get("media_id")}
        named = organization.for_series_type(settings, naming_series_type(db, picked, settings))
        ids = template_ids(db, picked, named)
        plan = plan_import(row, src, list_dir=fs.list_dir, probe=probe_info,
                           settings=named, force=True, override=override,
                           expected_duration_sec=_expected_duration_sec(db, row), ids=ids,
                           absolute_of=absolute_lookup(db, ids, named))
        if plan["action"] in ("import", "upgrade") and plan.get("dest"):
            try:
                db.update_video_download(dl_id, dest_path=plan["dest"]["path"])
            except Exception:   # noqa: BLE001, S110 - best-effort
                pass
        patch = run_import(row, src, fs=fs, settings=settings,
                           force=True, override=override, recycle=discarder(db, settings),
                           plan=plan)
        try:
            db.update_video_download(dl_id, **patch)
        except Exception:
            logger.exception("manual place: failed to persist import %s", dl_id)
            return jsonify({"success": False, "error": "Couldn't save the result."}), 500
        # The row is persisted — now reclaim the download copy (see run_import).
        if patch.get("status") == "completed":
            _reclaim_source(patch.get("_cleanup_source"))
        ok = patch.get("status") == "completed"
        if ok:
            # The row now carries the USER's chosen identity, not the grab's:
            # the subtitle hook (and the worker's residue check) resolve
            # per-show overrides by re-reading the download row from the DB —
            # without this the worker would see the stale grab identity,
            # degrade to the global list, and delete the hook's rows as
            # residue. Best-effort; the hook falls back to global without it.
            if override.get("media_id") is not None:
                try:
                    db.update_video_download(dl_id, media_source="tmdb",
                                             media_id=override.get("media_id"))
                except Exception:   # noqa: BLE001, S110 - best-effort
                    pass
            # Write NFO + artwork sidecars for the chosen identity (best-effort), then
            # refresh the server + DB the same way the auto-download path does
            # (batch-complete → scan chain), so the manually-placed title shows up
            # without waiting for a scheduled scan.
            # "id" threads the real download id through: write_subtitles_for keys its
            # subtitle_wanted rows on it. The row itself now carries the user's
            # chosen identity (persisted above), so the hook's DB re-read and
            # the worker's residue check resolve the same per-show override.
            sidecar_dl = {"id": dl_id, "kind": _KIND_FOR_SCOPE[scope], "media_source": "tmdb",
                          "media_id": override.get("media_id"),
                          "poster_url": row.get("poster_url"),
                          "search_ctx": json.dumps({"scope": scope, "season": override.get("season"),
                                                    "episode": override.get("episode")})}
            # in the library now, as the picked title (core/video/library_ingest.py)
            from core.video.library_ingest import ingest_landed
            ingest_landed(db, {**row, **sidecar_dl, "title": body.get("title") or row.get("title"),
                               "year": body.get("year") or row.get("year"),
                               "search_ctx": json.dumps({"scope": scope, "season": override.get("season"),
                                                         "episode": override.get("episode"),
                                                         "title": body.get("title") or row.get("title"),
                                                         "year": body.get("year") or row.get("year")})},
                          patch["dest_path"], replaced=patch.get("_replaced"))
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
                from core.video.download_events import notify_batch_complete, publish
                publish("video_download_completed", {
                    "kind": _KIND_FOR_SCOPE[scope], "title": body.get("title") or row.get("title") or "",
                    "year": body.get("year") or "", "season": body.get("season") or "",
                    "episode": body.get("episode") or "", "channel": "",
                    "quality": patch.get("quality_label") or "", "source": row.get("source") or "",
                    "dest_path": patch.get("dest_path") or ""})
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
                from core.video import organization, recycle
                res = recycle.discard(src, organization.load(db), db, reason="dismissed import")
                if not res.get("ok"):
                    logger.warning("dismiss: could not delete %s", src)
        db.update_video_download(dl_id, status="cancelled",
                                 error="Dismissed from manual import")
        return jsonify({"success": True})

    # ── the import folder (#2129): drop files in, review what was found, import.
    #    the import itself is the download importer's (core.video.import_folder).
    @bp.route("/import/folder", methods=["GET"])
    def video_import_folder_list():
        from . import get_video_db
        from core.video import import_folder
        return jsonify({"success": True, **import_folder.listing(get_video_db())})

    @bp.route("/import/folder/settings", methods=["GET"])
    def video_import_folder_settings():
        from . import get_video_db
        from core.video import import_folder
        return jsonify({"success": True, **import_folder.load_settings(get_video_db())})

    @bp.route("/import/folder/settings", methods=["POST"])
    def video_import_folder_settings_save():
        from . import get_video_db
        from core.video import import_folder
        body = request.get_json(silent=True) or {}
        return jsonify({"success": True, **import_folder.save_settings(get_video_db(), body)})

    @bp.route("/import/folder/import", methods=["POST"])
    def video_import_folder_import():
        """Import one entry of the folder. Body {key, tmdb_id?, kind?, title?,
        year?, season?, episode?, poster_url?}: without a tmdb_id it goes in as
        what was found, with one as what the user picked."""
        from . import get_video_db
        from core.video import import_folder
        db = get_video_db()
        body = request.get_json(silent=True) or {}
        try:
            entry = import_folder.find_entry(db, str(body.get("key") or ""))
        except OSError:
            return jsonify({"success": False, "error": "Can't read the import folder."}), 500
        if entry is None:
            return jsonify({"success": False, "error": "That's not in the import folder any more."}), 404
        override = {k: body.get(k) for k in ("tmdb_id", "kind", "title", "year", "season",
                                              "episode", "poster_url") if k in body}
        why = import_folder.start_import(db, entry, override or None)
        if why:
            return jsonify({"success": False, "error": why}), 409
        return jsonify({"success": True, "status": "importing"})
