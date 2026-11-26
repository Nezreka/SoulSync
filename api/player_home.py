"""home videos in the player (core/video/home_videos.py, HOME_VIDEOS_CONTRACT.md).

    GET  /api/player/home/overview?library=     counts, on this day, albums, months
    GET  /api/player/home/memories?date=        on this day across every home library
                                                 the profile can see (home's row)
    GET  /api/player/home/videos?library=&month=|album=|q=&offset=&limit=
    GET  /api/player/home/video/<id>            a clip and its candidate frames
    PUT  /api/player/home/video/<id>            the person's edits (admins, unless
                                                 setup lets everyone)
    GET  /api/player/home/thumb/<id>            its picture
    GET  /api/player/home/frame/<id>?at=        a candidate frame
  admin, for player setup and settings:
    GET  /api/player/home/sources               the server's home libraries, the folders
    PUT  /api/player/home/sources               {home_more?, folders?}: saved, then scanned
    GET  /api/player/home/scan                  how the scan is going
    POST /api/player/home/scan                  {root?}: scan now
    POST /api/player/home/folder-check          {path}: can soulsync read it, what's in it

every browse route answers for the profile asking: a home library hidden
from it is not there (404, like a clip that isn't).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request, send_file

from core.connect.device_access import device_playback
from core.permissions import requires_any, self_only
from utils.logging_config import get_logger

logger = get_logger("player.home")

bp = Blueprint("player_home", __name__)

SECTIONS = ("movies.access", "tv.access")


def create_blueprint():
    return bp


def _vdb():
    from api.video import get_video_db
    return get_video_db()


def _pdb():
    from api.player import player_db
    return player_db()


def _is_admin() -> bool:
    from api.player import _is_player_admin
    return _is_player_admin()


def _profile():
    from api.player import _profile as p
    return p()


def _bad(message, status=400):
    return jsonify({"success": False, "error": str(message)}), status


def _gone():
    return jsonify({"success": False, "error": "not_found", "message": "That isn't in your library."}), 404


def _forbidden():
    return jsonify({"success": False, "error": "forbidden", "message": "Admin access required."}), 403


def _hidden():
    from core.player import home_access
    return home_access.hidden_now()


def _labels(vdb):
    from core.video import home_videos as H
    return {r["id"]: r["label"] for r in H.roots(vdb)}


def _library(library_arg):
    """(library, its roots) for ?library=, the first home library when not
    given; (None, None) when it isn't one this profile can see"""
    from core.player import home_access
    libs = home_access.home_libraries(_pdb(), _vdb(), _hidden())
    if not libs:
        return None, None
    if library_arg in (None, ""):
        lib = next((x for x in libs if x["is_default"]), libs[0])
    else:
        try:
            want = int(library_arg)
        except (TypeError, ValueError):
            return None, None
        lib = next((x for x in libs if x["id"] == want), None)
        if lib is None:
            return None, None
    return {"id": lib["id"], "name": lib["name"], "in_guide": lib["in_guide"]}, lib["roots"]


def _with_progress(videos):
    """each clip's place for this profile"""
    pid = _profile()
    if pid is None or not videos:
        return videos
    from core.player import progress
    try:
        rows = progress.lookup(_pdb(), pid, "home", [str(v["id"]) for v in videos][:progress.LOOKUP_MAX])
    except Exception:  # noqa: BLE001 - no progress is a page without bars, not a failed one
        logger.debug("home progress lookup failed", exc_info=True)
        return videos
    for v in videos:
        row = rows.get(str(v["id"]))
        if row:
            v["progress"] = {"position_s": round(row["position_seconds"], 1), "watched": row["finished"]}
    return videos


def _row_seen(video_id):
    """the clip, when it is in a library this profile can see"""
    from core.player import home_access
    from core.video import home_videos as H
    vdb = _vdb()
    conn = vdb._get_connection()
    try:
        row = H.get(conn, video_id)
    finally:
        conn.close()
    if row is None or row["root"] not in home_access.visible_roots(_pdb(), vdb, _hidden()):
        return None
    return row


def _library_of_row(row):
    from core.player import libraries as L
    return L.home_library_of(L.libraries(_pdb()), row["root"])


# ── browsing ──────────────────────────────────────────────────────────────

