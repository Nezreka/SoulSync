"""seerr (jellyseerr / overseerr) compatible api at /seerr/api/v1 (#2151).

apps built for seerr, like litefin's jellyfin plugin, set their seerr url to
http://<soulsync>/seerr with a soulsync admin api key. they discover and
search through soulsync's tmdb engine and their requests land on the video
wishlist (or wait for approval). off until it's turned on in settings, and
then only an admin key gets in. ``X-Api-User`` picks the soulsync profile a
call acts for, the way seerr uses it to pick a seerr user, and that profile's
permissions decide everything after (core.seerr_compat.service). a missing or
unknown user acts as the fallback profile, or is refused when
``seerr_compat.refuse_unknown`` is on (new installs, #2166).

the logic is in core/seerr_compat. this file parses requests and wires the
real engine, video db, profiles and jellyfin users in.
"""

from __future__ import annotations

import threading
import time
from functools import wraps

from flask import Blueprint, Response, jsonify, request

from core.permissions import ACCESS_ATTR
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


def unknown_users() -> str:
    """refuse | profile: what a missing or unknown X-Api-User gets. new
    installs ship refuse_unknown on (core/settings.py defaults); an install
    from before it has no key and keeps acting as the fallback profile, which
    the Seerr settings show with a switch to refuse."""
    from core.seerr_compat.service import FALLBACK_UNKNOWN, REFUSE_UNKNOWN
    try:
        refuse = config_manager.get("seerr_compat.refuse_unknown", False)
    except Exception:  # noqa: BLE001 - unreadable refuses
        return REFUSE_UNKNOWN
    if isinstance(refuse, str):
        refuse = refuse.strip().lower() not in ("false", "0", "off", "")
    return REFUSE_UNKNOWN if refuse is not False else FALLBACK_UNKNOWN


def _perms_for(profile):
    from core.permission_store import context_for
    return context_for(get_database(), profile.get("id"))


def _quality_profiles():
    """the video quality profiles with media type, tags and range, Default
    (id 0) first: the request resolver's own list
    (core.video.quality_profile.quality_options), so the 4K tag here is the
    one "4K always asks" means on every other path."""
    from core.video.quality_profile import quality_options
    return quality_options(_video_db())


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


def _plex_names() -> dict:
    """{profile id: plex username} for profiles linked to a plex account
    (plex sign-in or an import from plex, #2319), so a seerr client that
    matches people by their plex name finds them"""
    try:
        with get_database()._get_connection() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
            if "plex_account_id" not in cols:
                return {}
            rows = conn.execute(
                "SELECT id, name, plex_home_user_title FROM profiles "
                "WHERE plex_account_id IS NOT NULL AND plex_account_id != ''").fetchall()
        return {int(r[0]): (r[2] or r[1] or "") for r in rows}
    except Exception:  # noqa: BLE001 - the names are a nicety
        logger.debug("seerr compat: plex names failed", exc_info=True)
        return {}


def _get_profile(pid):
    p = get_database().get_profile(pid)
    if p:
        p = dict(p)
        name = _plex_names().get(int(p["id"]))
        if name:
            p["plex_username"] = name
    return p


def _list_profiles():
    db = get_database()
    out = []
    plex = _plex_names()
    for p in db.get_all_profiles() or []:
        p = dict(p)
        if plex.get(int(p["id"])):
            p["plex_username"] = plex[int(p["id"])]
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


def _title_allowed(kind, tmdb_id, cap) -> bool:
    """the acting profile's rating cap for this kind (an age, None = none),
    the same check the video pages use."""
    if cap is None:
        return True
    from api.video.kids import tmdb_title_allowed
    return tmdb_title_allowed(_video_db(), kind, tmdb_id, cap)


def _filter_titles(items, caps):
    """discover, search and lists keep only the titles the library rates
    under the cap of their kind (movies and tv may differ), the same rule the
    video discover rails use. people and other cards pass."""
    from api.video.kids import filter_tmdb_items
    db = _video_db()
    keep = set()
    for kind in ("movie", "show"):
        mine = [it for it in items if isinstance(it, dict) and it.get("kind") == kind]
        cap = caps.get(kind)
        kept = mine if cap is None else filter_tmdb_items(db, mine, cap)
        keep |= {id(it) for it in kept}
    return [it for it in items if not isinstance(it, dict) or it.get("kind") not in ("movie", "show")
            or id(it) in keep]


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
                get_profile=_get_profile,
                list_profiles=_list_profiles, jellyfin_users=_jellyfin_users,
                fallback_profile_id=fallback_profile_id, server=_server,
                title_allowed=_title_allowed, filter_titles=_filter_titles,
                publish=_publish, show_wanted=_show_wanted, perms_for=_perms_for,
                unknown_users=unknown_users, quality_profiles=_quality_profiles)
        return _service


def _message(status, message):
    return jsonify({"message": message}), status


def seerr_route(public=False, acting=True):
    """off -> 404 like the route isn't there. on -> admin api key only
    (``public`` routes are open, like seerr's own /status). ``acting`` routes
    then need someone to act for: X-Api-User's profile, or the fallback
    profile while unknown users aren't refused (else seerr's 403).

    route inventory (#2166): the key is admin-only, so these declare admin;
    what the acting profile may do is checked inside, per call."""

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
                if not public and acting:
                    service().profile(_api_user())
                return f(*args, **kwargs)
            except SeerrError as e:
                return _message(e.status, str(e))
            except Exception:  # noqa: BLE001
                logger.exception("seerr api %s %s failed", request.method, request.path)
                return _message(500, "Something went wrong.")
        setattr(wrapped, ACCESS_ATTR, ("public", None) if public else ("admin", None))
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
@seerr_route(acting=False)
def auth_me():
    return jsonify(service().me(_api_user()))


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
    return jsonify(service().user_one(uid))


