"""music apps: the Subsonic / OpenSubsonic api at /rest (PAIRING_SPEC.md 5).

every request is a paired device acting as its profile (web_server's
device gate runs ``authenticate`` for /rest before anything else), with
playback scope only. what a profile sees is its own library: own-library
rules, hidden explicit music, its own playlists, stars, ratings, queue
and plays.

one Flask route takes every method (``/rest/<method>`` and
``/rest/<method>.view``, GET or POST); METHODS is the whole list and
tests/subsonic pins it. answers are json, jsonp or xml (``f``), errors in
the same envelope with the spec's codes, always http 200 the way apps
expect.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from flask import Blueprint, Response, g, request, send_file

from core.connect.device_access import device_playback
from core.permissions import device_only
from core.security.rate_limit import AttemptLimiter
from core.subsonic import library as L
from core.subsonic import response as R
from utils.logging_config import get_logger

logger = get_logger("api.subsonic")

bp = Blueprint("subsonic", __name__)

PREFIX = "/rest/"
PUBLIC_METHODS = frozenset({"getOpenSubsonicExtensions"})
MUSIC_FOLDER_ID = 1
NOW_PLAYING_MINUTES = 15
MAX_LIST = 500

get_database: Callable[[], Any] = None  # type: ignore[assignment]
config_manager: Any = None
_hooks: Dict[str, Any] = {}

# wrong passwords and api keys per address: 10 a minute
_auth_limiter = AttemptLimiter(max_attempts=10, window_seconds=60)


def configure(*, get_database, config_manager, serve_file=None, resolve_path=None, proxy_url=None,
              media_engine=None, flush_scrobbles=None, version: str = ""):
    """``serve_file(path)`` answers with Range support, ``resolve_path(path)``
    finds a stored path on this machine (folder overrides and all),
    ``proxy_url(url)`` streams a url through with Range, ``media_engine()``
    is the media servers, ``flush_scrobbles()`` sends marked plays now."""
    globals()["get_database"] = get_database
    globals()["config_manager"] = config_manager
    _hooks.update(serve_file=serve_file, resolve_path=resolve_path, proxy_url=proxy_url,
                  media_engine=media_engine, flush_scrobbles=flush_scrobbles)
    R.set_server_version(version)


def create_blueprint():
    return bp


def _cfg(key, default=None):
    try:
        return config_manager.get(key, default) if config_manager is not None else default
    except Exception:  # noqa: BLE001
        return default


# ── the request ──────────────────────────────────────────────────────────

def is_rest_path(path: str) -> bool:
    return bool(path) and path.startswith(PREFIX)


def method_name(path: str) -> str:
    name = path[len(PREFIX):] if is_rest_path(path) else ""
    return name[:-5] if name.endswith(".view") else name


def _params():
    """query and form together (formPost): many methods repeat a param."""
    return request.values


def _one(name: str, default: Any = None) -> Any:
    v = _params().get(name)
    return default if v is None or v == "" else v


def _required(name: str) -> str:
    v = _one(name)
    if v is None:
        raise R.missing(name)
    return v


def _int(name: str, default: int, lo: int = 0, hi: int = 10 ** 9) -> int:
    v = _one(name)
    if v is None:
        return default
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        raise R.SubsonicError(R.GENERIC, "%s must be a number" % name) from None


def _bool(name: str, default: bool) -> bool:
    v = _one(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes")


def _render(body: Dict[str, Any]) -> Response:
    params = _params()
    text, mime = R.render(body, params.get("f") or "xml", params.get("callback") or "")
    return Response(text, status=200, mimetype=mime)


def error_response(code: int, message: str) -> Response:
    return _render(R.failed(code, message))


def ok(payload: Optional[Dict[str, Any]] = None) -> Response:
    return _render(R.ok(payload))


# ── who it is (called by web_server's device gate) ───────────────────────

def authenticate(database) -> tuple:
    """(device row, None), (None, None) for a public method asked without
    credentials, or (None, refusal response)."""
    from core.subsonic import auth as A
    params = _params()
    name = method_name(request.path)
    if not A.credentials_present(params):
        if name in PUBLIC_METHODS:
            return None, None
    ip = request.remote_addr or "unknown"
    now = time.time()
    locked, _retry = _auth_limiter.is_locked(ip, now)
    if locked:
        return None, error_response(R.WRONG_CREDENTIALS, "Too many wrong sign-ins. Wait a minute and try again.")
    from core.connect import device_tokens as T
    from core.subsonic import store as S

    def profiles_named(user):
        with closing(database._get_connection()) as conn:
            return [int(r[0]) for r in conn.execute(
                "SELECT id FROM profiles WHERE LOWER(name) = LOWER(?)", (user,)).fetchall()]

    row, refusal = A.resolve(params, find_token=lambda tok: T.find(database, tok),
                             profiles_named=profiles_named,
                             devices_for=lambda pids: S.devices_with_passwords(database, pids),
                             decrypt=_decrypt)
    if refusal is not None:
        if refusal[0] in (R.WRONG_CREDENTIALS, R.INVALID_API_KEY):
            _auth_limiter.record_failure(ip, now)
        return None, error_response(*refusal)
    return row, None


def _decrypt(value: str) -> Optional[str]:
    if not value:
        return None
    out = config_manager._decrypt_value(value)
    # a value that won't decrypt comes back as itself: that is no password
    if not isinstance(out, str) or out == value or out.startswith("gAAAAA"):
        return None
    return out


def _encrypt(value: str) -> str:
    return config_manager._encrypt_value(value)


# ── one method's context ─────────────────────────────────────────────────

class Ctx:
    def __init__(self, conn):
        from core.library_scope import library_scope_for_profile
        self.db = get_database()
        self.conn = conn
        self.device = g.get("device") or {}
        self.profile_id = int(g.get("profile_id") or 0)
        self.profile_name = g.get("profile_name") or ""
        self.scope = library_scope_for_profile(self.profile_id)
        self.hide_explicit = _hide_explicit()
        self._lib: Optional[L.Library] = None

    @property
    def lib(self) -> L.Library:
        if self._lib is None:
            from core.subsonic import bridge as B
            readable = _readable()
            self._lib = L.Library(
                self.conn, db_key=str(getattr(self.db, "database_path", id(self.db))), scope=self.scope,
                hide_explicit=self.hide_explicit, starred=_starred_map(self), ratings=_ratings_map(self),
                bridge=(lambda src, path: B.marker(_cfg, readable, src, path)) if readable else None)
        return self._lib


def _hide_explicit() -> bool:
    from api.content_guard import _hide_explicit as guard
    return guard()


_readable_obj = None
_readable_lock = threading.Lock()


def _readable():
    global _readable_obj
    from core.subsonic import bridge as B
    if not any(B.settings(_cfg).values()):
        return None
    with _readable_lock:
        if _readable_obj is None and _hooks.get("resolve_path"):
            _readable_obj = B.Readable(_hooks["resolve_path"])
        return _readable_obj


# ── the route ────────────────────────────────────────────────────────────

METHODS: Dict[str, Callable[[Ctx], Any]] = {}


def method(*names):
    def deco(fn):
        for n in names:
            METHODS[n] = fn
        return fn
    return deco


@bp.route("/rest/<path:name>", methods=["GET", "POST"])
@device_only
@device_playback
def rest(name):
    """every subsonic method. the device gate has already said who this is."""
    from core.library_scope import library_scope
    name = name[:-5] if name.endswith(".view") else name
    handler = METHODS.get(name)
    if handler is None:
        return error_response(R.NOT_FOUND, "SoulSync doesn't have %s." % name)
    if name not in PUBLIC_METHODS and not (g.get("device_auth") and g.get("device")
                                           and g.get("profile_id") is not None):
        return error_response(R.NOT_AUTHORIZED, "Sign in first.")
    try:
        with closing(get_database()._get_connection()) as conn:
            ctx = Ctx(conn)
            with library_scope(ctx.scope):
                out = handler(ctx)
    except R.SubsonicError as exc:
        return error_response(exc.code, exc.message)
    except Exception:  # noqa: BLE001 - an app gets the envelope, the log gets the trace
        logger.exception("music app method %s failed", name)
        return error_response(R.GENERIC, "Something went wrong on the server.")
    if isinstance(out, Response):
        return out
    return ok(out)


# ── system ───────────────────────────────────────────────────────────────

EXTENSIONS = [
    {"name": "apiKeyAuthentication", "versions": [1]},
    {"name": "formPost", "versions": [1]},
    {"name": "songLyrics", "versions": [1]},
    {"name": "transcodeOffset", "versions": [1]},
]


@method("ping")
def ping(ctx):
    return None


@method("getOpenSubsonicExtensions")
def get_extensions(ctx):
    return {"openSubsonicExtensions": EXTENSIONS}


@method("getLicense")
def get_license(ctx):
    return {"license": {"valid": True}}


@method("getScanStatus")
def get_scan_status(ctx):
    return {"scanStatus": {"scanning": False, "count": len(ctx.lib.snap().albums)}}


@method("startScan")
def start_scan(ctx):
    raise R.SubsonicError(R.NOT_AUTHORIZED, "Library scans run from SoulSync.")


@method("tokenInfo")
def token_info(ctx):
    return {"tokenInfo": {"username": ctx.profile_name}}


@method("getUser")
def get_user(ctx):
    user = _one("username") or ctx.profile_name
    if user.lower() != ctx.profile_name.lower():
        raise R.SubsonicError(R.NOT_AUTHORIZED, "A music app only sees its own profile.")
    from core.permissions import can, context_from_g
    may_get = can(context_from_g(), "music.get", "ask")
    return {"user": {"username": ctx.profile_name, "scrobblingEnabled": True, "adminRole": False,
                     "settingsRole": False, "downloadRole": True, "uploadRole": False,
                     "playlistRole": True, "coverArtRole": True, "commentRole": False,
                     "podcastRole": False, "streamRole": True, "jukeboxRole": False,
                     "shareRole": False, "videoConversionRole": False, "folder": [MUSIC_FOLDER_ID],
                     "soulsyncCanRequest": bool(may_get)}}


@method("getMusicFolders")
def get_music_folders(ctx):
    return {"musicFolders": {"musicFolder": [{"id": MUSIC_FOLDER_ID, "name": "Music"}]}}


@method("getBookmarks")
def get_bookmarks(ctx):
    return {"bookmarks": {}}


@method("getPodcasts")
def get_podcasts(ctx):
    return {"podcasts": {}}


@method("getNewestPodcasts")
def get_newest_podcasts(ctx):
    return {"newestPodcasts": {}}


@method("getInternetRadioStations")
def get_radio_stations(ctx):
    return {"internetRadioStations": {}}


@method("getShares")
def get_shares(ctx):
    return {"shares": {}}


# ── browsing ─────────────────────────────────────────────────────────────

def _last_modified(ctx) -> int:
    """changes when a file does: the snapshot's files counter."""
    return int(ctx.lib.snap().version or 0)


