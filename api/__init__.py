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


def _rate_limit_exempt():
    """True when the request carries a valid admin-scope API key.

    The 60/minute cap guards the unauthenticated surface: key guessing and
    scraping without a credential. An admin key is a trusted local
    credential — SoulSync mints one for MetaSync itself — and paginated
    walks are a designed use of it: MetaSync's weekly report pages
    /library/albums at limit=200, so a library past ~12,000 albums needs
    more pages than the cap allows in a minute and the walk died with a
    429 (#1923). No key, an invalid key, a read-scope key and
    session-only requests all stay capped.

    Fail closed: any error in the auth check means not exempt, so a
    broken auth path can never silently disable the limiter.
    """
    try:
        from .auth import SCOPE_ADMIN, key_scope, matched_api_key

        record = matched_api_key()
        return record is not None and key_scope(record) == SCOPE_ADMIN
    except Exception:
        return False


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
    from .metasync import register_routes as reg_metasync
    from .request import register_routes as reg_request
    from .video_v1 import register_routes as reg_video
    from .sample import register_routes as reg_sample
    from .request import start_cleanup_thread as _start_request_cleanup

    # ---- rate-limit only /api/v1 routes (not the whole app); requests
    # carrying a valid admin API key are exempt (see _rate_limit_exempt) ----
    limiter.limit("60 per minute", exempt_when=_rate_limit_exempt)(bp)

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
    reg_metasync(bp)
    reg_request(bp)
    reg_video(bp)
    reg_sample(bp)

    # Start the periodic cleanup timer for in-memory request tracking so
    # idle periods don't leave stale entries in memory. Idempotent across
    # calls; safe with multi-blueprint registration.
    _start_request_cleanup()

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