@bp.route("/api/player/home/overview", methods=["GET"])
@device_playback
@requires_any(*SECTIONS)
def home_overview():
    from core.video import home_videos as H
    lib, roots = _library(request.args.get("library"))
    if lib is None:
        return _gone()
    vdb = _vdb()
    labels = _labels(vdb)
    conn = vdb._get_connection()
    try:
        out = H.overview(conn, roots, labels)
    except Exception:  # noqa: BLE001
        logger.exception("home overview failed")
        return _bad("Couldn't read your home videos", 500)
    finally:
        conn.close()
    _with_progress(out["on_this_day"])
    out["library"] = lib
    out["sources"] = [labels[r] for r in roots if r in labels]
    out["can_edit"] = _may_edit()
    out["scanning"] = H.status().get("state") == "running"
    return jsonify({"success": True, **out})


MEMORIES_MAX = 20


@bp.route("/api/player/home/memories", methods=["GET"])
@device_playback
@requires_any(*SECTIONS)
def home_memories():
    """{videos}: the clips filmed on this month and day in earlier years, in
    every home library this profile can see, newest first; each with the
    library it is in. ?date=YYYY-MM-DD is the viewer's own day (else the
    server's). no home library, or nothing that day: no videos."""
    import datetime as _dt

    from core.player import home_access
    from core.player import libraries as L
    from core.video import home_videos as H
    day = None
    if request.args.get("date"):
        try:
            day = _dt.date.fromisoformat(str(request.args["date"])[:10])
        except ValueError:
            return _bad("date is YYYY-MM-DD")
    pdb, vdb = _pdb(), _vdb()
    libs = home_access.home_libraries(pdb, vdb, _hidden())
    roots = [r for lib in libs for r in lib["roots"]]
    if not roots:
        return jsonify({"success": True, "videos": []})
    labels = _labels(vdb)
    conn = vdb._get_connection()
    try:
        rows = H.on_this_day_rows(conn, roots, today=day, limit=MEMORIES_MAX)
    except Exception:  # noqa: BLE001
        logger.exception("home memories failed")
        return _bad("Couldn't read your home videos", 500)
    finally:
        conn.close()
    all_libs = L.libraries(pdb)
    videos = []
    for row in rows:
        v = H.video_json(row, labels)
        v["library"] = L.home_library_of(all_libs, row["root"])
        videos.append(v)
    return jsonify({"success": True, "videos": _with_progress(videos)})


@bp.route("/api/player/home/videos", methods=["GET"])
@device_playback
@requires_any(*SECTIONS)
def home_videos_page():
    from core.video import home_videos as H
    lib, roots = _library(request.args.get("library"))
    if lib is None:
        return _gone()
    try:
        offset = int(request.args.get("offset") or 0)
        limit = int(request.args.get("limit") or 60)
    except ValueError:
        return _bad("offset and limit are numbers")
    vdb = _vdb()
    conn = vdb._get_connection()
    try:
        videos, total = H.videos(conn, roots, _labels(vdb), month=request.args.get("month") or None,
                                 album=request.args.get("album") or None, q=request.args.get("q") or None,
                                 offset=offset, limit=limit)
    except ValueError as e:
        return _bad(e)
    finally:
        conn.close()
    return jsonify({"success": True, "videos": _with_progress(videos), "total": total})


@bp.route("/api/player/home/video/<int:video_id>", methods=["GET"])
@device_playback
@requires_any(*SECTIONS)
def home_video_get(video_id):
    from core.video import home_videos as H
    row = _row_seen(video_id)
    if row is None:
        return _gone()
    out = H.detail_json(row, _labels(_vdb()))
    _with_progress([out])
    out["library"] = _library_of_row(row)
    out["can_edit"] = _may_edit()
    return jsonify({"success": True, **out})


def _may_edit() -> bool:
    if _is_admin():
        return True
    from core.player import setup as player_setup
    try:
        return player_setup.get(_pdb()).get("home_editors") == "everyone"
    except Exception:  # noqa: BLE001
        return False


