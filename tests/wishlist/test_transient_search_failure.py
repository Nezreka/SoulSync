from __future__ import annotations

from core.downloads import wishlist_failed
from core.runtime_state import download_batches, download_tasks


def test_transient_scoped_search_failure_is_not_added_to_wishlist(monkeypatch):
    batch_id = "lib2-direct-test"
    task_id = "task-direct-test"
    original_batches = dict(download_batches)
    original_tasks = dict(download_tasks)
    calls = []

    class _Service:
        def add_failed_track_from_modal(self, **kwargs):
            calls.append(kwargs)
            return True

    monkeypatch.setattr(
        "core.wishlist_service.get_wishlist_service", lambda: _Service()
    )
    try:
        download_batches.clear()
        download_tasks.clear()
        failed = {
            "download_index": 0,
            "table_index": 0,
            "track_name": "One Shot",
            "artist_name": "Direct Artist",
            "retry_count": 0,
            "track_data": {
                "id": "sp-direct",
                "name": "One Shot",
                "artists": [{"name": "Direct Artist"}],
                "album": {"name": "Direct Single"},
            },
            "failure_reason": "No matching track found",
            "candidates": [],
        }
        download_batches[batch_id] = {
            "queue": [task_id],
            "playlist_id": "library_v2_search",
            "playlist_name": "Library v2 Automatic Search",
            "permanently_failed_tracks": [failed],
            "cancelled_tracks": set(),
            "profile_id": 1,
            "requeue_failed_to_wishlist": False,
        }
        download_tasks[task_id] = {
            "status": "failed",
            "track_index": 0,
            "track_info": failed["track_data"],
        }

        summary = wishlist_failed._process_failed_tracks_to_wishlist_exact(batch_id)

        assert summary == {"tracks_added": 0, "errors": 0, "total_failed": 1}
        assert calls == []
    finally:
        download_batches.clear()
        download_batches.update(original_batches)
        download_tasks.clear()
        download_tasks.update(original_tasks)
