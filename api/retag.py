"""
Retag queue endpoints — view and manage pending metadata corrections.

Answered from Library v2's pending Re-tag findings
(:mod:`core.library2.retag_queue`), in the shape the API always had.
"""

from core.library2 import retag_queue
from database.music_database import get_database
from .auth import require_api_key
from .helpers import api_success, api_error


def register_routes(bp):

    @bp.route("/retag/groups", methods=["GET"])
    @require_api_key
    def list_retag_groups():
        """List all retag groups with track counts."""
        try:
            return api_success({"groups": retag_queue.retag_groups(get_database())})
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)

    @bp.route("/retag/groups/<int:group_id>", methods=["GET"])
    @require_api_key
    def get_retag_group(group_id):
        """Get a retag group with its tracks."""
        try:
            found = retag_queue.retag_group(get_database(), group_id)
            if not found:
                return api_error("NOT_FOUND", f"Retag group {group_id} not found.", 404)
            return api_success(found)
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)

    @bp.route("/retag/groups/<int:group_id>", methods=["DELETE"])
    @require_api_key
    def delete_retag_group(group_id):
        """Delete a retag group and its tracks."""
        try:
            if retag_queue.dismiss_retag_group(get_database(), group_id):
                return api_success({"message": f"Retag group {group_id} deleted."})
            return api_error("NOT_FOUND", f"Retag group {group_id} not found.", 404)
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)

    @bp.route("/retag/groups", methods=["DELETE"])
    @require_api_key
    def clear_retag_groups():
        """Delete all retag groups and tracks."""
        try:
            count = retag_queue.dismiss_all_retag_groups(get_database())
            return api_success({"message": f"Cleared {count} retag groups."})
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)

    @bp.route("/retag/stats", methods=["GET"])
    @require_api_key
    def retag_stats():
        """Get retag queue statistics."""
        try:
            return api_success(retag_queue.retag_stats(get_database()))
        except Exception as e:
            return api_error("RETAG_ERROR", str(e), 500)
