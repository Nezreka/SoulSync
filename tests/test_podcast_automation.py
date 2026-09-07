"""Tests for core/podcast_automation.py and the watchlist scan endpoints."""

from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from api.podcasts import create_podcasts_blueprint
from core.podcast_automation import (
    get_scan_status,
    scan_and_auto_download_podcasts,
    start_podcast_automation,
    stop_podcast_automation,
)
from core.podcast_client import PodcastEpisode, PodcastShow


@pytest.fixture
def app():
    app = Flask(__name__)
    app.register_blueprint(create_podcasts_blueprint())
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_scan_and_auto_download_with_new_episodes():
    mock_db = MagicMock()
    mock_db.get_watchlist_podcasts.return_value = [
        {
            "id": 1,
            "feed_url": "https://feeds.example.com/test.xml",
            "title": "Test Show",
            "author": "Host",
            "auto_download": 1,
            "retention_days": 0,
        }
    ]
    # Episode 1 already downloaded, Episode 2 is new
    mock_db.is_podcast_episode_downloaded.side_effect = lambda **kwargs: kwargs.get("title") == "Ep 1"

    fake_ep1 = PodcastEpisode(
        guid="ep1",
        title="Ep 1",
        enclosure_url="https://example.com/ep1.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=1000,
        pub_date=datetime.now(timezone.utc),
        duration_seconds=300,
        description="",
        show_notes="",
        season=1,
        episode_number=1,
        episode_type="full",
        artwork_url="",
        chapter_url=None,
        transcript_url=None,
        show_title="Test Show",
        author="Host",
    )
    fake_ep2 = PodcastEpisode(
        guid="ep2",
        title="Ep 2",
        enclosure_url="https://example.com/ep2.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=2000,
        pub_date=datetime.now(timezone.utc) + timedelta(minutes=5),
        duration_seconds=600,
        description="",
        show_notes="",
        season=1,
        episode_number=2,
        episode_type="full",
        artwork_url="",
        chapter_url=None,
        transcript_url=None,
        show_title="Test Show",
        author="Host",
    )
    fake_show = PodcastShow(
        feed_url="https://feeds.example.com/test.xml",
        title="Test Show",
        author="Host",
        description="",
        artwork_url="",
        website="",
        itunes_id=None,
        language="en",
        categories=["Tech"],
        explicit=False,
        episode_count=2,
        episodes=[fake_ep1, fake_ep2],
    )

    mock_podcast_client = MagicMock()
    mock_podcast_client.fetch_feed.return_value = fake_show

    with patch("core.podcast_automation._get_db", return_value=mock_db), \
         patch("core.podcast_automation.get_podcast_client", return_value=mock_podcast_client), \
         patch("api.podcasts.queue_podcast_download", return_value={"success": True, "task_id": "mock_task"}) as mock_queue:

        result = scan_and_auto_download_podcasts(profile_id=1)

        assert result["success"] is True
        assert result["podcasts_checked"] == 1
        assert result["episodes_queued"] == 1
        mock_db.mark_watchlist_podcast_scanned.assert_called_once_with(
            "https://feeds.example.com/test.xml", episode_count=2
        )
        mock_queue.assert_called_once()
        args = mock_queue.call_args[0][0]
        assert args["title"] == "Ep 2"
        assert args["enclosure_url"] == "https://example.com/ep2.mp3"


def test_scan_skips_when_auto_download_disabled():
    mock_db = MagicMock()
    mock_db.get_watchlist_podcasts.return_value = [
        {
            "id": 1,
            "feed_url": "https://feeds.example.com/no-auto.xml",
            "title": "No Auto Show",
            "author": "Host",
            "auto_download": 0,
            "retention_days": 0,
        }
    ]
    fake_ep = PodcastEpisode(
        guid="ep1",
        title="Ep 1",
        enclosure_url="https://example.com/ep1.mp3",
        enclosure_type="audio/mpeg",
        enclosure_length=1000,
        pub_date=datetime.now(timezone.utc),
        duration_seconds=300,
        description="",
        show_notes="",
        season=1,
        episode_number=1,
        episode_type="full",
        artwork_url="",
        chapter_url=None,
        transcript_url=None,
        show_title="No Auto Show",
        author="Host",
    )
    fake_show = PodcastShow(
        feed_url="https://feeds.example.com/no-auto.xml",
        title="No Auto Show",
        author="Host",
        description="",
        artwork_url="",
        website="",
        itunes_id=None,
        language="en",
        categories=[],
        explicit=False,
        episode_count=1,
        episodes=[fake_ep],
    )
    mock_podcast_client = MagicMock()
    mock_podcast_client.fetch_feed.return_value = fake_show

    with patch("core.podcast_automation._get_db", return_value=mock_db), \
         patch("core.podcast_automation.get_podcast_client", return_value=mock_podcast_client), \
         patch("api.podcasts.queue_podcast_download") as mock_queue:

        result = scan_and_auto_download_podcasts(profile_id=1)

        assert result["success"] is True
        assert result["episodes_queued"] == 0
        mock_queue.assert_not_called()
        mock_db.mark_watchlist_podcast_scanned.assert_called_once()


