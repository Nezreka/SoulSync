"""The "Your library" tab: what you own of a title and the file-level actions.

GET  /api/video/detail/<kind>/<id>/library[?season=N]
    the tab's payload (core.video.library_manage). a show's seasons always
    carry counts; only ``season`` gets the per-file disk checks. file paths
    are an admin's view: other profiles get the facts without them.
GET  /api/video/files/<file_id>/rename-plan
    {current, expected, conforms} — the preview before a rename.
POST /api/video/files/<file_id>/rename
    rename to the current template (sidecars follow, nothing overwritten).
POST /api/video/files/<file_id>/delete
    move to the recycle bin and forget the file. never a silent permanent
    delete: a file with no recycle bin is refused.
GET  /api/video/detail/<kind>/<id>/identify/search?q=
    TMDB candidates for "Fix match".
POST /api/video/detail/<kind>/<id>/identify   {tmdb_id}
    re-point the title and lock it (core.video.identity).
POST /api/video/detail/<kind>/<id>/identify/release
    undo: the next scan takes the server's identity again.
GET  /api/video/title-delete/<kind>/<id>
    what deleting the whole title would do (core.video.delete_title.plan).
POST /api/video/title-delete/<kind>/<id>   {confirm_title}
    delete the whole title so it stays deleted (core.video.delete_title.run).
    admin only for any method, the preview included: it lists file paths.

Every write is admin-gated by the blueprint's permission gate.
"""

from __future__ import annotations

import threading

from flask import g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.library_manage")

_KINDS = ("movie", "show")
_PATH_KEYS = ("path", "local_path", "folder")


def _is_admin() -> bool:
    return bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1))


