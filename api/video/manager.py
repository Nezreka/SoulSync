"""The video library manager's API (admin).

GET /api/video/manager/titles          one page of movies or shows, as rows
GET /api/video/manager/files           one page of video files, as rows
GET /api/video/manager/facets          what there is to filter by, with counts
GET /api/video/manager/ids             every id the filters match ("select all")
GET /api/video/manager/rename-preview  what Rename would do to a selection
GET /api/video/manager/delete-preview  what Delete would take with it

Reads only; the operations go through /api/video/bulk. Admin for every
method: the rows carry file paths.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.manager")

#: files one rename preview lists. the count of files that would change is
#: always the whole selection's
_PREVIEW_FILES = 200
_MAX_IDS = 2000


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
