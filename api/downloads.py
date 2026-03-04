"""
Download management endpoints — list, cancel active downloads.
"""

from flask import request, current_app
from .auth import require_api_key
from .helpers import api_success, api_error


def _serialize_download(task_id, task):
    """Serialize a download task with all available fields."""
    track_info = task.get("track_info") or {}

    # Track names can be top-level or inside track_info
    track_name = task.get("track_name") or track_info.get("title") or track_info.get("track_name")
    artist_name = task.get("artist_name") or track_info.get("artist") or track_info.get("artist_name")
    album_name = task.get("album_name") or track_info.get("album") or track_info.get("album_name")

    return {
        "id": task_id,
        "status": task.get("status"),
        "track_name": track_name,
        "artist_name": artist_name,
        "album_name": album_name,
        "username": task.get("username"),
        "filename": task.get("filename"),
        "progress": task.get("progress", 0),
        "size": task.get("size"),
        "error": task.get("error") or task.get("error_message"),
        "batch_id": task.get("batch_id"),
        "track_index": task.get("track_index"),
        "retry_count": task.get("retry_count", 0),
        "metadata_enhanced": task.get("metadata_enhanced", False),
        "status_change_time": task.get("status_change_time"),
    }


def register_routes(bp):

    @bp.route("/downloads", methods=["GET"])
    @require_api_key
    def list_downloads():
        """List active and recent download tasks."""
        try:
            from web_server import download_tasks, tasks_lock

            tasks = []
            with tasks_lock:
                for task_id, task in download_tasks.items():
                    tasks.append(_serialize_download(task_id, task))

            return api_success({"downloads": tasks})
        except ImportError:
            return api_error("NOT_AVAILABLE", "Download tracking not available.", 501)
        except Exception as e:
            return api_error("DOWNLOAD_ERROR", str(e), 500)

    @bp.route("/downloads/<download_id>/cancel", methods=["POST"])
    @require_api_key
    def cancel_download(download_id):
        """Cancel a specific download.

        Body: {"username": "..."}
        """
        body = request.get_json(silent=True) or {}
        username = body.get("username")

        if not username:
            return api_error("BAD_REQUEST", "Missing 'username' in body.", 400)

        try:
            from utils.async_helpers import run_async
            soulseek = current_app.soulsync.get("soulseek_client")
            if not soulseek:
                return api_error("NOT_AVAILABLE", "Soulseek client not configured.", 503)

            ok = run_async(soulseek.cancel_download(download_id, username, remove=True))
            if ok:
                return api_success({"message": "Download cancelled."})
            return api_error("CANCEL_FAILED", "Failed to cancel download.", 500)
        except Exception as e:
            return api_error("DOWNLOAD_ERROR", str(e), 500)

    @bp.route("/downloads/cancel-all", methods=["POST"])
    @require_api_key
    def cancel_all_downloads():
        """Cancel all active downloads and clear completed ones."""
        try:
            from utils.async_helpers import run_async
            soulseek = current_app.soulsync.get("soulseek_client")
            if not soulseek:
                return api_error("NOT_AVAILABLE", "Soulseek client not configured.", 503)

            run_async(soulseek.cancel_all_downloads())
            run_async(soulseek.clear_all_completed_downloads())
            return api_success({"message": "All downloads cancelled and cleared."})
        except Exception as e:
            return api_error("DOWNLOAD_ERROR", str(e), 500)
