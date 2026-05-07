"""
Search endpoints — search external sources (Spotify, iTunes, Hydrabase).
"""

import logging

from flask import request, current_app
from .auth import require_api_key
from .helpers import api_success, api_error

logger = logging.getLogger(__name__)


def register_routes(bp):

    @bp.route("/search/tracks", methods=["POST"])
    @require_api_key
    def search_tracks():
        """Search for tracks across music sources.

        Body: {"query": "...", "source": "spotify"|"itunes"|"auto", "limit": 20}
        """
        body = request.get_json(silent=True) or {}
        query = body.get("query", "").strip()
        source = body.get("source", "auto")
        limit = min(50, max(1, int(body.get("limit", 20))))

        if not query:
            return api_error("BAD_REQUEST", "Missing 'query' in request body.", 400)

        try:
            ctx = current_app.soulsync
            tracks = []

            # Hydrabase first when active
            hydrabase = ctx.get("hydrabase_client")
            if source == "auto" and hydrabase:
                try:
                    from web_server import _is_hydrabase_active
                    if _is_hydrabase_active():
                        hydra_results = hydrabase.search_tracks(query, limit=limit)
                        if hydra_results:
                            tracks = [_serialize_track(t) for t in hydra_results]
                            return api_success({"tracks": tracks, "source": "hydrabase"})
                except Exception as e:
                    logger.debug("hydrabase search failed: %s", e)

            spotify = ctx.get("spotify_client")
            from core.metadata_service import get_primary_source, get_primary_client
            primary = get_primary_source()
            if source in ("spotify", "auto") and primary == 'spotify' and spotify and spotify.is_spotify_authenticated():
                results = spotify.search_tracks(query, limit=limit)
                if results:
                    tracks = [_serialize_track(t) for t in results]
                    return api_success({"tracks": tracks, "source": "spotify"})

            if source in ("itunes", "deezer", "auto"):
                fallback = get_primary_client()
                fallback_source = get_primary_source()
                results = fallback.search_tracks(query, limit=limit)
                if results:
                    tracks = [_serialize_track(t) for t in results]
                    return api_success({"tracks": tracks, "source": fallback_source})

            return api_success({"tracks": [], "source": source})
        except Exception as e:
            return api_error("SEARCH_ERROR", str(e), 500)

    @bp.route("/search/albums", methods=["POST"])
    @require_api_key
    def search_albums():
        """Search for albums.

        Body: {"query": "...", "limit": 20}
        """
        body = request.get_json(silent=True) or {}
        query = body.get("query", "").strip()
        limit = min(50, max(1, int(body.get("limit", 20))))

        if not query:
            return api_error("BAD_REQUEST", "Missing 'query' in request body.", 400)

        try:
            ctx = current_app.soulsync
            spotify = ctx.get("spotify_client")
            from core.metadata_service import get_primary_source, get_primary_client
            primary = get_primary_source()
            if primary == 'spotify' and spotify and spotify.is_spotify_authenticated():
                results = spotify.search_albums(query, limit=limit)
                if results:
                    return api_success({
                        "albums": [_serialize_album(a) for a in results],
                        "source": "spotify",
                    })

            fallback = get_primary_client()
            fallback_source = get_primary_source()
            results = fallback.search_albums(query, limit=limit)
            return api_success({
                "albums": [_serialize_album(a) for a in results] if results else [],
                "source": fallback_source,
            })
        except Exception as e:
            return api_error("SEARCH_ERROR", str(e), 500)

    @bp.route("/search/artists", methods=["POST"])
    @require_api_key
    def search_artists():
        """Search for artists.

        Body: {"query": "...", "limit": 20}
        """
        body = request.get_json(silent=True) or {}
        query = body.get("query", "").strip()
        limit = min(50, max(1, int(body.get("limit", 20))))

        if not query:
            return api_error("BAD_REQUEST", "Missing 'query' in request body.", 400)

        try:
            ctx = current_app.soulsync
            spotify = ctx.get("spotify_client")
            from core.metadata_service import get_primary_source, get_primary_client
            primary = get_primary_source()
            if primary == 'spotify' and spotify and spotify.is_spotify_authenticated():
                results = spotify.search_artists(query, limit=limit)
                if results:
                    return api_success({
                        "artists": [_serialize_artist(a) for a in results],
                        "source": "spotify",
                    })

            fallback = get_primary_client()
            fallback_source = get_primary_source()
            results = fallback.search_artists(query, limit=limit)
            return api_success({
                "artists": [_serialize_artist(a) for a in results] if results else [],
                "source": fallback_source,
            })
        except Exception as e:
            return api_error("SEARCH_ERROR", str(e), 500)


# ---- serialization (from core dataclasses) ----

def _serialize_track(t):
    return {
        "id": t.id,
        "name": t.name,
        "artists": t.artists,
        "album": t.album,
        "duration_ms": t.duration_ms,
        "popularity": t.popularity,
        "preview_url": t.preview_url,
        "image_url": t.image_url,
        "release_date": t.release_date,
    }


def _serialize_album(a):
    return {
        "id": a.id,
        "name": a.name,
        "artists": a.artists,
        "release_date": a.release_date,
        "total_tracks": a.total_tracks,
        "album_type": a.album_type,
        "image_url": a.image_url,
    }


def _serialize_artist(a):
    return {
        "id": a.id,
        "name": a.name,
        "popularity": a.popularity,
        "genres": a.genres,
        "followers": a.followers,
        "image_url": a.image_url,
    }
