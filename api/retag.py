"""
Retag queue endpoints — view and manage pending metadata corrections.
"""

from flask import request
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error


def register_routes(bp):

    @bp.route("/retag/groups", methods=["GET"])
    @require_api_key
    def list_retag_groups():
        """List all retag groups with track counts."""
        try:
            db = get_database()
            groups = db.get_retag_groups()
            return api_success({"groups": groups})
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)

    @bp.route("/retag/groups/<int:group_id>", methods=["GET"])
    @require_api_key
    def get_retag_group(group_id):
        """Get a retag group with its tracks."""
        try:
            db = get_database()
            # Get group info
            groups = db.get_retag_groups()
            group = next((g for g in groups if g["id"] == group_id), None)
            if not group:
                return api_error("NOT_FOUND", f"Retag group {group_id} not found.", 404)

            tracks = db.get_retag_tracks(group_id)
            return api_success({
                "group": group,
                "tracks": tracks,
            })
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)

    @bp.route("/retag/groups/<int:group_id>", methods=["DELETE"])
    @require_api_key
    def delete_retag_group(group_id):
        """Delete a retag group and its tracks."""
        try:
            db = get_database()
            ok = db.delete_retag_group(group_id)
            if ok:
                return api_success({"message": f"Retag group {group_id} deleted."})
            return api_error("NOT_FOUND", f"Retag group {group_id} not found.", 404)
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)

    @bp.route("/retag/groups", methods=["DELETE"])
    @require_api_key
    def clear_retag_groups():
        """Delete all retag groups and tracks."""
        try:
            db = get_database()
            count = db.clear_all_retag_groups()
            return api_success({"message": f"Cleared {count} retag groups."})
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)

    @bp.route("/retag/stats", methods=["GET"])
    @require_api_key
    def retag_stats():
        """Get retag queue statistics."""
        try:
            db = get_database()
            stats = db.get_retag_stats()
            return api_success(stats)
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)