@method("getArtists")
def get_artists(ctx):
    return {"artists": {"ignoredArticles": " ".join(L.IGNORED_ARTICLES), "index": ctx.lib.artist_index()}}


@method("getIndexes")
def get_indexes(ctx):
    index = []
    for group in ctx.lib.artist_index():
        index.append({"name": group["name"], "artist": [
            {"id": a["id"], "name": a["name"], "starred": a.get("starred"), "userRating": a.get("userRating"),
             "coverArt": a["coverArt"], "albumCount": a["albumCount"]} for a in group["artist"]]})
    return {"indexes": {"lastModified": _last_modified(ctx), "ignoredArticles": " ".join(L.IGNORED_ARTICLES),
                        "index": index}}


@method("getArtist")
def get_artist(ctx):
    _, aid = L.parse_id(_required("id"), L.ARTIST)
    info = ctx.lib.artist_row(aid)
    album_ids = ctx.lib.artist_album_ids(aid)
    artist = ctx.lib.artist_id3(aid, info)
    artist["album"] = ctx.lib.albums(album_ids)
    return {"artist": artist}


@method("getAlbum")
def get_album(ctx):
    _, aid = L.parse_id(_required("id"), L.ALBUM)
    row, songs = ctx.lib.album(aid)
    album = ctx.lib.album_id3(row)
    album["song"] = songs
    return {"album": album}


@method("getSong")
def get_song(ctx):
    _, tid = L.parse_id(_required("id"), L.TRACK)
    return {"song": ctx.lib.song(tid)}


@method("getMusicDirectory")
def get_music_directory(ctx):
    kind, n = L.parse_id(_required("id"), L.ARTIST, L.ALBUM)
    if kind == L.ARTIST:
        info = ctx.lib.artist_row(n)
        children = ctx.lib.albums(ctx.lib.artist_album_ids(n), as_dir=True)
        return {"directory": {"id": L.sid(L.ARTIST, n), "parent": str(MUSIC_FOLDER_ID), "name": info.get("name") or "",
                              "starred": ctx.lib.starred.get((L.ARTIST, n)), "child": children}}
    row, songs = ctx.lib.album(n)
    album = ctx.lib.album_id3(row)
    return {"directory": {"id": album["id"], "parent": album.get("artistId"), "name": album["name"],
                          "starred": album.get("starred"), "userRating": album.get("userRating"),
                          "playCount": album.get("playCount"), "child": songs}}


