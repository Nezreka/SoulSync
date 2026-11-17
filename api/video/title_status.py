"""where a movie or show stands, for the posters and the detail page (#2160).

one batched POST for a page of posters, one GET for the detail page's status
block. the status itself comes from core/video/title_status.py, the same rule
the seerr api answers with. open to every video profile (it's reading, not
acquiring); who asked is shown to admins only.
"""

from __future__ import annotations

from datetime import date

from flask import g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.title_status")

# a page of posters, a couple of rails deep. more than this is a probe.
_MAX_IDS = 300


def _me() -> int:
    return int(getattr(g, "profile_id", 1) or 1)


def _is_admin() -> bool:
    return bool(getattr(g, "is_admin", _me() == 1))


def _server():
    try:
        from core.video.sources import resolve_video_server
        return resolve_video_server()
    except Exception:  # noqa: BLE001 - no server means no server filter
        logger.debug("title status: no video server", exc_info=True)
        return None


def _ids(raw) -> list:
    out = []
    for x in (raw or [])[:_MAX_IDS] if isinstance(raw, list) else []:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.append(v)
    return out


def _kids_ids(db, want: dict) -> dict:
    """a kids profile's poster badges: only titles the library rates under
    its cap, the same rule its discover rails go by."""
    from .kids import filter_tmdb_items, video_cap
    cap = video_cap()
    if cap is None:
        return want
    out = {}
    for kind, ids in want.items():
        kept = filter_tmdb_items(db, [{"kind": kind, "tmdb_id": i} for i in ids], cap)
        out[kind] = [it["tmdb_id"] for it in kept]
    return out


def _store():
    from core.video.title_status import TitleStatusStore

    from . import get_video_db
    db = get_video_db()
    return db, TitleStatusStore(lambda: db._get_connection())


def register_routes(bp):
    @bp.route("/title-status", methods=["POST"])
    def video_title_status_batch():
        """{movies: [tmdb ids], shows: [tmdb ids]} -> {statuses: {"movie:603":
        {status, reason, mine, progress, wished}}}. only titles with something
        to say are in the answer."""
        from core.video import title_status as ts
        body = request.get_json(silent=True) or {}
        want = {"movie": _ids(body.get("movies")), "show": _ids(body.get("shows"))}
        db, store = _store()
        want = _kids_ids(db, want)
        srv, today, me = _server(), date.today().isoformat(), _me()
        out = {}
        try:
            for kind, ids in want.items():
                if not ids:
                    continue
                got = ts.batch(db, store, kind, ids, server=srv, today=today)
                reqs = store.title_requests(kind, ids)
                if kind == "movie":
                    mine_wished = set((db.wishlist_state(movie_ids=ids, profile_id=me) or {})
                                      .get("movies") or ())
                else:
                    mine_wished = {int(k) for k, v in
                                   (db.wishlist_keys_for_shows(ids, profile_id=me) or {}).items() if v}
                for tid in ids:
                    row = got.get(tid) or {}
                    rows = reqs.get(tid) or []
                    mine = any(int(r.get("profile_id") or 0) == me for r in rows)
                    st = row.get("status")
                    if not st and not mine and tid not in mine_wished:
                        continue
                    state = row.get("state") or {}
                    out["%s:%d" % (kind, tid)] = {
                        "status": st,
                        "reason": ts.processing_reason(state) if st == ts.PROCESSING else None,
                        "progress": state.get("progress") if st == ts.DOWNLOADING else None,
                        "mine": mine, "wished": tid in mine_wished,
                        "request": ts.request_state(kind, st, state, rows, me)}
        except Exception:  # noqa: BLE001 - a missing badge never breaks a page
            logger.exception("title status batch failed")
            return jsonify({"success": False, "error": "Failed"}), 500
        return jsonify({"success": True, "statuses": out})

    @bp.route("/title-status/<kind>/<int:tmdb_id>", methods=["GET"])
    def video_title_status_one(kind, tmdb_id):
        """the detail page's block: status, why, how far along, per season for
        a show, the open requests (names for admins), and what the request
        button should say for this profile."""
        from core.video import title_status as ts
        if kind not in ("movie", "show") or tmdb_id <= 0:
            return jsonify({"success": False, "error": "kind must be movie or show"}), 400
        db, store = _store()
        from .kids import restricted_response, tmdb_title_allowed, video_cap
        cap = video_cap()
        if cap is not None and not tmdb_title_allowed(db, kind, tmdb_id, cap):
            return restricted_response()
        srv, today, me = _server(), date.today().isoformat(), _me()
        try:
            row = ts.batch(db, store, kind, [tmdb_id], server=srv, today=today).get(tmdb_id) or {}
            state = row.get("state") or {}
            st = row.get("status")
            rows = store.title_requests(kind, [tmdb_id]).get(tmdb_id) or []
            seasons = []
            if kind == "show":
                seasons = ts.season_states(row.get("coverage") or {}, store.wished_seasons(tmdb_id))
            cov = row.get("coverage") or {}
            return jsonify({
                "success": True, "kind": kind, "tmdb_id": tmdb_id, "status": st,
                "reason": ts.processing_reason(state) if st == ts.PROCESSING else None,
                "progress": state.get("progress") if st == ts.DOWNLOADING else None,
                "episodes": ({"aired": sum(a for a, _o in cov.values()),
                              "owned": sum(o for _a, o in cov.values())} if cov else None),
                "seasons": seasons,
                "requests": ts.public_requests(rows, me, _is_admin()),
                "request": ts.request_state(kind, st, state, rows, me)})
        except Exception:  # noqa: BLE001 - the page still renders without the block
            logger.exception("title status for %s %s failed", kind, tmdb_id)
            return jsonify({"success": False, "error": "Failed"}), 500
