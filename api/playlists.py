"""
Playlist endpoints — list and inspect playlists from Spotify/Tidal.
"""

from flask import request, current_app
from .auth import require_api_key
from .helpers import api_success, api_error


def register_routes(bp):

    @bp.route("/playlists", methods=["GET"])
    @require_api_key
    def list_playlists():
        """List user playlists from Spotify or Tidal.

        Query: ?source=spotify|tidal  (default: spotify)
        """
        source = request.args.get("source", "spotify")
        ctx = current_app.soulsync

        try:
            if source == "spotify":
                spotify = ctx.get("spotify_client")
                if not spotify or not spotify.is_authenticated():
                    return api_error("NOT_AUTHENTICATED", "Spotify not authenticated.", 401)

                playlists = spotify.get_user_playlists_metadata_only()
                return api_success({
                    "playlists": [
                        {
                            "id": p.id,
                            "name": p.name,
                            "owner": p.owner,
                            "track_count": p.total_tracks,
                            "image_url": getattr(p, "image_url", None),
                        }
                        for p in playlists
                    ],
                    "source": "spotify",
                })

            elif source == "tidal":
                tidal = ctx.get("tidal_client")
                if not tidal:
                    return api_error("NOT_AVAILABLE", "Tidal client not configured.", 503)

                playlists = tidal.get_user_playlists_metadata_only()
                return api_success({
                    "playlists": [
                        {
                            "id": p.get("id") or p.get("uuid"),
                            "name": p.get("title") or p.get("name"),
                            "track_count": p.get("numberOfTracks", 0),
                            "image_url": p.get("image"),
                        }
                        for p in (playlists or [])
                    ],
                    "source": "tidal",
                })

            return api_error("BAD_REQUEST", "source must be 'spotify' or 'tidal'.", 400)
        except Exception as e:
            return api_error("PLAYLIST_ERROR", str(e), 500)

    @bp.route("/playlists/<playlist_id>", methods=["GET"])
    @require_api_key
    def get_playlist(playlist_id):
        """Get playlist details with tracks.

        Query: ?source=spotify  (default: spotify)
        """
        source = request.args.get("source", "spotify")
        ctx = current_app.soulsync

        try:
            if source == "spotify":
                spotify = ctx.get("spotify_client")
                if not spotify or not spotify.is_authenticated():
                    return api_error("NOT_AUTHENTICATED", "Spotify not authenticated.", 401)

                playlist = spotify.get_playlist_by_id(playlist_id)
                if not playlist:
                    return api_error("NOT_FOUND", "Playlist not found.", 404)

                tracks = []
                for item in playlist.get("tracks", {}).get("items", []):
                    t = item.get("track")
                    if not t:
                        continue
                    tracks.append({
                        "id": t.get("id"),
                        "name": t.get("name"),
                        "artists": [a.get("name") for a in t.get("artists", [])],
                        "album": t.get("album", {}).get("name"),
                        "duration_ms": t.get("duration_ms"),
                        "image_url": (t.get("album", {}).get("images", [{}])[0].get("url")
                                      if t.get("album", {}).get("images") else None),
                    })

                return api_success({
                    "playlist": {
                        "id": playlist.get("id"),
                        "name": playlist.get("name"),
                        "owner": playlist.get("owner", {}).get("display_name"),
                        "total_tracks": playlist.get("tracks", {}).get("total", len(tracks)),
                        "tracks": tracks,
                    },
                    "source": "spotify",
                })

            return api_error("BAD_REQUEST", "source must be 'spotify'.", 400)
        except Exception as e:
            return api_error("PLAYLIST_ERROR", str(e), 500)

    @bp.route("/playlists/<playlist_id>/sync", methods=["POST"])
    @require_api_key
    def sync_playlist(playlist_id):
        """Trigger playlist sync/download.

        This delegates to the internal sync endpoint by forwarding the request.
        Body: {"playlist_name": "...", "tracks": [...]}
        """
        body = request.get_json(silent=True) or {}
        playlist_name = body.get("playlist_name")
        tracks = body.get("tracks")

        if not playlist_name or not tracks:
            return api_error("BAD_REQUEST", "Missing 'playlist_name' or 'tracks' in body.", 400)

        try:
            from web_server import sync_states
            if playlist_id in sync_states and sync_states[playlist_id].get("phase") not in ("complete", "error", None):
                return api_error("CONFLICT", "Sync already in progress for this playlist.", 409)
        except ImportError:
            pass

        try:
            # Forward to the internal sync endpoint
            import requests as http_requests
            internal_url = "http://127.0.0.1:8008/api/sync/start"
            resp = http_requests.post(internal_url, json={
                "playlist_id": playlist_id,
                "playlist_name": playlist_name,
                "tracks": tracks,
            }, timeout=10)
            data = resp.json()
            if data.get("success"):
                return api_success({"message": "Playlist sync started.", "playlist_id": playlist_id})
            return api_error("SYNC_FAILED", data.get("error", "Sync failed to start."), 500)
        except Exception as e:
            return api_error("PLAYLIST_ERROR", str(e), 500)
