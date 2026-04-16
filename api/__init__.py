"""
SoulSync Public REST API  (v1)

Blueprint factory + rate-limiter initialisation.
"""

from flask import Blueprint
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from utils.logging_config import get_logger
from .helpers import api_error

logger = get_logger("api_v1")

# ---------------------------------------------------------------------------
# Rate limiter (initialised with the app in web_server.py via limiter.init_app)
# ---------------------------------------------------------------------------
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],           # No global default — limits are applied per-blueprint
    storage_uri="memory://",
)


def create_api_blueprint():
    """Build and return the /api/v1 Blueprint with all sub-modules registered."""

    bp = Blueprint("api_v1", __name__)

    # ---- import & register sub-module routes ----
    from .library import register_routes as reg_library
    from .system import register_routes as reg_system
    from .search import register_routes as reg_search
    from .wishlist import register_routes as reg_wishlist
    from .watchlist import register_routes as reg_watchlist
    from .downloads import register_routes as reg_downloads
    from .playlists import register_routes as reg_playlists
    from .settings import register_routes as reg_settings
    from .discover import register_routes as reg_discover
    from .profiles import register_routes as reg_profiles
    from .retag import register_routes as reg_retag
    from .listenbrainz import register_routes as reg_listenbrainz
    from .cache import register_routes as reg_cache
    from .request import register_routes as reg_request

    # ---- rate-limit only /api/v1 routes (not the whole app) ----
    limiter.limit("60 per minute")(bp)

    reg_library(bp)
    reg_system(bp)
    reg_search(bp)
    reg_wishlist(bp)
    reg_watchlist(bp)
    reg_downloads(bp)
    reg_playlists(bp)
    reg_settings(bp)
    reg_discover(bp)
    reg_profiles(bp)
    reg_retag(bp)
    reg_listenbrainz(bp)
    reg_cache(bp)
    reg_request(bp)

    # ---- error handlers (scoped to this Blueprint) ----
    @bp.errorhandler(400)
    def _bad_request(e):
        return api_error("BAD_REQUEST", str(e), 400)

    @bp.errorhandler(404)
    def _not_found(e):
        return api_error("NOT_FOUND", "Resource not found.", 404)

    @bp.errorhandler(429)
    def _rate_limited(e):
        return api_error("RATE_LIMITED", "Too many requests. Please slow down.", 429)

    @bp.errorhandler(500)
    def _internal(e):
        return api_error("INTERNAL_ERROR", "An internal server error occurred.", 500)

    @bp.errorhandler(Exception)
    def _unhandled(e):
        logger.error(f"Unhandled API error: {e}", exc_info=True)
        return api_error("INTERNAL_ERROR", "An unexpected error occurred.", 500)

    return bp
