"""lidarr compatible api at /lidarr/api/v1 (#1799).

request apps that only speak lidarr (singarr, musicseerr 1.x) point their
"lidarr url" at http://<soulsync>/lidarr with a soulsync admin api key. off
until it's turned on in settings, and then only an admin key gets in.

the logic is in core/lidarr_compat. this file parses requests and wires the
real musicbrainz, wishlist and library checks in.
"""

from __future__ import annotations

import threading
from functools import wraps

from flask import Blueprint, jsonify, request

from core.lidarr_compat import resources
from core.lidarr_compat.service import LidarrCompat, LidarrError, MusicBrainzSource
from core.lidarr_compat.store import LidarrStore
from utils.logging_config import get_logger

logger = get_logger("api.lidarr_compat")

bp = Blueprint("lidarr_compat", __name__, url_prefix="/lidarr/api/v1")

get_database = None
config_manager = None
_service = None
_service_lock = threading.Lock()


def configure(*, get_database, config_manager):
    globals()["get_database"] = get_database
    globals()["config_manager"] = config_manager


def create_blueprint():
    return bp


def enabled() -> bool:
    try:
        return bool(config_manager.get("lidarr_compat.enabled", False))
    except Exception:  # noqa: BLE001
        return False


def request_profile_id() -> int:
    """whose wishlist lidarr requests land on. a profile that can't download
    sends them through soulsync's own request approval."""
    try:
        pid = int(config_manager.get("lidarr_compat.profile_id", 1) or 1)
    except (TypeError, ValueError):
        return 1
    try:
        if get_database().get_profile(pid):
            return pid
    except Exception as e:  # noqa: BLE001
        logger.debug("lidarr request profile %s lookup failed: %s", pid, e)
    return 1


def _metadata_profile(pid):
    saved = config_manager.get("lidarr_compat.metadata_profiles", {}) or {}
    profiles = resources.metadata_profiles(saved if isinstance(saved, dict) else {})
    return next((p for p in profiles if p["id"] == int(pid)), profiles[0])


def _root_folder():
    return str(config_manager.get("soulseek.transfer_path", "./Transfer") or "./Transfer")


def _queue_album(album, artist_row) -> int:
    """put an album's tracks on the request profile's wishlist, through the
    same add the album modal uses (owned-track skip, ignore list, dedupe)."""
    from core.wishlist.album_request import queue_album_tracks

    return queue_album_tracks(
        album, {"id": artist_row.get("mbid") or "", "name": artist_row.get("name") or ""},
        profile_id=request_profile_id(), get_database=get_database,
        active_server=config_manager.get_active_media_server(), logger=logger,
        requested_via="lidarr_api")


def _ownership(title, artist_name, track_count):
    from core.library_scope import library_scope_for_profile, reset_library_scope, set_library_scope

    token = set_library_scope(library_scope_for_profile(request_profile_id()))
    try:
        found = get_database().check_album_exists_with_completeness(
            title, artist_name, expected_track_count=track_count or None)
    finally:
        reset_library_scope(token)
    album, _conf, owned, expected = found[0], found[1], found[2], found[3]
    if not album:
        return 0, track_count or 0
    return owned, track_count or expected


def service() -> LidarrCompat:
    global _service
    with _service_lock:
        if _service is None:
            _service = LidarrCompat(
                LidarrStore(lambda: get_database()._get_connection()), MusicBrainzSource(),
                queue_album=_queue_album, ownership=_ownership,
                metadata_profile=_metadata_profile, root_folder=_root_folder)
        return _service


def _unauthorized():
    return jsonify({"message": "Unauthorized"}), 401


def lidarr_route(f):
    """off -> 404 like the route isn't there. on -> admin api key only."""

    @wraps(f)
    def wrapped(*args, **kwargs):
        if not enabled():
            return jsonify({"message": "NotFound"}), 404
        from api.auth import SCOPE_ADMIN, key_scope, matched_api_key
        record = matched_api_key()
        if record is None or key_scope(record) != SCOPE_ADMIN:
            return _unauthorized()
        try:
            return f(*args, **kwargs)
        except LidarrError as e:
            return jsonify([{"propertyName": "", "errorMessage": str(e)}] if e.status == 400
                           else {"message": str(e)}), e.status
        except Exception as e:  # noqa: BLE001
            logger.exception("lidarr api %s %s failed", request.method, request.path)
            return jsonify({"message": str(e)}), 500
    return wrapped


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _int_list(name):
    out = []
    for raw in request.args.getlist(name):
        out.extend(int(p) for p in str(raw).split(",") if p.strip().isdigit())
    return out


# system and profiles

@bp.route("/system/status", methods=["GET"])
@lidarr_route
def system_status():
    return jsonify(resources.system_status())


@bp.route("/qualityprofile", methods=["GET"])
@lidarr_route
def quality_profiles():
    return jsonify(resources.QUALITY_PROFILES)


@bp.route("/qualityprofile/<int:pid>", methods=["GET"])
@lidarr_route
def quality_profile(pid):
    found = next((p for p in resources.QUALITY_PROFILES if p["id"] == pid), None)
    return (jsonify(found), 200) if found else (jsonify({"message": "NotFound"}), 404)


@bp.route("/metadataprofile", methods=["GET"])
@lidarr_route
def metadata_profiles():
    saved = config_manager.get("lidarr_compat.metadata_profiles", {}) or {}
    return jsonify(resources.metadata_profiles(saved if isinstance(saved, dict) else {}))


@bp.route("/metadataprofile/<int:pid>", methods=["GET"])
@lidarr_route
def metadata_profile(pid):
    if pid not in {p["id"] for p in resources.DEFAULT_METADATA_PROFILES}:
        return jsonify({"message": "NotFound"}), 404
    return jsonify(_metadata_profile(pid))


