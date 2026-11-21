"""the /player app's own endpoints: where each profile is in what it plays.

every route reads and writes the caller's own rows only. the profile comes
from the session (get_current_profile_id), never from the request, so there
is no way to name someone else's.
"""

from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, request

from core.permissions import requires, requires_any, self_only
from core.player import catalog, progress
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("player")

bp = Blueprint("player", __name__)


def create_blueprint():
    return bp


def get_database():
    from database.music_database import get_database as _get
    return _get()


def _profile():
    pid = get_current_profile_id()
    return int(pid) if pid is not None else None


def _signed_out():
    return jsonify({"success": False, "error": "Not signed in"}), 401


def _bad(message):
    return jsonify({"success": False, "error": str(message)}), 400


def _failed(what):
    logger.exception("player %s failed", what)
    return jsonify({"success": False, "error": "Something went wrong saving your place"}), 500


@bp.route("/api/player/progress", methods=["GET"])
@self_only
def get_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        row = progress.get(get_database(), pid, request.args.get("item_type"),
                           request.args.get("item_id"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("read")
    return jsonify({"success": True, "progress": row})


@bp.route("/api/player/progress", methods=["PUT"])
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
        row = progress.save(get_database(), pid, body.get("item_type"), body.get("item_id"),
                            body.get("position_seconds"), body.get("duration_seconds"),
                            finished=finished, meta=body.get("meta"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("save")
    return jsonify({"success": True, "progress": row})


@bp.route("/api/player/progress/lookup", methods=["POST"])
@self_only
def lookup_progress():
    """many items of one type in one call: {item_type, item_ids: [...]}."""
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        rows = progress.lookup(get_database(), pid, body.get("item_type"), body.get("item_ids"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("lookup")
    return jsonify({"success": True, "progress": rows})


@bp.route("/api/player/progress/finish", methods=["POST"])
@self_only
def finish_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        row = progress.finish(get_database(), pid, body.get("item_type"), body.get("item_id"),
                              body.get("duration_seconds"), meta=body.get("meta"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("finish")
    return jsonify({"success": True, "progress": row})


@bp.route("/api/player/progress", methods=["DELETE"])
@self_only
def delete_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        removed = progress.remove(get_database(), pid, request.args.get("item_type"),
                                  request.args.get("item_id"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("remove")
    return jsonify({"success": True, "removed": removed})


@bp.route("/api/player/continue", methods=["GET"])
@self_only
def continue_rail():
    pid = _profile()
    if pid is None:
        return _signed_out()
    raw = request.args.get("item_type") or ""
    types = [t for t in raw.split(",") if t.strip()]
    try:
        items = progress.continue_list(get_database(), pid, types, request.args.get("limit"))
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


RATING = 4  # content_rating's place in a movie or show row (catalog.MOVIE_FIELDS)
_CACHE_SECONDS = 120
_cache: dict = {}
_cache_lock = threading.Lock()


def shape_video_catalog(raw, sees_movies, sees_shows, sees_youtube, cap):
    """drop the kinds and ratings this profile can't have. pure, so the
    filtering is tested apart from the db."""
    from api.video.kids import kind_allows

    def ok(kind, rating):
        return cap is None or kind_allows(cap, kind, rating)

    return {
        "fields": {"movie": list(catalog.MOVIE_FIELDS), "show": list(catalog.SHOW_FIELDS)},
        "genres": {str(k): v for k, v in (raw.get("genres") or {}).items()},
        "movies": [m for m in raw.get("movies", []) if sees_movies and ok("movie", m[RATING])],
        "shows": [s for s in raw.get("shows", []) if sees_shows and ok("show", s[RATING])],
        "recent_episodes": [e for e in raw.get("recent_episodes", [])
                            if sees_shows and ok("show", e.get("content_rating"))],
        "youtube": list(raw.get("youtube", [])) if sees_youtube else [],
        "kinds": {"movies": bool(sees_movies), "shows": bool(sees_shows), "youtube": bool(sees_youtube)},
    }


def _read_catalog(sees_movies, sees_shows, sees_youtube):
    """the unfiltered read, shared for a couple of minutes: on a big library
    it is a few seconds of table scans, and every screen opening the player
    asks for it."""
    key = (sees_movies, sees_shows, sees_youtube)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
        conn = get_video_db()._get_connection()
        try:
            raw = catalog.read_all(conn, sees_movies, sees_shows, sees_youtube)
        finally:
            conn.close()
        _cache[key] = (time.monotonic(), raw)
        return raw


@bp.route("/api/player/video-catalog", methods=["GET"])
@requires_any("movies.access", "tv.access")
def video_catalog():
    """the owned movies, shows (with a sample of episodes) and youtube
    downloads, with runtimes, genres, networks and ratings: what the guide
    schedules and the home rails show. read only."""
    try:
        sees_movies, sees_shows, sees_youtube, cap = _video_view()
        raw = _read_catalog(sees_movies, sees_shows, sees_youtube)
        return jsonify({"success": True, **shape_video_catalog(raw, sees_movies, sees_shows, sees_youtube, cap)})
    except Exception:  # noqa: BLE001
        logger.exception("player video catalog failed")
        return jsonify({"success": False, "error": "Could not read the video library"}), 500


def _youtube_gate():
    """the video side's own rule for youtube: any video access at all."""
    from api.video import access
    if not access.any_side():
        return jsonify({"success": False, "error": "Video isn't part of your profile"}), 403
    return None


@bp.route("/api/player/youtube", methods=["GET"])
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


@bp.route("/api/player/music-channels", methods=["GET"])
@requires("music.library")
def music_channels():
    """the guide's genre and vibe channels from the caller's owned tracks.
    cached a couple of minutes per profile: it is one pass over the tracks."""
    from contextlib import closing

    from core.player import music_channels as mc
    pid = _profile()
    if pid is None:
        return _signed_out()
    key = (pid, str(_session_scope()))
    now = time.monotonic()
    hit = _music_cache.get(key)
    if hit and now - hit[0] < _CACHE_SECONDS:
        return jsonify({"success": True, "channels": hit[1]})
    try:
        with closing(get_database()._get_connection()) as conn:
            channels = mc.read(conn)
    except Exception:  # noqa: BLE001
        logger.exception("player music channels failed")
        return jsonify({"success": False, "error": "Could not read the music library"}), 500
    _music_cache[key] = (time.monotonic(), channels)
    return jsonify({"success": True, "channels": channels})


def _session_scope():
    """the library scope the caller picked (#1199), part of the cache key"""
    try:
        from flask import session
        return session.get("library_scope")
    except Exception:  # noqa: BLE001
        return None


@bp.route("/api/player/guide/favorites", methods=["GET"])
@self_only
def get_favorites():
    from core.player import favorites
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        return jsonify({"success": True, "channels": favorites.get(get_database(), pid)})
    except Exception:  # noqa: BLE001
        return _failed("favorites read")


@bp.route("/api/player/guide/favorites", methods=["PUT"])
@self_only
def put_favorites():
    from core.player import favorites
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"success": True,
                        "channels": favorites.put(get_database(), pid, body.get("channels"))})
    except favorites.FavoritesError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("favorites save")
