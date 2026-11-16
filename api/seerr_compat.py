"""seerr (jellyseerr / overseerr) compatible api at /seerr/api/v1 (#2151).

apps built for seerr, like litefin's jellyfin plugin, set their seerr url to
http://<soulsync>/seerr with a soulsync admin api key. they discover and
search through soulsync's tmdb engine and their requests land on the video
wishlist (or wait for approval). off until it's turned on in settings, and
then only an admin key gets in. ``X-Api-User`` picks the soulsync profile a
call acts for, the way seerr uses it to pick a seerr user.

the logic is in core/seerr_compat. this file parses requests and wires the
real engine, video db, profiles and jellyfin users in.
"""

from __future__ import annotations

import threading
import time
from functools import wraps

from flask import Blueprint, Response, jsonify, request

from core.seerr_compat import shapes
from core.seerr_compat.service import SeerrCompat, SeerrError
from core.seerr_compat.store import SeerrStore
from utils.logging_config import get_logger

logger = get_logger("api.seerr_compat")

bp = Blueprint("seerr_compat", __name__, url_prefix="/seerr/api/v1")

get_database = None
config_manager = None
_service = None
_service_lock = threading.Lock()
_jf_cache = {"at": 0.0, "users": []}
_JF_TTL_S = 300


def configure(*, get_database, config_manager):
    globals()["get_database"] = get_database
    globals()["config_manager"] = config_manager


def create_blueprint():
    return bp


def enabled() -> bool:
    try:
        return bool(config_manager.get("seerr_compat.enabled", False))
    except Exception:  # noqa: BLE001
        return False


def fallback_profile_id() -> int:
    """whose requests the ones from jellyfin users with no soulsync profile are."""
    try:
        return int(config_manager.get("seerr_compat.profile_id", 1) or 1)
    except (TypeError, ValueError):
        return 1


def _video_db():
    from api.video import get_video_db
    return get_video_db()


def _engine():
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine()


def _server():
    try:
        from core.video.sources import resolve_video_server
        return resolve_video_server()
    except Exception:  # noqa: BLE001
        return None


def _list_profiles():
    db = get_database()
    out = []
    for p in db.get_all_profiles() or []:
        p = dict(p)
        try:
            p["jellyfin_user_id"] = (db.get_profile_server_library(p["id"]) or {}).get("jellyfin_user_id")
        except Exception:  # noqa: BLE001
            logger.debug("seerr compat: server identity for profile %s failed", p.get("id"))
        out.append(p)
    return out


def _jellyfin_users():
    """the jellyfin server's users (id + name), kept a few minutes. the video
    side's jellyfin connection, or the music one it inherits."""
    now = time.monotonic()
    if now - _jf_cache["at"] < _JF_TTL_S:
        return list(_jf_cache["users"])
    users = []
    try:
        import requests

        from core.jellyfin_client import jellyfin_auth_headers
        from core.video.sources import video_jellyfin_config
        cfg = video_jellyfin_config()
        base = (cfg.get("base_url") or "").rstrip("/")
        if base and cfg.get("api_key"):
            r = requests.get(base + "/Users", headers=jellyfin_auth_headers(cfg["api_key"]), timeout=8)
            r.raise_for_status()
            users = [{"id": u.get("Id"), "name": u.get("Name")} for u in r.json() or [] if u.get("Id")]
    except Exception as e:  # noqa: BLE001 - no jellyfin, profiles alone answer
        logger.debug("seerr compat: jellyfin users failed: %s", e)
    _jf_cache.update(at=now, users=users)
    return list(users)


def _title_allowed(kind, tmdb_id, profile) -> bool:
    """kids profiles: the same rating cap the video pages use."""
    from core.content_filter import restrictions_for
    cap = restrictions_for(profile).get("cap")
    if cap is None:
        return True
    from api.video.kids import tmdb_title_allowed
    return tmdb_title_allowed(_video_db(), kind, tmdb_id, cap)


def _publish(event, payload):
    from core.video.download_events import publish
    publish(event, payload)


def _show_wanted(tmdb_id):
    from core.video.anime_hint import note_show_wanted
    note_show_wanted(_video_db, tmdb_id)