@method("getGenres")
def get_genres(ctx):
    return {"genres": {"genre": ctx.lib.genres()}}


# ── lists ────────────────────────────────────────────────────────────────

def _ranked_albums(ctx, kind: str) -> Optional[List[int]]:
    if kind == "starred":
        from core.subsonic import store as S
        return [int(r["item_id"]) for r in S.stars(ctx.db, ctx.profile_id) if r["kind"] == L.ALBUM]
    if kind == "highest":
        from core.subsonic import store as S
        return [int(r["item_id"]) for r in S.ratings(ctx.db, ctx.profile_id) if r["kind"] == L.ALBUM]
    if kind in ("frequent", "recent"):
        from core.listening_scope import listening_owner, stats_owner_clause
        owner = listening_owner(ctx.db, ctx.profile_id)
        order = "COUNT(*) DESC" if kind == "frequent" else "MAX(lh.played_at) DESC"
        rows = ctx.conn.execute(
            "SELECT t.album_id FROM listening_history lh JOIN lib2_tracks t ON t.id = lh.lib2_track_id "
            f"WHERE lh.lib2_track_id IS NOT NULL{stats_owner_clause(owner, 'lh')} "
            f"GROUP BY t.album_id ORDER BY {order} LIMIT 2000").fetchall()
        return [int(r[0]) for r in rows]
    return None


def _album_list(ctx):
    kind = _required("type")
    size = _int("size", 10, 0, MAX_LIST)
    offset = _int("offset", 0)
    if kind == "byYear" and (_one("fromYear") is None or _one("toYear") is None):
        raise R.missing("fromYear" if _one("fromYear") is None else "toYear")
    if kind == "byGenre" and _one("genre") is None:
        raise R.missing("genre")
    return ctx.lib.album_list(kind, size=size, offset=offset, from_year=_one("fromYear"), to_year=_one("toYear"),
                              genre=_one("genre") or "", ranked=_ranked_albums(ctx, kind))


@method("getAlbumList")
def get_album_list(ctx):
    return {"albumList": {"album": ctx.lib.albums(_album_list(ctx), as_dir=True)}}


@method("getAlbumList2")
def get_album_list2(ctx):
    return {"albumList2": {"album": ctx.lib.albums(_album_list(ctx))}}


@method("getRandomSongs")
def get_random_songs(ctx):
    genre = _one("genre") or ""
    ids = ctx.lib.random_songs(_int("size", 10, 0, MAX_LIST), genre=genre,
                               from_year=_one("fromYear"), to_year=_one("toYear"))
    songs = ctx.lib.songs(ids)
    # #2332: a client's shuffle leaves holiday songs out of season, unless it
    # asked for a holiday genre
    from core.holiday_music import filter_tracks, genres_say_holiday
    if not genres_say_holiday([genre]):
        songs = filter_tracks(songs, database=ctx.db)
    return {"randomSongs": {"song": songs}}


@method("getSongsByGenre")
def get_songs_by_genre(ctx):
    ids = ctx.lib.genre_songs(_required("genre"), _int("offset", 0), _int("count", 10, 0, MAX_LIST))
    return {"songsByGenre": {"song": ctx.lib.songs(ids)}}


# ── search ───────────────────────────────────────────────────────────────

def _search(ctx):
    q = _one("query", "") or ""
    return ctx.lib.search(q, artist_count=_int("artistCount", 20, 0, MAX_LIST), artist_offset=_int("artistOffset", 0),
                          album_count=_int("albumCount", 20, 0, MAX_LIST), album_offset=_int("albumOffset", 0),
                          song_count=_int("songCount", 20, 0, MAX_LIST), song_offset=_int("songOffset", 0))


@method("search3")
def search3(ctx):
    artists, albums, songs = _search(ctx)
    return {"searchResult3": {"artist": [ctx.lib.artist_id3(a) for a in artists],
                              "album": ctx.lib.albums(albums), "song": ctx.lib.songs(songs)}}


@method("search2")
def search2(ctx):
    artists, albums, songs = _search(ctx)
    return {"searchResult2": {
        "artist": [{"id": L.sid(L.ARTIST, a), "name": ctx.lib.artist_id3(a)["name"],
                    "starred": ctx.lib.starred.get((L.ARTIST, a))} for a in artists],
        "album": ctx.lib.albums(albums, as_dir=True), "song": ctx.lib.songs(songs)}}


# ── stars and ratings ────────────────────────────────────────────────────

_liked_cache: Dict[tuple, Dict[int, str]] = {}
_liked_lock = threading.Lock()


def _liked_rows(ctx):
    from core.playlists.user_playlists import liked_playlist
    p = liked_playlist(ctx.db, ctx.profile_id)
    return p, (ctx.db.get_mirrored_playlist_tracks(p["id"], profile_id=ctx.profile_id) if p else [])


def _playlist_track_ids(ctx, playlist: Dict[str, Any], rows: Optional[list] = None,
                        lib: Optional[L.Library] = None) -> List[tuple]:
    """[(lib2 id or None, row)] for a playlist's rows, by the library match
    identify stamped, else by name. cached per playlist version."""
    key = (ctx.profile_id, int(playlist["id"]), str(playlist.get("updated_at") or ""),
           int(playlist.get("track_count") or 0), str(ctx.scope), ctx.hide_explicit)
    with _liked_lock:
        hit = _liked_cache.get(key)
    if rows is None:
        rows = ctx.db.get_mirrored_playlist_tracks(playlist["id"], profile_id=ctx.profile_id) or []
    if hit is not None and len(hit) == len(rows):
        return [(hit.get(i), r) for i, r in enumerate(rows)]
    out, ids = [], {}
    for i, r in enumerate(rows):
        tid = _row_track_id(lib or ctx.lib, r)
        if tid is not None:
            ids[i] = tid
        out.append((tid, r))
    with _liked_lock:
        if len(_liked_cache) > 500:
            _liked_cache.clear()
        _liked_cache[key] = ids
    return out


def _row_track_id(lib: L.Library, r) -> Optional[int]:
    extra = r.get("extra_data")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (TypeError, ValueError):
            extra = {}
    extra = extra if isinstance(extra, dict) else {}
    # a stamp only when it can be trusted (see stamped_tracks), else by name
    stamped = L.stamped_tracks(lib.conn, [{**r, "extra_data": extra}])[0]
    if stamped:
        return stamped
    return lib.find_track(r.get("track_name") or "", r.get("artist_name") or "", r.get("album_name") or "")