def _strip_paths(obj):
    """a non-admin profile sees what's owned, not where it lives."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _PATH_KEYS:
                out[k] = None
            elif k == "naming" and isinstance(v, dict):
                out[k] = {"conforms": v.get("conforms")}
            else:
                out[k] = _strip_paths(v)
        return out
    if isinstance(obj, list):
        return [_strip_paths(x) for x in obj]
    return obj


def _refresh_server(kind: str) -> None:
    """tell the media server to look again, so a rename or delete shows up
    there without waiting for its own schedule. best-effort, off-thread."""
    def run():
        try:
            from core.video.sources import refresh_video_server_sections
            refresh_video_server_sections("movie" if kind == "movie" else "show")
        except Exception:   # noqa: BLE001 - the server's own scan catches up anyway
            logger.debug("server refresh after a file change failed", exc_info=True)
    threading.Thread(target=run, name="video-file-refresh", daemon=True).start()


_PREVIEW_ROWS = 200


def _file_ids(raw) -> list:
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out[:2000]


def _episode_files(db, episode_id) -> list:
    """every file on record for one episode"""
    conn = db._get_connection()
    try:
        return [{"file_id": int(r[0]), "item_id": episode_id} for r in conn.execute(
            "SELECT id FROM media_files WHERE episode_id = ?", (int(episode_id),))]
    finally:
        conn.close()


def _stop_hunting(db, episodes: dict) -> int:
    """the deleted files' episodes that have no copy left stop being hunted,
    so the file a person just threw out is not fetched again. returns how
    many episodes that was."""
    by_show: dict = {}
    for show_id, episode_id in episodes.values():
        by_show.setdefault(show_id, set()).add(episode_id)
    moved = 0
    for show_id, ids in by_show.items():
        gone = db.episodes_left_without_files(ids)
        if gone:
            moved += db.set_episodes_monitored(show_id, gone, False)
    return moved


def register_routes(bp):
    @bp.route("/detail/<kind>/<int:item_id>/library", methods=["GET"])
    def video_title_library(kind, item_id):
        from . import get_video_db
        from .kids import restricted_response, video_cap
        from core.content_filter import title_allowed
        from core.video import library_manage
        if kind not in _KINDS:
            return jsonify({"error": "kind must be movie|show"}), 400
        db = get_video_db()
        cap = video_cap()
        if cap is not None:
            # kids profiles: over the cap (or unrated) is a hard no, as on the detail page
            ident = db.title_identity(kind, item_id) or {}
            if not title_allowed(ident.get("content_rating"), cap):
                return restricted_response()
        season = request.args.get("season")
        try:
            season = int(season) if season not in (None, "") else None
        except ValueError:
            return jsonify({"error": "season must be a number"}), 400
        try:
            from core.video.watch_scope import current_watcher
            watcher = current_watcher().rows
            data = (library_manage.movie_library(db, item_id, watcher=watcher) if kind == "movie"
                    else library_manage.show_library(db, item_id, season=season,
                                                     watcher=watcher))
        except Exception:
            logger.exception("library tab failed for %s %s", kind, item_id)
            return jsonify({"error": "Couldn't read this title's files"}), 500
        if data is None:
            return jsonify({"error": "not found"}), 404
        data["can_manage"] = _is_admin()
        return jsonify(data if _is_admin() else _strip_paths(data))

    @bp.route("/files/<int:file_id>/rename-plan", methods=["GET"])
    def video_file_rename_plan(file_id):
        from . import get_video_db
        from core.video import file_ops
        if not _is_admin():
            return jsonify({"error": "Admin only."}), 403
        plan = file_ops.rename_plan(get_video_db(), file_id)
        if plan.get("error"):
            return jsonify({"success": False, "error": plan["error"]}), 409
        return jsonify({"success": True, **plan})

    @bp.route("/files/<int:file_id>/rename", methods=["POST"])
    def video_file_rename(file_id):
        from . import get_video_db
        from core.video import file_ops
        db = get_video_db()
        row = db.library_file_row(file_id) or {}
        from core.video import file_history
        try:
            with file_history.batch() as batch_id:
                res = file_ops.rename_to_template(db, file_id)
        except Exception:
            logger.exception("rename failed for file %s", file_id)
            return jsonify({"success": False, "error": "Rename failed — see app.log"}), 500
        if not res.get("success"):
            return jsonify(res), 409
        if res.get("action") == "renamed":
            res["batch"] = batch_id        # what Undo on the page reverses
            _refresh_server("movie" if row.get("scope") == "movie" else "show")
        return jsonify(res)

    @bp.route("/files/<int:file_id>/delete", methods=["POST"])
    def video_file_delete(file_id):
        from . import get_video_db
        from core.video import file_ops
        db = get_video_db()
        row = db.library_file_row(file_id) or {}
        from core.video import file_history
        body = request.get_json(silent=True) or {}
        episode = (db.file_episodes([file_id]) or {}).get(file_id)
        try:
            with file_history.batch() as batch_id:
                res = file_ops.delete_to_recycle(db, file_id)
        except Exception:
            logger.exception("delete failed for file %s", file_id)
            return jsonify({"success": False, "error": "Delete failed — see app.log"}), 500
        if not res.get("success"):
            return jsonify(res), 409
        logger.info("library tab deleted file %s (%s) — recycled=%s",
                    file_id, row.get("relative_path"), res.get("recycled"))
        if res.get("recycled"):
            res["batch"] = batch_id        # only a recycled file can come back
        if body.get("stop_hunting") and episode:
            res["unmonitored"] = _stop_hunting(db, {file_id: episode})
        _refresh_server("movie" if row.get("scope") == "movie" else "show")
        return jsonify(res)

    # ── acting on what the tab reports (core.video.library_actions) ─────────
    # all under /files, so the permission gate's admin rule for file writes
    # covers them; the previews list paths, so they check for an admin too.

    @bp.route("/files/bulk", methods=["POST"])
    def video_files_bulk():
        """body {action: rename|delete, file_ids: [...]}. the chosen files, one
        at a time; the answer counts what happened to each."""
        from . import get_video_db
        from core.video import library_actions
        body = request.get_json(silent=True) or {}
        ids = body.get("file_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 2000:
            return jsonify({"success": False, "error": "file_ids must be a list of 1 to 2000 files"}), 400
        try:
            ids = [int(i) for i in ids]
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "file_ids must be numbers"}), 400
        db = get_video_db()
        action = str(body.get("action") or "")
        # read before the delete drops the rows: whose episodes these were
        episodes = db.file_episodes(ids) if action == "delete" and body.get("stop_hunting") else {}
        res = library_actions.bulk(db, action, ids)
        if not res.get("success"):
            return jsonify(res), 400
        if episodes:
            res["unmonitored"] = _stop_hunting(db, episodes)
        if res.get("done"):
            _refresh_server(str(body.get("kind") or "show"))
        return jsonify(res)

    @bp.route("/files/rename-preview", methods=["GET"])
    def video_files_rename_preview():
        """?file_ids=1,2,3: what Rename would do to these files. the ones not
        named by the template, each with the name it would get."""
        from . import get_video_db
        from core.video import file_ops
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        ids = _file_ids(request.args.get("file_ids"))
        if not ids:
            return jsonify({"success": False, "error": "file_ids is required"}), 400
        db = get_video_db()
        changes, conforming, unreachable = [], 0, 0
        rows = db.library_file_rows(ids)          # each title read once
        for file_id in ids:
            try:
                plan = (file_ops.rename_plan(db, file_id, row=rows[file_id])
                        if file_id in rows else {"error": "gone"}) or {}
            except Exception:   # noqa: BLE001 - one unreadable file never ends the preview
                plan = {"error": "could not be read"}
            if plan.get("error"):
                unreachable += 1
            elif plan.get("conforms"):
                conforming += 1
            else:
                changes.append({"file_id": file_id, "current": plan.get("current"),
                                "expected": plan.get("expected")})
        return jsonify({"success": True, "files": len(ids), "to_rename": len(changes),
                        "already_named": conforming, "unreachable": unreachable,
                        "changes": changes[:_PREVIEW_ROWS],
                        "more": max(0, len(changes) - _PREVIEW_ROWS)})

    @bp.route("/files/delete-preview", methods=["GET"])
    def video_files_delete_preview():
        """?file_ids=1,2,3: what deleting these files would take. each file
        with its size, and how many episodes or movies would be left with no
        copy at all."""
        from . import get_video_db
        from core.video import file_ops, library_actions, library_manage
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        ids = _file_ids(request.args.get("file_ids"))
        if not ids:
            return jsonify({"success": False, "error": "file_ids is required"}), 400
        db = get_video_db()
        # only a file soulsync can reach is deleted, so only those count
        # toward leaving an episode with no copy
        picked, items, size, unreachable = set(), [], 0, 0
        owners: set = set()
        rows = db.library_file_rows(ids)          # each title read once
        for file_id in ids:
            row = rows.get(file_id)
            if not row:
                continue
            real = file_ops._locate(db, row)
            if not real:
                unreachable += 1
            else:
                picked.add(file_id)
                size += int(row.get("size_bytes") or 0)
            owners.add((row.get("scope"), row.get("item_id")))
            items.append({"file_id": file_id, "label": library_actions._label(row),
                          "filename": library_actions._name(row),
                          "size_bytes": int(row.get("size_bytes") or 0),
                          "reachable": bool(real)})
        # an episode or movie goes missing when every copy it has is picked
        emptied = 0
        for scope, item_id in owners:
            rows = (db.library_title_files("movie", item_id) if scope == "movie"
                    else _episode_files(db, item_id))
            have = {r["file_id"] for r in rows}
            if have and have <= picked:
                emptied += 1
        return jsonify({"success": True, "files": len(items), "size_bytes": size,
                        "unreachable": unreachable, "emptied": emptied,
                        "recycle": library_manage._recycle_on(db),
                        "items": items[:_PREVIEW_ROWS],
                        "more": max(0, len(items) - _PREVIEW_ROWS)})

    @bp.route("/detail/show/<int:show_id>/episodes/monitor", methods=["POST"])
    def video_set_episodes_monitor(show_id):
        """body {episode_ids, monitored}: hunt, or stop hunting, a picked set
        of this show's episodes."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        ids = body.get("episode_ids")
        if not isinstance(ids, list) or not ids or len(ids) > 5000:
            return jsonify({"success": False, "error": "episode_ids must be a list"}), 400
        if not isinstance(body.get("monitored"), bool):
            return jsonify({"success": False, "error": "monitored must be true or false"}), 400
        moved = get_video_db().set_episodes_monitored(show_id, ids, body["monitored"])
        if not moved:
            return jsonify({"success": False, "error": "None of those episodes are this show's."}), 404
        return jsonify({"success": True, "monitored": body["monitored"], "episodes": moved})

    @bp.route("/detail/<kind>/<int:item_id>/padlock", methods=["POST"])
    def video_set_padlock(kind, item_id):
        """body {locked}: the padlock on one title. locked, automation leaves
        the title and its files alone; what a person does on its page still
        goes through."""
        from . import get_video_db
        from core.video import bulk_ops
        from core.video.sources import get_active_video_source
        body = request.get_json(silent=True) or {}
        if kind not in _KINDS or not isinstance(body.get("locked"), bool):
            return jsonify({"success": False, "error": "kind and locked are required"}), 400
        db = get_video_db()
        try:
            source = get_active_video_source()
        except Exception:   # noqa: BLE001 - no server: the lock is still ours to keep
            source = None
        try:
            res = bulk_ops._lock_title(db, kind, item_id, body["locked"], source)
        except Exception:
            logger.exception("padlock failed for %s %s", kind, item_id)
            return jsonify({"success": False, "error": "Couldn't change the padlock"}), 500
        if not res.get("ok"):
            return jsonify({"success": False,
                            "error": res.get("reason") or "Couldn't change the padlock"}), 409
        return jsonify({"success": True, "locked": body["locked"]})

    @bp.route("/files/<int:file_id>/reassign", methods=["POST"])
    def video_file_reassign(file_id):
        """body {episode_id}: file this under another episode of the same show."""
        from . import get_video_db
        from core.video import library_actions
        body = request.get_json(silent=True) or {}
        from core.video import file_history
        try:
            with file_history.batch() as batch_id:
                res = library_actions.reassign_file(get_video_db(), file_id, body.get("episode_id"))
        except Exception:
            logger.exception("reassign failed for file %s", file_id)
            return jsonify({"success": False, "error": "Couldn't move the file — see app.log"}), 500
        if not res.get("success"):
            return jsonify(res), 409
        if res.get("action") == "reassigned":
            res["batch"] = batch_id
        if res.get("renamed"):
            _refresh_server("show")
        return jsonify(res)

    @bp.route("/files/extra-copies/<kind>/<int:item_id>", methods=["GET", "POST"])
    def video_extra_copies(kind, item_id):
        """GET: what keeping the best copy would remove. POST: remove them."""
        from . import get_video_db
        from core.video import library_actions
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        db = get_video_db()
        try:
            if request.method == "GET":
                return jsonify({"success": True, **library_actions.extra_copies_plan(db, kind, item_id)})
            res = library_actions.remove_extra_copies(db, kind, item_id)
        except Exception:
            logger.exception("extra copies failed for %s %s", kind, item_id)
            return jsonify({"success": False, "error": "Couldn't work out the copies — see app.log"}), 500
        if res.get("removed"):
            logger.info("library tab removed %s extra copies of %s %s", res["removed"], kind, item_id)
            _refresh_server(kind)
        return jsonify(res)

    @bp.route("/files/consolidate/<int:show_id>", methods=["GET", "POST"])
    def video_consolidate(show_id):
        """GET ?target=: what bringing the show into one folder would move.
        POST {target}: start moving (a background job; poll /status)."""
        from . import get_video_db
        from core.video import library_actions
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        db = get_video_db()
        try:
            if request.method == "GET":
                plan = library_actions.consolidate_plan(db, show_id, request.args.get("target") or None)
                if plan.get("error"):
                    return jsonify({"success": False, "error": plan["error"]}), 409
                return jsonify({"success": True, **plan})
            body = request.get_json(silent=True) or {}
            res = library_actions.consolidate_start(
                db, show_id, body.get("target") or None,
                on_done=lambda: _refresh_server("show"))
        except Exception:
            logger.exception("consolidate failed for show %s", show_id)
            return jsonify({"success": False, "error": "Couldn't plan the move — see app.log"}), 500
        return jsonify(res), (200 if res.get("success") else 409)

    @bp.route("/files/consolidate/<int:show_id>/status", methods=["GET"])
    def video_consolidate_status(show_id):
        from core.video import library_actions
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        return jsonify({"success": True, **library_actions.consolidate_state(show_id)})

    @bp.route("/files/consolidate/<int:show_id>/stop", methods=["POST"])
    def video_consolidate_stop(show_id):
        from core.video import library_actions
        return jsonify({"success": library_actions.consolidate_stop(show_id)})

    @bp.route("/title-delete/<kind>/<int:item_id>", methods=["GET"])
    def video_title_delete_preview(kind, item_id):
        """what deleting the whole title would do -- the first confirmation."""
        from . import get_video_db
        from core.video import delete_title
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        p = delete_title.plan(get_video_db(), kind, item_id)
        if not p:
            return jsonify({"success": False, "error": "That title isn't in the library any more."}), 404
        return jsonify({"success": True, **p})

    @bp.route("/title-delete/<kind>/<int:item_id>", methods=["POST"])
    def video_title_delete(kind, item_id):
        """delete the whole title. body {confirm_title}: the title typed
        exactly -- the second confirmation, checked here, not just in the UI."""
        from . import get_video_db
        from .requests import _notify
        from core.video import delete_title
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        body = request.get_json(silent=True) or {}
        try:
            res = delete_title.run(get_video_db(), kind, item_id,
                                   typed_title=str(body.get("confirm_title") or ""),
                                   resolved_by=int(getattr(g, "profile_id", 1) or 1),
                                   notify=_notify)
        except Exception:
            logger.exception("title delete failed for %s %s", kind, item_id)
            return jsonify({"success": False, "error": "Delete failed — see app.log"}), 500
        if not res.get("success"):
            return jsonify(res), 409
        return jsonify(res)

    @bp.route("/detail/<kind>/<int:item_id>/identify/search", methods=["GET"])
    def video_identify_search(kind, item_id):
        from core.video.enrichment.engine import get_video_enrichment_engine
        if kind not in _KINDS:
            return jsonify({"error": "kind must be movie|show"}), 400
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({"results": []})
        w = get_video_enrichment_engine().workers.get("tmdb")
        client = getattr(w, "client", None)
        if not client or not hasattr(client, "search_candidates"):
            return jsonify({"error": "TMDB isn't configured"}), 503
        results = []
        try:
            # a bare TMDB id is a search too: paste it, get exactly that title
            if query.isdigit():
                d = get_video_enrichment_engine().tmdb_full_detail(kind, int(query)) or {}
                if d.get("title"):
                    results.append({"id": int(query), "title": d.get("title"),
                                    "year": int(str(d["year"])[:4]) if str(d.get("year") or "")[:4].isdigit() else None,
                                    "overview": d.get("overview") or "",
                                    "poster_url": d.get("poster_url")})
            results.extend(r for r in client.search_candidates(kind, query, limit=10)
                           if not results or r.get("id") != results[0]["id"])
        except Exception:
            logger.exception("identify search failed (%s: %r)", kind, query)
            return jsonify({"error": "Search failed — try again"}), 502
        return jsonify({"results": results})

    @bp.route("/detail/<kind>/<int:item_id>/identify", methods=["POST"])
    def video_identify(kind, item_id):
        from . import get_video_db
        from core.video import identity
        body = request.get_json(silent=True) or {}
        res = identity.identify(get_video_db(), kind, item_id, body.get("tmdb_id"))
        return jsonify(res), (200 if res.get("success") else 409)

    @bp.route("/detail/<kind>/<int:item_id>/identify/release", methods=["POST"])
    def video_identify_release(kind, item_id):
        from . import get_video_db
        from core.video import identity
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        res = identity.release(get_video_db(), kind, item_id)
        return jsonify(res), (200 if res.get("success") else 404)