@bp.route("/api/player/home/video/<int:video_id>", methods=["PUT"])
@device_playback
@requires_any(*SECTIONS)
def home_video_put(video_id):
    from core.player import home_media
    from core.video import home_videos as H
    row = _row_seen(video_id)
    if row is None:
        return _gone()
    if not _may_edit():
        return _forbidden()
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _bad("send {title?, filmed_at?, thumb_at_s?, notes?}")
    changes = {k: body[k] for k in ("title", "filmed_at", "thumb_at_s", "notes") if k in body}
    vdb = _vdb()
    labels = _labels(vdb)
    conn = vdb._get_connection()
    try:
        saved = H.edit(conn, video_id, changes, labels)
        conn.commit()
    except ValueError as e:
        conn.rollback()
        return _bad(e)
    finally:
        conn.close()
    if "thumb_at_s" in changes:
        # a frame the sheet already showed is the picture at once; else made soon
        if not home_media.use_frame(vdb, saved):
            home_media.start(vdb)
        conn = vdb._get_connection()
        try:
            saved = H.get(conn, video_id)
        finally:
            conn.close()
    out = H.detail_json(saved, labels)
    _with_progress([out])
    out["can_edit"] = True
    return jsonify({"success": True, **out})


def _jpeg(path, max_age):
    resp = send_file(path, mimetype="image/jpeg", conditional=True, max_age=max_age)
    resp.headers["Cache-Control"] = f"private, max-age={max_age}"
    return resp


@bp.route("/api/player/home/thumb/<int:video_id>", methods=["GET"])
@device_playback
@requires_any(*SECTIONS)
def home_thumb(video_id):
    import os
    row = _row_seen(video_id)
    if row is None or not row.get("thumb_path") or not os.path.isfile(row["thumb_path"]):
        return _gone()
    # the url carries ?v= for the frame it shows, so a new picture is a new url
    return _jpeg(row["thumb_path"], 7 * 86400)


@bp.route("/api/player/home/frame/<int:video_id>", methods=["GET"])
@device_playback
@requires_any(*SECTIONS)
def home_frame(video_id):
    from core.player import home_media
    row = _row_seen(video_id)
    if row is None:
        return _gone()
    try:
        at = float(request.args.get("at") or 0)
    except ValueError:
        return _bad("at is seconds into the clip")
    dur = float(row.get("duration_s") or 0)
    if at < 0 or (dur and at > dur):
        return _bad("that point isn't in the clip")
    out = home_media.candidate(_vdb(), row, round(at, 2))
    if out is None:
        return jsonify({"success": False, "error": "no_frame", "message": "Couldn't make that frame."}), 404
    return _jpeg(str(out), 86400)


# ── for the guide ─────────────────────────────────────────────────────────

GUIDE_MAX = 2000


def guide_items(player_db, video_db, hidden):
    """the clips of home libraries switched into the guide, newest first:
    [{id, title, duration_s, thumb_url, album, filmed_at}]"""
    from core.player import home_access
    from core.video import home_videos as H
    roots = [r for lib in home_access.home_libraries(player_db, video_db, hidden) if lib["in_guide"]
             for r in lib["roots"]]
    if not roots:
        return []
    conn = video_db._get_connection()
    try:
        videos, _total = H.videos(conn, roots, _labels(video_db), limit=H.PAGE_MAX)
        out = list(videos)
        while len(out) < min(_total, GUIDE_MAX):
            more, _t = H.videos(conn, roots, _labels(video_db), offset=len(out), limit=H.PAGE_MAX)
            if not more:
                break
            out.extend(more)
    finally:
        conn.close()
    return [{"id": v["id"], "title": v["title"], "duration_s": v["duration_s"], "thumb_url": v["thumb_url"],
             "album": v["album"], "filmed_at": v["filmed_at"]} for v in out if v["duration_s"]][:GUIDE_MAX]


# ── admin: where home videos come from ────────────────────────────────────

def _admin_or_403():
    if _profile() is None:
        return jsonify({"success": False, "error": "Not signed in"}), 401
    if not _is_admin():
        return _forbidden()
    return None


def _sources_payload():
    from core.video import home_videos as H
    from core.video.library_roots import home_paths
    vdb = _vdb()
    server, picks = H._server_picks(vdb)
    libs = []
    error = None
    if server in ("plex", "jellyfin"):
        try:
            from core.video.sources import _build_source
            src = _build_source()
            found = (src.available_libraries() or {}).get("home") if src is not None else None
            if found is None:
                error = f"Couldn't reach {server.capitalize()} just now."
            for item in found or []:
                libs.append({"title": item.get("title"), "count": item.get("count"),
                             "on": item.get("title") in picks,
                             "root": H.server_root_id(server, item.get("title") or "")})
        except Exception:  # noqa: BLE001 - the folders still work without the server
            logger.debug("home sources: server libraries unreadable", exc_info=True)
            error = f"Couldn't reach {server.capitalize()} just now."
    roots = H.roots(vdb)
    conn = vdb._get_connection()
    try:
        counts = H.root_counts(conn, [r["id"] for r in roots])
    finally:
        conn.close()
    for lib in libs:
        lib["videos"] = counts.get(lib["root"], (0, []))[0]
    same = H.twins(vdb)
    folders = [{"path": p, "root": H._norm(p), "videos": counts.get(H._norm(p), (0, []))[0],
                # clips also given by the server, counted once there
                "shared": int((same.get(H._norm(p)) or {}).get("count") or 0),
                "shared_with": (same.get(H._norm(p)) or {}).get("with") or None}
               for p in home_paths(vdb)]
    return {"success": True, "server": server if server in ("plex", "jellyfin") else None,
            "libraries": libs, "folders": folders, "error": error, "scan": H.status()}


