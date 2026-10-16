"""Music routes: artists whose music downloads are filed in the audiobook library.

  - GET    /api/audiobook-routes:         the routed artists, and whether an audiobook
                                          folder is configured.
  - POST   /api/audiobook-routes:         route an artist ({"name": ...}); refused until an
                                          audiobook folder is configured.
  - DELETE /api/audiobook-routes/<name>:  stop routing an artist.
  - POST   /api/audiobook-routes/check:   which of {"names": [...]} are routed ({"routed": [...]}).
  - POST   /api/audiobook-routes/<name>/refresh: (re)write metadata.opf for the artist's
                                              albums already in the audiobook folder.
  - POST   /api/audiobook-routes/<name>/move: preview (default) or perform ({"apply": true})
                                              moving the artist's existing music albums.

Its own module, not part of ``api/audiobooks.py``: that blueprint writes only to
the audiobook subsystem's database, and this list lives in the music database
because the music import reads it (``core/audiobook_routing.py``).

A route changes where everyone's downloads go and ``/move`` moves folders on
disk, so everything that writes is admin-only.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from core.profile_context import admin_only

bp = Blueprint("audiobook_routes", __name__)


def create_blueprint():
    return bp


@bp.route("/api/audiobook-routes", methods=["GET"])
def list_routed_artists():
    from core.audiobook_routing import audiobook_folder_configured, list_routes
    return jsonify({"success": True, "artists": list_routes(),
                    "audiobook_folder_configured": audiobook_folder_configured()})


@bp.route("/api/audiobook-routes", methods=["POST"])
@admin_only
def route_artist():
    """Route an artist. Their NEXT downloads land in the audiobook folder."""
    from core.audiobook_routing import add_route, audiobook_folder_configured
    name = str((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "error": "name is required"}), 400
    if not audiobook_folder_configured():
        return jsonify({"success": False, "error":
                        "Set the audiobook folder first (Settings > Library > Folders)."}), 409
    if not add_route(name):
        return jsonify({"success": False, "error": "could not save the route"}), 500
    return jsonify({"success": True, "name": name})


@bp.route("/api/audiobook-routes/check", methods=["POST"])
def check_routed_artists():
    """Which of these names are routed. One round trip for a whole list of cards."""
    from core.audiobook_routing import routed_names
    names = (request.get_json(silent=True) or {}).get("names")
    names = [str(n) for n in names if isinstance(n, str)] if isinstance(names, list) else []
    return jsonify({"success": True, "routed": routed_names(names)})


@bp.route("/api/audiobook-routes/<path:name>", methods=["DELETE"])
@admin_only
def unroute_artist(name: str):
    """Stop routing an artist. Files already in the audiobook folder stay there."""
    from core.audiobook_routing import remove_route
    return jsonify({"success": True, "removed": remove_route(name)})


@bp.route("/api/audiobook-routes/<path:name>/move", methods=["POST"])
@admin_only
def move_existing_albums(name: str):
    """Preview, or with ``{"apply": true}`` perform, moving the albums already filed
    in the music library to the audiobook folder. Never overwrites."""
    from core.audiobook_routing import move_existing
    apply = (request.get_json(silent=True) or {}).get("apply") is True
    return jsonify({"success": True, **move_existing(name, apply=apply)})


@bp.route("/api/audiobook-routes/<path:name>/refresh", methods=["POST"])
@admin_only
def refresh_album_metadata(name: str):
    """Write ``metadata.opf`` (series, episode number) for albums already filed as audiobooks."""
    from core.audiobook_routing import refresh_sidecars
    return jsonify({"success": True, **refresh_sidecars(name)})
