"""
Exact SoundCloud uploads — the companion extension's "add THIS upload".

GET  /soundcloud/exact            capability probe + status of one upload
GET  /soundcloud/exact/playlists  the profile's SoulSync playlists to attach to
POST /soundcloud/exact            wishlist one exact upload

Every answer is the v1 envelope and none of them is a 404 or 405: an older
server answers those for an unknown route, so the extension reads them as
"not supported" (core/wishlist/soundcloud_exact.py does the work).
"""

from flask import request
from core.api_validation import parse_strict_int
from .auth import require_api_key
from .helpers import api_success, api_error, parse_profile_id
from .serializers import serialize_wishlist_track


def _positive_int(value):
    parsed = parse_strict_int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _string_list(value):
    """None, or a list of strings (a lone string is one item)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def register_routes(bp):

    @bp.route("/soundcloud/exact", methods=["GET"])
    @require_api_key
    def soundcloud_exact_status():
        """Probe (no query) or status of one upload by ``sc_id`` or ``url``."""
        profile_id = parse_profile_id(request)
        sc_id = (request.args.get("sc_id") or "").strip()
        url = (request.args.get("url") or "").strip()
        if sc_id and not sc_id.isdigit():
            return api_error("BAD_REQUEST", "sc_id must be digits.", 400)
        try:
            from core.download_orchestrator import get_download_orchestrator
            from core.wishlist import soundcloud_exact as exact
            from database.music_database import get_database

            enabled, reason = exact.source_state(get_download_orchestrator())
            data = {"supported": True, "enabled": enabled, "reason": reason}
            status = {"state": "none", "matched": False, "track_id": None,
                      "failure_reason": None, "retry_count": 0, "last_attempted": None}
            if sc_id or url:
                status = exact.status_for(get_database(), sc_id=sc_id or None,
                                          url=url or None, profile_id=profile_id)
            data.update(status)
            return api_success(data)
        except Exception as e:
            return api_error("WISHLIST_ERROR", str(e), 500)

    @bp.route("/soundcloud/exact/playlists", methods=["GET"])
    @require_api_key
    def soundcloud_exact_playlists():
        """The profile's own SoulSync playlists, the only ones an upload can join."""
        profile_id = parse_profile_id(request)
        try:
            from core.playlists import user_playlists
            from database.music_database import get_database

            rows = user_playlists.list_playlists(get_database(), profile_id)
            return api_success({"playlists": [
                {"id": p["id"], "name": p["name"], "track_count": p["track_count"],
                 "liked": p["liked"]} for p in rows
            ]})
        except Exception as e:
            return api_error("WISHLIST_ERROR", str(e), 500)

    @bp.route("/soundcloud/exact", methods=["POST"])
    @require_api_key
    def soundcloud_exact_add():
        """Wishlist one exact SoundCloud upload.

        Body: {"url": "...", "title": "...", "artists": ["..."], "remixers": ["..."],
               "playlist_id": 12, "quality_profile_id": 3}

        ``201`` a new row, ``200`` an existing row refreshed (or the upload is
        already in the library, ``state: "acquired"``). A refusal writes no
        row, with one exception: like every user-initiated wishlist add
        (``add_to_wishlist_detailed``), the add first clears a stale
        wishlist-ignore entry for this upload, so a quota or permission
        refusal that lands after that leaves the ignore entry cleared.

        Blocklist: track and album entries are keyed to catalogue ids, so for
        an uncatalogued upload only artist blocks can fire, checked against
        the tagged artists and the uploader's name.
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return api_error("BAD_REQUEST", "Body must be a JSON object.", 400)
        profile_id = parse_profile_id(request)

        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            return api_error("BAD_REQUEST", "Missing 'url' in body.", 400)
        title = body.get("title")
        if title is not None and not isinstance(title, str):
            return api_error("BAD_REQUEST", "'title' must be a string.", 400)
        artists = _string_list(body.get("artists"))
        if artists is None or len(artists) > 10:
            return api_error("BAD_REQUEST", "'artists' must be 1-10 strings.", 400)
        remixers = _string_list(body.get("remixers"))
        if remixers is None or len(remixers) > 10:
            return api_error("BAD_REQUEST", "'remixers' must be a list of strings.", 400)

        playlist_id = None
        if body.get("playlist_id") is not None:
            playlist_id = _positive_int(body.get("playlist_id"))
            if playlist_id is None:
                return api_error("BAD_REQUEST", "playlist_id must be a positive integer.", 400)

        try:
            from core.download_orchestrator import get_download_orchestrator
            from core.wishlist import soundcloud_exact as exact
            from database.music_database import get_database

            db = get_database()
            quality_profile_id = None
            if body.get("quality_profile_id") is not None:
                quality_profile_id = _positive_int(body.get("quality_profile_id"))
                if quality_profile_id is None:
                    return api_error("BAD_REQUEST", "quality_profile_id must be a positive integer.", 400)
                if not db.quality_profile_exists(quality_profile_id):
                    return api_error("BAD_REQUEST", "Unknown quality_profile_id.", 400)

            try:
                status, data = exact.add_exact(
                    db, get_download_orchestrator(),
                    url=url, title=title, artists=artists, remixers=remixers,
                    playlist_id=playlist_id, quality_profile_id=quality_profile_id,
                    profile_id=profile_id,
                )
            except exact.ExactError as refusal:
                return api_error(refusal.code, refusal.message, refusal.status)
            if data.get("track"):
                data = {**data, "track": serialize_wishlist_track(data["track"])}
            return api_success(data, status=status)
        except Exception as e:
            return api_error("WISHLIST_ERROR", str(e), 500)
