"""Tests for api/podcasts.py — podcast search, feed fetching, download status, and error handling."""

from unittest.mock import MagicMock, patch
import pytest
from flask import Flask

from api.podcasts import create_podcasts_blueprint
from core.podcast_client import PodcastEpisode, PodcastShow


@pytest.fixture
def app():
    app = Flask(__name__)
    app.register_blueprint(create_podcasts_blueprint())
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _sample_show(**kwargs) -> PodcastShow:
    defaults = dict(
        title="Test Podcast",
        author="Test Host",
        description="A great podcast about testing",
        artwork_url="https://example.com/art.jpg",
        feed_url="https://example.com/feed.xml",
        itunes_id=123456,
        website="https://example.com",
        language="en",
        explicit=False,
        categories=["Technology"],
        episode_count=10,
        episodes=[],
    )
    defaults.update(kwargs)
    return PodcastShow(**defaults)


def _sample_episode(**kwargs) -> PodcastEpisode:
    defaults = dict(
        guid="ep-001",
        title="Episode 1: The Beginning",
        enclosure_url="https://example.com/ep1.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=15000000,
        pub_date=None,
        duration_seconds=1800,
        description="First episode notes",
        show_notes="<p>Full show notes</p>",
        season=1,
        episode_number=1,
        episode_type="full",
        artwork_url=None,
        chapter_url=None,
        transcript_url=None,
    )
    defaults.update(kwargs)
    return PodcastEpisode(**defaults)


# ---------------------------------------------------------------------------
# Search Tests
# ---------------------------------------------------------------------------

def test_search_empty_query_returns_empty(client):
    res = client.get("/api/podcasts/search?q=")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["results"] == []


