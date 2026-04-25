"""
Download management endpoints — list, cancel active downloads.
"""

from flask import request, current_app
from .auth import require_api_key
from .helpers import api_success, api_error
from core.imports.runtime_state import download_tasks, tasks_lock


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
        """List download tasks with optional filtering and pagination.

        Query params:
            status: comma-separated statuses to include (e.g. "downloading,queued").
                    Default includes all.
            limit:  max tasks to return (default 100, max 500).
            offset: skip the first N tasks (default 0).

        Response includes `total` (post-filter count) so clients can paginate
        without fetching everything. Tasks are sorted by `status_change_time`
        descending so newest/in-flight tasks appear first.
        """
        try:
            # Parse pagination params
            try:
                limit = int(request.args.get("limit", 100))
            except (TypeError, ValueError):
                limit = 100
            try:
                offset = int(request.args.get("offset", 0))
            except (TypeError, ValueError):
                offset = 0
            # Clamp to sensible bounds
            limit = max(1, min(limit, 500))
            offset = max(0, offset)

            status_param = request.args.get("status", "").strip()
            status_filter = (
                {s.strip() for s in status_param.split(",") if s.strip()}
                if status_param
                else None
            )

            # Snapshot under the lock, then sort/slice outside.
            with tasks_lock:
                snapshot = list(download_tasks.items())

            if status_filter:
                snapshot = [
                    (tid, t) for tid, t in snapshot
                    if (t.get("status") or "") in status_filter
                ]

            # Sort newest-first by status_change_time; fall back to string id
            # so ordering is stable when timestamps are missing or tied.
            snapshot.sort(
                key=lambda item: (item[1].get("status_change_time") or "", item[0]),
                reverse=True,
            )

            total = len(snapshot)
            page = snapshot[offset:offset + limit]
            tasks = [_serialize_download(tid, t) for tid, t in page]

            return api_success({
                "downloads": tasks,
                "total": total,
                "limit": limit,
                "offset": offset,
            })
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
