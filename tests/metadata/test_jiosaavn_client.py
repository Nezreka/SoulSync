"""Tests for core/jiosaavn_client.py — JioSaavn metadata search adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.jiosaavn_client import (
    Album,
    Artist,
    JioSaavnClient,
    Track,
    _artist_names,
    _best_image,
    _duration_ms,
)


SONG_PAYLOAD = {
    "success": True,
    "data": {
        "total": 1,
        "start": 0,
        "results": [
            {
                "id": "abc123",
                "name": "Kesariya",
                "type": "song",
                "year": "2022",
                "duration": 268,
                "playCount": 123456,
                "url": "https://www.jiosaavn.com/song/kesariya/abc123",
                "album": {"id": "album1", "name": "Brahmastra", "url": "https://www.jiosaavn.com/album/brahmastra/album1"},
                "artists": {
                    "primary": [{"id": "1", "name": "Arijit Singh", "role": "primary_artists"}],
                    "featured": [{"id": "2", "name": "Pritam", "role": "featured_artists"}],
                },
                "image": [
                    {"quality": "50x50", "url": "https://example.com/50.jpg"},
                    {"quality": "500x500", "url": "https://example.com/500.jpg"},
                ],
            }
        ],
    },
}

ALBUM_PAYLOAD = {
    "success": True,
    "data": {
        "total": 1,
        "results": [
            {
                "id": "album1",
                "name": "Brahmastra",
                "year": 2022,
                "type": "album",
                "songCount": 9,
                "url": "https://www.jiosaavn.com/album/brahmastra/album1",
                "artists": {
                    "primary": [{"id": "2", "name": "Pritam", "role": "primary_artists"}],
                    "featured": [],
                },
                "image": [{"quality": "500x500", "url": "https://example.com/album.jpg"}],
            }
        ],
    },
}

ARTIST_PAYLOAD = {
    "success": True,
    "data": {
        "total": 1,
        "results": [
            {
                "id": "456269",
                "name": "A.R. Rahman",
                "type": "artist",
                "url": "https://www.jiosaavn.com/artist/a.r.-rahman/456269",
                "image": [{"quality": "500x500", "url": "https://example.com/artist.jpg"}],
            }
        ],
    },
}


def test_best_image_prefers_500x500():
    images = [
        {"quality": "50x50", "url": "https://example.com/50.jpg"},
        {"quality": "500x500", "url": "https://example.com/500.jpg"},
    ]
    assert _best_image(images) == "https://example.com/500.jpg"


def test_artist_names_primary_and_featured_deduped():
    names = _artist_names({
        "primary": [{"name": "Arijit Singh"}, {"name": "Pritam"}],
        "featured": [{"name": "pritam"}],
    })
    assert names == ["Arijit Singh", "Pritam"]


def test_duration_ms_from_seconds():
    assert _duration_ms(144) == 144000
    assert _duration_ms(None) == 0


def test_rate_limit_waits_when_calls_are_too_close(monkeypatch):
    import core.jiosaavn_client as mod

    mod._last_api_call_time = 1000.0
    sleeps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(mod.time, "time", lambda: 1000.4)

    mod._rate_limit()

    assert sleeps == [pytest.approx(0.6)]


def test_rate_limit_skips_sleep_on_first_call(monkeypatch):
    import core.jiosaavn_client as mod

    mod._last_api_call_time = 0.0
    sleeps: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(mod.time, "time", lambda: 5000.0)
    monkeypatch.setattr(mod.api_call_tracker, "record_call", lambda *_args, **_kwargs: None)

    mod._rate_limit()

    assert sleeps == []


def test_track_from_api_maps_fields():
    track = Track.from_api(SONG_PAYLOAD["data"]["results"][0])
    assert track.id == "abc123"
    assert track.name == "Kesariya"
    assert track.artists == ["Arijit Singh", "Pritam"]
    assert track.album == "Brahmastra"
    assert track.album_id == "album1"
    assert track.duration_ms == 268000
    assert track.image_url == "https://example.com/500.jpg"
    assert track.external_urls["jiosaavn"].endswith("/abc123")


class TestJioSaavnClientSearch:
    def setup_method(self):
        self.client = JioSaavnClient(base_url="https://saavn.test", session=MagicMock())

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_search_tracks_uses_cache_on_hit(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_search_results.return_value = [SONG_PAYLOAD["data"]["results"][0]]
        mock_get_cache.return_value = cache

        tracks = self.client.search_tracks("Kesariya", limit=10)

        assert len(tracks) == 1
        assert tracks[0].name == "Kesariya"
        self.client.session.get.assert_not_called()
        cache.store_search_results.assert_not_called()

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_search_tracks_fetches_and_caches_on_miss(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_search_results.return_value = None
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = SONG_PAYLOAD
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        tracks = self.client.search_tracks("Kesariya", limit=10)

        assert len(tracks) == 1
        assert tracks[0].id == "abc123"
        self.client.session.get.assert_called_once_with(
            "https://saavn.test/api/search/songs",
            params={"query": "Kesariya", "page": 0, "limit": 10},
            timeout=20,
        )
        cache.store_entities_bulk.assert_called_once()
        cache.store_search_results.assert_called_once_with(
            "jiosaavn", "track", "Kesariya", 10, ["abc123"],
        )

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_search_albums(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_search_results.return_value = None
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = ALBUM_PAYLOAD
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        albums = self.client.search_albums("Brahmastra", limit=5)

        assert len(albums) == 1
        assert isinstance(albums[0], Album)
        assert albums[0].name == "Brahmastra"
        assert albums[0].total_tracks == 9
        assert albums[0].release_date == "2022"

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_search_artists(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_search_results.return_value = None
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = ARTIST_PAYLOAD
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        artists = self.client.search_artists("A.R. Rahman", limit=5)

        assert len(artists) == 1
        assert isinstance(artists[0], Artist)
        assert artists[0].name == "A.R. Rahman"
        assert artists[0].image_url == "https://example.com/artist.jpg"

    @patch("core.jiosaavn_client._rate_limit")
    def test_search_all_parses_sections(self, _rate_limit):
        response = MagicMock()
        response.json.return_value = {
            "success": True,
            "data": {
                "songs": {"results": [SONG_PAYLOAD["data"]["results"][0]]},
                "albums": {"results": [ALBUM_PAYLOAD["data"]["results"][0]]},
                "artists": {"results": [ARTIST_PAYLOAD["data"]["results"][0]]},
                "playlists": {"results": [{"id": "pl1", "title": "Bollywood Hits"}]},
            },
        }
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        results = self.client.search_all("Bollywood")

        assert len(results["tracks"]) == 1
        assert len(results["albums"]) == 1
        assert len(results["artists"]) == 1
        assert len(results["playlists"]) == 1

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_get_track_details_uses_entity_cache(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_entity.return_value = SONG_PAYLOAD["data"]["results"][0]
        mock_get_cache.return_value = cache

        details = self.client.get_track_details("abc123")

        assert details["id"] == "abc123"
        assert details["name"] == "Kesariya"
        self.client.session.get.assert_not_called()

    def test_empty_query_returns_no_results(self):
        assert self.client.search_tracks("") == []
        assert self.client.search_albums("  ") == []
        assert self.client.search_artists("") == []


@patch("core.metadata.registry._client_cache", {})
def test_registry_get_jiosaavn_client_caches_by_base_url():
    from core.metadata.registry import get_jiosaavn_client

    with patch("core.jiosaavn_client.JioSaavnClient") as mock_cls:
        mock_cls.return_value = MagicMock()
        with patch("core.metadata.registry._get_config_value", return_value="https://saavn.test"):
            first = get_jiosaavn_client()
            second = get_jiosaavn_client()
            assert first is second
            mock_cls.assert_called_once()
