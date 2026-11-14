"""The video library manager's API (admin).

GET /api/video/manager/titles          one page of movies or shows, as rows
GET /api/video/manager/files           one page of video files, as rows
GET /api/video/manager/facets          what there is to filter by, with counts
GET /api/video/manager/ids             every id the filters match ("select all")
GET /api/video/manager/rename-preview  what Rename would do to a selection
GET /api/video/manager/delete-preview  what Delete would take with it
GET /api/video/manager/move-preview    what moving a selection to a library would do
GET /api/video/manager/libraries       the libraries a title can move to
GET /api/video/manager/history         file changes, newest first (one title or all)
POST /api/video/manager/history/<id>/undo     put one change back
POST /api/video/manager/history/undo-batch    put a whole bulk run back
GET|PUT /api/video/manager/views       the saved views
GET /api/video/manager/episodes/<show> a show's episodes with their files
POST /api/video/manager/renumber/<show>  file a run of files under other episodes

The bulk operations go through /api/video/bulk. Admin for every method: the
rows carry file paths.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.manager")

#: files one rename preview lists. the count of files that would change is
#: always the whole selection's
_PREVIEW_FILES = 200
_MAX_IDS = 2000
_VIEWS_KEY = "video_manager_views"


def _ids():
    raw = str(request.args.get("ids") or "")
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out[:_MAX_IDS]


def register_routes(bp):
    def _ctx():
        from core.video.sources import resolve_video_server
        from core.video.watch_scope import current_watcher

        from . import get_video_db
        return get_video_db(), resolve_video_server(), current_watcher().rows

    @bp.route("/manager/titles", methods=["GET"])
    def video_manager_titles():
        from core.video import library_manager
        try:
            db, server, watcher = _ctx()
            return jsonify({"success": True, **library_manager.titles(
                db, request.args.get("kind", "movies"), request.args.to_dict(),
                server_source=server, watcher=watcher)})
        except Exception:
            logger.exception("library manager titles failed")
            return jsonify({"success": False, "error": "Could not load the library"}), 500

    @bp.route("/manager/files", methods=["GET"])
    def video_manager_files():
        from core.video import library_manager
        try:
            db, server, _watcher = _ctx()
            return jsonify({"success": True, **library_manager.files(
                db, request.args.to_dict(), server_source=server)})
        except Exception:
            logger.exception("library manager files failed")
            return jsonify({"success": False, "error": "Could not load the files"}), 500

    @bp.route("/manager/facets", methods=["GET"])
    def video_manager_facets():
        from core.video import library_manager
        from core.video import quality_profile as qp
        from core.video.libraries import list_libraries
        try:
            db, server, watcher = _ctx()
            kind = request.args.get("kind", "movies")
            out = library_manager.facets(db, kind, server_source=server, watcher=watcher)
            title_kind = "show" if kind == "shows" else "movie"
            try:
                profiles = [{"id": p.get("id"), "name": p.get("name")}
                            for p in qp.list_profiles(db) if p.get("id")]
            except Exception:   # noqa: BLE001 - no named profiles is a normal install
                profiles = []
            try:
                libraries = [{"id": x["id"], "name": x["name"]}
                             for x in list_libraries(db, title_kind) if x.get("usable")]
            except Exception:   # noqa: BLE001
                libraries = []
            return jsonify({"success": True, **out, "quality_profiles": profiles,
                            "libraries": libraries})
        except Exception:
            logger.exception("library manager facets failed")
            return jsonify({"success": False, "error": "Could not load the filters"}), 500

    @bp.route("/manager/ids", methods=["GET"])
    def video_manager_ids():
        from core.video import library_manager
        try:
            db, server, watcher = _ctx()
            ids = library_manager.title_ids(db, request.args.get("kind", "movies"),
                                            request.args.to_dict(), server_source=server,
                                            watcher=watcher, cap=_MAX_IDS + 1)
            return jsonify({"success": True, "ids": ids[:_MAX_IDS],
                            "truncated": len(ids) > _MAX_IDS})
        except Exception:
            logger.exception("library manager ids failed")
            return jsonify({"success": False, "error": "Could not select the titles"}), 500

    @bp.route("/manager/rename-preview", methods=["GET"])
    def video_manager_rename_preview():
        """what Rename would do to these titles' files: each file that is not
        named by the template, with the name it would get."""
        from core.video import bulk_ops, file_ops

        from . import get_video_db
        kind = request.args.get("kind", "movie")
        ids = _ids()
        if kind not in ("movie", "show") or not ids:
            return jsonify({"success": False, "error": "kind and ids are required"}), 400
        db = get_video_db()
        changes, total, conforming, unreachable = [], 0, 0, 0
        for item_id in ids:
            for file_id in bulk_ops._title_file_ids(db, kind, item_id):
                total += 1
                try:
                    plan = file_ops.rename_plan(db, file_id) or {}
                except Exception:   # noqa: BLE001 - one unreadable file never ends the preview
                    plan = {"error": "could not be read"}
                if plan.get("error"):
                    unreachable += 1
                elif plan.get("conforms"):
                    conforming += 1
                elif len(changes) < _PREVIEW_FILES:
                    changes.append({"file_id": file_id, "current": plan.get("current"),
                                    "expected": plan.get("expected")})
        to_rename = total - conforming - unreachable
        return jsonify({"success": True, "files": total, "to_rename": to_rename,
                        "already_named": conforming, "unreachable": unreachable,
                        "changes": changes, "more": max(0, to_rename - len(changes))})

    @bp.route("/manager/delete-preview", methods=["GET"])
    def video_manager_delete_preview():
        """what deleting these titles would take with it, added up."""
        from core.video import delete_title

        from . import get_video_db
        kind = request.args.get("kind", "movie")
        ids = _ids()
        if kind not in ("movie", "show") or not ids:
            return jsonify({"success": False, "error": "kind and ids are required"}), 400
        db = get_video_db()
        titles, files, size, blocked = [], 0, 0, []
        for item_id in ids:
            try:
                plan = delete_title.plan(db, kind, item_id)
            except Exception:   # noqa: BLE001
                logger.exception("delete preview failed for %s %s", kind, item_id)
                plan = None
            if not plan:
                continue
            titles.append(plan.get("title"))
            files += int(plan.get("files") or 0)
            size += int(plan.get("size_bytes") or 0)
            if plan.get("blocked"):
                blocked.append({"title": plan.get("title"), "reason": plan["blocked"]})
        return jsonify({"success": True, "titles": len(titles), "sample": titles[:8],
                        "files": files, "size_bytes": size, "blocked": blocked})

    # ── move to another library ─────────────────────────────────────────────

    @bp.route("/manager/libraries", methods=["GET"])
    def video_manager_libraries():
        from core.video import libraries

        from . import get_video_db
        kind = "show" if request.args.get("kind") in ("show", "shows") else "movie"
        libs = [{"id": x["id"], "name": x["name"], "path": x["path"], "is_default": x["is_default"]}
                for x in libraries.list_libraries(get_video_db(), kind) if x.get("usable")]
        return jsonify({"success": True, "libraries": libs})

    @bp.route("/manager/move-preview", methods=["GET"])
    def video_manager_move_preview():
        from core.video import bulk_ops

        from . import get_video_db
        kind = "show" if request.args.get("kind") in ("show", "shows") else "movie"
        try:
            library_id = int(request.args.get("library_id") or 0)
        except ValueError:
            library_id = 0
        db = get_video_db()
        files = size = copy = skipped = held = 0
        sample, error, why = [], None, {}
        for item_id in _ids():
            if db.is_hands_off(kind, item_id):
                held += 1
                continue
            plan = bulk_ops.move_plan(db, kind, item_id, library_id)
            if plan.get("error"):
                error = plan["error"]
                break
            files += plan["files"]
            size += plan["size_bytes"]
            copy += plan["copy_bytes"]
            skipped += len(plan["skipped"])
            for skip in plan["skipped"]:
                why[skip["reason"]] = why.get(skip["reason"], 0) + 1
            for mv in plan["moves"]:
                if len(sample) < _PREVIEW_FILES:
                    sample.append({"from": mv["from"], "to": mv["to"]})
        if error:
            return jsonify({"success": False, "error": error}), 409
        free = None
        try:
            import shutil

            from core.video import libraries
            lib = libraries.get_library(db, library_id) or {}
            free = shutil.disk_usage(lib.get("path") or "").free
        except OSError:
            free = None
        return jsonify({"success": True, "files": files, "size_bytes": size, "copy_bytes": copy,
                        "skipped": skipped, "held": held, "free_bytes": free,
                        # the commonest reason a file stays put, to say it plainly
                        "skipped_why": max(why, key=why.get) if why else None,
                        "enough_room": free is None or free > copy, "moves": sample})

    # ── history and undo ────────────────────────────────────────────────────

    def _refresh_titles(db, titles):
        from core.video import library_manager
        by_kind = {}
        for t in titles or []:
            if t.get("kind") and t.get("item_id"):
                by_kind.setdefault("shows" if t["kind"] == "show" else "movies", []).append(
                    int(t["item_id"]))
        for kind, ids in by_kind.items():
            try:
                library_manager.refresh(db, kind, ids)
            except Exception:   # noqa: BLE001 - a stale row is not a failed undo
                logger.debug("manager refresh after undo failed", exc_info=True)

    @bp.route("/manager/history", methods=["GET"])
    def video_manager_history():
        from core.video import file_history

        from . import get_video_db
        item_id = request.args.get("id")
        try:
            out = file_history.changes(
                get_video_db(), kind=request.args.get("kind"),
                item_id=int(item_id) if str(item_id or "").isdigit() else None,
                limit=int(request.args.get("limit") or 100),
                offset=int(request.args.get("offset") or 0))
        except ValueError:
            return jsonify({"success": False, "error": "limit and offset must be numbers"}), 400
        return jsonify({"success": True, **out})

    @bp.route("/manager/history/<int:change_id>/undo", methods=["POST"])
    def video_manager_undo(change_id):
        from core.video import file_history

        from . import get_video_db
        db = get_video_db()
        res = file_history.undo(db, change_id)
        if res.get("success"):
            _refresh_titles(db, [res])
        return jsonify(res), (200 if res.get("success") else 409)

    @bp.route("/manager/history/undo-batch", methods=["POST"])
    def video_manager_undo_batch():
        from core.video import file_history

        from . import get_video_db
        batch = str((request.get_json(silent=True) or {}).get("batch") or "").strip()
        if not batch:
            return jsonify({"success": False, "error": "batch is required"}), 400
        db = get_video_db()
        res = file_history.undo_batch(db, batch)
        _refresh_titles(db, res.get("titles"))
        return jsonify(res), (200 if res.get("undone") else 409)

    # ── saved views ─────────────────────────────────────────────────────────

    @bp.route("/manager/views", methods=["GET", "PUT"])
    def video_manager_views():
        import json

        from . import get_video_db
        db = get_video_db()
        if request.method == "PUT":
            views = (request.get_json(silent=True) or {}).get("views")
            if not isinstance(views, list) or len(views) > 50:
                return jsonify({"success": False, "error": "views must be a list of up to 50"}), 400
            clean = []
            for v in views:
                name = str((v or {}).get("name") or "").strip()[:60] if isinstance(v, dict) else ""
                if not name or not isinstance(v.get("state"), dict):
                    return jsonify({"success": False,
                                    "error": "each view needs a name and a state"}), 400
                clean.append({"name": name, "state": v["state"]})
            text = json.dumps(clean)
            if len(text) > 100_000:
                return jsonify({"success": False, "error": "too much to save"}), 400
            db.set_setting(_VIEWS_KEY, text)
            return jsonify({"success": True, "views": clean})
        try:
            views = json.loads(db.get_setting(_VIEWS_KEY) or "[]")
        except ValueError:
            views = []
        return jsonify({"success": True, "views": views if isinstance(views, list) else []})

    # ── episodes and renumbering ────────────────────────────────────────────

    @bp.route("/manager/episodes/<int:show_id>", methods=["GET"])
    def video_manager_episodes(show_id):
        from core.video import library_actions

        from . import get_video_db
        return jsonify({"success": True, **library_actions.show_episodes(get_video_db(), show_id)})

    @bp.route("/manager/renumber/<int:show_id>", methods=["POST"])
    def video_manager_renumber(show_id):
        """body {file_ids, season, start_episode, apply}. without apply it
        answers with the plan and changes nothing."""
        from core.video import library_actions, library_manager

        from . import get_video_db
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        args = (db, show_id, body.get("file_ids") or [], body.get("season"),
                body.get("start_episode"))
        if not body.get("apply"):
            plan = library_actions.renumber_plan(*args)
            return jsonify({"success": not plan.get("error"), **plan}), \
                (409 if plan.get("error") else 200)
        try:
            res = library_actions.renumber(*args)
        except Exception:
            logger.exception("renumber failed for show %s", show_id)
            return jsonify({"success": False, "error": "Renumber failed. See app.log."}), 500
        if res.get("success"):
            library_manager.refresh(db, "shows", [show_id])
        return jsonify(res), (200 if res.get("success") else 409)