@bp.route("/metadataprofile/<int:pid>", methods=["PUT"])
@lidarr_route
def save_metadata_profile(pid):
    base = next((p for p in resources.DEFAULT_METADATA_PROFILES if p["id"] == pid), None)
    if base is None:
        return jsonify({"message": "NotFound"}), 404
    merged = resources.merge_metadata_profile(base, _body())
    saved = dict(config_manager.get("lidarr_compat.metadata_profiles", {}) or {})
    saved[str(pid)] = merged
    config_manager.set("lidarr_compat.metadata_profiles", saved)
    return jsonify(merged), 202


@bp.route("/rootfolder", methods=["GET"])
@lidarr_route
def root_folders():
    return jsonify([resources.root_folder(_root_folder())])


@bp.route("/tag", methods=["GET"])
@lidarr_route
def tags():
    return jsonify([])


# artists

@bp.route("/artist", methods=["GET"])
@lidarr_route
def list_artists():
    svc = service()
    mbid = request.args.get("mbId") or request.args.get("mbid")
    rows = [svc.store.artist_by_mbid(mbid)] if mbid else svc.store.artists()
    return jsonify([svc.artist_json(r) for r in rows if r])


@bp.route("/artist/lookup", methods=["GET"])
@lidarr_route
def lookup_artist():
    return jsonify(service().lookup_artists(request.args.get("term", "")))


@bp.route("/artist/<int:artist_id>", methods=["GET"])
@lidarr_route
def get_artist(artist_id):
    svc = service()
    return jsonify(svc.artist_json(svc.get_artist(artist_id)))


@bp.route("/artist", methods=["POST"])
@lidarr_route
def add_artist():
    svc = service()
    row, created = svc.add_artist(_body())
    return jsonify(svc.artist_json(row)), 201 if created else 200


@bp.route("/artist/<int:artist_id>", methods=["PUT"])
@lidarr_route
def update_artist(artist_id):
    svc = service()
    return jsonify(svc.artist_json(svc.update_artist(artist_id, _body()))), 202


@bp.route("/artist/editor", methods=["PUT"])
@lidarr_route
def edit_artists():
    svc = service()
    return jsonify([svc.artist_json(r) for r in svc.edit_artists(_body())]), 202


@bp.route("/artist/<int:artist_id>", methods=["DELETE"])
@lidarr_route
def delete_artist(artist_id):
    service().delete_artist(artist_id)
    return jsonify({}), 200


# albums

@bp.route("/album", methods=["GET"])
@lidarr_route
def list_albums():
    ids = _int_list("albumIds")
    artist_id = request.args.get("artistId", type=int)
    mbid = request.args.get("foreignAlbumId")
    return jsonify(service().list_albums(artist_id=artist_id, mbid=mbid, ids=ids or None))


@bp.route("/album/lookup", methods=["GET"])
@lidarr_route
def lookup_album():
    return jsonify(service().lookup_albums(request.args.get("term", "")))


@bp.route("/album/<int:album_id>", methods=["GET"])
@lidarr_route
def get_album(album_id):
    svc = service()
    row = svc.get_album(album_id)
    artist = svc.store.artist(row["artist_id"]) or {"id": row["artist_id"]}
    return jsonify(svc.album_json(svc.refresh_stats(row, artist), artist))


@bp.route("/album", methods=["POST"])
@lidarr_route
def add_album():
    svc = service()
    return jsonify(svc.album_json(svc.add_album(_body()))), 201


@bp.route("/album/<int:album_id>", methods=["PUT"])
@lidarr_route
def update_album(album_id):
    svc = service()
    return jsonify(svc.album_json(svc.update_album(album_id, _body()))), 202


@bp.route("/album/monitor", methods=["PUT"])
@lidarr_route
def monitor_albums():
    svc = service()
    body = _body()
    rows = svc.set_monitored(body.get("albumIds") or [], bool(body.get("monitored", True)))
    return jsonify([svc.album_json(r) for r in rows]), 202


@bp.route("/album/<int:album_id>", methods=["DELETE"])
@lidarr_route
def delete_album(album_id):
    service().delete_album(album_id)
    return jsonify({}), 200


@bp.route("/track", methods=["GET"])
@lidarr_route
def tracks():
    # per-track rows live in soulsync's library, not here
    return jsonify([])


@bp.route("/trackfile", methods=["GET"])
@lidarr_route
def track_files():
    return jsonify([])


# commands

@bp.route("/command", methods=["POST"])
@lidarr_route
def post_command():
    return jsonify(service().run_command(_body())), 201


@bp.route("/command", methods=["GET"])
@lidarr_route
def list_commands():
    return jsonify(service().commands.all())


@bp.route("/command/<int:cid>", methods=["GET"])
@lidarr_route
def get_command(cid):
    found = service().commands.get(cid)
    return (jsonify(found), 200) if found else (jsonify({"message": "NotFound"}), 404)


# queue, history, wanted: downloads show on soulsync's own downloads page

@bp.route("/queue", methods=["GET"])
@lidarr_route
def queue():
    return jsonify(resources.paged([], request.args.get("page", 1), request.args.get("pageSize", 10),
                                   "timeleft"))


@bp.route("/history", methods=["GET"])
@lidarr_route
def history():
    return jsonify(resources.paged([], request.args.get("page", 1), request.args.get("pageSize", 10)))


@bp.route("/wanted/missing", methods=["GET"])
@lidarr_route
def wanted_missing():
    svc = service()
    rows = [a for a in svc.list_albums() if a["monitored"] and a["statistics"]["percentOfTracks"] < 100]
    return jsonify(resources.paged(rows, request.args.get("page", 1), request.args.get("pageSize", 10),
                                   "releaseDate"))
