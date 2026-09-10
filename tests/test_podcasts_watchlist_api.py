"""Tests for the Podcast Watchlist database layer and REST API.

Tests the parity with artist and label watchlists:
- SQLite table creation and indexes
- DB CRUD methods (add, get, check, update settings, remove, count)
- REST API endpoints (/api/podcasts/watchlist/*)
"""

from __future__ import annotations

import pytest
from flask import Flask

from api.podcasts import create_podcasts_blueprint
from database.music_database import MusicDatabase


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test_music.db"
    return MusicDatabase(str(db_path))


@pytest.fixture
def test_client(tmp_db, monkeypatch):
    import api.podcasts as pod_api
    monkeypatch.setattr(pod_api, "_db", lambda: tmp_db)

    app = Flask(__name__)
    app.register_blueprint(create_podcasts_blueprint())
    app.config["TESTING"] = True
    return app.test_client()


class TestPodcastWatchlistDatabase:
    def test_add_and_get_watchlist_podcast(self, tmp_db):
        ok = tmp_db.add_watchlist_podcast(
            feed_url="https://feeds.example.com/hardcorehistory",
            title="Dan Carlin's Hardcore History",
            itunes_id=173001861,
            author="Dan Carlin",
            description="The past through Dan's eyes",
            artwork_url="https://example.com/art.jpg",
            website="https://dancarlin.com",
            auto_download=True,
            retention_days=30,
            episode_count=100,
        )
        assert ok is True

        podcasts = tmp_db.get_watchlist_podcasts(profile_id=1)
        assert len(podcasts) == 1
        pod = podcasts[0]
        assert pod["feed_url"] == "https://feeds.example.com/hardcorehistory"
        assert pod["itunes_id"] == 173001861
        assert pod["title"] == "Dan Carlin's Hardcore History"
        assert pod["author"] == "Dan Carlin"
        assert pod["auto_download"] is True
        assert pod["retention_days"] == 30

    def test_is_podcast_in_watchlist(self, tmp_db):
        tmp_db.add_watchlist_podcast(
            feed_url="https://feeds.example.com/serial",
            title="Serial",
            itunes_id=910140494,
        )
        assert tmp_db.is_podcast_in_watchlist(feed_url="https://feeds.example.com/serial") is True
        assert tmp_db.is_podcast_in_watchlist(itunes_id=910140494) is True
        assert tmp_db.is_podcast_in_watchlist(feed_url="https://unknown.com/rss") is False
        assert tmp_db.is_podcast_in_watchlist(itunes_id=999999999) is False

    def test_update_watchlist_podcast_settings(self, tmp_db):
        tmp_db.add_watchlist_podcast(
            feed_url="https://feeds.example.com/huberman",
            title="Huberman Lab",
            retention_days=14,
            auto_download=False,
        )
        # Update auto_download and retention_days
        ok = tmp_db.update_watchlist_podcast_settings(
            feed_url="https://feeds.example.com/huberman",
            auto_download=True,
            retention_days=60,
        )
        assert ok is True

        pod = tmp_db.get_watchlist_podcast(feed_url="https://feeds.example.com/huberman")
        assert pod is not None
        assert pod["auto_download"] is True
        assert pod["retention_days"] == 60

    def test_remove_watchlist_podcast(self, tmp_db):
        tmp_db.add_watchlist_podcast(
            feed_url="https://feeds.example.com/radiolab",
            title="Radiolab",
            itunes_id=152307840,
        )
        assert tmp_db.get_watchlist_podcasts_count() == 1

        removed = tmp_db.remove_watchlist_podcast(feed_url="https://feeds.example.com/radiolab")
        assert removed is True
        assert tmp_db.get_watchlist_podcasts_count() == 0
        assert tmp_db.is_podcast_in_watchlist(feed_url="https://feeds.example.com/radiolab") is False

    def test_idempotent_add_updates_existing(self, tmp_db):
        tmp_db.add_watchlist_podcast(
            feed_url="https://feeds.example.com/daily",
            title="The Daily",
            episode_count=500,
        )
        # Re-add with updated episode_count and author
        tmp_db.add_watchlist_podcast(
            feed_url="https://feeds.example.com/daily",
            title="The Daily (Updated)",
            author="The New York Times",
            episode_count=550,
        )
        podcasts = tmp_db.get_watchlist_podcasts()
        assert len(podcasts) == 1
        assert podcasts[0]["title"] == "The Daily (Updated)"
        assert podcasts[0]["author"] == "The New York Times"
        assert podcasts[0]["episode_count"] == 550


class TestPodcastWatchlistAPI:
    def test_watchlist_list_empty(self, test_client):
        res = test_client.get("/api/podcasts/watchlist")
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["podcasts"] == []

    def test_watchlist_add_check_and_remove_flow(self, test_client):
        # 1. Check initially not in watchlist
        check_res = test_client.post(
            "/api/podcasts/watchlist/check",
            json={"feed_url": "https://feeds.example.com/lex"},
        )
        assert check_res.status_code == 200
        assert check_res.get_json()["is_watching"] is False

        # 2. Add to watchlist
        add_res = test_client.post(
            "/api/podcasts/watchlist/add",
            json={
                "feed_url": "https://feeds.example.com/lex",
                "title": "Lex Fridman Podcast",
                "itunes_id": 1434243584,
                "author": "Lex Fridman",
                "artwork_url": "https://example.com/lex.jpg",
                "auto_download": False,
                "retention_days": 14,
            },
        )
        assert add_res.status_code == 200
        add_data = add_res.get_json()
        assert add_data["success"] is True
        assert add_data["is_watching"] is True
        assert add_data["podcast"]["title"] == "Lex Fridman Podcast"
        assert add_data["podcast"]["retention_days"] == 14

        # 3. Check now in watchlist
        check_res2 = test_client.post(
            "/api/podcasts/watchlist/check",
            json={"itunes_id": 1434243584},
        )
        assert check_res2.status_code == 200
        assert check_res2.get_json()["is_watching"] is True

        # 4. Update settings
        settings_res = test_client.post(
            "/api/podcasts/watchlist/settings",
            json={
                "feed_url": "https://feeds.example.com/lex",
                "auto_download": True,
                "retention_days": 21,
            },
        )
        assert settings_res.status_code == 200
        assert settings_res.get_json()["podcast"]["auto_download"] is True
        assert settings_res.get_json()["podcast"]["retention_days"] == 21

        # 5. List reflects the podcast
        list_res = test_client.get("/api/podcasts/watchlist")
        assert list_res.status_code == 200
        shows = list_res.get_json()["podcasts"]
        assert len(shows) == 1
        assert shows[0]["feed_url"] == "https://feeds.example.com/lex"

        # 6. Remove from watchlist
        remove_res = test_client.post(
            "/api/podcasts/watchlist/remove",
            json={"feed_url": "https://feeds.example.com/lex"},
        )
        assert remove_res.status_code == 200
        assert remove_res.get_json()["is_watching"] is False

        # 7. Check reflects removed
        check_res3 = test_client.post(
            "/api/podcasts/watchlist/check",
            json={"feed_url": "https://feeds.example.com/lex"},
        )
        assert check_res3.get_json()["is_watching"] is False

    def test_watchlist_add_defaults_auto_download_to_true(self, test_client):
        add_res = test_client.post(
            "/api/podcasts/watchlist/add",
            json={
                "feed_url": "https://feeds.example.com/default_test",
                "title": "Default Auto Download Podcast",
            },
        )
        assert add_res.status_code == 200
        pod = add_res.get_json()["podcast"]
        assert pod["auto_download"] is True
        assert pod["retention_days"] == 14