def _starred_map(ctx) -> Dict[tuple, str]:
    from core.subsonic import store as S
    out: Dict[tuple, str] = {}
    for r in S.stars(ctx.db, ctx.profile_id):
        out[(r["kind"], int(r["item_id"]))] = L._iso(r["starred_at"]) or ""
    try:
        p, rows = _liked_rows(ctx)
        if p:
            # the name lookups run on a Library without stars (it is being made)
            plain = L.Library(ctx.conn, db_key=str(getattr(ctx.db, "database_path", id(ctx.db))),
                              scope=ctx.scope, hide_explicit=ctx.hide_explicit)
            for tid, r in _playlist_track_ids(ctx, p, rows, lib=plain):
                if tid is not None:
                    out.setdefault((L.TRACK, tid), L._iso(r.get("added_at")) or L._iso(time.time()))
    except Exception:  # noqa: BLE001 - stars are a nicety on a list
        logger.debug("liked songs unreadable", exc_info=True)
    return out


def _ratings_map(ctx) -> Dict[tuple, int]:
    from core.subsonic import store as S
    return {(r["kind"], int(r["item_id"])): int(r["rating"]) for r in S.ratings(ctx.db, ctx.profile_id)}


def _ids(name: str) -> List[str]:
    return [v for v in _params().getlist(name) if v]


def _raw_track(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"title": row.get("title") or "", "artist": row.get("track_artist") or row.get("artist_name") or "",
            "album": row.get("album_title") or "", "duration_ms": int(row.get("duration") or 0),
            "lib2_track_id": int(row["id"])}


def _track_any(ctx, tid: int) -> Optional[Dict[str, Any]]:
    """a catalogue track whether or not this profile has its file."""
    r = ctx.conn.execute(
        "SELECT t.id, t.title, t.track_artist, t.duration, t.spotify_id, t.track_number, t.disc_number, "
        "al.title AS album_title, al.id AS album_id, al.spotify_id AS album_spotify_id, al.release_date, "
        "ar.name AS artist_name FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
        "LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id WHERE t.id = ?", (int(tid),)).fetchone()
    return dict(r) if r else None


def _want(ctx, row: Dict[str, Any]) -> str:
    """a starred song this profile doesn't have: the wishlist when it may
    get music right away, a request when it may only ask, else nothing."""
    from core.permission_store import music_rights
    profile = ctx.db.get_profile(ctx.profile_id) or {}
    mode = (music_rights(ctx.db, profile) or {}).get("mode") or "none"
    if mode == "none":
        return "none"
    artist = row.get("track_artist") or row.get("artist_name") or ""
    track_data = {
        "id": row.get("spotify_id") or "lib2:%s" % row["id"], "name": row.get("title") or "",
        "artists": [{"name": artist}], "duration_ms": int(row.get("duration") or 0),
        "track_number": row.get("track_number"), "disc_number": row.get("disc_number"),
        "album": {"id": row.get("album_spotify_id") or "lib2:%s" % row.get("album_id"),
                  "name": row.get("album_title") or "", "artists": [{"name": row.get("artist_name") or artist}],
                  "images": [], "release_date": row.get("release_date") or ""},
    }
    res = ctx.db.add_to_wishlist_detailed(spotify_track_data=track_data, failure_reason="Starred in a music app",
                                          source_type="music_app", source_info={"device": ctx.device.get("name")},
                                          profile_id=ctx.profile_id, user_initiated=True,
                                          request_approved=False)
    status = (res or {}).get("status")
    if status in ("created", "updated", "satisfied"):
        return "wishlist" if mode == "download" else "request"
    if (res or {}).get("reason") == "request_limit":
        raise R.SubsonicError(R.NOT_AUTHORIZED, "You've reached your request limit for now.")
    return "none"


def _star(ctx, on: bool):
    from core.connect.may_play import may_play_track
    from core.playlists import user_playlists as up
    from core.subsonic import store as S
    did = False
    for raw in _ids("id"):
        kind, n = L.parse_id(raw, L.TRACK, L.ALBUM, L.ARTIST)
        if kind == L.TRACK:
            row = _track_any(ctx, n)
            if not row:
                raise R.not_found("Song")
            raw_track = _raw_track(row)
            if on:
                if may_play_track(ctx.conn, n, scope=ctx.scope, hide_explicit=ctx.hide_explicit):
                    up.like(ctx.db, ctx.profile_id, [raw_track])
                elif _want(ctx, row) == "none":
                    raise R.SubsonicError(R.NOT_AUTHORIZED, "You can't add music you don't have.")
            else:
                up.unlike(ctx.db, ctx.profile_id, [raw_track])
        else:
            (S.star if on else S.unstar)(ctx.db, ctx.profile_id, kind, n)
        did = True
    for name, kind in (("albumId", L.ALBUM), ("artistId", L.ARTIST)):
        for raw in _ids(name):
            _, n = L.parse_id(raw if "-" in raw else "%s-%s" % (kind, raw), kind)
            (S.star if on else S.unstar)(ctx.db, ctx.profile_id, kind, n)
            did = True
    if not did:
        raise R.missing("id")
    with _liked_lock:
        _liked_cache.clear()


@method("star")
def star(ctx):
    _star(ctx, True)


@method("unstar")
def unstar(ctx):
    _star(ctx, False)


@method("setRating")
def set_rating(ctx):
    from core.subsonic import store as S
    kind, n = L.parse_id(_required("id"), L.TRACK, L.ALBUM, L.ARTIST)
    rating = _int("rating", -1, -1, 5)
    if rating < 0:
        raise R.missing("rating")
    S.set_rating(ctx.db, ctx.profile_id, kind, n, rating)


def _starred(ctx):
    lib = ctx.lib
    songs = sorted(((k[1], v) for k, v in lib.starred.items() if k[0] == L.TRACK), key=lambda x: x[1], reverse=True)
    albums = [k[1] for k, _ in sorted(((k, v) for k, v in lib.starred.items() if k[0] == L.ALBUM),
                                      key=lambda x: x[1], reverse=True)]
    snap = lib.snap()
    artists = [k[1] for k, _ in sorted(((k, v) for k, v in lib.starred.items() if k[0] == L.ARTIST),
                                       key=lambda x: x[1], reverse=True) if k[1] in snap.artists]
    return artists, albums, lib.songs([s for s, _ in songs])


@method("getStarred")
def get_starred(ctx):
    artists, albums, songs = _starred(ctx)
    return {"starred": {"artist": [{"id": L.sid(L.ARTIST, a), "name": ctx.lib.artist_id3(a)["name"],
                                    "starred": ctx.lib.starred.get((L.ARTIST, a))} for a in artists],
                        "album": ctx.lib.albums(albums, as_dir=True), "song": songs}}


@method("getStarred2")
def get_starred2(ctx):
    artists, albums, songs = _starred(ctx)
    return {"starred2": {"artist": [ctx.lib.artist_id3(a) for a in artists], "album": ctx.lib.albums(albums),
                         "song": songs}}


