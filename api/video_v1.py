"""
Video side of the public v1 API.

the music side had a v1 surface for a year; the video side had none, so an
app like Wavio could add a track to the music wishlist but had no way to
ask for a movie. these are thin: each one runs the app's own /api/video
handler in this request context (same query args, same body) and wraps the
answer in the v1 envelope. the handlers are looked up by their registered
endpoint name, so a change to the internal route is caught by the tests
here rather than by a client.
"""

from flask import current_app, request

from .auth import require_api_key
from .helpers import api_error, api_success

VIDEO_BLUEPRINT = "video_api"


def _call(view_name):
    """Run the internal video view under the current request, returning
    (payload dict, status)."""
    view = current_app.view_functions.get(f"{VIDEO_BLUEPRINT}.{view_name}")
    if view is None:
        return None, 503
    result = view(**request.view_args) if request.view_args else view()
    response, status = result if isinstance(result, tuple) else (result, 200)
    payload = response.get_json(silent=True)
    return (payload if isinstance(payload, dict) else {"data": payload}), status


def _relay(view_name, *, error_code="VIDEO_ERROR", conflict_message=None):
    """Envelope the internal answer. Internal handlers say success:false with
    an error string; those become the v1 error shape with the same status."""
    payload, status = _call(view_name)
    if payload is None:
        return api_error("NOT_AVAILABLE", "The video side is not enabled on this server.", 503)
    if status == 409:
        return api_error("CONFLICT", conflict_message or payload.get("error") or "Already running.", 409)
    if status >= 400 or payload.get("success") is False:
        return api_error(error_code, payload.get("error") or "Request failed.", status if status >= 400 else 500)
    payload.pop("success", None)
    return api_success(payload)


def register_routes(bp):

    # ---- library ----

    @bp.route("/video/library", methods=["GET"])
    @require_api_key
    def video_library():
        """What is in the video library. ?kind=movies|shows&search=&letter=&sort=&status=&genre=&page=&limit="""
        return _relay("video_library")

    @bp.route("/video/library/genres", methods=["GET"])
    @require_api_key
    def video_library_genres():
        return _relay("video_library_genres")

    # ---- search ----

    @bp.route("/video/search", methods=["GET"])
    @require_api_key
    def video_search():
        """TMDB multi-search. ?q=<text>"""
        if not (request.args.get("q") or "").strip():
            return api_error("BAD_REQUEST", "q is required.", 400)
        return _relay("video_search")

    @bp.route("/video/trending", methods=["GET"])
    @require_api_key
    def video_trending():
        return _relay("video_trending")

    # ---- wishlist ----

    @bp.route("/video/wishlist", methods=["GET"])
    @require_api_key
    def video_wishlist_list():
        """?kind=movie|show&search=&sort=&page=&limit= for a page of items; no kind for counts only."""
        return _relay("video_wishlist_list")

    @bp.route("/video/wishlist/counts", methods=["GET"])
    @require_api_key
    def video_wishlist_counts():
        return _relay("video_wishlist_counts")

    @bp.route("/video/wishlist", methods=["POST"])
    @require_api_key
    def video_wishlist_add():
        """Body: {"movie": {tmdb_id, title, year?, poster_url?}} or
        {"show": {tmdb_id, title, poster_url?}, "episodes": [{season_number, episode_number, title?, air_date?}]}"""
        return _relay("video_wishlist_add", error_code="WISHLIST_ERROR")

    @bp.route("/video/wishlist", methods=["DELETE"])
    @require_api_key
    def video_wishlist_remove():
        """Body: {scope: movie|show|season|episode, tmdb_id, season_number?, episode_number?}"""
        return _relay("video_wishlist_remove", error_code="WISHLIST_ERROR")

    # ---- watchlist (followed shows / people / studios) ----

    @bp.route("/video/watchlist", methods=["GET"])
    @require_api_key
    def video_watchlist_list():
        return _relay("video_watchlist_list")

    @bp.route("/video/watchlist", methods=["POST"])
    @require_api_key
    def video_watchlist_add():
        """Body: {kind: show|person|studio, tmdb_id, title, poster_url?}"""
        return _relay("video_watchlist_add", error_code="WATCHLIST_ERROR")

    @bp.route("/video/watchlist", methods=["DELETE"])
    @require_api_key
    def video_watchlist_remove():
        """Body: {kind, tmdb_id}"""
        return _relay("video_watchlist_remove", error_code="WATCHLIST_ERROR")

    # ---- scan ----

    @bp.route("/video/scan", methods=["POST"])
    @require_api_key
    def video_scan_request():
        """Ask for a library scan. Body: {mode?: incremental|deep|full}"""
        return _relay("video_scan_request", error_code="SCAN_ERROR",
                      conflict_message="A video scan is already running.")

    @bp.route("/video/scan/status", methods=["GET"])
    @require_api_key
    def video_scan_status():
        return _relay("video_scan_status")

    # ---- downloads ----

    @bp.route("/video/downloads", methods=["GET"])
    @require_api_key
    def video_downloads_active():
        return _relay("video_downloads_active")

    @bp.route("/video/downloads/status", methods=["GET"])
    @require_api_key
    def video_downloads_status():
        return _relay("video_downloads_status")

    @bp.route("/video/downloads/history", methods=["GET"])
    @require_api_key
    def video_downloads_history():
        return _relay("video_downloads_history")

    # ---- calendar ----

    @bp.route("/video/calendar", methods=["GET"])
    @require_api_key
    def video_calendar():
        """Upcoming and recent episodes / releases. ?start=&end= as ISO dates."""
        return _relay("video_calendar")

    # ---- requests (a viewer asks, an admin approves) ----

    @bp.route("/video/requests", methods=["GET"])
    @require_api_key
    def video_request_list():
        return _relay("video_request_list")

    @bp.route("/video/requests", methods=["POST"])
    @require_api_key
    def video_request_create():
        """Body: {kind: movie|show, tmdb_id, title, year?, poster_url?, note?, monitor?}"""
        return _relay("video_request_create", error_code="REQUEST_ERROR")

    @bp.route("/video/requests/<int:request_id>/approve", methods=["POST"])
    @require_api_key
    def video_request_approve(request_id):
        return _relay("video_request_approve", error_code="REQUEST_ERROR")

    @bp.route("/video/requests/<int:request_id>/deny", methods=["POST"])
    @require_api_key
    def video_request_deny(request_id):
        return _relay("video_request_deny", error_code="REQUEST_ERROR")