def test_scan_retention_prunes_expired_files():
    mock_db = MagicMock()
    mock_db.get_watchlist_podcasts.return_value = [
        {
            "id": 1,
            "feed_url": "https://feeds.example.com/prune.xml",
            "title": "Prune Show",
            "author": "Host",
            "auto_download": 0,
            "retention_days": 7,
        }
    ]

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
        tmp_file.write(b"fake audio data")
        temp_path = tmp_file.name

    try:
        # File downloaded 10 days ago (retention is 7 days)
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        mock_db.get_downloaded_podcast_episodes.return_value = [
            {
                "id": 42,
                "feed_url": "https://feeds.example.com/prune.xml",
                "enclosure_url": "https://example.com/old.mp3",
                "file_path": temp_path,
                "downloaded_at": old_date,
            }
        ]

        mock_podcast_client = MagicMock()
        mock_podcast_client.fetch_feed.return_value = PodcastShow(
            feed_url="https://feeds.example.com/prune.xml",
            title="Prune Show",
            author="Host",
            description="",
            artwork_url="",
            website="",
            itunes_id=None,
            language="en",
            categories=[],
            explicit=False,
            episode_count=0,
            episodes=[],
        )

        with patch("core.podcast_automation._get_db", return_value=mock_db), \
             patch("core.podcast_automation.get_podcast_client", return_value=mock_podcast_client):

            result = scan_and_auto_download_podcasts(profile_id=1)

            assert result["success"] is True
            assert result["episodes_pruned"] == 1
            mock_db.mark_podcast_episode_pruned.assert_called_once_with(42)
            assert not os.path.exists(temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_api_watchlist_scan_endpoints(client):
    with patch("core.podcast_automation.scan_and_auto_download_podcasts", return_value={"success": True, "episodes_queued": 2}):
        resp = client.post("/api/podcasts/watchlist/scan-now")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["episodes_queued"] == 2

    resp_status = client.get("/api/podcasts/watchlist/scan-status")
    assert resp_status.status_code == 200
    status_data = resp_status.get_json()
    assert status_data["success"] is True
    assert "status" in status_data


def test_scan_only_downloads_most_recent_episode_even_with_many_unseen():
    """Verify that when a feed has multiple (e.g. 5) un-downloaded episodes,
    only the single newest episode is queued for download."""
    mock_db = MagicMock()
    mock_db.get_watchlist_podcasts.return_value = [
        {
            "id": 1,
            "feed_url": "https://feeds.example.com/multi.xml",
            "title": "Multi Show",
            "author": "Host",
            "auto_download": 1,
            "retention_days": 0,
        }
    ]
    # None of the episodes have been downloaded yet
    mock_db.is_podcast_episode_downloaded.return_value = False

    episodes = []
    base_time = datetime.now(timezone.utc)
    for i in range(1, 6):
        ep = PodcastEpisode(
            guid=f"ep{i}",
            title=f"Episode {i}",
            enclosure_url=f"https://example.com/ep{i}.mp3",
            enclosure_type="audio/mpeg",
            enclosure_length=1000 * i,
            pub_date=base_time + timedelta(days=i),  # Episode 5 is the newest
            duration_seconds=300 * i,
            description="",
            show_notes="",
            season=1,
            episode_number=i,
            episode_type="full",
            artwork_url="",
            chapter_url=None,
            transcript_url=None,
            show_title="Multi Show",
            author="Host",
        )
        episodes.append(ep)

    fake_show = PodcastShow(
        feed_url="https://feeds.example.com/multi.xml",
        title="Multi Show",
        author="Host",
        description="",
        artwork_url="",
        website="",
        itunes_id=None,
        language="en",
        categories=[],
        explicit=False,
        episode_count=5,
        episodes=episodes,
    )

    mock_podcast_client = MagicMock()
    mock_podcast_client.fetch_feed.return_value = fake_show

    with patch("core.podcast_automation._get_db", return_value=mock_db), \
         patch("core.podcast_automation.get_podcast_client", return_value=mock_podcast_client), \
         patch("api.podcasts.queue_podcast_download", return_value={"success": True, "task_id": "mock_task"}) as mock_queue:

        result = scan_and_auto_download_podcasts(profile_id=1)

        assert result["success"] is True
        assert result["podcasts_checked"] == 1
        # Crucial assertion: exactly 1 episode queued (Episode 5, the most recent), NOT all 5
        assert result["episodes_queued"] == 1
        mock_queue.assert_called_once()
        args = mock_queue.call_args[0][0]
        assert args["title"] == "Episode 5"
        assert args["enclosure_url"] == "https://example.com/ep5.mp3"