# ── plays ────────────────────────────────────────────────────────────────

_now_playing: Dict[int, Dict[str, Any]] = {}
_now_lock = threading.Lock()


def _played_at(ms: Optional[str]) -> str:
    try:
        when = datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc) if ms else datetime.now(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        when = datetime.now(timezone.utc)
    return when.strftime("%Y-%m-%d %H:%M:%S")


@method("scrobble")
def scrobble(ctx):
    from core.connect.may_play import may_play_track
    from core.playback.record_play import now_playing, record_play
    ids = _ids("id")
    if not ids:
        raise R.missing("id")
    times = _ids("time")
    submission = _bool("submission", True)
    for i, raw in enumerate(ids):
        _, tid = L.parse_id(raw, L.TRACK)
        if not may_play_track(ctx.conn, tid, scope=ctx.scope, hide_explicit=ctx.hide_explicit):
            raise R.not_found("Song")
        row = _track_any(ctx, tid) or {}
        track = {"title": row.get("title") or "", "artist": row.get("track_artist") or row.get("artist_name") or "",
                 "album": row.get("album_title") or "", "lib2_track_id": tid}
        if submission:
            record_play(ctx.db, ctx.profile_id, track, played_at=_played_at(times[i] if i < len(times) else None),
                        duration_ms=int(row.get("duration") or 0), eligible=True,
                        flush=_hooks.get("flush_scrobbles"))
        else:
            with _now_lock:
                _now_playing[int(ctx.device.get("id") or 0)] = {
                    "profile_id": ctx.profile_id, "track_id": tid, "at": time.time(),
                    "player": ctx.device.get("name") or "", "device_id": ctx.device.get("id")}
            try:
                now_playing(ctx.db, config_manager, ctx.profile_id, {
                    "title": track["title"], "artist": track["artist"], "album": track["album"],
                    "duration_seconds": int(row.get("duration") or 0) / 1000.0})
            except Exception:  # noqa: BLE001 - a scrobble account being down isn't the app's problem
                logger.debug("now playing send failed", exc_info=True)


@method("getNowPlaying")
def get_now_playing(ctx):
    cutoff = time.time() - NOW_PLAYING_MINUTES * 60
    with _now_lock:
        mine = [dict(v) for v in _now_playing.values() if v["profile_id"] == ctx.profile_id and v["at"] >= cutoff]
    entries = []
    for e in mine:
        for song in ctx.lib.songs([e["track_id"]]):
            song.update({"username": ctx.profile_name, "minutesAgo": int((time.time() - e["at"]) // 60),
                         "playerId": e.get("device_id") or 0, "playerName": e.get("player") or ""})
            entries.append(song)
    return {"nowPlaying": {"entry": entries}}


# ── play queue ───────────────────────────────────────────────────────────

@method("getPlayQueue")
def get_play_queue(ctx):
    from core.subsonic import store as S
    q = S.load_queue(ctx.db, ctx.profile_id, int(ctx.device.get("id") or 0))
    if not q:
        return None
    ids = []
    for raw in q["track_ids"]:
        try:
            ids.append(L.parse_id(raw, L.TRACK)[1])
        except R.SubsonicError:
            continue
    return {"playQueue": {"current": q.get("current_id") or None, "position": int(q.get("position_ms") or 0),
                          "username": ctx.profile_name, "changed": L._iso(q.get("changed_at")),
                          "changedBy": q.get("changed_by") or "", "entry": ctx.lib.songs(ids)}}


@method("savePlayQueue")
def save_play_queue(ctx):
    from core.subsonic import store as S
    ids = []
    for raw in _ids("id"):
        _, tid = L.parse_id(raw, L.TRACK)
        ids.append(L.sid(L.TRACK, tid))
    current = _one("current")
    if current:
        current = L.sid(L.TRACK, L.parse_id(current, L.TRACK)[1])
    S.save_queue(ctx.db, ctx.profile_id, int(ctx.device.get("id") or 0), ids, current,
                 _int("position", 0), changed_by=_one("c") or ctx.device.get("name") or "")


# ── playlists ────────────────────────────────────────────────────────────

def _mixes(ctx) -> List[Dict[str, Any]]:
    try:
        from core.personalized.daily_mixes import CURATED_KEY
        stored = ctx.db.get_curated_playlist(CURATED_KEY, ctx.profile_id)
    except Exception:  # noqa: BLE001 - no mixes yet
        return []
    return [m for m in (stored.get("mixes") or []) if isinstance(m, dict)] if isinstance(stored, dict) else []


def _mix_ids(ctx, mix) -> List[int]:
    out = []
    for t in mix.get("tracks") or []:
        if not t.get("owned"):
            continue
        tid = t.get("lib2_track_id")
        if tid is None:
            artists = t.get("artists") or [{}]
            tid = ctx.lib.find_track(t.get("name") or "", (artists[0] or {}).get("name") or "",
                                     (t.get("album") or {}).get("name") or "")
        if tid is not None:
            out.append(int(tid))
    return out


def _visible_playlists(ctx) -> List[Dict[str, Any]]:
    return [p for p in (ctx.db.get_mirrored_playlists(profile_id=ctx.profile_id, is_request_list=False,
                                                      backfill_covers=False) or [])]


def _playlist_entry(ctx, p, songs: List[Dict[str, Any]]) -> Dict[str, Any]:
    from core.playlists.naming import effective_mirrored_name
    from core.playlists.user_playlists import is_liked_playlist, is_user_playlist
    return {"id": L.sid(L.PLAYLIST, p["id"]), "name": effective_mirrored_name(p) or p.get("name") or "",
            "comment": p.get("description") or None, "owner": ctx.profile_name, "public": False,
            "songCount": len(songs), "duration": sum(s.get("duration") or 0 for s in songs),
            "created": L._iso(p.get("mirrored_at")) or L._iso(time.time()),
            "changed": L._iso(p.get("updated_at") or p.get("mirrored_at")) or L._iso(time.time()),
            "coverArt": songs[0]["coverArt"] if songs else None,
            "readonly": not is_user_playlist(p) or is_liked_playlist(p)}


def _mix_entry(ctx, n, mix, songs) -> Dict[str, Any]:
    return {"id": L.sid(L.MIX, n), "name": mix.get("name") or "Daily Mix %d" % n,
            "comment": mix.get("subtitle") or None, "owner": ctx.profile_name, "public": False,
            "songCount": len(songs), "duration": sum(s.get("duration") or 0 for s in songs),
            "created": L._iso(time.time()), "changed": L._iso(time.time()),
            "coverArt": songs[0]["coverArt"] if songs else None, "readonly": True}


def _playlist_songs(ctx, p) -> List[Dict[str, Any]]:
    return ctx.lib.songs([tid for tid, _ in _playlist_track_ids(ctx, p) if tid is not None])


@method("getPlaylists")
def get_playlists(ctx):
    user = _one("username")
    if user and user.lower() != ctx.profile_name.lower():
        raise R.SubsonicError(R.NOT_AUTHORIZED, "A music app only sees its own profile's playlists.")
    out = []
    for p in _visible_playlists(ctx):
        out.append(_playlist_entry(ctx, p, _playlist_songs(ctx, p)))
    for n, mix in enumerate(_mixes(ctx), start=1):
        out.append(_mix_entry(ctx, n, mix, ctx.lib.songs(_mix_ids(ctx, mix))))
    return {"playlists": {"playlist": out}}


def _own_playlist(ctx, pid: int) -> Dict[str, Any]:
    p = ctx.db.get_mirrored_playlist(pid, profile_id=ctx.profile_id)
    if not p or int(p.get("profile_id") or 0) != ctx.profile_id or p.get("is_request_list"):
        raise R.not_found("Playlist")
    return p


def _writable(ctx, pid: int) -> Dict[str, Any]:
    from core.playlists.user_playlists import is_liked_playlist, is_user_playlist
    p = _own_playlist(ctx, pid)
    if not is_user_playlist(p) or is_liked_playlist(p):
        raise R.SubsonicError(R.NOT_AUTHORIZED, "This playlist is kept in step with its source. Change it there.")
    return p


@method("getPlaylist")
def get_playlist(ctx):
    kind, n = L.parse_id(_required("id"), L.PLAYLIST, L.MIX)
    if kind == L.MIX:
        mixes = _mixes(ctx)
        if not 1 <= n <= len(mixes):
            raise R.not_found("Playlist")
        songs = ctx.lib.songs(_mix_ids(ctx, mixes[n - 1]))
        entry = _mix_entry(ctx, n, mixes[n - 1], songs)
    else:
        p = _own_playlist(ctx, n)
        songs = _playlist_songs(ctx, p)
        entry = _playlist_entry(ctx, p, songs)
    entry["entry"] = songs
    return {"playlist": entry}


def _raw_tracks(ctx, raw_ids: List[str]) -> List[Dict[str, Any]]:
    rows = ctx.lib.song_rows([L.parse_id(r, L.TRACK)[1] for r in raw_ids])
    by = {int(r["id"]): r for r in rows}
    out = []
    for raw in raw_ids:
        tid = L.parse_id(raw, L.TRACK)[1]
        if tid not in by:
            raise R.not_found("Song")
        out.append(_raw_track(by[tid]))
    return out


def _forget_playlist_cache():
    with _liked_lock:
        _liked_cache.clear()


@method("createPlaylist")
def create_playlist(ctx):
    from core.playlists import user_playlists as up
    songs = _raw_tracks(ctx, _ids("songId"))
    existing = _one("playlistId")
    if existing:
        p = _writable(ctx, L.parse_id(existing, L.PLAYLIST)[1])
        up.replace_tracks(ctx.db, p, songs)
        pid = int(p["id"])
    else:
        try:
            pid = up.create_playlist(ctx.db, ctx.profile_id, _required("name"))
        except up.UserPlaylistError as exc:
            raise R.SubsonicError(R.GENERIC, str(exc)) from None
        if songs:
            up.add_tracks(ctx.db, ctx.db.get_mirrored_playlist(pid, profile_id=ctx.profile_id), songs,
                          allow_duplicates=True)
    _forget_playlist_cache()
    p = _own_playlist(ctx, pid)
    entry_songs = _playlist_songs(ctx, p)
    entry = _playlist_entry(ctx, p, entry_songs)
    entry["entry"] = entry_songs
    return {"playlist": entry}


@method("updatePlaylist")
def update_playlist(ctx):
    from core.playlists import user_playlists as up
    p = _writable(ctx, L.parse_id(_required("playlistId"), L.PLAYLIST)[1])
    name = _one("name")
    if name is not None:
        try:
            ctx.db.set_mirrored_playlist_custom_name(p["id"], up.clean_name(name), profile_id=ctx.profile_id)
        except up.UserPlaylistError as exc:
            raise R.SubsonicError(R.GENERIC, str(exc)) from None
    comment = _params().get("comment")
    if comment is not None:
        up.set_description(ctx.db, p, comment)
    removes = sorted({int(x) for x in _ids("songIndexToRemove") if str(x).isdigit()}, reverse=True)
    if removes:
        # positions in what the app was shown: only the rows this profile sees
        shown = {s["id"] for s in _playlist_songs(ctx, p)}
        visible = [i for i, (tid, _r) in enumerate(_playlist_track_ids(ctx, p))
                   if tid is not None and L.sid(L.TRACK, tid) in shown]
        for idx in removes:
            if idx < len(visible):
                up.remove_track(ctx.db, ctx.db.get_mirrored_playlist(p["id"], profile_id=ctx.profile_id),
                                visible[idx] + 1)
    adds = _ids("songIdToAdd")
    if adds:
        up.add_tracks(ctx.db, ctx.db.get_mirrored_playlist(p["id"], profile_id=ctx.profile_id),
                      _raw_tracks(ctx, adds), allow_duplicates=True)
    _forget_playlist_cache()


@method("deletePlaylist")
def delete_playlist(ctx):
    p = _writable(ctx, L.parse_id(_required("id"), L.PLAYLIST)[1])
    if not ctx.db.delete_mirrored_playlist(p["id"], profile_id=ctx.profile_id):
        raise R.not_found("Playlist")
    _forget_playlist_cache()


# ── artwork and lyrics ───────────────────────────────────────────────────

@method("getCoverArt")
def get_cover_art(ctx):
    from core.subsonic import media as M
    kind, n = L.parse_id(_required("id"), L.ALBUM, L.ARTIST, L.TRACK, L.PLAYLIST, L.MIX)
    if kind == L.TRACK:
        rows = ctx.lib.song_rows([n])
        if not rows:
            raise R.not_found("Cover art")
        kind, n = L.ALBUM, int(rows[0]["album_id"])
    elif kind in (L.PLAYLIST, L.MIX):
        songs = []
        if kind == L.PLAYLIST:
            songs = _playlist_songs(ctx, _own_playlist(ctx, n))
        else:
            mixes = _mixes(ctx)
            songs = ctx.lib.songs(_mix_ids(ctx, mixes[n - 1])) if 1 <= n <= len(mixes) else []
        if not songs:
            raise R.not_found("Cover art")
        kind, n = L.parse_id(songs[0]["coverArt"], L.ALBUM)
    elif kind == L.ALBUM:
        if n not in ctx.lib.snap().by_id:
            raise R.not_found("Cover art")
    elif n not in ctx.lib.snap().artists:
        raise R.not_found("Cover art")
    path = M.artwork_path(ctx.db, ctx.conn, config_manager, "album" if kind == L.ALBUM else "artist", n)
    if not path or not os.path.exists(path):
        raise R.not_found("Cover art")
    path = M.sized_image(path, M.size_step(_one("size")), M.sized_cache_dir(ctx.db))
    resp = send_file(path, mimetype="image/jpeg", conditional=True)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


def _local_file(ctx, row) -> Optional[str]:
    resolve = _hooks.get("resolve_path")
    path = row.get("path") or ""
    if not path:
        return None
    try:
        return resolve(path) if resolve else (path if os.path.exists(path) else None)
    except Exception:  # noqa: BLE001
        return None


@method("getLyricsBySongId")
def get_lyrics_by_song_id(ctx):
    from core.subsonic import media as M
    _, tid = L.parse_id(_required("id"), L.TRACK)
    rows = ctx.lib.song_rows([tid])
    if not rows:
        raise R.not_found("Song")
    r = rows[0]
    artist = r.get("track_artist") or r.get("artist_name") or ""
    synced, plain = M.lyrics_for(_local_file(ctx, r), title=r.get("title") or "", artist=artist,
                                 album=r.get("album_title") or "", duration_s=L._secs(r.get("duration")))
    return {"lyricsList": {"structuredLyrics": M.structured(synced, plain, artist=artist,
                                                             title=r.get("title") or "")}}


@method("getLyrics")
def get_lyrics(ctx):
    from core.subsonic import media as M
    artist, title = _one("artist", "") or "", _one("title", "") or ""
    if not title:
        return {"lyrics": {}}
    tid = ctx.lib.find_track(title, artist)
    rows = ctx.lib.song_rows([tid]) if tid else []
    r = rows[0] if rows else {}
    synced, plain = M.lyrics_for(_local_file(ctx, r) if r else None, title=title, artist=artist,
                                 album=r.get("album_title") or "", duration_s=L._secs(r.get("duration")))
    text = plain or (M.strip_lrc(synced) if synced else "")
    return {"lyrics": {"artist": artist or None, "title": title, "value": text or None}}


# ── artist and album info, similar and top songs ─────────────────────────

def _json(raw) -> Dict[str, Any]:
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _image(url) -> Optional[str]:
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def _similar_artist_ids(ctx, info, count: int) -> List[int]:
    from core.library2.song_search import fold
    ids = [info.get("spotify_id"), info.get("musicbrainz_id")]
    ext = _json(info.get("external_ids"))
    ids += [ext.get("itunes"), ext.get("deezer")]
    ids = [str(i) for i in ids if i]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = ctx.conn.execute(
        f"SELECT similar_artist_name FROM similar_artists WHERE source_artist_id IN ({marks}) "
        "ORDER BY similarity_rank LIMIT 200", ids).fetchall()
    snap = ctx.lib.snap()
    by_name = {}
    for aid, meta in snap.artists.items():
        by_name.setdefault(fold(meta.get("name") or ""), aid)
    out = []
    for r in rows:
        aid = by_name.get(fold(r[0] or ""))
        if aid is not None and aid not in out and aid != int(info["id"]):
            out.append(aid)
        if len(out) >= count:
            break
    return out


def _artist_info(ctx, id3: bool):
    from core.library2.artist_about import resolve_artist_about
    _, aid = L.parse_id(_required("id"), L.ARTIST)
    if aid not in ctx.lib.snap().artists:
        raise R.not_found("Artist")
    info = ctx.lib.artist_row(aid)
    about = resolve_artist_about(_json(info.get("enrichment")))
    image = _image(info.get("image_url"))
    try:
        similar = _similar_artist_ids(ctx, info, _int("count", 20, 0, 100))
    except Exception:  # noqa: BLE001 - no similar-artist data yet
        similar = []
    out = {"biography": R.Text(about.get("bio") or "") if about.get("bio") else None,
           "musicBrainzId": R.Text(info["musicbrainz_id"]) if info.get("musicbrainz_id") else None,
           "lastFmUrl": R.Text(about["bio_url"]) if "last.fm" in str(about.get("bio_url") or "") else None,
           "smallImageUrl": R.Text(image) if image else None, "mediumImageUrl": R.Text(image) if image else None,
           "largeImageUrl": R.Text(image) if image else None}
    if id3:
        out["similarArtist"] = [ctx.lib.artist_id3(a) for a in similar]
    else:
        out["similarArtist"] = [{"id": L.sid(L.ARTIST, a), "name": ctx.lib.artist_id3(a)["name"]} for a in similar]
    return out


@method("getArtistInfo")
def get_artist_info(ctx):
    return {"artistInfo": _artist_info(ctx, False)}


@method("getArtistInfo2")
def get_artist_info2(ctx):
    return {"artistInfo2": _artist_info(ctx, True)}


def _album_info(ctx):
    kind, aid = L.parse_id(_required("id"), L.ALBUM, L.TRACK)
    if kind == L.TRACK:
        rows = ctx.lib.song_rows([aid])
        if not rows:
            raise R.not_found("Album")
        aid = int(rows[0]["album_id"])
    if aid not in ctx.lib.snap().by_id:
        raise R.not_found("Album")
    row = ctx.conn.execute("SELECT musicbrainz_id, image_url, enrichment FROM lib2_albums WHERE id = ?",
                           (aid,)).fetchone()
    enrich = _json(row["enrichment"]) if row else {}
    wiki = (enrich.get("lastfm") or {}).get("wiki") if isinstance(enrich.get("lastfm"), dict) else None
    notes = wiki.get("summary") or wiki.get("content") if isinstance(wiki, dict) else wiki
    url = (enrich.get("lastfm") or {}).get("url") if isinstance(enrich.get("lastfm"), dict) else None
    image = _image(row["image_url"]) if row else None
    return {"notes": R.Text(str(notes)) if notes else None,
            "musicBrainzId": R.Text(row["musicbrainz_id"]) if row and row["musicbrainz_id"] else None,
            "lastFmUrl": R.Text(url) if url else None,
            "smallImageUrl": R.Text(image) if image else None, "mediumImageUrl": R.Text(image) if image else None,
            "largeImageUrl": R.Text(image) if image else None}


@method("getAlbumInfo")
def get_album_info(ctx):
    return {"albumInfo": _album_info(ctx)}


@method("getAlbumInfo2")
def get_album_info2(ctx):
    return {"albumInfo": _album_info(ctx)}


def _similar_songs(ctx) -> List[Dict[str, Any]]:
    kind, n = L.parse_id(_required("id"), L.TRACK, L.ALBUM, L.ARTIST)
    count = _int("count", 50, 1, MAX_LIST)
    seed = None
    if kind == L.TRACK:
        seed = n
    else:
        album_ids = [n] if kind == L.ALBUM else ctx.lib.artist_album_ids(n)
        ids = ctx.lib.songs_in_albums(album_ids[:20], limit=200)
        seed = ids[0] if ids else None
        if ids:
            import random
            seed = random.choice(ids)
    if seed is None:
        raise R.not_found()
    try:
        got = ctx.db.get_radio_tracks(str(seed), limit=count) or {}
    except Exception:  # noqa: BLE001
        logger.debug("radio for %s failed", seed, exc_info=True)
        got = {}
    ids = []
    for t in got.get("tracks") or []:
        tid = t.get("lib2_track_id") or t.get("id")
        try:
            ids.append(int(tid))
        except (TypeError, ValueError):
            continue
    return ctx.lib.songs([i for i in ids if i != seed])[:count]


@method("getSimilarSongs")
def get_similar_songs(ctx):
    return {"similarSongs": {"song": _similar_songs(ctx)}}


@method("getSimilarSongs2")
def get_similar_songs2(ctx):
    return {"similarSongs2": {"song": _similar_songs(ctx)}}


@method("getTopSongs")
def get_top_songs(ctx):
    from core.library2.song_search import fold
    name = _required("artist")
    count = _int("count", 50, 1, MAX_LIST)
    snap = ctx.lib.snap()
    aid = next((a for a, meta in snap.artists.items() if fold(meta.get("name") or "") == fold(name)), None)
    if aid is None:
        return {"topSongs": {}}
    album_ids = ctx.lib.artist_album_ids(aid)
    rows = ctx.conn.execute(
        "SELECT t.id, t.title FROM lib2_tracks t WHERE t.album_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY COALESCE(t.play_count, 0) DESC, t.id", (json.dumps(album_ids),)).fetchall()
    by_title: Dict[str, int] = {}
    local = []
    for tid, title in rows:
        by_title.setdefault(fold(title or ""), int(tid))
        local.append(int(tid))
    ordered: List[int] = []
    try:
        from core.lastfm_fallback import artist_top_tracks
        top, _src = artist_top_tracks(name, limit=count)
        for t in top or []:
            tid = by_title.get(fold(str(t.get("name") or "")))
            if tid is not None and tid not in ordered:
                ordered.append(tid)
    except Exception:  # noqa: BLE001 - our own plays still rank them
        logger.debug("top tracks for %s unavailable", name, exc_info=True)
    seen = set(ordered)
    ordered += [t for t in local if t not in seen]
    return {"topSongs": {"song": ctx.lib.songs(ordered)[:count]}}


# ── stream and download ──────────────────────────────────────────────────

def _stream_row(ctx) -> Dict[str, Any]:
    from core.connect.may_play import may_play_track
    _, tid = L.parse_id(_required("id"), L.TRACK)
    if not may_play_track(ctx.conn, tid, scope=ctx.scope, hide_explicit=ctx.hide_explicit):
        raise R.not_found("Song")
    rows = ctx.lib.song_rows([tid])
    if not rows:
        raise R.not_found("Song")
    return rows[0]


def _bridged(ctx, row) -> Optional[Response]:
    """the media server's stream of a file SoulSync can't read, when its
    switch is on."""
    from core.subsonic import bridge as B
    engine = (_hooks.get("media_engine") or (lambda: None))()
    for source in B.sources_for(ctx.conn, int(row["id"]), row.get("file_source")):
        if not B.enabled(_cfg, source):
            continue
        item = B.server_item_id(ctx.conn, int(row["id"]), source)
        url = B.stream_url(engine, source, item, want_size=row.get("size"), want_path=row.get("path")) if item else None
        if url and _hooks.get("proxy_url"):
            resp = _hooks["proxy_url"](url)
            if isinstance(resp, Response):
                resp.headers[B.MARKER_FIELD] = source
            return resp
    return None


def _cant_read(ctx, row) -> Response:
    from core.subsonic import bridge as B
    resp = _bridged(ctx, row)
    if resp is not None:
        return resp
    servers = [B.SOURCE_NAMES[s] for s in B.sources_for(ctx.conn, int(row["id"]), row.get("file_source"))]
    tip = (" Turn on \"Stream files SoulSync can't read through %s\" in Settings > Devices, or add a folder "
           "override in Settings > Library." % servers[0]) if servers else " Check the music folders in Settings."
    raise R.SubsonicError(R.NOT_FOUND, "SoulSync can't read this file." + tip)


@method("stream")
def stream(ctx):
    from core.subsonic import transcode as X
    row = _stream_row(ctx)
    local = _local_file(ctx, row)
    if not local:
        return _cant_read(ctx, row)
    suffix = os.path.splitext(local)[1].lstrip(".").lower()
    plan = X.decide(suffix=suffix, source_kbps=L._kbps(row.get("bitrate")), fmt=_one("format", "") or "",
                    max_kbps=_int("maxBitRate", 0), default_fmt=str(_cfg("music_apps.transcode_format", "mp3")),
                    have_ffmpeg=bool(X.ffmpeg()))
    offset = _int("timeOffset", 0)
    if not plan.raw:
        try:
            chunks = X.open_stream(local, plan, offset,
                                   max_jobs=X.clamp_jobs(_cfg("music_apps.transcode_slots", X.MAX_JOBS)))
        except X.Busy:
            # never the bigger file instead: the app asked for less, often on mobile data
            logger.info("music apps: every transcoder stayed busy for %ss, asking the app to retry",
                        int(X.WAIT_SECONDS))
            raise R.SubsonicError(R.GENERIC, "The server is busy converting other songs. Try again in a moment.") from None
        except OSError as exc:
            logger.warning("music apps: transcode failed to start: %s", exc)
            chunks = None
        if chunks is not None:
            headers = {"X-Content-Duration": str(max(0, L._secs(row.get("duration")) - offset)),
                       "Cache-Control": "no-cache"}
            if _bool("estimateContentLength", False):
                headers["Content-Length"] = str(X.estimate_length(L._secs(row.get("duration")), plan.kbps, offset))
            return Response(chunks, mimetype=plan.mime, headers=headers, direct_passthrough=True)
    return _hooks["serve_file"](local)


@method("download")
def download(ctx):
    row = _stream_row(ctx)
    local = _local_file(ctx, row)
    if not local:
        return _cant_read(ctx, row)
    resp = _hooks["serve_file"](local)
    if isinstance(resp, Response):
        resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % os.path.basename(local).replace('"', "")
    return resp


__all__ = ["bp", "configure", "create_blueprint", "authenticate", "error_response", "is_rest_path",
           "method_name", "METHODS", "PUBLIC_METHODS", "EXTENSIONS"]