# ---- discover and search ---------------------------------------------------------

@bp.route("/discover/trending", methods=["GET"])
@seerr_route()
def discover_trending():
    return jsonify(service().trending(_page(), pid=_api_user()))


@bp.route("/discover/movies", methods=["GET"])
@seerr_route()
def discover_movies():
    return jsonify(service().discover("movie", _page(), request.args.to_dict(), pid=_api_user()))


@bp.route("/discover/tv", methods=["GET"])
@seerr_route()
def discover_tv():
    return jsonify(service().discover("show", _page(), request.args.to_dict(), pid=_api_user()))


@bp.route("/discover/movies/upcoming", methods=["GET"])
@seerr_route()
def discover_movies_upcoming():
    return jsonify(service().upcoming("movie", _page(), pid=_api_user()))


@bp.route("/discover/tv/upcoming", methods=["GET"])
@seerr_route()
def discover_tv_upcoming():
    return jsonify(service().upcoming("show", _page(), pid=_api_user()))


@bp.route("/discover/movies/studio/<int:studio_id>", methods=["GET"])
@seerr_route()
def discover_studio(studio_id):
    return jsonify(service().discover("movie", _page(), {"studio": str(studio_id)}, pid=_api_user()))


@bp.route("/discover/tv/network/<int:network_id>", methods=["GET"])
@seerr_route()
def discover_network(network_id):
    return jsonify(service().discover("show", _page(), {"network": str(network_id)}, pid=_api_user()))


@bp.route("/discover/movies/genre/<int:genre_id>", methods=["GET"])
@seerr_route()
def discover_movie_genre(genre_id):
    return jsonify(service().discover("movie", _page(), {"genre": str(genre_id)}, pid=_api_user()))


@bp.route("/discover/tv/genre/<int:genre_id>", methods=["GET"])
@seerr_route()
def discover_tv_genre(genre_id):
    return jsonify(service().discover("show", _page(), {"genre": str(genre_id)}, pid=_api_user()))


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
    return jsonify(service().search(request.args.get("query") or "", _page(), pid=_api_user()))


# ---- details ---------------------------------------------------------------------

@bp.route("/<media_type>/<int:tmdb_id>", methods=["GET"])
@seerr_route()
def details(media_type, tmdb_id):
    return jsonify(service().details(_kind(media_type), tmdb_id, pid=_api_user()))


@bp.route("/<media_type>/<int:tmdb_id>/similar", methods=["GET"])
@seerr_route()
def similar(media_type, tmdb_id):
    return jsonify(service().similar(_kind(media_type), tmdb_id, _page(), pid=_api_user()))


@bp.route("/<media_type>/<int:tmdb_id>/recommendations", methods=["GET"])
@seerr_route()
def recommendations(media_type, tmdb_id):
    return jsonify(service().recommendations(_kind(media_type), tmdb_id, _page(), pid=_api_user()))


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
    return jsonify(service().person_credits(person_id, pid=_api_user()))


@bp.route("/collection/<int:collection_id>", methods=["GET"])
@seerr_route()
def collection(collection_id):
    return jsonify(service().collection(collection_id, pid=_api_user()))


@bp.route("/media", methods=["GET"])
@seerr_route()
def media():
    return jsonify(service().recently_added(_arg_int("take", 20), _arg_int("skip", 0), pid=_api_user()))


# ---- requests --------------------------------------------------------------------

@bp.route("/request", methods=["GET"])
@seerr_route()
def request_list():
    return jsonify(service().requests(
        take=_arg_int("take", 10), skip=_arg_int("skip", 0),
        filt=request.args.get("filter") or "all",
        requested_by=request.args.get("requestedBy"), pid=_api_user()))


@bp.route("/request", methods=["POST"])
@seerr_route()
def request_create():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise SeerrError(400, "A JSON body is required.")
    # is4k and profileId are read by the service: the acting profile's 4K
    # profile, or its allowed pick (resolve_request_quality)
    return jsonify(service().create_request(_api_user(), body)), 201


@bp.route("/request/<int:request_id>", methods=["DELETE"])
@seerr_route()
def request_cancel(request_id):
    service().cancel_request(_api_user(), request_id)
    return Response(status=204)


# ---- quality: soulsync's own profiles as radarr/sonarr "servers" ------------------

@bp.route("/service/radarr", methods=["GET"])
@seerr_route()
def services_radarr():
    svc = service()
    return jsonify(svc.services(svc.profile(_api_user()), "movie"))


@bp.route("/service/sonarr", methods=["GET"])
@seerr_route()
def services_sonarr():
    svc = service()
    return jsonify(svc.services(svc.profile(_api_user()), "show"))


@bp.route("/service/radarr/<int:server_id>", methods=["GET"])
@seerr_route()
def service_radarr(server_id):
    svc = service()
    out = svc.services(svc.profile(_api_user()), "movie", server_id)
    if out is None:
        raise SeerrError(404, "Not Found")
    return jsonify(out)


@bp.route("/service/sonarr/<int:server_id>", methods=["GET"])
@seerr_route()
def service_sonarr(server_id):
    svc = service()
    out = svc.services(svc.profile(_api_user()), "show", server_id)
    if out is None:
        raise SeerrError(404, "Not Found")
    return jsonify(out)


# ---- what soulsync doesn't have ----------------------------------------------------


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
