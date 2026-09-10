"""Client-owned video grabs must never enter Soulseek fallback searches."""
import json
from unittest.mock import Mock

import pytest

from core.video import download_monitor as monitor
from core.video.retry import plan_retry


@pytest.mark.parametrize("source", ["torrent", "usenet", "extto"])
def test_retry_planner_rejects_client_rows_even_with_soulseek_candidates(source):
    row = {"source": source, "search_ctx": json.dumps({"scope": "movie", "title": "Example"}),
           "candidates": json.dumps([{"username": "peer", "filename": "alternate.mkv"}])}
    assert plan_retry(row)["action"] == "fail"


@pytest.mark.parametrize("source", ["torrent", "usenet"])
@pytest.mark.parametrize("initial_status", ["downloading", "searching"])
def test_monitor_keeps_original_client_reference(monkeypatch, source, initial_status):
    import core.video.client_download as client
    row = {"id": 987654, "source": source, "status": initial_status, "client_ref": "original-hash",
           "progress": 0, "search_ctx": {"scope": "movie", "title": "Example"}}
    db = Mock()
    db.get_active_video_downloads.return_value = [row]
    db.update_video_download.side_effect = lambda key, **patch: row.update(patch)
    forbidden = Mock(side_effect=AssertionError("Client grab entered Soulseek"))
    monkeypatch.setattr(monitor, "list_downloads", forbidden)
    monkeypatch.setattr(monitor, "_spawn_requery", forbidden)
    monkeypatch.setattr(monitor, "_make_organizer", lambda db: None)
    monkeypatch.setattr(monitor, "_make_pack_importer", lambda db, organizer: None)
    monkeypatch.setattr(monitor, "_misses", {})
    poll = Mock(side_effect=[{"_missing": True}, {"status": "downloading", "progress": 15}])
    monkeypatch.setattr(client, "process_active_client_download", poll)
    monitor._tick(db)
    assert row["status"] == "downloading"
    monitor._tick(db)
    assert row["progress"] == 15
    assert row["client_ref"] == "original-hash"
    assert not monitor._misses
    forbidden.assert_not_called()


@pytest.mark.parametrize("source", ["torrent", "usenet"])
def test_client_failure_surfaces_original_error_without_requery(monkeypatch, source):
    db = Mock()
    monkeypatch.setattr(monitor, "_blocked_pairs", lambda db: set())
    monkeypatch.setattr(monitor, "_blocked_users", lambda db: set())
    monkeypatch.setattr(monitor, "_wishlist_failed", lambda *args: None)
    monkeypatch.setattr(monitor, "_archive_history", lambda *args: None)
    import core.video.download_events as events
    monkeypatch.setattr(events, "publish", lambda *args: None)
    search = Mock(side_effect=AssertionError("Unexpected Soulseek search"))
    monkeypatch.setattr(monitor, "_spawn_requery", search)
    monitor._fail_or_retry(db, {"id": 1, "source": source,
                               "search_ctx": {"scope": "movie", "title": "Example"}}, "Disk full")
    assert db.update_video_download.call_args.kwargs["status"] == "failed"
    assert db.update_video_download.call_args.kwargs["error"] == "Disk full"
    search.assert_not_called()