def test_search_calls_podcast_client(client):
    show = _sample_show()
    with patch("api.podcasts.get_podcast_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.search_podcasts.return_value = [show]
        mock_get_client.return_value = mock_client

        res = client.get("/api/podcasts/search?q=technology&limit=10")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert len(data["results"]) == 1
        assert data["results"][0]["title"] == "Test Podcast"
        mock_client.search_podcasts.assert_called_once_with("technology", limit=10)


# ---------------------------------------------------------------------------
# Featured Tests
# ---------------------------------------------------------------------------

def test_featured_returns_cached_or_fetched_shows(client):
    show = _sample_show(title="Featured Pod")
    with patch("api.podcasts.get_podcast_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.search_podcasts.return_value = [show]
        mock_get_client.return_value = mock_client

        res = client.get("/api/podcasts/featured?category=Technology")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert len(data["results"]) >= 1


# ---------------------------------------------------------------------------
# Show Detail Tests
# ---------------------------------------------------------------------------

def test_show_detail_requires_url_or_itunes_id(client):
    res = client.get("/api/podcasts/show")
    assert res.status_code == 400
    data = res.get_json()
    assert data["success"] is False


def test_show_detail_fetches_feed(client):
    ep = _sample_episode()
    show = _sample_show(episodes=[ep])
    with patch("api.podcasts.get_podcast_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.fetch_feed.return_value = show
        mock_get_client.return_value = mock_client

        res = client.get("/api/podcasts/show?url=https://example.com/feed.xml")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["show"]["title"] == "Test Podcast"
        assert len(data["show"]["episodes"]) == 1
        assert data["show"]["episodes"][0]["title"] == "Episode 1: The Beginning"


def test_show_detail_handles_fetch_failure(client):
    with patch("api.podcasts.get_podcast_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.fetch_feed.return_value = None
        mock_get_client.return_value = mock_client

        res = client.get("/api/podcasts/show?url=https://example.com/bad.xml")
        assert res.status_code == 502
        data = res.get_json()
        assert data["success"] is False


# ---------------------------------------------------------------------------
# Download Tests
# ---------------------------------------------------------------------------

def test_download_episode_queues_task(client):
    with patch("api.podcasts._get_download_client") as mock_get_dl:
        mock_dl = MagicMock()
        mock_dl.download_episode.return_value = "/downloads/ep1.mp3"
        mock_get_dl.return_value = mock_dl

        res = client.post(
            "/api/podcasts/download",
            json={
                "enclosure_url": "https://example.com/ep1.mp3",
                "title": "Episode 1",
                "show_title": "Test Show",
                "author": "Host A",
                "enclosure_type": "audio/mpeg",
                "enclosure_length": 10485760,
            },
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "download_id" in data
        assert "task_id" in data

        from core.runtime_state import download_batches, download_tasks
        task_id = data["task_id"]
        assert task_id in download_tasks
        assert download_tasks[task_id]["download_source"] == "Podcast"
        assert download_tasks[task_id]["playlist_id"] == "podcasts"
        assert "podcasts" in download_batches
        assert task_id in download_batches["podcasts"]["queue"]


def test_download_episode_records_history_and_completes(client):
    import time
    from core.runtime_state import download_tasks

    mock_db = MagicMock()
    mock_db.add_library_history_entry.return_value = 999

    with patch("api.podcasts._get_download_client") as mock_get_dl, \
         patch("api.podcasts._db", return_value=mock_db):

        def fake_download(ep, progress_callback=None, show_title=None, author=None, is_cancelled=None):
            if progress_callback:
                progress_callback(5242880, 10485760)
            return "/downloads/ep_test.mp3"

        mock_dl = MagicMock()
        mock_dl.download_episode.side_effect = fake_download
        mock_get_dl.return_value = mock_dl

        res = client.post(
            "/api/podcasts/download",
            json={
                "enclosure_url": "https://example.com/ep_test.mp3",
                "title": "Episode Test",
                "show_title": "Show Test",
                "author": "Host Test",
            },
        )
        assert res.status_code == 200
        task_id = res.get_json()["task_id"]

        # Wait briefly for worker thread to complete
        deadline = time.time() + 2.0
        while time.time() < deadline:
            task = download_tasks.get(task_id, {})
            if task.get("status") in ("completed", "failed"):
                break
            time.sleep(0.02)

        task = download_tasks.get(task_id, {})
        assert task.get("status") == "completed"
        assert task.get("history_id") == 999
        assert task.get("final_file_path") == "/downloads/ep_test.mp3"

        # Verify add_library_history_entry was called with event_type="podcast"
        mock_db.add_library_history_entry.assert_called_once()
        kws = mock_db.add_library_history_entry.call_args.kwargs
        assert kws["event_type"] == "podcast"
        assert kws["title"] == "Episode Test"
        assert kws["download_source"] == "Podcast"


def test_download_episode_cancellation_marks_task_cancelled(client):
    import time
    from core.runtime_state import download_tasks

    with patch("api.podcasts._get_download_client") as mock_get_dl:
        def fake_download_cancel(ep, progress_callback=None, show_title=None, author=None, is_cancelled=None):
            # simulate cancellation check triggering
            raise InterruptedError("Cancelled")

        mock_dl = MagicMock()
        mock_dl.download_episode.side_effect = fake_download_cancel
        mock_get_dl.return_value = mock_dl

        res = client.post(
            "/api/podcasts/download",
            json={
                "enclosure_url": "https://example.com/ep_cancel.mp3",
                "title": "Episode Cancel",
                "show_title": "Show Cancel",
            },
        )
        assert res.status_code == 200
        task_id = res.get_json()["task_id"]

        deadline = time.time() + 2.0
        while time.time() < deadline:
            task = download_tasks.get(task_id, {})
            if task.get("status") in ("cancelled", "failed"):
                break
            time.sleep(0.02)

        task = download_tasks.get(task_id, {})
        assert task.get("status") == "cancelled"


def test_podcast_task_in_unified_downloads_response():
    from core.downloads.status import build_unified_downloads_response
    from core.runtime_state import download_batches, download_tasks
    from unittest.mock import MagicMock

    deps = MagicMock()
    deps.get_recent_completed_tasks = MagicMock(return_value=[])
    deps.get_persistent_download_history = MagicMock(return_value=[])
    deps.get_slskd_downloads = MagicMock(return_value=[])

    download_batches["podcasts"] = {
        "playlist_id": "podcasts",
        "playlist_name": "Podcasts",
        "phase": "downloading",
        "queue": ["podcast_task_123"],
    }
    download_tasks["podcast_task_123"] = {
        "status": "downloading",
        "track_index": 1,
        "playlist_id": "podcasts",
        "batch_id": "podcasts",
        "download_source": "Podcast",
        "quality": "audio/mpeg",
        "progress": 45.0,
        "speed": 1048576.0,
        "bytes_transferred": 4500000,
        "size": 10000000,
        "track_info": {
            "title": "My Podcast Episode",
            "artist": "Podcast Host",
            "album": "The Great Show",
        },
        "status_change_time": 100.0,
    }

    resp = build_unified_downloads_response(100, deps)
    downloads = resp.get("downloads", [])
    podcast_row = next((d for d in downloads if d.get("task_id") == "podcast_task_123"), None)
    assert podcast_row is not None
    assert podcast_row["title"] == "My Podcast Episode"
    assert podcast_row["artist"] == "Podcast Host"
    assert podcast_row["album"] == "The Great Show"
    assert podcast_row["download_source"] == "Podcast"
    assert podcast_row["status"] == "downloading"
    assert podcast_row["progress"] == 45.0
    assert podcast_row["live_detail"] is not None
    assert podcast_row["live_detail"]["speed"] == 1048576.0
    assert podcast_row["live_detail"]["bytes"] == 4500000
    assert podcast_row["live_detail"]["size"] == 10000000


def test_get_downloads(client):
    res = client.get("/api/podcasts/downloads")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert isinstance(data["downloads"], list)


def test_cancel_queued_downloads(client):
    from core.runtime_state import download_batches, download_tasks
    import api.podcasts as pod_api

    download_batches["podcasts"] = {
        "queue": ["podcast_q1", "podcast_q2"],
        "playlist_id": "podcasts",
    }
    download_tasks["podcast_q1"] = {"status": "queued", "playlist_id": "podcasts"}
    download_tasks["podcast_q2"] = {"status": "downloading", "playlist_id": "podcasts"}

    with pod_api._download_lock:
        pod_api._downloads["q1"] = {"status": "queued"}

    res = client.post("/api/podcasts/downloads/cancel-queued")
    assert res.status_code == 200
    assert res.get_json()["cancelled_count"] == 1

    assert download_tasks["podcast_q1"]["status"] == "cancelled"
    assert download_tasks["podcast_q2"]["status"] == "downloading"
    with pod_api._download_lock:
        assert pod_api._downloads["q1"]["status"] == "cancelled"


def test_library_history_podcast_tab_and_stats(tmp_path):
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=tmp_path / "test.db")
    # Add download, import, and podcast entries
    id_dl = db.add_library_history_entry(
        event_type="download",
        title="Music Track",
        artist_name="Music Artist",
        download_source="Soulseek",
    )
    id_imp = db.add_library_history_entry(
        event_type="import",
        title="Imported Track",
        artist_name="Import Artist",
        server_source="plex",
    )
    id_pod = db.add_library_history_entry(
        event_type="podcast",
        title="Podcast Episode",
        artist_name="Podcast Host",
        album_name="Podcast Show",
        download_source="Podcast",
        file_path="/downloads/podcasts/episode.mp3",
    )

    stats = db.get_library_history_stats()
    assert stats["downloads"] == 1
    assert stats["imports"] == 1
    assert stats["podcasts"] == 1
    assert stats["source_counts"].get("Podcast") == 1

    # Query podcast history
    entries, total = db.get_library_history(event_type="podcast", limit=10, page=1)
    assert total == 1
    assert len(entries) == 1
    assert entries[0]["id"] == id_pod
    assert entries[0]["title"] == "Podcast Episode"
    assert entries[0]["event_type"] == "podcast"
    assert entries[0]["download_source"] == "Podcast"

    # Query download history (should not include podcast)
    entries_dl, total_dl = db.get_library_history(event_type="download", limit=10, page=1)
    assert total_dl == 1
    assert entries_dl[0]["id"] == id_dl

    # Query both download and podcast (used by clear/purge and persistent history)
    entries_both, total_both = db.get_library_history(event_type=("download", "podcast"), limit=10, page=1)
    assert total_both == 2
    types = {e["event_type"] for e in entries_both}
    assert types == {"download", "podcast"}


