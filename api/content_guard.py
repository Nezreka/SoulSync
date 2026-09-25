"""kids profiles on the music side: hide_explicit, enforced at the http edge.

the music routes live in web_server.py, so instead of threading a check
through each one this hooks the app once (register(app)):

  hard block (before the route runs, 403 restricted)
    POST /api/library/play        body file_path / track_id
    GET  /stream/library-audio    ?path= / ?track_id=
    POST /api/stream/start, GET /stream/audio
                                  playing a soulseek search result: there's
                                  no explicit data on a peer's file, so a
                                  hide_explicit profile can't play them at all

  filtered (after the route, only for a hide_explicit profile)
    POST /api/enhanced-search                 spotify_tracks / spotify_albums
    POST /api/enhanced-search/source/<src>    ndjson tracks / albums lines
    GET  /api/album/<id>/tracks               tracks (all of them on an explicit album)
    GET  /api/library/artist/<id>/enhanced    albums and their tracks
    POST /api/enhanced-search/by-id, GET /api/artist-detail/<id>,
    GET  /api/artist/<id>/discography         any card flagged explicit, any depth

explicit NULL is allowed on purpose: most libraries carry no explicit data,
and hiding every unknown would empty them. only a truthy flag counts
(core/content_filter.is_explicit).

nothing here runs for an admin or an unrestricted profile past one cached
lookup of the profile's restrictions.
"""

from __future__ import annotations

import json
import re

from flask import jsonify, request

from core.content_filter import current_restrictions, is_explicit
from utils.logging_config import get_logger

logger = get_logger("content_guard")

_get_database = None

_ALBUM_TRACKS = re.compile(r"^/api/album/[^/]+/tracks$")
_ENHANCED_ARTIST = re.compile(r"^/api/library/artist/[^/]+/enhanced$")
_SEARCH_SOURCE = re.compile(r"^/api/enhanced-search/source/[^/]+$")
# payloads with explicit-flagged cards nested at any depth: cleaned whole
_DEEP = re.compile(r"^(/api/enhanced-search/by-id|/api/artist-detail/[^/]+|/api/artist/[^/]+/discography)$")


def _hide_explicit() -> bool:
    try:
        return bool(current_restrictions().get("hide_explicit"))
    except Exception:  # noqa: BLE001 - the kids guard fails closed
        logger.exception("content guard: restrictions unreadable")
        return True


def _restricted():
    return jsonify({"success": False, "error": "restricted", "restricted": True}), 403


def track_is_explicit(db, file_path=None, track_id=None) -> bool:
    """is the library track behind this path / id explicit (its own flag or
    its album's). a path the tracks table doesn't know exactly is matched on
    its last two parts (album folder + file), so a re-rooted path can't walk
    around the check."""
    fp = str(file_path or "").strip()
    tid = str(track_id or "").strip()
    if not fp and not tid:
        return False
    sql = ("SELECT t.explicit, a.explicit FROM tracks t "
           "LEFT JOIN albums a ON a.id = t.album_id WHERE ")
    conds, params = [], []
    if fp:
        conds.append("t.file_path = ?")
        params.append(fp)
    if tid:
        conds.append("CAST(t.id AS TEXT) = ?")
        params.append(tid)
    with db._get_connection() as conn:
        rows = conn.execute(sql + " OR ".join(conds), params).fetchall()
        if not rows and fp:
            parts = [p for p in re.split(r"[\\/]+", fp) if p]
            tail = "/".join(parts[-2:])
            if tail:
                esc = tail.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = conn.execute(
                    sql + "REPLACE(t.file_path, '\\', '/') LIKE ? ESCAPE '\\'",
                    ("%/" + esc,)).fetchall()
    return any(is_explicit(r[0]) or is_explicit(r[1]) for r in rows)


_UNVOUCHED_STREAMS = {("/api/stream/start", "POST"), ("/stream/audio", "GET")}


def _guard_play():
    path = request.path
    if (path, request.method) in _UNVOUCHED_STREAMS:
        return _restricted() if _hide_explicit() else None
    if path == "/api/library/play" and request.method == "POST":
        data = request.get_json(silent=True) or {}
        fp, tid = data.get("file_path"), data.get("track_id")
    elif path == "/stream/library-audio":
        fp, tid = request.args.get("path"), request.args.get("track_id")
    else:
        return None
    if not _hide_explicit():
        return None
    try:
        blocked = track_is_explicit(_get_database(), file_path=fp, track_id=tid)
    except Exception:  # noqa: BLE001 - can't check it, don't play it
        logger.exception("content guard: explicit lookup failed")
        blocked = True
    if blocked:
        return _restricted()
    return None


def _clean(items):
    return [x for x in (items or []) if not (isinstance(x, dict) and is_explicit(x.get("explicit")))]


def _filter_ndjson(chunks):
    """ndjson search stream: drop explicit rows out of tracks / albums lines."""
    buf = ""
    for chunk in chunks:
        buf += chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            yield _filter_line(line) + "\n"
    if buf:
        yield _filter_line(buf)


def _filter_line(line):
    try:
        obj = json.loads(line)
    except ValueError:
        return line
    if isinstance(obj, dict) and obj.get("type") in ("tracks", "albums"):
        obj["data"] = _clean(obj.get("data"))
        return json.dumps(obj)
    return line


def _deep_clean(value):
    """drop explicit cards from every list, however deep."""
    if isinstance(value, list):
        return [_deep_clean(x) for x in value if not (isinstance(x, dict) and is_explicit(x.get("explicit")))]
    if isinstance(value, dict):
        return {k: _deep_clean(v) for k, v in value.items()}
    return value


def _filter_response(response):
    path = request.path
    wanted = (path == "/api/enhanced-search" or _SEARCH_SOURCE.match(path)
              or _ALBUM_TRACKS.match(path) or _ENHANCED_ARTIST.match(path) or _DEEP.match(path))
    if not wanted or response.status_code != 200 or not _hide_explicit():
        return response

    if _SEARCH_SOURCE.match(path) and "ndjson" in (response.mimetype or ""):
        response.response = _filter_ndjson(response.response)
        response.headers.pop("Content-Length", None)
        return response

    data = response.get_json(silent=True)
    if not isinstance(data, dict):
        return response
    if _DEEP.match(path):
        response.set_data(json.dumps(_deep_clean(data)))
        return response
    if path == "/api/enhanced-search" or _SEARCH_SOURCE.match(path):
        for key in ("spotify_tracks", "spotify_albums", "tracks", "albums"):
            if isinstance(data.get(key), list):
                data[key] = _clean(data[key])
    elif _ALBUM_TRACKS.match(path):
        album = data.get("album") if isinstance(data.get("album"), dict) else {}
        data["tracks"] = [] if is_explicit(album.get("explicit")) else _clean(data.get("tracks"))
    else:
        albums = []
        for al in data.get("albums") or []:
            if not isinstance(al, dict) or is_explicit(al.get("explicit")):
                continue
            if isinstance(al.get("tracks"), list):
                al = {**al, "tracks": _clean(al["tracks"])}
            albums.append(al)
        data["albums"] = albums
    response.set_data(json.dumps(data))
    return response


def register(app, get_database):
    """hook the kids guard into the app. once, from web_server."""
    global _get_database
    _get_database = get_database
    app.before_request(_guard_play)
    app.after_request(_filter_response)
