"""the /player app's own endpoints: where each profile is in what it plays.

every route reads and writes the caller's own rows only. the profile comes
from the session (get_current_profile_id), never from the request, so there
is no way to name someone else's.
"""

from __future__ import annotations

import re
import threading
import time

from flask import Blueprint, jsonify, request

from core.connect.device_access import device_playback
from core.permissions import requires, requires_any, self_only
from core.player import catalog, progress, watched
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("player")

bp = Blueprint("player", __name__)


def create_blueprint():
    return bp


def get_database():
    """the music library (the guide's music channels read it)"""
    from database.music_database import get_database as _get
    return _get()


def player_db():
    """player.db: progress, favorite channels, home layouts"""
    from database.player_database import get_player_database
    return get_player_database()


def _profile():
    pid = get_current_profile_id()
    return int(pid) if pid is not None else None


def _signed_out():
    return jsonify({"success": False, "error": "Not signed in"}), 401


def _bad(message):
    return jsonify({"success": False, "error": str(message)}), 400


def _failed(what, message="Something went wrong saving your place"):
    logger.exception("player %s failed", what)
    return jsonify({"success": False, "error": message}), 500


@bp.route("/api/player/progress", methods=["GET"])
@device_playback
@self_only
def get_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        row = progress.get(player_db(), pid, request.args.get("item_type"),
                           request.args.get("item_id"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("read")
    return jsonify({"success": True, "progress": row})


@bp.route("/api/player/progress", methods=["PUT"])
@device_playback
@self_only
def put_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    finished = body.get("finished")
    if finished is not None and not isinstance(finished, bool):
        return _bad("finished must be true or false")
    try:
        row = progress.save(player_db(), pid, body.get("item_type"), body.get("item_id"),
                            body.get("position_seconds"), body.get("duration_seconds"),
                            finished=finished, meta=body.get("meta"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("save")
    return jsonify({"success": True, "progress": row})


@bp.route("/api/player/progress/lookup", methods=["POST"])
@device_playback
@self_only
def lookup_progress():
    """many items of one type in one call: {item_type, item_ids: [...]}."""
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        rows = progress.lookup(player_db(), pid, body.get("item_type"), body.get("item_ids"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("lookup")
    return jsonify({"success": True, "progress": rows})


@bp.route("/api/player/progress/finish", methods=["POST"])
@device_playback
@self_only
def finish_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        row = progress.finish(player_db(), pid, body.get("item_type"), body.get("item_id"),
                              body.get("duration_seconds"), meta=body.get("meta"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("finish")
    return jsonify({"success": True, "progress": row,
                    "watched": _mark_if_allowed(body.get("item_type"), body.get("item_id"))})


@bp.route("/api/player/watched", methods=["POST"])
@device_playback
@self_only
def mark_watched():
    """the player is into the credits of a movie or episode: it counts as
    watched (in soulsync and on the media server account)"""
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    return jsonify({"success": True, "watched": _mark_if_allowed(body.get("item_type"), body.get("item_id"))})


def _mark_if_allowed(item_type, item_id):
    """watched goes to the media server account too: only for a title this
    profile may see (a kids profile marking any movie id watched pushed it)"""
    if item_type in ("movie", "episode") and not _may_see_title(str(item_type), str(item_id or "")):
        return False
    return watched.mark(item_type, item_id)


@bp.route("/api/player/progress", methods=["DELETE"])
@device_playback
@self_only
def delete_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        removed = progress.remove(player_db(), pid, request.args.get("item_type"),
                                  request.args.get("item_id"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("remove")
    return jsonify({"success": True, "removed": removed})


@bp.route("/api/player/continue", methods=["GET"])
@device_playback
@self_only
def continue_rail():
    pid = _profile()
    if pid is None:
        return _signed_out()
    raw = request.args.get("item_type") or ""
    types = [t for t in raw.split(",") if t.strip()]
    try:
        items = progress.continue_list(player_db(), pid, types, request.args.get("limit"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("continue")
    return jsonify({"success": True, "items": items})


def get_video_db():
    from api.video import get_video_db as _get
    return _get()


def _video_view():
    """(sees movies, sees shows, sees youtube, rating cap) for this request,
    off the same checks the video blueprint's gate and kids filters use."""
    from api.video import access
    from api.video.kids import video_cap
    side = access.any_side()
    return (side and access.has_kind("movie"), side and access.has_kind("show"), side, video_cap())


def _library_of(video_db):
    """(kind, file path) -> the player library that file is in, or None when
    the libraries can't be read (every title then counts as in none)"""
    try:
        from core.player import libraries as player_libraries
        return player_libraries.resolver(player_db(), video_db).library_of
    except Exception:  # noqa: BLE001
        logger.exception("player libraries: no resolver")
        return None


def _visible_libraries(sees_movies=True, sees_shows=True):
    """the video libraries this profile can see, as the player lists them.
    never fails a page: no libraries is the player as it always was."""
    try:
        from core.player import libraries as player_libraries
        kinds = [k for k, on in (("movie", sees_movies), ("show", sees_shows)) if on]
        return player_libraries.visible(player_db(), _profile(), is_admin=_is_player_admin(), kinds=kinds)
    except Exception:  # noqa: BLE001
        logger.exception("player libraries failed")
        return []


RATING = 4  # content_rating's place in a movie or show row (catalog.MOVIE_FIELDS)
_CACHE_SECONDS = 30
_cache: dict = {}
_cache_lock = threading.Lock()


def shape_video_catalog(raw, sees_movies, sees_shows, sees_youtube, cap):
    """drop the kinds and ratings this profile can't have. pure, so the
    filtering is tested apart from the db."""
    from api.video.kids import kind_allows

    from api.video.kids import hidden_libraries
    hidden = hidden_libraries(cap)

    def ok(kind, rating):
        return cap is None or kind_allows(cap, kind, rating)

    def here(row):
        # the library a title is in is the last field of its row (catalog.*_FIELDS)
        return not hidden or row[-1] not in hidden

    shows = [s for s in raw.get("shows", []) if sees_shows and ok("show", s[RATING]) and here(s)]
    show_ids = {s[0] for s in shows} if hidden else None
    return {
        "fields": {"movie": list(catalog.MOVIE_FIELDS), "show": list(catalog.SHOW_FIELDS)},
        "genres": {str(k): v for k, v in (raw.get("genres") or {}).items()},
        "movies": [m for m in raw.get("movies", [])
                   if sees_movies and ok("movie", m[RATING]) and here(m)],
        "shows": shows,
        "recent_episodes": [e for e in raw.get("recent_episodes", [])
                            if sees_shows and ok("show", e.get("content_rating"))
                            and (show_ids is None or e.get("show_id") in show_ids)],
        "youtube": list(raw.get("youtube", [])) if sees_youtube else [],
        "kinds": {"movies": bool(sees_movies), "shows": bool(sees_shows), "youtube": bool(sees_youtube)},
    }


_CACHE_STALE_SECONDS = 600
_cache_refreshing: set = set()
_cache_key_locks: dict = {}


def _refresh_catalog_bg(key, sees_movies, sees_shows, sees_youtube):
    try:
        vdb = get_video_db()
        library_of = _library_of(vdb)
        conn = vdb._get_connection()
        try:
            raw = catalog.read_all(conn, sees_movies, sees_shows, sees_youtube, library_of)
        finally:
            conn.close()
        with _cache_lock:
            _cache[key] = (time.monotonic(), raw)
    except Exception:  # noqa: BLE001
        logger.debug("background video catalog refresh failed", exc_info=True)
    finally:
        with _cache_lock:
            _cache_refreshing.discard(key)


def _read_catalog(sees_movies, sees_shows, sees_youtube, *, force: bool = False):
    """Stale-while-revalidate video catalog read:
    - < 30s old: instant cache hit (<1ms)
    - 30s..600s old: instant stale hit + background refresh thread (never blocks Home!)
    - cold start or force=True (?refresh=1): synchronous DB read.
    """
    key = (sees_movies, sees_shows, sees_youtube)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if not force and hit:
            age = now - hit[0]
            if age < _CACHE_SECONDS:
                return hit[1]
            if age < _CACHE_STALE_SECONDS:
                if key not in _cache_refreshing:
                    _cache_refreshing.add(key)
                    threading.Thread(
                        target=_refresh_catalog_bg,
                        args=(key, sees_movies, sees_shows, sees_youtube),
                        daemon=True,
                        name="player-catalog-swr",
                    ).start()
                return hit[1]
        key_lock = _cache_key_locks.setdefault(key, threading.Lock())
    # the read runs outside _cache_lock: a cold read of one view used to hold
    # every catalog request (every view) and the background refreshes behind
    # it. the same view asking twice still reads once (single flight)
    with key_lock:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and not force and time.monotonic() - hit[0] < _CACHE_SECONDS:
                return hit[1]
        started = time.monotonic()
        vdb = get_video_db()
        library_of = _library_of(vdb)
        conn = vdb._get_connection()
        try:
            raw = catalog.read_all(conn, sees_movies, sees_shows, sees_youtube, library_of)
        finally:
            conn.close()
        with _cache_lock:
            _cache[key] = (started, raw)
        return raw


@bp.route("/api/player/video-catalog", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def video_catalog():
    """the owned movies, shows (with a sample of episodes) and youtube
    downloads, with runtimes, genres, networks and ratings: what the guide
    schedules and the home rails show. read only."""
    try:
        sees_movies, sees_shows, sees_youtube, cap = _video_view()
        force = str(request.args.get("refresh") or "").strip().lower() in ("1", "true", "yes")

        def shaped():
            raw = _read_catalog(sees_movies, sees_shows, sees_youtube, force=force)
            out = shape_video_catalog(raw, sees_movies, sees_shows, sees_youtube, cap)
            out["libraries"] = _visible_libraries(sees_movies, sees_shows)
            return out

        if str(request.args.get("guide") or "") == "1":
            # the guide's pool: frozen for the day, so the shared timeline
            # doesn't re-seed when the library changes (core/player/guide_day.py)
            from core.player import guide_day
            from api.video.kids import hidden_libraries
            # a profile with a library hidden gets its own frozen pool: the day's
            # guide for everyone else must not be built from, or shown to, it
            gone = ",".join(str(i) for i in sorted(hidden_libraries(cap)))
            view = (f"catalog:{int(bool(sees_movies))}{int(bool(sees_shows))}{int(bool(sees_youtube))}:{cap}"
                    + (f":hide{gone}" if gone else ""))
            day = guide_day.frozen(
                player_db(), view, shaped,
                keep=lambda c: bool(c.get("movies") or c.get("shows") or c.get("youtube")),
            )
            # and the clock the day is counted on, so every device seeds the
            # same schedule whatever timezone it is in
            return jsonify({"success": True, **day, "frozen": True, "clock": guide_day.clock()})
        return jsonify({"success": True, **shaped()})
    except Exception:  # noqa: BLE001
        logger.exception("player video catalog failed")
        return jsonify({"success": False, "error": "Could not read the video library"}), 500


@bp.route("/api/player/guide/premieres", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def guide_premieres():
    """what's new and when it airs (core/player/premieres.py): the same slots
    and the same channels for every device and profile. a profile only gets
    the kinds and ratings it may see; that never moves anyone else's slot."""
    from api.video.kids import kind_allows
    from core.player import premieres
    try:
        sees_movies, sees_shows, _yt, cap = _video_view()
        conn = get_video_db()._get_connection()
        try:
            found = premieres.listing(player_db(), conn)
        finally:
            conn.close()
        out = []
        for p in found:
            kind = "movie" if p["kind"] == "movie" else "show"
            if not (sees_movies if kind == "movie" else sees_shows):
                continue
            if cap is not None and not kind_allows(cap, kind, p.get("rating")):
                continue
            out.append({k: v for k, v in p.items() if k != "rating"})
        import time as _time
        return jsonify({"success": True, "now": int(_time.time() * 1000), "premieres": out})
    except Exception:  # noqa: BLE001
        logger.exception("guide premieres failed")
        # no premieres is a guide without them, never a broken guide
        return jsonify({"success": True, "now": 0, "premieres": []})


@bp.route("/api/player/collections/<int:collection_id>/poster", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def collection_poster(collection_id: int):
    """Serve a Collection Studio generated poster to any player profile or mobile device."""
    from flask import Response
    from core.video.collections import poster_gen

    data = poster_gen.read_poster(collection_id)
    if not data:
        return jsonify({"success": False, "error": "Poster not found"}), 404
    return Response(
        data,
        mimetype="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


_collections_cache: dict = {}


@bp.route("/api/player/collections", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def collections():
    """the collection studio's collections, each with the members this
    profile can see (it's the catalog's own list, so kinds and the kids cap
    hold). a collection with nothing visible isn't listed. resolving is
    cached for half an hour: a list collection may fetch a remote list."""
    from core.player import collections as coll
    try:
        sees_movies, sees_shows, sees_youtube, cap = _video_view()
        now = time.monotonic()
        hit = _collections_cache.get("all")
        if hit and now - hit[0] < 1800:
            data = hit[1]
        else:
            data = coll.read(get_video_db())
            if not data.get("partial"):
                _collections_cache["all"] = (now, data)
        shaped = shape_video_catalog(_read_catalog(sees_movies, sees_shows, sees_youtube),
                                     sees_movies, sees_shows, sees_youtube, cap)
        visible = {"movie": {r[0] for r in shaped.get("movies") or []},
                   "show": {r[0] for r in shaped.get("shows") or []}}
        out = []
        for c in data["collections"]:
            members = [m for m in c["members"] if m in visible.get(c["media_type"], set())]
            if members:
                out.append(dict(c, members=members))
        return jsonify({"success": True, "collections": out})
    except Exception:  # noqa: BLE001
        logger.exception("player collections failed")
        return jsonify({"success": False, "error": "Could not read collections"}), 500


def _youtube_gate():
    """the video side's own rule for youtube: any video access at all."""
    from api.video import access
    if not access.any_side():
        return jsonify({"success": False, "error": "Video isn't part of your profile"}), 403
    return None


@bp.route("/api/player/youtube", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def youtube_home():
    """followed channels, newest uploads from them, newest downloads. read only:
    nothing here calls youtube."""
    from core.player import youtube
    refused = _youtube_gate()
    if refused:
        return refused
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        conn = get_video_db()._get_connection()
        try:
            return jsonify({"success": True, **youtube.home(conn, pid)})
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("player youtube home failed")
        return jsonify({"success": False, "error": "Could not read YouTube"}), 500


@bp.route("/api/player/youtube/channel/<channel_id>", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def youtube_channel(channel_id):
    from core.player import youtube
    refused = _youtube_gate()
    if refused:
        return refused
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not channel_id or len(channel_id) > 64:
        return _bad("unknown channel")
    try:
        conn = get_video_db()._get_connection()
        try:
            data = youtube.channel(conn, channel_id, pid)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("player youtube channel failed")
        return jsonify({"success": False, "error": "Could not read this channel"}), 500
    if data is None:
        return jsonify({"success": False, "error": "Channel not found"}), 404
    return jsonify({"success": True, **data})


_music_cache: dict = {}
# a library's genres move slowly; a cold read is a pass over every owned track
# (seconds on a 300k-track library), so a profile pays it a few times a day
_MUSIC_CACHE_SECONDS = 6 * 3600


@bp.route("/api/player/music-channels", methods=["GET"])
@device_playback
@requires("music.library")
def music_channels():
    """the guide's genre and vibe channels from the caller's owned tracks.
    cached hours per profile and scope: it is one pass over the tracks."""
    from contextlib import closing

    from core.player import music_channels as mc
    pid = _profile()
    if pid is None:
        return _signed_out()
    from core.player.sources.music import hides_explicit
    clean = hides_explicit()
    key = (pid, str(_session_scope()), clean)
    now = time.monotonic()
    hit = _music_cache.get(key)
    if hit and now - hit[0] < _MUSIC_CACHE_SECONDS:
        return jsonify({"success": True, "channels": hit[1]})
    try:
        with closing(get_database()._get_connection()) as conn:
            channels = mc.read(conn, hide_explicit=clean)
    except Exception:  # noqa: BLE001
        logger.exception("player music channels failed")
        return jsonify({"success": False, "error": "Could not read the music library"}), 500
    _music_cache[key] = (time.monotonic(), channels)
    return jsonify({"success": True, "channels": channels})


_listening_cache: dict = {}
_LISTENING_CACHE_SECONDS = 120


@bp.route("/api/player/music/listening", methods=["GET"])
@device_playback
@requires("music.library")
def music_listening():
    """the music page's listening rows (recently played, heavy rotation,
    most played): this profile's plays as albums, artists and tracks of its
    library. cached two minutes per profile and scope."""
    from core.player import music_listening as ml
    pid = _profile()
    if pid is None:
        return _signed_out()
    from core.player.sources.music import hides_explicit
    clean = hides_explicit()
    key = (pid, str(_session_scope()), clean)
    hit = _listening_cache.get(key)
    if hit and time.monotonic() - hit[0] < _LISTENING_CACHE_SECONDS:
        return jsonify({"success": True, **hit[1]})
    try:
        rows = ml.read(get_database(), pid, hide_explicit=clean)
    except Exception:  # noqa: BLE001
        logger.exception("player music listening failed")
        return jsonify({"success": False, "error": "Could not read your listening"}), 500
    _listening_cache[key] = (time.monotonic(), rows)
    return jsonify({"success": True, **rows})


def _session_scope():
    """the library scope the caller picked (#1199), part of the cache key"""
    try:
        from flask import session
        return session.get("library_scope")
    except Exception:  # noqa: BLE001
        return None


@bp.route("/api/player/guide/favorites", methods=["GET"])
@device_playback
@self_only
def get_favorites():
    from core.player import favorites
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        return jsonify({"success": True, "channels": favorites.get(player_db(), pid)})
    except Exception:  # noqa: BLE001
        return _failed("favorites read")


@bp.route("/api/player/guide/favorites", methods=["PUT"])
@device_playback
@self_only
def put_favorites():
    from core.player import favorites
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"success": True,
                        "channels": favorites.put(player_db(), pid, body.get("channels"))})
    except favorites.FavoritesError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("favorites save")


@bp.route("/api/player/watchlist", methods=["GET"])
@device_playback
@self_only
def get_watchlist():
    """this profile's watchlist, newest first: [{item_type, item_id, meta, added_at}]"""
    from core.player import watchlist
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        return jsonify({"success": True, "items": watchlist.list_items(player_db(), pid)})
    except Exception:  # noqa: BLE001
        return _failed("watchlist read")


@bp.route("/api/player/watchlist", methods=["PUT"])
@device_playback
@self_only
def add_to_watchlist():
    """{item_type: movie|show|youtube, item_id, meta?}"""
    from core.player import watchlist
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        item = watchlist.add(player_db(), pid, body.get("item_type"), body.get("item_id"), body.get("meta"))
        return jsonify({"success": True, "item": item})
    except watchlist.WatchlistError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("watchlist save")


@bp.route("/api/player/watchlist", methods=["DELETE"])
@device_playback
@self_only
def remove_from_watchlist():
    from core.player import watchlist
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        removed = watchlist.remove(player_db(), pid, request.args.get("item_type"), request.args.get("item_id"))
        return jsonify({"success": True, "removed": removed})
    except watchlist.WatchlistError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("watchlist remove")


@bp.route("/api/player/home-layout", methods=["GET"])
@device_playback
@self_only
def get_home_layout():
    """this screen's home rows (?device=), else the profile's shared ones,
    else the default; scope says which (device, all, or null). always clean,
    and with the catalog of rows and styles (core/player/home_layout.py is
    the contract every player app follows)."""
    from core.player import home_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        device = home_layout.device(request.args.get("device"))
        return jsonify({"success": True, **home_layout.get(player_db(), pid, device),
                        "catalog": home_layout.catalog()})
    except home_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("home layout read")


@bp.route("/api/player/home-layout", methods=["PUT"])
@device_playback
@self_only
def put_home_layout():
    """{layout, device}: device is this screen's id, or '*' for every
    screen of the profile (which drops this screen's own)"""
    from core.player import home_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        device = home_layout.device(body.get("device"))
        db = player_db()
        saved = home_layout.put(db, pid, body.get("layout"), device)
        if device == home_layout.ALL and body.get("from_device"):
            home_layout.reset(db, pid, home_layout.device(body.get("from_device")))
        return jsonify({"success": True, "layout": saved,
                        "scope": "all" if device == home_layout.ALL else "device"})
    except home_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("home layout save")


@bp.route("/api/player/home-layout", methods=["DELETE"])
@device_playback
@self_only
def reset_home_layout():
    """?device=: this screen follows the shared layout again. no device:
    every screen of the profile goes back to the default."""
    from core.player import home_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        home_layout.reset(player_db(), pid, home_layout.device(request.args.get("device")))
        return jsonify({"success": True})
    except home_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("home layout reset")


KEYWORD_MAX = 80


@bp.route("/api/player/page-layout/<page_key>", methods=["GET"])
@device_playback
@self_only
def get_page_layout(page_key):
    """the rows of a browse page (movies, tv, music, youtube, or one
    library's page "movies:lib:<id>") for this screen, with the catalog of
    rows the page can show (core/player/page_layout.py is the contract)."""
    from core.player import page_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        key = page_layout.page(page_key)
        dev = page_layout.device(request.args.get("device"))
        return jsonify({"success": True, **page_layout.get(player_db(), pid, key, dev),
                        "catalog": page_layout.catalog(key)})
    except page_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("page layout read")


@bp.route("/api/player/page-layout/<page_key>", methods=["PUT"])
@device_playback
@self_only
def put_page_layout(page_key):
    """{layout, device}: device is this screen's id, or '*' for every
    screen of the profile (which drops this screen's own, from_device)"""
    from core.player import page_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        key = page_layout.page(page_key)
        dev = page_layout.device(body.get("device"))
        db = player_db()
        saved = page_layout.put(db, pid, key, body.get("layout"), dev)
        if dev == page_layout.ALL and body.get("from_device"):
            page_layout.reset(db, pid, key, page_layout.device(body.get("from_device")))
        return jsonify({"success": True, "layout": saved,
                        "scope": "all" if dev == page_layout.ALL else "device"})
    except page_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("page layout save")


@bp.route("/api/player/page-layout/<page_key>", methods=["DELETE"])
@device_playback
@self_only
def reset_page_layout(page_key):
    """?device=: this screen follows the shared layout again. no device:
    the page goes back to its default on every screen."""
    from core.player import page_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        key = page_layout.page(page_key)
        page_layout.reset(player_db(), pid, key, page_layout.device(request.args.get("device")))
        return jsonify({"success": True})
    except page_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("page layout reset")


@bp.route("/api/player/maker", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def maker_titles():
    """everything a studio (?kind=studio) or a network (?kind=network) made,
    by name: its tmdb card and a page of titles (?sort=popular|newest|rated
    &page=), owned ones stamped with their library id. a profile without
    movies gets no studio, one without tv no network; rating caps apply."""
    from api.video.kids import filter_tmdb_items
    from core.player import makers
    from core.video.enrichment.engine import get_video_enrichment_engine

    which = request.args.get("kind")
    name = (request.args.get("name") or "").strip()
    if which not in ("studio", "network") or not name or len(name) > 200:
        return _bad("a kind (studio or network) and a name are required")
    try:
        page = max(1, min(20, int(request.args.get("page") or 1)))
    except ValueError:
        page = 1
    sort = request.args.get("sort") or "popular"
    try:
        sees_movies, sees_shows, _youtube, cap = _video_view()
        kind = "movie" if which == "studio" else "show"
        if not (sees_movies if kind == "movie" else sees_shows):
            return jsonify({"success": False, "error": "That isn't part of your profile"}), 403
        eng = get_video_enrichment_engine()
        if kind == "movie":
            maker = makers.studio_id(eng, name)
            card = eng.company_detail(maker) if maker else None
        else:
            conn = get_video_db()._get_connection()
            try:
                ids = [r[0] for r in conn.execute(
                    "SELECT s.tmdb_id FROM shows s JOIN show_networks sn ON sn.show_id = s.id "
                    "JOIN networks n ON n.id = sn.network_id "
                    "WHERE n.name = ? AND s.tmdb_id IS NOT NULL LIMIT 3", (name,))]
            finally:
                conn.close()
            maker = makers.network_id(eng, name, ids)
            card = makers.network_card(eng, maker) if maker else None
        if not maker:
            return jsonify({"success": True, "found": False, "titles": []})
        items = makers.titles(eng, kind, maker, sort, page)
        if cap is not None:
            items = filter_tmdb_items(get_video_db(), items, cap)
        return jsonify({"success": True, "found": True, "card": card, "titles": items,
                        "page": page, "has_more": len(items) >= 15})
    except Exception:  # noqa: BLE001
        logger.exception("player maker failed for %s %s", which, name)
        return jsonify({"success": False, "error": "Could not look that up"}), 500


LOGO_MAX = 12


@bp.route("/api/player/logos", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def title_logos():
    """tmdb title logos (the wordmark art) for a few titles at once, for the
    home billboard: ?items=movie:603,show:1399 (up to 12). the engine keeps
    each a day, a miss included; a cold set is fetched side by side."""
    import concurrent.futures

    from core.video.enrichment.engine import get_video_enrichment_engine
    wanted = []
    for part in (request.args.get("items") or "").split(",")[:LOGO_MAX]:
        kind, _, ident = part.partition(":")
        if kind in ("movie", "show") and ident.isdigit():
            wanted.append((kind, int(ident)))
    if not wanted:
        return jsonify({"success": True, "logos": {}})
    try:
        eng = get_video_enrichment_engine()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            found = list(ex.map(lambda w: eng.title_logo(w[0], w[1]), wanted))
        return jsonify({"success": True,
                        "logos": {f"{k}:{i}": (url or None) for (k, i), url in zip(wanted, found, strict=True)}})
    except Exception:  # noqa: BLE001 - the billboard falls back to the text title
        logger.exception("player logos failed")
        return jsonify({"success": True, "logos": {}})


@bp.route("/api/player/keyword", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def keyword_titles():
    """a tmdb keyword's titles ('heist', 'based on novel or book'): the
    video side's own discover, by the keyword's id, owned ones stamped with
    their library id. kinds and rating caps as everywhere else."""
    name = (request.args.get("name") or "").strip()
    if not name or len(name) > KEYWORD_MAX:
        return _bad("a keyword is required")
    try:
        from api.video.kids import filter_tmdb_items
        from core.video.enrichment.engine import get_video_enrichment_engine
        sees_movies, sees_shows, _youtube, cap = _video_view()
        eng = get_video_enrichment_engine()
        keyword_id = eng.keyword_id(name)
        if not keyword_id:
            return jsonify({"success": True, "keyword": name, "found": False, "movies": [], "shows": []})
        out = {}
        for kind, sees in (("movie", sees_movies), ("show", sees_shows)):
            items = []
            if sees:
                for page in (1, 2):
                    items += eng.discover_filter(kind, keywords=str(keyword_id), page=page) or []
            seen, unique = set(), []
            for item in items:
                if item.get("tmdb_id") not in seen:
                    seen.add(item.get("tmdb_id"))
                    unique.append({**item, "kind": kind})
            out[kind] = filter_tmdb_items(get_video_db(), unique, cap) if cap is not None else unique
        return jsonify({"success": True, "keyword": name, "found": True,
                        "movies": out["movie"], "shows": out["show"]})
    except Exception:  # noqa: BLE001
        logger.exception("player keyword failed for %s", name)
        return jsonify({"success": False, "error": "Could not look that keyword up"}), 500


# ── playlists (music) ────────────────────────────────────────────────────

@bp.route("/api/player/playlists", methods=["GET"])
@device_playback
@requires("music.access")
def player_playlists():
    """this profile's music playlists: yours (Liked Songs first) and mirrored"""
    pid = _profile()
    if pid is None:
        return _signed_out()
    from core.player import playlists
    try:
        return jsonify({"success": True, **playlists.list_for(get_database(), pid)})
    except Exception:  # noqa: BLE001
        return _failed("playlists", "Couldn't load your playlists.")


@bp.route("/api/player/playlists/<int:playlist_id>", methods=["GET"])
@device_playback
@requires("music.access")
def player_playlist(playlist_id):
    """one of this profile's playlists with its songs, each with the library
    track it plays (track_id) or null when this profile can't play it"""
    pid = _profile()
    if pid is None:
        return _signed_out()
    from core.player import playlists
    from core.player.sources.music import _scope, hides_explicit
    try:
        out = playlists.detail(get_database(), pid, playlist_id, scope=_scope(),
                               hide_explicit=hides_explicit())
    except Exception:  # noqa: BLE001
        return _failed("playlist", "Couldn't load that playlist.")
    if out is None:
        return jsonify({"success": False, "error": "not_found", "message": "No such playlist."}), 404
    return jsonify({"success": True, "playlist": out})


# ── discovery playlists (what discover made for this profile) ────────────

@bp.route("/api/player/discover-playlists", methods=["GET"])
@device_playback
# the manager's own discover gate: a profile with discover off sees none
@requires("music.discover")
def player_discover_playlists():
    """this profile's discovery playlists in sections: stored rows only"""
    pid = _profile()
    if pid is None:
        return _signed_out()
    from core.player import discovery
    try:
        return jsonify({"success": True, "sections": discovery.list_for(get_database(), pid)})
    except Exception:  # noqa: BLE001
        return _failed("discover playlists", "Couldn't load your mixes.")


@bp.route("/api/player/discover-playlists/<key>", methods=["GET"])
@device_playback
# the manager's own discover gate: a profile with discover off sees none
@requires("music.discover")
def player_discover_playlist(key):
    """one discovery playlist with its songs, each with the library track it
    plays (track_id) or why it can't (unplayable)"""
    pid = _profile()
    if pid is None:
        return _signed_out()
    from core.player import discovery
    from core.player.sources.music import _scope, hides_explicit
    try:
        out = discovery.detail(get_database(), pid, key, scope=_scope(), hide_explicit=hides_explicit())
    except Exception:  # noqa: BLE001
        return _failed("discover playlist", "Couldn't load that mix.")
    if out is None:
        return jsonify({"success": False, "error": "not_found", "message": "No such playlist."}), 404
    return jsonify({"success": True, "playlist": out})


@bp.route("/api/player/discover-playlists/<key>/get", methods=["POST"])
# the manager's own discover gate: a profile with discover off sees none
@requires("music.discover")
def player_discover_playlist_get(key):
    """the songs you don't have onto your wishlist: {positions?: [int]}.
    the manager's own wishlist add (the same gate as its add-album route,
    plus music.get: a profile that can't request music is told so)"""
    from core.permissions import map_refuses, refusal
    pid = _profile()
    if pid is None:
        return _signed_out()
    if map_refuses("music.get", "ask"):
        return refusal("music.get")
    body = request.get_json(silent=True) or {}
    positions = body.get("positions")
    if positions is not None and (not isinstance(positions, list) or not all(
            isinstance(p, int) and not isinstance(p, bool) and p > 0 for p in positions)):
        return _bad("positions must be a list of song positions")
    from api.wishlist_routes import _build_wishlist_route_runtime
    from core.player import discovery
    from core.player.sources.music import _scope, hides_explicit
    try:
        out = discovery.get_missing(get_database(), pid, key, scope=_scope(), hide_explicit=hides_explicit(),
                                    runtime=_build_wishlist_route_runtime(), positions=positions)
    except Exception:  # noqa: BLE001
        return _failed("discover get", "Couldn't add those to your wishlist.")
    if out is None:
        return jsonify({"success": False, "error": "not_found", "message": "No such playlist."}), 404
    return jsonify({"success": True, **out})


@bp.route("/api/player/music/artist/<int:artist_id>/top", methods=["GET"])
@device_playback
@requires("music.access")
def artist_top_tracks(artist_id):
    """an artist's most played songs this profile can play"""
    from core.player import playlists
    from core.player.sources.music import _scope, hides_explicit
    try:
        with get_database()._get_connection() as conn:
            rows = playlists.top_tracks(conn, artist_id, scope=_scope(), hide_explicit=hides_explicit(),
                                        limit=request.args.get("limit", 10, type=int) or 10)
    except Exception:  # noqa: BLE001
        return _failed("top tracks", "Couldn't load their songs.")
    return jsonify({"success": True, "tracks": rows})


@bp.route("/api/player/music/request-track", methods=["POST"])
@device_playback
@requires("music.access")
def request_music_track():
    """Put a missing album track onto this profile's wishlist (or pending music
    request queue when music.get == 'ask'). Uses the manager's own wishlist add."""
    from core.permissions import can, enforcing, map_refuses, refusal
    pid = _profile()
    if pid is None:
        return _signed_out()
    if map_refuses("music.get", "ask"):
        return refusal("music.get")
    body = request.get_json(silent=True) or {}
    title = str(body.get("title") or "").strip()
    artist_name = str(body.get("artist") or "").strip()
    album_name = str(body.get("album") or "").strip()
    if not title or not artist_name:
        return _bad("title and artist are required")
    album_id = body.get("album_id")
    artist_id = body.get("artist_id")
    track_number = body.get("track_number")
    disc_number = body.get("disc_number")
    duration_ms = body.get("duration_ms")
    year = body.get("year")
    image_url = body.get("image_url")
    raw_tid = body.get("track_id")
    # Stable synthetic ID when a missing album track has no external provider ID yet
    if raw_tid is not None and str(raw_tid).strip():
        track_key = str(raw_tid).strip()
    else:
        import hashlib
        slug = f"{artist_name.lower()}|{album_name.lower()}|{disc_number or 1}|{track_number or 0}|{title.lower()}"
        track_key = f"player:{hashlib.sha1(slug.encode('utf-8')).hexdigest()[:16]}"

    artist_obj = {"id": str(artist_id) if artist_id is not None else artist_name, "name": artist_name}
    album_obj = {
        "id": str(album_id) if album_id is not None else album_name,
        "name": album_name or title,
        "artists": [artist_obj],
        "release_date": str(year) if year else "",
        "images": [{"url": image_url}] if image_url else [],
    }
    track_obj = {
        "id": track_key,
        "name": title,
        "artists": [artist_obj],
        "track_number": track_number if isinstance(track_number, int) else None,
        "disc_number": disc_number if isinstance(disc_number, int) else None,
        "duration_ms": duration_ms if isinstance(duration_ms, int) else None,
    }
    from api.wishlist_routes import _build_wishlist_route_runtime
    from core.wishlist import routes as wishlist_routes
    try:
        payload, status = wishlist_routes.add_album_track_to_wishlist(
            _build_wishlist_route_runtime(),
            track=track_obj,
            artist=artist_obj,
            album=album_obj,
            source_type="album",
            source_context={"source_page": "player"},
        )
    except Exception:  # noqa: BLE001
        return _failed("request track", "Couldn't request that track right now.")
    can_download = (not enforcing()) or can(None, "music.get", "download")
    mode = "download" if can_download else "ask"
    if isinstance(payload, dict):
        payload = {**payload, "mode": mode}
    return jsonify(payload), status


# ── player settings (per profile) ────────────────────────────────────────

@bp.route("/api/player/settings", methods=["GET"])
@device_playback
@self_only
def get_player_settings():
    """this profile's player settings, each with what it does"""
    from core.player import settings
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        from core.player import setup as player_setup
        server = player_setup.get(player_db())
        return jsonify({"success": True, "settings": settings.get(player_db(), pid),
                        "about": {k: v[2] for k, v in settings.SETTINGS.items()},
                        # what the admin set for everyone: the name, the sidebar order a
                        # profile starts from, and the places nobody here gets
                        "server": {"name": server["server_name"],
                                   "sidebar_order": server["sidebar_order"],
                                   "sidebar_hidden": server["sidebar_hidden"]}})
    except Exception:  # noqa: BLE001
        return _failed("settings read", "Couldn't load your settings.")


@bp.route("/api/player/settings", methods=["PUT"])
@device_playback
@self_only
def put_player_settings():
    """{key: value, ...}: all saved or none. turning sharing off takes you
    off everyone's watching-now at once"""
    from core.player import presence, settings
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        out = settings.put(player_db(), pid, request.get_json(silent=True))
    except settings.SettingsError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("settings save", "Couldn't save that setting.")
    # its screens are still kept (the admin's Server Activity sees every
    # stream); members stop seeing them at once
    presence.set_shared(pid, bool(out["share_activity"]))
    if out.get("commercials_enabled"):
        try:
            from core.player import commercials
            commercials.warm_async(out.get("commercials_era") or "mix")
        except Exception as e:  # noqa: BLE001
            logger.debug("commercials warm after a settings save failed: %s", e)
    return jsonify({"success": True, "settings": out})


@bp.route("/api/player/commercials/config", methods=["GET"])
@device_playback
@self_only
def get_commercials_config():
    """Return server-wide Live Guide commercial broadcast config + deterministic clip catalog."""
    from core.player import commercials
    pid = _profile()
    if pid is None:
        return _signed_out()
    cfg = commercials.get_server_config(player_db())
    clips = commercials.list_clips_sorted() if cfg["enabled"] else []
    if cfg["enabled"] and str(request.args.get("guide") or "") == "1":
        # the guide asks once a day: the weekly fresh set rides on it. the
        # day's breaks are frozen below, so a rotation shows from tomorrow
        try:
            commercials.maybe_rotate(cfg["era"])
        except Exception:  # noqa: BLE001 - a rotation never fails the guide
            logger.debug("commercials rotation check failed", exc_info=True)
        # the clips the day's breaks are scheduled from: frozen like the pool,
        # or each clip the cache fetched re-seeded everyone's day
        from core.player import guide_day
        clips = guide_day.frozen(player_db(), f"clips:{cfg['era']}:{cfg['mode']}", lambda: clips)
    return jsonify({
        "success": True,
        **cfg,
        "can_edit": _is_player_admin(),
        "clips": clips,
        "status": commercials.status(),
    })


@bp.route("/api/player/commercials/config", methods=["PUT"])
@device_playback
@self_only
def put_commercials_config():
    """Admin-only: update server-wide Live Guide commercial broadcast settings."""
    from core.player import commercials, settings
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _is_player_admin():
        return jsonify({
            "success": False,
            "error": "admin_required",
            "message": "Only an admin can change server-wide commercial settings.",
        }), 403
    try:
        cfg = commercials.put_server_config(player_db(), request.get_json(silent=True))
    except settings.SettingsError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("commercials config save", "Couldn't save commercial settings.")
    clips = commercials.list_clips_sorted() if cfg["enabled"] else []
    return jsonify({
        "success": True,
        **cfg,
        "can_edit": True,
        "clips": clips,
        "status": commercials.status(),
    })


@bp.route("/api/player/commercials/status", methods=["GET"])
@device_playback
@self_only
def get_commercials_status():
    """Return retro commercial cache readiness and counts."""
    from core.player import commercials
    pid = _profile()
    if pid is None:
        return _signed_out()
    return jsonify({"success": True, **commercials.status()})


@bp.route("/api/player/commercials/warm", methods=["POST"])
@device_playback
@self_only
def warm_commercials():
    """Trigger non-blocking background fetch of retro commercials from Archive.org."""
    from core.player import commercials
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    era = str(body.get("era") or "mix").strip().lower()
    started = commercials.warm_async(era)
    return jsonify({"success": True, "started": started, **commercials.status()})


@bp.route("/api/player/commercials/break", methods=["GET"])
@device_playback
@self_only
def get_commercial_break():
    """Pick 1-3 retro commercials for a break between Live Guide programs."""
    from core.player import commercials, settings
    pid = _profile()
    if pid is None:
        return _signed_out()
    prefs = settings.get(player_db(), pid)
    era = str(request.args.get("era") or prefs.get("commercials_era") or "auto").strip().lower()
    mode = str(request.args.get("mode") or prefs.get("commercials_mode") or "short").strip().lower()
    channel_id = str(request.args.get("channel_id") or "")
    channel_name = str(request.args.get("channel_name") or "")
    try:
        max_sec = int(request.args.get("max_seconds") or 0) or None
    except ValueError:
        max_sec = None
    try:
        item_year = int(request.args.get("year") or 0) or None
    except ValueError:
        item_year = None
    clips = commercials.pick_break(
        era=era,
        channel_id=channel_id,
        channel_name=channel_name,
        mode=mode,
        max_seconds=max_sec,
        item_year=item_year,
    )
    return jsonify({"success": True, "clips": clips})


@bp.route("/api/player/commercials/schedule", methods=["POST"])
@device_playback
@self_only
def schedule_commercial_breaks():
    """Plan continuous commercial breaks between every show in a Live Guide queue (5-min cooldown)."""
    from core.player import commercials, settings
    pid = _profile()
    if pid is None:
        return _signed_out()
    prefs = settings.get(player_db(), pid)
    body = request.get_json(silent=True) or {}
    items = body.get("items") if isinstance(body.get("items"), list) else []
    era = str(body.get("era") or prefs.get("commercials_era") or "auto").strip().lower()
    mode = str(body.get("mode") or prefs.get("commercials_mode") or "cable").strip().lower()
    channel_id = str(body.get("channel_id") or "")
    channel_name = str(body.get("channel_name") or "")
    try:
        min_gap = max(60, int(body.get("min_gap_seconds") or 300))
    except (TypeError, ValueError):
        min_gap = 300
    breaks = commercials.pick_breaks_for_queue(
        items=items[:20],
        era=era,
        channel_id=channel_id,
        channel_name=channel_name,
        mode=mode,
        min_gap_seconds=min_gap,
    )
    return jsonify({"success": True, "breaks": breaks})


@bp.route("/api/player/commercials/cache", methods=["DELETE"])
@device_playback
@self_only
def clear_commercials_cache():
    """Admin-only: clear the auto-cached Archive.org retro commercials from disk."""
    from core.player import commercials
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _is_player_admin():
        return jsonify({"success": False, "error": "admin_required", "message": "Only an admin can clear the commercial cache."}), 403
    return jsonify({"success": True, **commercials.clear_cache()})


# ── watching now (who plays what, live) ─────────────────────────────────

def _shares(profile_id: int) -> bool:
    from core.player import settings
    try:
        return bool(settings.get(player_db(), profile_id)["share_activity"])
    except Exception:  # noqa: BLE001 - can't tell: don't show it
        return False


def _viewer_may_see(s) -> bool:
    """the viewer may see this kind of media, and this film or show under
    its rating cap"""
    from core.permissions import map_refuses
    from core.player.presence import KIND_PERMISSIONS
    perms = KIND_PERMISSIONS.get(s.item_type, ())
    if not perms or all(map_refuses(p) for p in perms):
        return False
    if s.item_type not in ("movie", "episode"):
        return True
    try:
        from api.video import get_video_db
        from api.video.kids import library_title_allowed, video_cap
        cap = video_cap()
        if cap is None:
            return True
        if s.item_type == "movie":
            return library_title_allowed(get_video_db(), "movie", int(s.item_id), cap)
        # the episode's real show, never the parent_id the client sent
        return _may_see_title("episode", str(s.item_id))
    except Exception:  # noqa: BLE001 - can't tell: don't show it
        return False


_who: dict = {"at": 0.0, "map": {}}


def _profiles_by_id() -> dict:
    """names and colours, kept a minute"""
    if time.time() - _who["at"] > 60:
        try:
            rows = get_database().get_all_profiles() or []
        except Exception:  # noqa: BLE001
            rows = []
        _who["map"] = {int(r["id"]): {"name": r.get("name") or "Someone",
                                      "avatar_color": r.get("avatar_color"),
                                      "avatar_url": r.get("avatar_url")} for r in rows if r.get("id")}
        _who["at"] = time.time()
    return _who["map"]


def _beat_client() -> dict:
    """what the screen is, from the request: a paired device's own name and
    platform, else the browser and system its user agent names"""
    from flask import g
    dev = getattr(g, "device", None) if getattr(g, "device_auth", False) else None
    if isinstance(dev, dict) and dev:
        return {k: v for k, v in (("product", dev.get("app")), ("name", dev.get("name")),
                                  ("platform", dev.get("platform"))) if v}
    from core.security.devices import device_label
    label = device_label(request.headers.get("User-Agent", ""))
    browser, _, system = label.partition(" on ")
    return {k: v for k, v in (("product", "SoulSync player"), ("device", browser),
                              ("platform", system)) if v}


@bp.route("/api/player/presence", methods=["POST"])
@device_playback
@self_only
def presence_beat():
    """a screen plays something: {session, item_type, item_id, position,
    duration, playing, live, channel?, meta?, stream?, client?}, every few
    seconds. every beat is kept, marked with whether the profile shares
    (members only ever see profiles that share; the admin's Server Activity
    sees all). the answer carries the admin's orders for this screen:
    {stop: {message}} or {message: {header, text}}"""
    from core.player import presence
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True)
    shared = _shares(pid)
    try:
        presence.beat(pid, body, shared=shared, ip=request.remote_addr, client=_beat_client())
    except presence.PresenceError as e:
        return _bad(e)
    return jsonify({"success": True, "shared": shared,
                    **presence.orders(pid, str((body or {}).get("session") or ""))})


@bp.route("/api/player/presence", methods=["DELETE"])
@device_playback
@self_only
def presence_stop():
    from core.player import presence
    pid = _profile()
    if pid is None:
        return _signed_out()
    presence.stop(pid, request.args.get("session") or "")
    return jsonify({"success": True})


@bp.route("/api/player/presence", methods=["GET"])
@device_playback
@self_only
def presence_list():
    """what's playing on this server right now: this profile's own screens
    (marked you) and others' (profiles that share, things this one may see)"""
    from core.player import presence
    pid = _profile()
    if pid is None:
        return _signed_out()
    rows = presence.visible_to(pid, presence.everyone(), shares=_shares, may_see=_viewer_may_see)
    who = _profiles_by_id()
    for r in rows:
        r["profile"] = who.get(r["profile_id"], {"name": "Someone"})
    return jsonify({"success": True, "sessions": rows, "sharing": _shares(pid)})


@bp.route("/api/player/presence/together", methods=["GET"])
@device_playback
@self_only
def presence_together():
    """titles you and others (that share) have both started lately: your
    place and theirs. a show is one title, whichever episode you're each on"""
    from core.player import presence
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        rows = presence.together(player_db(), pid, shares=_shares, may_see=_viewer_may_see)
    except Exception:  # noqa: BLE001
        return _failed("together", "Couldn't work that out.")
    who = _profiles_by_id()
    for r in rows:
        for o in r["others"]:
            o["profile"] = who.get(o["profile_id"], {"name": "Someone"})
    return jsonify({"success": True, "titles": rows})


# ── notes: comments on a title, marks on a moment ───────────────────────

NOTE_PERMISSIONS = {"show": ("tv.access",), "album": ("music.access",)}


def _may_see_title(item_type: str, item_id: str) -> bool:
    """the viewer may see this title: its media access, and its rating cap
    for films and shows (an episode answers for its show)"""
    from core.permissions import map_refuses
    from core.player.presence import KIND_PERMISSIONS
    perms = NOTE_PERMISSIONS.get(item_type) or KIND_PERMISSIONS.get(item_type, ())
    if not perms or all(map_refuses(p) for p in perms):
        return False
    if item_type not in ("movie", "show", "episode"):
        return True
    try:
        from api.video import get_video_db
        from api.video.kids import library_title_allowed, video_cap
        cap = video_cap()
        if cap is None:
            return True
        db = get_video_db()
        if item_type == "episode":
            with db._get_connection() as conn:
                row = conn.execute("SELECT show_id FROM episodes WHERE id = ?", (int(item_id),)).fetchone()
            if not row:
                return False
            return library_title_allowed(db, "show", int(row[0]), cap)
        return library_title_allowed(db, item_type, int(item_id), cap)
    except Exception:  # noqa: BLE001 - can't tell: no
        return False


def _with_people(notes):
    who = _profiles_by_id()
    for n in notes:
        n["profile"] = who.get(n["profile_id"], {"name": "Someone"})
    return notes


@bp.route("/api/player/notes/<item_type>/<path:item_id>", methods=["GET"])
@device_playback
@self_only
def list_notes(item_type, item_id):
    """a title's comments (newest first) and marks (in time order): yours,
    and what others shared"""
    from core.player import notes
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _may_see_title(item_type, item_id):
        return jsonify({"success": False, "error": "not_found", "message": "Nothing here."}), 404
    try:
        return jsonify({"success": True, "notes": _with_people(notes.list_for(player_db(), pid, item_type, item_id))})
    except notes.NoteError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("notes read", "Couldn't load the notes.")


@bp.route("/api/player/notes/<item_type>/<path:item_id>", methods=["POST"])
@device_playback
@self_only
def add_note(item_type, item_id):
    """{body} a comment; {at, body?, shared?} a mark"""
    from core.player import notes
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _may_see_title(item_type, item_id):
        return jsonify({"success": False, "error": "not_found", "message": "Nothing here."}), 404
    body = request.get_json(silent=True) or {}
    try:
        note = notes.add(player_db(), pid, item_type, item_id, body.get("body"), body.get("at"),
                         body.get("shared"))
    except notes.NoteError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("note save", "Couldn't save that.")
    return jsonify({"success": True, "note": _with_people([note])[0]})


@bp.route("/api/player/note/<int:note_id>", methods=["PATCH"])
@device_playback
@self_only
def edit_note(note_id):
    from core.player import notes
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        note = notes.edit(player_db(), pid, note_id, body.get("body"), body.get("shared"))
    except notes.NoteError as e:
        return _bad(e)
    if note is None:
        return jsonify({"success": False, "error": "not_found", "message": "No such note of yours."}), 404
    return jsonify({"success": True, "note": _with_people([note])[0]})


@bp.route("/api/player/note/<int:note_id>", methods=["DELETE"])
@device_playback
@self_only
def delete_note(note_id):
    """your own note; an admin can take down anyone's shared one"""
    from flask import g

    from core.player import notes
    pid = _profile()
    if pid is None:
        return _signed_out()
    ok = notes.remove(player_db(), pid, note_id, moderator=bool(getattr(g, "is_admin", False)))
    if not ok:
        return jsonify({"success": False, "error": "not_found", "message": "No such note of yours."}), 404
    return jsonify({"success": True})


# ── the guide: channels people make, and each profile's lineup ──────────

def _is_admin() -> bool:
    from flask import g
    return bool(getattr(g, "is_admin", False))


def _is_player_admin() -> bool:
    """True for browser admins AND paired mobile devices signed into an admin profile.

    every admin check in the player uses this one: a phone on an admin
    profile runs the same player as that admin's browser (Users & Downloads,
    server guide channels, party overrides), as the mobile app's README says."""
    from flask import g
    return bool(getattr(g, "is_admin", False) or getattr(g, "perms_source", None) == "admin")


@bp.route("/api/player/guide/channels", methods=["GET"])
@device_playback
@self_only
def guide_channels():
    """the server's channels and this profile's own; can_edit_server: an admin"""
    from core.player import lineup
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        out = lineup.list_channels(player_db(), pid)
    except Exception:  # noqa: BLE001
        return _failed("channels read", "Couldn't load the channels.")
    return jsonify({"success": True, **out, "can_edit_server": _is_player_admin()})


@bp.route("/api/player/guide/channels", methods=["POST"])
@device_playback
@self_only
def add_guide_channel():
    """{scope: mine|server, spec}: a server channel is an admin's to make"""
    from core.player import lineup
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    server = body.get("scope") == "server"
    if server and not _is_player_admin():
        return jsonify({"success": False, "error": "not_allowed",
                        "message": "Only an admin makes channels for everyone."}), 403
    try:
        ch = lineup.add_channel(player_db(), None if server else pid, body.get("spec"))
    except lineup.LineupError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("channel save", "Couldn't make that channel.")
    return jsonify({"success": True, "channel": ch})


@bp.route("/api/player/guide/channels/<channel_id>", methods=["PUT"])
@device_playback
@self_only
def edit_guide_channel(channel_id):
    from core.player import lineup
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        ch = lineup.edit_channel(player_db(), pid, channel_id, (request.get_json(silent=True) or {}).get("spec"),
                                 admin=_is_player_admin())
    except lineup.LineupError as e:
        return _bad(e)
    if ch is None:
        return jsonify({"success": False, "error": "not_found", "message": "No such channel of yours."}), 404
    return jsonify({"success": True, "channel": ch})


@bp.route("/api/player/guide/channels/<channel_id>", methods=["DELETE"])
@device_playback
@self_only
def remove_guide_channel(channel_id):
    from core.player import lineup
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not lineup.remove_channel(player_db(), pid, channel_id, admin=_is_player_admin()):
        return jsonify({"success": False, "error": "not_found", "message": "No such channel of yours."}), 404
    return jsonify({"success": True})


@bp.route("/api/player/guide/lineup", methods=["GET"])
@device_playback
@self_only
def get_guide_lineup():
    """this profile's order, hidden channels and names"""
    from core.player import lineup
    pid = _profile()
    if pid is None:
        return _signed_out()
    return jsonify({"success": True, "entries": lineup.get_lineup(player_db(), pid)})


@bp.route("/api/player/guide/lineup", methods=["PUT"])
@device_playback
@self_only
def put_guide_lineup():
    """{entries: [{channel_id, hidden?, name?}]} in order: the whole lineup"""
    from core.player import lineup
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        entries = lineup.put_lineup(player_db(), pid, (request.get_json(silent=True) or {}).get("entries"))
    except lineup.LineupError as e:
        return _bad(e)
    return jsonify({"success": True, "entries": entries})


# ── the guide's extras: watched, people, reminders ───────────────────────

@bp.route("/api/player/guide/watched", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def guide_watched():
    """what this profile has seen, so channels air the unseen first"""
    from api.video import get_video_db
    from core.player import guide_data
    from core.video.watch_scope import current_watcher
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        out = guide_data.watched_ids(get_video_db(), current_watcher().rows, player_db(), pid)
    except Exception:  # noqa: BLE001
        return _failed("watched read", "Couldn't read what you've watched.")
    return jsonify({"success": True, **out})


@bp.route("/api/player/guide/people", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def guide_people():
    """?q=: people in the library by name, for a channel of their work"""
    from api.video import get_video_db
    from core.player import guide_data
    try:
        return jsonify({"success": True, "people": guide_data.find_people(get_video_db(), request.args.get("q", ""))})
    except Exception:  # noqa: BLE001
        return _failed("people search", "Couldn't search people.")


@bp.route("/api/player/guide/people/<int:person_id>", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def guide_person(person_id):
    """a person's owned films and their shows"""
    from api.video import get_video_db
    from core.player import guide_data
    try:
        return jsonify({"success": True, **guide_data.person_titles(get_video_db(), person_id)})
    except guide_data.GuideDataError:
        return jsonify({"success": False, "error": "not_found", "message": "No such person."}), 404


@bp.route("/api/player/guide/reminders", methods=["GET"])
@device_playback
@self_only
def guide_reminders():
    from core.player import guide_data
    pid = _profile()
    if pid is None:
        return _signed_out()
    return jsonify({"success": True, "reminders": guide_data.list_reminders(player_db(), pid)})


@bp.route("/api/player/guide/reminders", methods=["POST"])
@device_playback
@self_only
def add_guide_reminder():
    """{channel_id, starts_at (seconds), program_key, title, subtitle?, channel_name?}"""
    from core.player import guide_data
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        r = guide_data.add_reminder(player_db(), pid, request.get_json(silent=True))
    except guide_data.GuideDataError as e:
        return _bad(e)
    return jsonify({"success": True, "reminder": r})


@bp.route("/api/player/guide/reminders", methods=["DELETE"])
@device_playback
@self_only
def remove_guide_reminder():
    from core.player import guide_data
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        starts = float(request.args.get("starts_at", ""))
    except ValueError:
        return _bad("starts_at must be a number")
    guide_data.remove_reminder(player_db(), pid, request.args.get("channel_id", ""), starts)
    return jsonify({"success": True})


# ── watch parties (watch together + theater waiting rooms) ──────────────

def _hydrate_party_profiles(party: dict, who: dict) -> dict:
    party["host_profile"] = who.get(party["host_profile_id"], {"name": "Host"})
    for m in party.get("members", []):
        m["profile"] = who.get(m["profile_id"], {"name": "Someone"})
    for msg in party.get("messages", []):
        msg["profile"] = who.get(msg["profile_id"], {"name": "Someone"})
    pee = party.get("pee_break")
    if isinstance(pee, dict) and pee.get("profile_id") is not None:
        pee["profile"] = who.get(int(pee["profile_id"]), {"name": "Someone"})
    return party


def _pick_default_lobby_track() -> dict | None:
    """Pick a random owned track from the music library for the theater waiting room."""
    try:
        from core.library2.sql_util import owned_sql
        from database.music_database import get_database
        db = get_database()
        with db._get_connection() as conn:
            # a random point in the id range, then the first owned track from
            # there (wrapping): ORDER BY RANDOM() read every owned track
            import random
            top = conn.execute("SELECT MAX(id) FROM lib2_tracks").fetchone()
            if not top or not top[0]:
                return None
            pivot = random.randint(1, int(top[0]))  # noqa: S311 - not security
            pick = (
                "SELECT t.id, t.title, COALESCE(NULLIF(t.track_artist, ''), ar.name, '') AS artist, "
                "t.album_id, t.duration FROM lib2_tracks t "
                "JOIN lib2_albums al ON al.id = t.album_id "
                "LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
                f"WHERE {owned_sql('track', 't')} AND t.id {{}} ? ORDER BY t.id {{}} LIMIT 1"
            )
            row = (conn.execute(pick.format(">=", "ASC"), (pivot,)).fetchone()
                   or conn.execute(pick.format("<", "DESC"), (pivot,)).fetchone())
            if not row:
                return None
            tid, title, artist, album_id, duration = row
            dur_ms = int(duration) if duration else 210000
            return {
                "track_id": str(tid),
                "title": title or "Lobby Music",
                "artist": artist or "",
                "album_id": int(album_id) if album_id is not None else None,
                "artwork": f"/api/library/v2/artwork/album/{album_id}?size=thumb" if album_id is not None else None,
                "duration_ms": dur_ms,
            }
    except Exception:  # noqa: BLE001
        return None


def _party_may_see(item_type: str, item_id: str) -> bool:
    from types import SimpleNamespace
    return _viewer_may_see(SimpleNamespace(item_type=item_type, item_id=item_id))


@bp.route("/api/player/parties", methods=["GET"])
@device_playback
@self_only
def list_watch_parties():
    """Active and scheduled watch parties visible to this profile."""
    from core.player import parties
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        rows = parties.list_parties(player_db(), pid, may_see=_party_may_see)
    except Exception:  # noqa: BLE001
        return _failed("parties list", "Couldn't load watch parties.")
    who = _profiles_by_id()
    return jsonify({"success": True, "parties": [_hydrate_party_profiles(r, who) for r in rows]})


@bp.route("/api/player/parties", methods=["POST"])
@device_playback
@self_only
def create_watch_party():
    """Create a watch party (instant theater lobby or scheduled premiere)."""
    from core.player import parties
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    default_lobby = None if body.get("lobby_music") is False else _pick_default_lobby_track()
    try:
        party = parties.create_party(player_db(), pid, body, default_lobby_track=default_lobby)
    except parties.PartyError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("party create", "Couldn't create watch party.")
    who = _profiles_by_id()
    return jsonify({"success": True, "party": _hydrate_party_profiles(party, who)}), 201


@bp.route("/api/player/parties/<party_id>", methods=["GET"])
@device_playback
@self_only
def get_watch_party(party_id):
    """Live room state: members, ready flags, synced position, lobby music, chat."""
    from core.player import parties
    pid = _profile()
    if pid is None:
        return _signed_out()
    party = parties.get_party(player_db(), party_id, pid, may_see=_party_may_see)
    if not party:
        return jsonify({"success": False, "error": "not_found", "message": "Watch party not found."}), 404
    who = _profiles_by_id()
    return jsonify({"success": True, "party": _hydrate_party_profiles(party, who)})


@bp.route("/api/player/parties/<party_id>/join", methods=["POST"])
@device_playback
@self_only
def join_watch_party(party_id):
    """Join a watch party, RSVP to a scheduled one, or toggle Ready."""
    from core.player import parties
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    ready = body.get("ready") if isinstance(body.get("ready"), bool) else None
    rsvp = bool(body.get("rsvp", False))
    try:
        party = parties.join_party(player_db(), party_id, pid, ready=ready, rsvp=rsvp,
                                   may_see=_party_may_see)
    except parties.PartyError as e:
        return _bad(e)
    who = _profiles_by_id()
    return jsonify({"success": True, "party": _hydrate_party_profiles(party, who)})


@bp.route("/api/player/parties/<party_id>/leave", methods=["POST"])
@device_playback
@self_only
def leave_watch_party(party_id):
    from core.player import parties
    pid = _profile()
    if pid is None:
        return _signed_out()
    parties.leave_party(player_db(), party_id, pid)
    return jsonify({"success": True})


@bp.route("/api/player/parties/<party_id>/state", methods=["POST"])
@device_playback
@self_only
def update_watch_party_state(party_id):
    """Start movie from lobby, play/pause/seek, change lobby track, or switch title."""
    from core.player import parties
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    if body.get("shuffle_lobby_music"):
        body = {**body, "lobby_track": _pick_default_lobby_track()}
    if body.get("item_type") and body.get("item_id") and not _party_may_see(
        str(body["item_type"]), str(body["item_id"])
    ):
        return jsonify({"success": False, "error": "not_allowed",
                        "message": "That title isn't available to this profile."}), 403
    try:
        party = parties.update_state(player_db(), party_id, pid, body, is_admin=_is_player_admin())
    except parties.PartyError as e:
        return _bad(e)
    who = _profiles_by_id()
    return jsonify({"success": True, "party": _hydrate_party_profiles(party, who)})


@bp.route("/api/player/parties/<party_id>/chat", methods=["POST"])
@device_playback
@self_only
def post_watch_party_chat(party_id):
    """Send a chat message or quick emoji reaction in a watch party."""
    from core.player import parties
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        msg = parties.post_message(player_db(), party_id, pid, request.get_json(silent=True))
    except parties.PartyError as e:
        return _bad(e)
    who = _profiles_by_id()
    msg["profile"] = who.get(pid, {"name": "You"})
    return jsonify({"success": True, "message": msg})


@bp.route("/api/player/parties/<party_id>", methods=["DELETE"])
@device_playback
@self_only
def delete_watch_party(party_id):
    from core.player import parties
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        deleted = parties.delete_party(player_db(), party_id, pid, is_admin=_is_player_admin())
    except parties.PartyError as e:
        return _bad(e)
    return jsonify({"success": True, "deleted": deleted})


# ── Offline Downloads Tracking & Admin User Management ───────────────────────


@bp.route("/api/player/downloads", methods=["GET"])
@device_playback
@self_only
def player_downloads_list():
    """List this profile's downloaded items and whether downloads are enabled."""
    from core.player import downloads
    pid = _profile()
    if pid is None:
        return _signed_out()
    include_removed = request.args.get("include_removed", "").lower() in ("1", "true", "yes")
    perm = downloads.get_permission(player_db(), pid)
    items = downloads.list_for_profile(player_db(), pid, include_removed=include_removed)
    return jsonify({
        "success": True,
        "allow_downloads": perm["allow_downloads"],
        "max_items": perm["max_items"],
        "is_admin": _is_player_admin(),
        "downloads": items,
    })


@bp.route("/api/player/downloads", methods=["POST"])
@device_playback
@self_only
def player_downloads_log():
    """Log an offline download (movie, episode, youtube, podcast, audiobook, track, album)."""
    from core.player import downloads
    pid = _profile()
    if pid is None:
        return _signed_out()
    perm = downloads.get_permission(player_db(), pid)
    if not perm["allow_downloads"] and not _is_player_admin():
        return jsonify({
            "success": False,
            "error": "downloads_disabled",
            "message": "Downloads have been disabled for your profile by an admin.",
        }), 403
    body = request.get_json(silent=True) or {}
    try:
        row = downloads.log_download(
            player_db(),
            pid,
            item_type=body.get("item_type") or "",
            item_id=str(body.get("item_id") or ""),
            title=body.get("title") or "",
            subtitle=body.get("subtitle"),
            artwork=body.get("artwork"),
            device_id=body.get("device_id") or "web",
            device_name=body.get("device_name") or "Web Browser",
            quality=body.get("quality") or "original",
            size_bytes=body.get("size_bytes") or 0,
            # the admin's cap; an admin keeps what they like
            max_items=0 if _is_player_admin() else int(perm.get("max_items") or 0),
        )
    except downloads.DownloadLimitReached as e:
        return jsonify({"success": False, "error": "download_limit", "message": str(e)}), 403
    except ValueError as e:
        return _bad(e)
    return jsonify({"success": True, "download": row})


@bp.route("/api/player/downloads/removed", methods=["POST"])
@device_playback
@self_only
def player_downloads_removed():
    """Sync deletion of a downloaded item from a user's device back to the server."""
    from core.player import downloads
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    dl_id = body.get("id")
    updated = downloads.mark_removed(
        player_db(),
        pid,
        download_id=int(dl_id) if isinstance(dl_id, int) or str(dl_id or "").isdigit() else None,
        item_type=body.get("item_type"),
        item_id=str(body.get("item_id")) if body.get("item_id") is not None else None,
        device_id=body.get("device_id"),
    )
    return jsonify({"success": True, "updated": updated})


@bp.route("/api/player/admin/users", methods=["GET"])
@device_playback
@self_only
def player_admin_users():
    """Admin-only overview of all profiles, download permissions, and device downloads."""
    from core.player import downloads
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _is_player_admin():
        return jsonify({"success": False, "error": "forbidden", "message": "Admin access required."}), 403
    profiles = get_database().get_all_profiles() or []
    filter_pid = request.args.get("profile_id")
    overview = downloads.admin_overview(
        player_db(),
        profiles,
        filter_profile_id=int(filter_pid) if filter_pid and filter_pid.isdigit() else None,
        filter_type=request.args.get("item_type") or None,
        filter_status=request.args.get("status") or None,
    )
    return jsonify({"success": True, **overview})


@bp.route("/api/player/admin/users/<int:target_profile_id>/downloads", methods=["PUT"])
@device_playback
@self_only
def player_admin_user_downloads(target_profile_id: int):
    """Admin-only toggle for a user's offline download permission and optional item cap."""
    from core.player import downloads
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _is_player_admin():
        return jsonify({"success": False, "error": "forbidden", "message": "Admin access required."}), 403
    body = request.get_json(silent=True) or {}
    allow = bool(body.get("allow_downloads", True))
    try:
        max_items = max(0, int(body.get("max_items") or 0))
    except (TypeError, ValueError):
        return _bad("max_items must be a number")
    perm = downloads.set_permission(player_db(), target_profile_id, allow, max_items)
    return jsonify({"success": True, "profile_id": target_profile_id, **perm})


# ── libraries: their own pages in the player, and who can't see them ────────

@bp.route("/api/player/libraries", methods=["GET"])
@device_playback
@requires_any("movies.access", "tv.access")
def player_libraries_list():
    """the video libraries this profile can see: {id, kind, name, own_page,
    main}. a library with own_page gets its own entry and page in the player;
    the rest are part of Movies / TV Shows. (core/player/libraries.py)"""
    sees_movies, sees_shows, _yt, _cap = _video_view()
    return jsonify({"success": True, "libraries": _visible_libraries(sees_movies, sees_shows)})


@bp.route("/api/player/setup", methods=["GET"])
@device_playback
@self_only
def player_setup_get():
    """player setup. everyone gets {done, server_name, is_admin}; an admin
    also gets what there is to set up: every folder of each kind with the
    library it is in and a few titles to know it by, the libraries, the
    profiles, who each library is hidden from and the server's sidebar."""
    from core.player import libraries as player_libraries
    from core.player import setup as player_setup
    if _profile() is None:
        return _signed_out()
    try:
        db = player_db()
        state = player_setup.get(db)
        out = {"success": True, "done": state["done"], "server_name": state["server_name"],
               "is_admin": _is_player_admin()}
        if not out["is_admin"]:
            return jsonify(out)
        vdb = get_video_db()
        out.update({
            "sidebar_order": state["sidebar_order"],
            "sidebar_hidden": state["sidebar_hidden"],
            "libraries": player_libraries.libraries(db),
            "folders": player_libraries.folder_overview(vdb, db),
            "hidden": {str(k): v for k, v in player_libraries.hidden_map(db).items()},
            "profiles": [{"id": p.get("id"), "name": p.get("name"), "is_admin": bool(p.get("is_admin"))}
                         for p in (get_database().get_all_profiles() or [])],
        })
        return jsonify(out)
    except Exception:  # noqa: BLE001
        return _failed("player setup read")


@bp.route("/api/player/setup", methods=["PUT"])
@device_playback
@self_only
def player_setup_put():
    """admin: save setup. any of {server_name, sidebar_order, sidebar_hidden,
    done, libraries: [{id?, kind, name, folders}], hidden: {library: [profile
    id]}}. libraries is the whole set (core/player/libraries.save); a library
    hidden from an admin profile is dropped from the list, admins see all."""
    from core.player import libraries as player_libraries
    from core.player import setup as player_setup
    if _profile() is None:
        return _signed_out()
    if not _is_player_admin():
        return jsonify({"success": False, "error": "forbidden", "message": "Admin access required."}), 403
    body = request.get_json(silent=True) or {}
    try:
        db = player_db()
        plain = {k: body[k] for k in ("server_name", "sidebar_order", "sidebar_hidden", "done") if k in body}
        # a library made in this same save has no id yet: the sidebar names it
        # by its place in the list ("lib:new:2") until the save gives it one
        wanted = body.get("libraries") if isinstance(body.get("libraries"), list) else []

        def _places(value, ids):
            if not isinstance(value, list):
                return value
            out = []
            for v in value:
                m = re.match(r"^lib:new:(\d{1,3})$", v) if isinstance(v, str) else None
                if not m:
                    out.append(v)
                elif int(m.group(1)) in ids:
                    out.append(f"lib:{ids[int(m.group(1))]}")
            return out

        # checked before anything is written, so a bad name doesn't leave the libraries half saved
        maybe = {n: 0 for n, item in enumerate(wanted) if isinstance(item, dict) and item.get("id") is None}
        for k, v in plain.items():
            player_setup._clean(k, _places(v, maybe) if k.startswith("sidebar_") else v)
        if "libraries" in body:
            hidden = body.get("hidden")
            if isinstance(hidden, dict):
                admins = {p.get("id") for p in (get_database().get_all_profiles() or []) if p.get("is_admin")}
                hidden = {k: [p for p in (v or []) if p not in admins] for k, v in hidden.items()}
            saved = player_libraries.save(db, get_video_db(), body.get("libraries"), hidden)
            _forget_catalog_views()
            by_name = {(lib["kind"], lib["name"].casefold()): lib["id"] for lib in saved}
            new_ids = {}
            for n in maybe:
                key = (str(wanted[n].get("kind") or ""), " ".join(str(wanted[n].get("name") or "").split()).casefold())
                if key in by_name:
                    new_ids[n] = by_name[key]
            for k in ("sidebar_order", "sidebar_hidden"):
                if k in plain:
                    plain[k] = _places(plain[k], new_ids)
        if plain:
            player_setup.put(db, plain)
        return player_setup_get()
    except (player_libraries.LibraryError, player_setup.SetupError) as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("player setup save")


def _forget_catalog_views():
    """who sees what just changed: the next catalog read is a fresh one"""
    with _cache_lock:
        _cache.clear()


# ── player media issue reports (all media types: movie/episode/youtube/track/podcast/audiobook) ──


@bp.route("/api/player/issues", methods=["GET"])
@device_playback
@self_only
def player_issues_list():
    """List reported media issues. Admins see all; members see their own reports."""
    from core.player import issues as player_issues
    pid = _profile()
    if pid is None:
        return _signed_out()
    can_manage = _is_player_admin()
    status = request.args.get("status") or None
    try:
        limit = int(request.args.get("limit") or 100)
    except (TypeError, ValueError):
        limit = 100
    data = player_issues.list_issues(
        player_db(),
        profile_id=pid,
        is_admin=can_manage,
        status=status,
        limit=limit,
    )
    if not can_manage:
        # where a file lives is the admin's business, not the reporter's
        for issue in data.get("issues") or []:
            issue.pop("file_path", None)
    return jsonify({"success": True, "can_manage": can_manage, **data})


@bp.route("/api/player/issues", methods=["POST"])
@device_playback
@self_only
def player_issues_create():
    """Report a playback, audio, subtitle, quality, or metadata issue on any media item."""
    from flask import g
    from core.player import issues as player_issues
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        pos = int(body.get("position_sec") or 0)
    except (TypeError, ValueError):
        pos = 0
    try:
        issue = player_issues.create_issue(
            player_db(),
            profile_id=pid,
            profile_name=str(getattr(g, "profile_name", "") or f"Profile {pid}"),
            item_type=body.get("item_type") or "",
            item_id=body.get("item_id") or "",
            title=body.get("title") or "",
            subtitle=body.get("subtitle") or "",
            category=body.get("category") or "playback",
            note=body.get("note") or "",
            position_sec=pos,
        )
    except ValueError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("player issue create", "Couldn't submit issue report.")
    if not _is_player_admin():
        issue.pop("file_path", None)
    return jsonify({"success": True, "issue": issue}), 201


@bp.route("/api/player/issues/<int:issue_id>", methods=["PUT"])
@device_playback
@self_only
def player_issues_update(issue_id: int):
    """Admin-only: resolve, dismiss, or reopen a reported media issue."""
    from core.player import issues as player_issues
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _is_player_admin():
        return jsonify({"success": False, "error": "admin_required", "message": "Admin access required."}), 403
    body = request.get_json(silent=True) or {}
    try:
        updated = player_issues.update_issue(
            player_db(),
            issue_id,
            status=body.get("status"),
            admin_note=body.get("admin_note"),
        )
    except ValueError as e:
        return _bad(e)
    if updated is None:
        return jsonify({"success": False, "error": "not_found", "message": "Issue not found."}), 404
    return jsonify({"success": True, "issue": updated})


@bp.route("/api/player/issues/<int:issue_id>", methods=["DELETE"])
@device_playback
@self_only
def player_issues_delete(issue_id: int):
    """Delete a reported media issue (admin or original reporter)."""
    from core.player import issues as player_issues
    pid = _profile()
    if pid is None:
        return _signed_out()
    ok = player_issues.delete_issue(player_db(), issue_id, profile_id=pid, is_admin=_is_player_admin())
    if not ok:
        return jsonify({"success": False, "error": "not_found", "message": "Issue not found."}), 404
    return jsonify({"success": True})
