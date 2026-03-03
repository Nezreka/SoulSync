"""
Library endpoints — browse artists, albums, tracks, and stats.
"""

from flask import request, current_app
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error, build_pagination, parse_pagination


def register_routes(bp):

    @bp.route("/library/artists", methods=["GET"])
    @require_api_key
    def list_artists():
        """List library artists with optional search, letter filter, and pagination."""
        page, limit = parse_pagination(request)
        search = request.args.get("search", "")
        letter = request.args.get("letter", "all")
        watchlist = request.args.get("watchlist", "all")

        try:
            db = get_database()
            result = db.get_library_artists(
                search_query=search,
                letter=letter,
                page=page,
                limit=limit,
                watchlist_filter=watchlist,
            )
            artists = result.get("artists", [])
            pag = result.get("pagination", {})
            pagination = build_pagination(
                page, limit, pag.get("total_count", len(artists))
            )
            return api_success({"artists": artists}, pagination=pagination)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/artists/<artist_id>", methods=["GET"])
    @require_api_key
    def get_artist(artist_id):
        """Get a single artist by ID with album list."""
        try:
            db = get_database()
            artist = db.get_artist(int(artist_id))
            if not artist:
                return api_error("NOT_FOUND", f"Artist {artist_id} not found.", 404)

            albums = db.get_albums_by_artist(int(artist_id))
            return api_success({
                "artist": _serialize_artist(artist),
                "albums": [_serialize_album(a) for a in albums],
            })
        except ValueError:
            return api_error("BAD_REQUEST", "artist_id must be an integer.", 400)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/artists/<artist_id>/albums", methods=["GET"])
    @require_api_key
    def get_artist_albums(artist_id):
        """List albums for an artist."""
        try:
            db = get_database()
            albums = db.get_albums_by_artist(int(artist_id))
            return api_success({"albums": [_serialize_album(a) for a in albums]})
        except ValueError:
            return api_error("BAD_REQUEST", "artist_id must be an integer.", 400)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/albums/<album_id>/tracks", methods=["GET"])
    @require_api_key
    def get_album_tracks(album_id):
        """List tracks in an album."""
        try:
            db = get_database()
            tracks = db.get_tracks_by_album(int(album_id))
            return api_success({"tracks": [_serialize_track(t) for t in tracks]})
        except ValueError:
            return api_error("BAD_REQUEST", "album_id must be an integer.", 400)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/tracks", methods=["GET"])
    @require_api_key
    def library_search_tracks():
        """Search tracks by title and/or artist."""
        title = request.args.get("title", "")
        artist = request.args.get("artist", "")
        limit = min(200, max(1, int(request.args.get("limit", 50))))

        if not title and not artist:
            return api_error("BAD_REQUEST", "Provide at least 'title' or 'artist' query param.", 400)

        try:
            db = get_database()
            tracks = db.search_tracks(title=title, artist=artist, limit=limit)
            return api_success({"tracks": [_serialize_track(t) for t in tracks]})
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/stats", methods=["GET"])
    @require_api_key
    def library_stats():
        """Get library statistics (artist/album/track counts, DB info)."""
        try:
            db = get_database()
            info = db.get_database_info_for_server()
            stats = db.get_statistics_for_server()
            return api_success({
                "artists": stats.get("artists", 0),
                "albums": stats.get("albums", 0),
                "tracks": stats.get("tracks", 0),
                "database_size_mb": info.get("database_size_mb"),
                "last_update": info.get("last_update"),
            })
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)


# ---- serialization helpers ----

def _serialize_artist(a):
    return {
        "id": a.id,
        "name": a.name,
        "thumb_url": a.thumb_url,
        "genres": a.genres or [],
        "summary": a.summary,
    }


def _serialize_album(a):
    return {
        "id": a.id,
        "artist_id": a.artist_id,
        "title": a.title,
        "year": a.year,
        "thumb_url": a.thumb_url,
        "genres": a.genres or [],
        "track_count": a.track_count,
        "duration": a.duration,
    }


def _serialize_track(t):
    return {
        "id": t.id,
        "album_id": t.album_id,
        "artist_id": t.artist_id,
        "title": t.title,
        "track_number": t.track_number,
        "duration": t.duration,
        "file_path": t.file_path,
        "bitrate": t.bitrate,
    }