def service() -> SeerrCompat:
    global _service
    with _service_lock:
        if _service is None:
            _service = SeerrCompat(
                engine=_engine, video_db=_video_db,
                store=SeerrStore(lambda: _video_db()._get_connection()),
                get_profile=lambda pid: get_database().get_profile(pid),
                list_profiles=_list_profiles, jellyfin_users=_jellyfin_users,
                fallback_profile_id=fallback_profile_id, server=_server,
                title_allowed=_title_allowed, publish=_publish, show_wanted=_show_wanted)
        return _service


def _message(status, message):
    return jsonify({"message": message}), status


def seerr_route(public=False):
    """off -> 404 like the route isn't there. on -> admin api key only
    (``public`` routes are open, like seerr's own /status)."""

    def deco(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not enabled():
                return _message(404, "Not Found")
            if not public:
                from api.auth import SCOPE_ADMIN, key_scope, matched_api_key
                record = matched_api_key()
                if record is None or key_scope(record) != SCOPE_ADMIN:
                    return _message(403, "You do not have permission to access this endpoint")
            try:
                return f(*args, **kwargs)
            except SeerrError as e:
                return _message(e.status, str(e))
            except Exception:  # noqa: BLE001
                logger.exception("seerr api %s %s failed", request.method, request.path)
                return _message(500, "Something went wrong.")
        return wrapped
    return deco


def _arg_int(name, default):
    try:
        return int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default


def _page():
    return max(1, _arg_int("page", 1))


def _api_user():
    return request.headers.get("X-Api-User")


def _kind(media_type):
    kind = shapes.soulsync_kind(media_type)
    if not kind:
        raise SeerrError(404, "Not Found")
    return kind


# ---- status, auth and users ------------------------------------------------------

@bp.route("/status", methods=["GET"])
@seerr_route(public=True)
def status():
    return jsonify({"version": shapes.VERSION, "commitTag": "soulsync", "updateAvailable": False,
                    "commitsBehind": 0, "restartRequired": False})


@bp.route("/auth/me", methods=["GET"])
@seerr_route()
def auth_me():
    svc = service()
    return jsonify(svc.user(svc.profile(_api_user())))


@bp.route("/user", methods=["GET"])
@seerr_route()
def users():
    take = max(1, min(1000, _arg_int("take", 10)))
    skip = max(0, _arg_int("skip", 0))
    everyone = service().users()
    return jsonify(shapes.page_info(everyone[skip:skip + take], skip // take + 1, take, len(everyone)))


@bp.route("/user/<int:uid>", methods=["GET"])
@seerr_route()
def user_one(uid):
    svc = service()
    p = svc._get_profile(uid)
    if not p:
        raise SeerrError(404, "User not found.")
    return jsonify(svc.user(p))


# ---- discover and search ---------------------------------------------------------

@bp.route("/discover/trending", methods=["GET"])
@seerr_route()
def discover_trending():
    return jsonify(service().trending(_page()))


@bp.route("/discover/movies", methods=["GET"])
@seerr_route()
def discover_movies():
    return jsonify(service().discover("movie", _page(), request.args.to_dict()))


@bp.route("/discover/tv", methods=["GET"])
@seerr_route()
def discover_tv():
    return jsonify(service().discover("show", _page(), request.args.to_dict()))


@bp.route("/discover/movies/upcoming", methods=["GET"])
@seerr_route()
def discover_movies_upcoming():
    return jsonify(service().upcoming("movie", _page()))


@bp.route("/discover/tv/upcoming", methods=["GET"])
@seerr_route()
def discover_tv_upcoming():
    return jsonify(service().upcoming("show", _page()))


@bp.route("/discover/movies/studio/<int:studio_id>", methods=["GET"])
@seerr_route()
def discover_studio(studio_id):
    return jsonify(service().discover("movie", _page(), {"studio": str(studio_id)}))


@bp.route("/discover/tv/network/<int:network_id>", methods=["GET"])
@seerr_route()
def discover_network(network_id):
    return jsonify(service().discover("show", _page(), {"network": str(network_id)}))


@bp.route("/discover/movies/genre/<int:genre_id>", methods=["GET"])
@seerr_route()
def discover_movie_genre(genre_id):
    return jsonify(service().discover("movie", _page(), {"genre": str(genre_id)}))


@bp.route("/discover/tv/genre/<int:genre_id>", methods=["GET"])
@seerr_route()
def discover_tv_genre(genre_id):
    return jsonify(service().discover("show", _page(), {"genre": str(genre_id)}))


@bp.route("/discover/genreslider/movie", methods=["GET"])
@seerr_route()
def genreslider_movie():
    return jsonify(service().genre_slider("movie"))


@bp.route("/discover/genreslider/tv", methods=["GET"])
@seerr_route()
def genreslider_tv():
    return jsonify(service().genre_slider("show"))


@bp.route("/search", methods=["GET"])
@seerr_route()
def search():
    return jsonify(service().search(request.args.get("query") or "", _page()))


# ---- details ---------------------------------------------------------------------

@bp.route("/<media_type>/<int:tmdb_id>", methods=["GET"])
@seerr_route()
def details(media_type, tmdb_id):
    return jsonify(service().details(_kind(media_type), tmdb_id))


@bp.route("/<media_type>/<int:tmdb_id>/similar", methods=["GET"])
@seerr_route()
def similar(media_type, tmdb_id):
    return jsonify(service().similar(_kind(media_type), tmdb_id, _page()))


@bp.route("/<media_type>/<int:tmdb_id>/recommendations", methods=["GET"])
@seerr_route()
def recommendations(media_type, tmdb_id):
    return jsonify(service().recommendations(_kind(media_type), tmdb_id, _page()))


@bp.route("/<media_type>/<int:tmdb_id>/ratingscombined", methods=["GET"])
@bp.route("/<media_type>/<int:tmdb_id>/ratings", methods=["GET"])
@seerr_route()
def ratings(media_type, tmdb_id):
    # rotten tomatoes and imdb scores aren't something soulsync looks up for a
    # title it doesn't own, so the neutral empty answer
    _kind(media_type)
    return jsonify({})


@bp.route("/person/<int:person_id>", methods=["GET"])
@seerr_route()
def person(person_id):
    return jsonify(service().person(person_id))


@bp.route("/person/<int:person_id>/combined_credits", methods=["GET"])
@seerr_route()
def person_credits(person_id):
    return jsonify(service().person_credits(person_id))


@bp.route("/collection/<int:collection_id>", methods=["GET"])
@seerr_route()
def collection(collection_id):
    return jsonify(service().collection(collection_id))


@bp.route("/media", methods=["GET"])
@seerr_route()
def media():
    return jsonify(service().recently_added(_arg_int("take", 20), _arg_int("skip", 0)))


# ---- requests --------------------------------------------------------------------

@bp.route("/request", methods=["GET"])
@seerr_route()
def request_list():
    return jsonify(service().requests(
        take=_arg_int("take", 10), skip=_arg_int("skip", 0),
        filt=request.args.get("filter") or "all",
        requested_by=request.args.get("requestedBy")))


@bp.route("/request", methods=["POST"])
@seerr_route()
def request_create():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise SeerrError(400, "A JSON body is required.")
    if body.get("is4k"):
        raise SeerrError(403, "SoulSync keeps one copy of a title, 4K requests aren't separate.")
    return jsonify(service().create_request(_api_user(), body)), 201


@bp.route("/request/<int:request_id>", methods=["DELETE"])
@seerr_route()
def request_cancel(request_id):
    service().cancel_request(_api_user(), request_id)
    return Response(status=204)


# ---- what soulsync doesn't have ----------------------------------------------------

@bp.route("/service/radarr", methods=["GET"])
@bp.route("/service/sonarr", methods=["GET"])
@seerr_route()
def services():
    # no radarr or sonarr behind this: quality comes from soulsync's own profiles
    return jsonify([])


@bp.route("/user/<int:uid>/watchlist", methods=["GET"])
@seerr_route()
def watchlist(uid):
    # seerr's watchlist is a watch-later list soulsync doesn't keep
    return jsonify({"page": 1, "totalPages": 1, "totalResults": 0, "results": []})


@bp.route("/watchlist", methods=["POST"])
@bp.route("/watchlist/<int:tmdb_id>", methods=["DELETE"])
@seerr_route()
def watchlist_change(tmdb_id=None):
    raise SeerrError(501, "SoulSync doesn't keep a watch-later list. Request the title instead.")
