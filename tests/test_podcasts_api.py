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
            },
        )
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "download_id" in data


def test_get_downloads(client):
    res = client.get("/api/podcasts/downloads")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert isinstance(data["downloads"], list)