@bp.route("/api/player/home/sources", methods=["GET"])
@device_playback
@self_only
def home_sources_get():
    refused = _admin_or_403()
    if refused:
        return refused
    return jsonify(_sources_payload())


@bp.route("/api/player/home/sources", methods=["PUT"])
@device_playback
@self_only
def home_sources_put():
    """{home_more?: [title], folders?: [path]}: saved whole, then the new
    ones read at once (just the one, when one was added)"""
    import json

    from core.video import home_videos as H
    from core.video.library_roots import HOME_KEY, home_paths, normalize_paths
    refused = _admin_or_403()
    if refused:
        return refused
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _bad("send {home_more?, folders?}")
    vdb = _vdb()
    before = {r["id"] for r in H.roots(vdb)}
    if "folders" in body:
        try:
            folders = normalize_paths(body["folders"])
        except ValueError as e:
            return _bad(e)
        if folders != home_paths(vdb):
            vdb.set_setting(HOME_KEY, json.dumps(folders))
    if "home_more" in body:
        titles = body["home_more"]
        if not isinstance(titles, list) or any(not isinstance(t, str) for t in titles):
            return _bad("home_more is a list of library names")
        server, _picks = H._server_picks(vdb)
        if server not in ("plex", "jellyfin"):
            return _bad("There's no Plex or Jellyfin connected for video.")
        vdb.set_home_libraries(server, titles)
    H.forget_roots()
    after = {r["id"] for r in H.roots(vdb)}
    new = sorted(after - before)
    if after != before:
        H.scan_async(vdb, only=new[0] if len(new) == 1 else None)
    try:
        from core.player import libraries as L
        L.ensure_home(_pdb(), vdb)
    except Exception:  # noqa: BLE001
        logger.debug("home library not made yet", exc_info=True)
    return jsonify(_sources_payload())


@bp.route("/api/player/home/scan", methods=["GET"])
@device_playback
@self_only
def home_scan_status():
    from core.video import home_videos as H
    refused = _admin_or_403()
    if refused:
        return refused
    return jsonify({"success": True, "scan": H.status()})


@bp.route("/api/player/home/scan", methods=["POST"])
@device_playback
@self_only
def home_scan_start():
    from core.video import home_videos as H
    refused = _admin_or_403()
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    root = body.get("root") if isinstance(body, dict) else None
    started = H.scan_async(_vdb(), only=str(root) if root else None)
    return jsonify({"success": True, "started": started, "scan": H.status()})


@bp.route("/api/player/home/folder-check", methods=["POST"])
@device_playback
@self_only
def home_folder_check():
    """can soulsync read this folder, and how many clips are in it (the first
    PREVIEW of them; reading never writes)"""
    import os

    from core.video import home_videos as H
    refused = _admin_or_403()
    if refused:
        return refused
    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip() if isinstance(body, dict) else ""
    if not path or "\x00" in path:
        return jsonify({"success": True, "ok": False, "message": "Type a folder."})
    if not os.path.isdir(path):
        return jsonify({"success": True, "ok": False, "message": "SoulSync can't find that folder."})
    found = H.walk_folder(path, limit=PREVIEW)
    if found is None:
        return jsonify({"success": True, "ok": False, "message": "SoulSync can't read that folder."})
    n = len(found)
    if not n:
        return jsonify({"success": True, "ok": True, "videos": 0,
                        "message": "No videos in it yet. Ones added later show up."})
    words = f"{PREVIEW}+ videos" if n >= PREVIEW else f"{n} video{'s' if n != 1 else ''}"
    return jsonify({"success": True, "ok": True, "videos": n, "more": n >= PREVIEW,
                    "message": f"Found {words}."})


PREVIEW = 500
