"""kids profiles on the music side: hide_explicit, enforced at the http edge.

the music routes live in web_server.py, so instead of threading a check
through each one this hooks the app once (register(app)):

  hard block (before the route runs, 403 restricted)
    POST /api/library/play        body file_path / track_id
    GET  /stream/library-audio    ?path= / ?track_id=
    POST /api/stream/start        playing a soulseek search result: there's
    POST /api/verification/<id>/play, /api/quarantine/<id>/play
                                  no explicit data on a peer's file or an
                                  unchecked download, so a hide_explicit
                                  profile can't play them at all
    GET  /stream/audio            serves whatever the listener's session
                                  points at. allowed only while that's the
                                  library file /api/library/play checked
                                  (it vouches for it in the session); once
                                  anything else moves the session on, no

  filtered (after the route, only for a hide_explicit profile)
    POST /api/enhanced-search                 spotify_tracks / spotify_albums
    POST /api/enhanced-search/source/<src>    ndjson tracks / albums lines
    GET  /api/album/<id>/tracks               tracks (all of them on an explicit album)
    GET  /api/library/artist/<id>/enhanced    albums and their tracks
    POST /api/enhanced-search/by-id, GET /api/artist-detail/<id>,
    GET  /api/artist/<id>/discography         any card flagged explicit, any depth

  library v2 (this branch's library pages)
    GET  /api/library/v2/artists/<id>             albums / eps / singles
    GET  /api/library/v2/albums/<id>              tracks (all of them on an explicit album)
    GET  /api/library/v2/artists/<id>/play-queue  files of explicit tracks
    GET  /api/library/v2/tracks/<id>              an explicit track is 403 restricted

  more lists (S17, #2166): what a kid can still reach elsewhere
    GET  /api/library/radio, /api/library/tracks(/recent),
         /api/library/search-tracks, /api/library/recently-added
                                              library tracks: their own flag,
                                              else the catalogue's (lib2)
    GET  /api/discover/*, /api/personalized/*, /api/stats/*
                                              any card flagged explicit, any
                                              depth; library tracks by lookup
    /api/sample/*                             a library track that's explicit
                                              is 403 restricted (Sample Studio
                                              plays and splits its audio)
    POST /api/music-video/download            no explicit data on a video:
                                              a hide_explicit profile can't
                                              fetch one (asking is refused in
                                              api/music_requests.py)
  wishlist adds are held in the database (core.permission_store.wish_refusal)

which profile is restricted: music.hide_explicit in the profile's permission
map (g.perms, so a global policy can force it on), or its hide_explicit
column; either one hides.

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
_get_stream_state = None

_ALBUM_TRACKS = re.compile(r"^/api/album/[^/]+/tracks$")
_ENHANCED_ARTIST = re.compile(r"^/api/library/artist/[^/]+/enhanced$")
_SEARCH_SOURCE = re.compile(r"^/api/enhanced-search/source/[^/]+$")
_LIB2_ARTIST = re.compile(r"^/api/library/v2/artists/\d+$")
_LIB2_ALBUM = re.compile(r"^/api/library/v2/albums/\d+$")
_LIB2_PLAY_QUEUE = re.compile(r"^/api/library/v2/artists/\d+/play-queue$")
_LIB2_TRACK = re.compile(r"^/api/library/v2/tracks/(\d+)$")
# payloads with explicit-flagged cards nested at any depth: cleaned whole
# (+ gap-fill: Library v2's discovery view draws its extra releases from it)
_DEEP = re.compile(r"^(/api/enhanced-search/by-id|/api/artist-detail/[^/]+"
                   r"|/api/artist/[^/]+/discography(/gap-fill)?)$")


_LIBRARY_TRACK_LISTS = {
    "/api/library/radio": ("tracks",),
    "/api/library/tracks": ("data", "tracks"),
    "/api/library/tracks/recent": ("data", "tracks"),
    "/api/library/search-tracks": ("tracks",),
}
_FEEDS = re.compile(r"^/api/(discover|personalized|stats)/")
_SAMPLE = "/api/sample/"


def _hide_explicit() -> bool:
    try:
        from flask import g
        if "perms" in g:
            from core.permissions import restricted
            if restricted("music.hide_explicit"):
                return True
    except Exception:  # noqa: BLE001 - the kids guard fails closed
        logger.exception("content guard: permissions unreadable")
        return True
    try:
        return bool(current_restrictions().get("hide_explicit"))
    except Exception:  # noqa: BLE001 - the kids guard fails closed
        logger.exception("content guard: restrictions unreadable")
        return True


def _track_ref(item, *, ids_are_tracks=False):
    """(file_path, lib2 id, server/legacy id) of a library track card, or None
    when the card names no library track."""
    if not isinstance(item, dict):
        return None
    fp = item.get("file_path") or item.get("path")
    lib2 = item.get("lib2_track_id")
    tid = item.get("track_id") or item.get("db_track_id") or item.get("legacy_track_id")
    if tid is None and ids_are_tracks:
        tid = item.get("id")
    if not fp and lib2 in (None, "") and tid in (None, ""):
        return None
    return fp, lib2, tid


def _explicit_card(conn, item, *, ids_are_tracks=False):
    """a card flagged explicit, or a library track the catalogue says is."""
    if not isinstance(item, dict):
        return False
    if is_explicit(item.get("explicit")):
        return True
    album = item.get("album")
    if isinstance(album, dict) and is_explicit(album.get("explicit")):
        return True
    ref = _track_ref(item, ids_are_tracks=ids_are_tracks)
    if ref is None or conn is None:
        return False
    from core.library2.explicit import library_track_is_explicit
    fp, lib2, tid = ref
    try:
        return library_track_is_explicit(conn, file_path=fp, lib2_track_id=lib2, track_id=tid)
    except Exception:  # noqa: BLE001 - can't check it, don't show it
        logger.exception("content guard: explicit lookup failed")
        return True


def _drop_explicit(value, conn, *, ids_are_tracks=False):
    """drop explicit cards from every list, however deep, library tracks by
    lookup. the walk keeps every other shape as it was."""
    if isinstance(value, list):
        return [_drop_explicit(x, conn, ids_are_tracks=ids_are_tracks) for x in value
                if not _explicit_card(conn, x, ids_are_tracks=ids_are_tracks)]
    if isinstance(value, dict):
        return {k: _drop_explicit(v, conn, ids_are_tracks=ids_are_tracks) for k, v in value.items()}
    return value


def _sample_track_ids():
    ids = [(request.view_args or {}).get("track_id"), request.args.get("track_id")]
    if request.method in ("POST", "PUT", "PATCH"):
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            ids.append(body.get("track_id"))
    return [t for t in ids if t not in (None, "")]


def _restricted():
    return jsonify({"success": False, "error": "restricted", "restricted": True}), 403


def track_is_explicit(db, file_path=None, track_id=None, lib2_track_id=None) -> bool:
    """is the library track behind this path / id explicit (its own flag or
    its album's). Library v2 answers it (core/library2/explicit.py): the
    catalogue here is lib2, the legacy tables are not read any more."""
    from core.library2.explicit import library_track_is_explicit
    with db._get_connection() as conn:
        return library_track_is_explicit(conn, file_path=file_path,
                                         lib2_track_id=lib2_track_id, track_id=track_id)


_UNVOUCHED_STREAMS = {("/api/stream/start", "POST")}
_REVIEW_PLAY = re.compile(r"^/api/(verification|quarantine)/[^/]+/play$")
# the session key /api/library/play sets to the file (or server stream) it
# checked. /stream/audio for a kid only serves while the session still
# points at exactly that
VOUCHED_KEY = "vouched_source"


def session_source(state) -> str:
    """what a stream session currently plays: its server stream or its file"""
    return str(state.get("stream_url") or state.get("file_path") or "")


def _stream_is_vouched() -> bool:
    if _get_stream_state is None:
        return False
    sess = _get_stream_state()
    with sess.lock:
        if sess.get("status") != "ready" or not sess.get("is_library"):
            return False
        vouched = sess.get(VOUCHED_KEY)
        return bool(vouched) and vouched == session_source(sess)


def _guard_play():
    path = request.path
    if path.startswith(_SAMPLE):
        ids = _sample_track_ids()
        if not ids or not _hide_explicit():
            return None
        try:
            with _get_database()._get_connection() as conn:
                from core.library2.explicit import library_track_is_explicit
                blocked = any(library_track_is_explicit(conn, track_id=t) for t in ids)
        except Exception:  # noqa: BLE001 - can't check it, don't open it
            logger.exception("content guard: explicit lookup failed")
            blocked = True
        return _restricted() if blocked else None
    if path == "/api/music-video/download" and request.method == "POST":
        return _restricted() if _hide_explicit() else None
    if (path, request.method) in _UNVOUCHED_STREAMS or (
            request.method == "POST" and _REVIEW_PLAY.match(path)):
        return _restricted() if _hide_explicit() else None
    if path == "/stream/audio" and request.method == "GET":
        if not _hide_explicit():
            return None
        try:
            return None if _stream_is_vouched() else _restricted()
        except Exception:  # noqa: BLE001 - can't tell what it is, don't play it
            logger.exception("content guard: stream session unreadable")
            return _restricted()
    lib2_tid = None
    if path == "/api/library/play" and request.method == "POST":
        data = request.get_json(silent=True) or {}
        fp, lib2_tid = data.get("file_path"), data.get("lib2_track_id")
        tid = data.get("track_id") or data.get("server_track_id") or data.get("legacy_track_id")
    elif path == "/stream/library-audio":
        fp, tid = request.args.get("path"), request.args.get("track_id")
    else:
        return None
    if not _hide_explicit():
        return None
    try:
        blocked = track_is_explicit(_get_database(), file_path=fp, track_id=tid, lib2_track_id=lib2_tid)
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


def _filter_lib2(path, response):
    """the library v2 pages: same rules as the legacy ones above."""
    track = _LIB2_TRACK.match(path)
    if track:
        try:
            blocked = track_is_explicit(_get_database(), lib2_track_id=track.group(1))
        except Exception:  # noqa: BLE001 - can't check it, don't show it
            logger.exception("content guard: explicit lookup failed")
            blocked = True
        if not blocked:
            return response
        body, status = _restricted()
        body.status_code = status
        return body
    data = response.get_json(silent=True)
    if not isinstance(data, dict):
        return response
    if _LIB2_ARTIST.match(path) and isinstance(data.get("artist"), dict):
        artist = data["artist"]
        for key in ("albums", "eps", "singles"):
            if isinstance(artist.get(key), list):
                artist[key] = _clean(artist[key])
    elif _LIB2_ALBUM.match(path) and isinstance(data.get("album"), dict):
        album = data["album"]
        album["tracks"] = [] if is_explicit(album.get("explicit")) else _clean(album.get("tracks"))
    elif _LIB2_PLAY_QUEUE.match(path) and isinstance(data.get("files"), list):
        from core.library2.explicit import explicit_track_ids
        try:
            with _get_database()._get_connection() as conn:
                hidden = explicit_track_ids(conn, (f.get("track_id") for f in data["files"] if isinstance(f, dict)))
            data["files"] = [f for f in data["files"] if not (isinstance(f, dict) and f.get("track_id") in hidden)]
        except Exception:  # noqa: BLE001 - can't check them, don't queue them
            logger.exception("content guard: explicit lookup failed")
            data["files"] = []
    else:
        return response
    response.set_data(json.dumps(data))
    return response
def _deep_clean(value):
    """drop explicit cards from every list, however deep."""
    if isinstance(value, list):
        return [_deep_clean(x) for x in value if not (isinstance(x, dict) and is_explicit(x.get("explicit")))]
    if isinstance(value, dict):
        return {k: _deep_clean(v) for k, v in value.items()}
    return value


def _filter_lists(path, response):
    """S17: the library lists and the discover / stats feeds."""
    data = response.get_json(silent=True)
    if not isinstance(data, (dict, list)):
        return response
    library = path in _LIBRARY_TRACK_LISTS or path == "/api/library/recently-added"
    try:
        with _get_database()._get_connection() as conn:
            if path in _LIBRARY_TRACK_LISTS:
                keys = _LIBRARY_TRACK_LISTS[path]
                holder = data
                for k in keys[:-1]:
                    holder = holder.get(k) if isinstance(holder, dict) else None
                if isinstance(holder, dict) and isinstance(holder.get(keys[-1]), list):
                    holder[keys[-1]] = _drop_explicit(holder[keys[-1]], conn, ids_are_tracks=True)
            else:
                data = _drop_explicit(data, conn, ids_are_tracks=library)
    except Exception:  # noqa: BLE001 - can't check them, don't show them
        logger.exception("content guard: explicit lookup failed")
        body, status = _restricted()
        body.status_code = status
        return body
    response.set_data(json.dumps(data))
    return response


def _filter_response(response):
    path = request.path
    if (path in _LIBRARY_TRACK_LISTS or path == "/api/library/recently-added" or _FEEDS.match(path)) \
            and request.method == "GET":
        if response.status_code != 200 or "json" not in (response.mimetype or "") or not _hide_explicit():
            return response
        return _filter_lists(path, response)
    lib2 = (_LIB2_ARTIST.match(path) or _LIB2_ALBUM.match(path)
            or _LIB2_PLAY_QUEUE.match(path) or _LIB2_TRACK.match(path))
    wanted = (path == "/api/enhanced-search" or _SEARCH_SOURCE.match(path)
              or _ALBUM_TRACKS.match(path) or _ENHANCED_ARTIST.match(path) or _DEEP.match(path)
              or lib2)
    if not wanted or response.status_code != 200 or not _hide_explicit():
        return response
    if lib2:
        return _filter_lib2(path, response)

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


def register(app, get_database, get_stream_state=None):
    """hook the kids guard into the app. once, from web_server."""
    global _get_database, _get_stream_state
    _get_database = get_database
    _get_stream_state = get_stream_state
    app.before_request(_guard_play)
    app.after_request(_filter_response)
