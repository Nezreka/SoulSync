"""
Library endpoints — browse artists, albums, tracks, genres, and stats.
"""

from flask import request, current_app
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error, build_pagination, parse_pagination, parse_fields, parse_profile_id
from .serializers import serialize_artist, serialize_album, serialize_track


def register_routes(bp):

    @bp.route("/library/artists", methods=["GET"])
    @require_api_key
    def list_artists():
        """List library artists with optional search, letter filter, and pagination."""
        page, limit = parse_pagination(request)
        search = request.args.get("search", "")
        letter = request.args.get("letter", "all")
        watchlist = request.args.get("watchlist", "all")
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)

        try:
            # The artists query scopes by current_library_scope(), which doesn't
            # resolve for API-key requests. Set it explicitly from the profile.
            from core.library_scope import set_library_scope, library_scope_for_profile
            token = set_library_scope(library_scope_for_profile(profile_id))
            try:
                db = get_database()
                result = db.get_library_artists(
                    search_query=search,
                    letter=letter,
                    page=page,
                    limit=limit,
                    watchlist_filter=watchlist,
                    profile_id=profile_id,
                    skip_server_filter=True,
                )
            finally:
                from core.library_scope import _explicit_scope
                _explicit_scope.reset(token)
            artists = result.get("artists", [])
            pag = result.get("pagination", {})
            pagination = build_pagination(
                page, limit, pag.get("total_count", len(artists))
            )
            # Artists from get_library_artists are already dicts with external IDs
            serialized = [serialize_artist(a, fields) for a in artists]
            return api_success({"artists": serialized}, pagination=pagination)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/artists/<artist_id>", methods=["GET"])
    @require_api_key
    def get_artist(artist_id):
        """Get a single artist by ID with all metadata and album list."""
        fields = parse_fields(request)
        try:
            db = get_database()
            artist = db.api_get_artist(int(artist_id))
            if not artist:
                return api_error("NOT_FOUND", f"Artist {artist_id} not found.", 404)

            albums = db.api_get_albums_by_artist(int(artist_id))
            return api_success({
                "artist": serialize_artist(artist, fields),
                "albums": [serialize_album(a, fields) for a in albums],
            })
        except ValueError:
            return api_error("BAD_REQUEST", "artist_id must be an integer.", 400)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/artists/<artist_id>/albums", methods=["GET"])
    @require_api_key
    def get_artist_albums(artist_id):
        """List albums for an artist with full metadata."""
        fields = parse_fields(request)
        try:
            db = get_database()
            albums = db.api_get_albums_by_artist(int(artist_id))
            return api_success({"albums": [serialize_album(a, fields) for a in albums]})
        except ValueError:
            return api_error("BAD_REQUEST", "artist_id must be an integer.", 400)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/albums", methods=["GET"])
    @require_api_key
    def list_albums():
        """List/search albums with pagination and optional filters."""
        page, limit = parse_pagination(request)
        search = request.args.get("search", "")
        fields = parse_fields(request)

        artist_id = request.args.get("artist_id")
        year = request.args.get("year")

        try:
            artist_id_int = int(artist_id) if artist_id else None
        except ValueError:
            return api_error("BAD_REQUEST", "artist_id must be an integer.", 400)
        try:
            year_int = int(year) if year else None
        except ValueError:
            return api_error("BAD_REQUEST", "year must be an integer.", 400)

        try:
            db = get_database()
            result = db.api_list_albums(
                search=search,
                artist_id=artist_id_int,
                year=year_int,
                page=page,
                limit=limit,
            )
            albums = result.get("albums", [])
            total = result.get("total", 0)
            pagination = build_pagination(page, limit, total)
            return api_success(
                {"albums": [serialize_album(a, fields) for a in albums]},
                pagination=pagination,
            )
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/albums/<album_id>", methods=["GET"])
    @require_api_key
    def get_album(album_id):
        """Get a single album by ID with all metadata and embedded tracks."""
        fields = parse_fields(request)
        try:
            db = get_database()
            album = db.api_get_album(int(album_id))
            if not album:
                return api_error("NOT_FOUND", f"Album {album_id} not found.", 404)

            tracks = db.api_get_tracks_by_album(int(album_id))
            return api_success({
                "album": serialize_album(album, fields),
                "tracks": [serialize_track(t, fields) for t in tracks],
            })
        except ValueError:
            return api_error("BAD_REQUEST", "album_id must be an integer.", 400)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/albums/<album_id>/tracks", methods=["GET"])
    @require_api_key
    def get_album_tracks(album_id):
        """List tracks in an album with full metadata."""
        fields = parse_fields(request)
        try:
            db = get_database()
            tracks = db.api_get_tracks_by_album(int(album_id))
            return api_success({"tracks": [serialize_track(t, fields) for t in tracks]})
        except ValueError:
            return api_error("BAD_REQUEST", "album_id must be an integer.", 400)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/tracks/<track_id>", methods=["GET"])
    @require_api_key
    def get_track(track_id):
        """Get a single track by ID with all metadata."""
        fields = parse_fields(request)
        try:
            db = get_database()
            track = db.api_get_track(int(track_id))
            if not track:
                return api_error("NOT_FOUND", f"Track {track_id} not found.", 404)

            return api_success({"track": serialize_track(track, fields)})
        except ValueError:
            return api_error("BAD_REQUEST", "track_id must be an integer.", 400)
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/tracks", methods=["GET"])
    @require_api_key
    def library_search_tracks():
        """Search tracks by title and/or artist."""
        title = request.args.get("title", "")
        artist = request.args.get("artist", "")
        try:
            limit = min(200, max(1, int(request.args.get("limit", 50))))
        except (ValueError, TypeError):
            limit = 50
        fields = parse_fields(request)

        if not title and not artist:
            return api_error("BAD_REQUEST", "Provide at least 'title' or 'artist' query param.", 400)

        try:
            db = get_database()
            tracks = db.api_search_tracks(title=title, artist=artist, limit=limit)
            return api_success({"tracks": [serialize_track(t, fields) for t in tracks]})
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/genres", methods=["GET"])
    @require_api_key
    def list_genres():
        """List all genres with occurrence counts.

        Query params:
            source: 'artists' or 'albums' (default: 'artists')
        """
        source = request.args.get("source", "artists")
        if source not in ("artists", "albums"):
            return api_error("BAD_REQUEST", "source must be 'artists' or 'albums'.", 400)

        try:
            db = get_database()
            genres = db.api_get_genres(table=source)
            return api_success({"genres": genres, "source": source})
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/recently-added", methods=["GET"])
    @require_api_key
    def recently_added():
        """Get recently added content ordered by created_at.

        Query params:
            type: 'albums', 'artists', or 'tracks' (default: 'albums')
            limit: max items to return (default: 50, max: 200)
        """
        entity_type = request.args.get("type", "albums")
        if entity_type not in ("albums", "artists", "tracks"):
            return api_error("BAD_REQUEST", "type must be 'albums', 'artists', or 'tracks'.", 400)

        try:
            limit = min(200, max(1, int(request.args.get("limit", 50))))
        except (ValueError, TypeError):
            limit = 50
        fields = parse_fields(request)

        try:
            db = get_database()
            items = db.api_get_recently_added(entity_type=entity_type, limit=limit)

            serializer = {
                "artists": serialize_artist,
                "albums": serialize_album,
                "tracks": serialize_track,
            }[entity_type]

            return api_success({
                "items": [serializer(item, fields) for item in items],
                "type": entity_type,
            })
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/recently-played", methods=["GET"])
    @require_api_key
    def recently_played():
        """Get recently played tracks from listening history.

        Query params:
            limit: max items to return (default: 20, max: 100)
        """
        try:
            limit = min(100, max(1, int(request.args.get("limit", 20))))
        except (ValueError, TypeError):
            limit = 20
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)

        try:
            db = get_database()
            # Get recently played with library track IDs for playback.
            # A play is linked to the catalogue by lib2_track_id; the file is
            # the track's primary one.
            from core.stats.queries import listening_owner, owner_clause
            scope = owner_clause(listening_owner(db, profile_id), 'lh')
            conn = db._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    f"""
                    SELECT lh.title, lh.artist, lh.album, lh.played_at,
                           t.id as track_id, f.path, al.primary_artist_id,
                           t.album_id, al.image_url
                    FROM listening_history lh
                    LEFT JOIN lib2_tracks t ON t.id = lh.lib2_track_id
                    LEFT JOIN lib2_albums al ON al.id = t.album_id
                    LEFT JOIN lib2_track_files f
                           ON f.track_id = t.id AND f.is_primary = 1
                          AND COALESCE(f.file_state, 'active') <> 'deleted'
                    WHERE {scope}
                    ORDER BY lh.played_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
            finally:
                conn.close()

            tracks = []
            for row in rows:
                track = {
                    "title": row[0],
                    "artist_name": row[1],
                    "album_title": row[2],
                    "played_at": row[3],
                    "id": row[4],
                    "file_path": row[5],
                    "artist_id": row[6],
                    "album_id": row[7],
                    "thumb_url": row[8],
                }
                tracks.append(serialize_track(track, fields))

            return api_success({"tracks": tracks})
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/playlists", methods=["GET"])
    @require_api_key
    def list_library_playlists():
        """List curated playlists with track counts."""
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)
        try:
            db = get_database()
            playlists = db.api_list_curated_playlists(profile_id=profile_id)
            # fields filtering: only include requested fields if specified
            if fields is not None:
                playlists = [
                    {k: v for k, v in p.items() if k in fields}
                    for p in playlists
                ]
            return api_success({"playlists": playlists})
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/playlists/<playlist_id>/tracks", methods=["GET"])
    @require_api_key
    def get_playlist_tracks(playlist_id):
        """List tracks in a curated playlist, in order."""
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)
        try:
            pid = int(playlist_id)
        except (ValueError, TypeError):
            return api_error("BAD_REQUEST", "playlist_id must be an integer.", 400)
        try:
            db = get_database()
            tracks = db.api_get_curated_playlist_tracks(pid, profile_id=profile_id)
            return api_success({"tracks": [serialize_track(t, fields) for t in tracks]})
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/mirrored-playlists", methods=["GET"])
    @require_api_key
    def list_mirrored_playlists():
        """List mirrored playlists for the profile."""
        profile_id = parse_profile_id(request)
        try:
            db = get_database()
            playlists = db.get_mirrored_playlists(profile_id=profile_id)
            out = []
            for pl in playlists:
                out.append({
                    "id": pl.get("id"),
                    "name": pl.get("name") or pl.get("custom_name") or "Untitled",
                    "track_count": pl.get("track_count", 0),
                    "cover_url": pl.get("cover_url"),
                })
            return api_success({"playlists": out})
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/mirrored-playlists/<playlist_id>/tracks", methods=["GET"])
    @require_api_key
    def get_mirrored_playlist_tracks(playlist_id):
        """List tracks in a mirrored playlist, in order."""
        fields = parse_fields(request)
        profile_id = parse_profile_id(request)
        try:
            pid = int(playlist_id)
        except (ValueError, TypeError):
            return api_error("BAD_REQUEST", "playlist_id must be an integer.", 400)
        try:
            db = get_database()
            mtracks = db.get_mirrored_playlist_tracks(pid, profile_id=profile_id)
            out = []
            for mt in mtracks:
                t = None
                # Try external ID first (source_track_id is Spotify/etc).
                sid = mt.get("source_track_id")
                if sid:
                    t = db.api_get_track_by_external_id(str(sid))
                # Fallback to title/artist.
                if not t and mt.get("track_name"):
                    t = db.api_find_track_by_title_artist(
                        mt.get("track_name"), mt.get("artist_name") or "")
                if t:
                    out.append(serialize_track(t, fields))
            return api_success({"tracks": out})
        except Exception as e:
            return api_error("LIBRARY_ERROR", str(e), 500)

    @bp.route("/library/lookup", methods=["GET"])
    @require_api_key
    def lookup_by_external_id():
        """Look up a library entity by external provider ID.

        Query params:
            type: 'artist', 'album', or 'track' (required)
            provider: 'spotify', 'musicbrainz', 'itunes', 'deezer', 'audiodb', 'tidal', 'qobuz', 'genius' (required)
            id: the external ID value (required)
        """
        entity_type = request.args.get("type")
        provider = request.args.get("provider")
        external_id = request.args.get("id")
        fields = parse_fields(request)

        if not entity_type or not provider or not external_id:
            return api_error("BAD_REQUEST", "Required params: type, provider, id.", 400)

        table_map = {"artist": "artists", "album": "albums", "track": "tracks"}
        table = table_map.get(entity_type)
        if not table:
            return api_error("BAD_REQUEST", "type must be 'artist', 'album', or 'track'.", 400)

        # genius only exists on artists and tracks, not albums
        valid_providers = ("spotify", "musicbrainz", "itunes", "deezer", "audiodb", "tidal", "qobuz", "genius")
        if provider not in valid_providers:
            return api_error("BAD_REQUEST", f"provider must be one of: {', '.join(valid_providers)}.", 400)
        if provider == "genius" and entity_type == "album":
            return api_error("BAD_REQUEST", "Genius IDs are not available for albums. Use artist or track.", 400)

        try:
            db = get_database()
            result = db.api_lookup_by_external_id(table, provider, external_id)
            if not result:
                return api_error("NOT_FOUND", f"No {entity_type} found for {provider} ID: {external_id}", 404)

            serializer = {
                "artists": serialize_artist,
                "albums": serialize_album,
                "tracks": serialize_track,
            }[table]

            return api_success({entity_type: serializer(result, fields)})
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
