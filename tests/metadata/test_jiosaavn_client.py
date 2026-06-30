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


def test_album_from_api_derives_track_count_from_songids():
    album = Album.from_api({
        "id": "album1",
        "name": "Brahmastra",
        "songIds": "s1, s2 ,s3,",
    })
    assert album.total_tracks == 3


def test_album_from_api_falls_back_to_unknown_artist():
    album = Album.from_api({"id": "album1", "name": "Brahmastra"})
    assert album.artists == ["Unknown Artist"]


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

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_get_track_details_uses_entity_cache(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_entity.return_value = SONG_PAYLOAD["data"]["results"][0]
        mock_get_cache.return_value = cache

        details = self.client.get_track_details("abc123")

        assert details["id"] == "abc123"
        assert details["name"] == "Kesariya"
        assert details["artists"] == [{"name": "Arijit Singh"}, {"name": "Pritam"}]
        assert details["album"]["name"] == "Brahmastra"
        self.client.session.get.assert_not_called()

    def test_empty_query_returns_no_results(self):
        assert self.client.search_tracks("") == []
        assert self.client.search_albums("  ") == []
        assert self.client.search_artists("") == []

    @patch("core.jiosaavn_client._rate_limit")
    def test_get_json_raises_when_api_reports_failure(self, _rate_limit):
        response = MagicMock()
        response.json.return_value = {"success": False, "message": "bad request"}
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        with pytest.raises(RuntimeError, match="bad request"):
            self.client._get_json("/api/songs/abc123")

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_get_album_returns_none_when_payload_lacks_data(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_entity.return_value = None
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = {"success": True, "data": None}
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        assert self.client.get_album("missing") is None
        cache.store_entity.assert_not_called()

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_get_album_refetches_when_cache_lacks_songs(self, _rate_limit, mock_get_cache):
        """Search stubs cache album metadata without a track list — get_album must refetch."""
        cache = MagicMock()
        cache.get_entity.return_value = {
            "id": "1017247",
            "name": "3 Nights 4 Days",
            "year": 2009,
            "type": "album",
            "songCount": 10,
            "url": "https://www.jiosaavn.com/album/3-nights-4-days/-FE7FJ61jhA_",
            "artists": {"primary": [{"id": "455701", "name": "Daboo Malik"}]},
        }
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = {
            "success": True,
            "data": {
                "id": "1017247",
                "name": "3 Nights 4 Days",
                "year": 2009,
                "type": "album",
                "songCount": 2,
                "songs": [
                    {
                        "id": "song1",
                        "name": "Track One",
                        "duration": 200,
                        "album": {"id": "1017247", "name": "3 Nights 4 Days"},
                        "artists": {"primary": [{"name": "Daboo Malik"}]},
                    },
                    {
                        "id": "song2",
                        "name": "Track Two",
                        "duration": 180,
                        "album": {"id": "1017247", "name": "3 Nights 4 Days"},
                        "artists": {"primary": [{"name": "Daboo Malik"}]},
                    },
                ],
            },
        }
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        album = self.client.get_album("1017247")

        assert album is not None
        assert len(album["tracks"]) == 2
        assert album["tracks"][0]["name"] == "Track One"
        self.client.session.get.assert_called_once_with(
            "https://saavn.test/api/albums",
            params={"id": "1017247"},
            timeout=20,
        )
        cache.store_entity.assert_called_once()

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_search_albums_skips_overwriting_existing_cache_entries(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_search_results.return_value = None
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = ALBUM_PAYLOAD
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        self.client.search_albums("Brahmastra", limit=5)

        cache.store_entities_bulk.assert_called_once_with(
            "jiosaavn",
            "album",
            [("album1", ALBUM_PAYLOAD["data"]["results"][0])],
            skip_if_exists=True,
        )


ARTIST_DETAIL_PAYLOAD = {
    "success": True,
    "data": {
        "id": "456863",
        "name": "Badshah",
        "type": "artist",
        "followerCount": 14263354,
        "url": "https://www.jiosaavn.com/artist/badshah/d4OwAaEcnD0_",
        "image": [{"quality": "500x500", "url": "https://example.com/badshah.jpg"}],
    },
}

ARTIST_ALBUMS_PAYLOAD = {
    "success": True,
    "data": {
        "total": 2,
        "albums": ALBUM_PAYLOAD["data"]["results"],
    },
}

ARTIST_ALBUMS_SEARCH_PAYLOAD = {
    "success": True,
    "data": {
        "total": 2,
        "results": ALBUM_PAYLOAD["data"]["results"],
    },
}


class TestJioSaavnClientArtistAndAlbumDetail:
    def setup_method(self):
        self.client = JioSaavnClient(base_url="https://saavn.test", session=MagicMock())

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_get_artist_fetches_and_caches(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_entity.return_value = None
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = ARTIST_DETAIL_PAYLOAD
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        artist = self.client.get_artist("456863")

        assert artist is not None
        assert artist["name"] == "Badshah"
        assert artist["followers"]["total"] == 14263354
        assert artist["images"][0]["url"] == "https://example.com/badshah.jpg"
        self.client.session.get.assert_called_once_with(
            "https://saavn.test/api/artists/456863",
            params={},
            timeout=20,
        )
        cache.store_entity.assert_called_once()

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_get_artist_albums_uses_album_search_by_artist_name(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_search_results.return_value = None
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = ARTIST_ALBUMS_SEARCH_PAYLOAD
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        albums = self.client.get_artist_albums("456863", limit=100, artist_name="Badshah")

        assert len(albums) == 1
        assert isinstance(albums[0], Album)
        assert albums[0].name == "Brahmastra"
        self.client.session.get.assert_called_once_with(
            "https://saavn.test/api/search/albums",
            params={"query": "Badshah", "page": 0, "limit": 100},
            timeout=20,
        )

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_get_artist_albums_falls_back_to_artist_feed_without_name(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_entity.return_value = None
        mock_get_cache.return_value = cache

        artist_fail = MagicMock()
        artist_fail.json.return_value = {"success": True, "data": None}
        artist_fail.raise_for_status.return_value = None
        albums_resp = MagicMock()
        albums_resp.json.return_value = ARTIST_ALBUMS_PAYLOAD
        albums_resp.raise_for_status.return_value = None
        self.client.session.get.side_effect = [artist_fail, albums_resp]

        albums = self.client.get_artist_albums("456863", limit=50)

        assert len(albums) == 1
        assert albums[0].name == "Brahmastra"
        assert self.client.session.get.call_args_list[-1] == (
            ("https://saavn.test/api/artists/456863/albums",),
            {"params": {"page": 0, "limit": 50}, "timeout": 20},
        )

    @patch("core.jiosaavn_client.get_metadata_cache")
    @patch("core.jiosaavn_client._rate_limit")
    def test_get_album_tracks_wraps_track_items(self, _rate_limit, mock_get_cache):
        cache = MagicMock()
        cache.get_entity.return_value = None
        mock_get_cache.return_value = cache

        response = MagicMock()
        response.json.return_value = {
            "success": True,
            "data": {
                "id": "album1",
                "name": "Brahmastra",
                "year": 2022,
                "type": "album",
                "songCount": 1,
                "songs": [SONG_PAYLOAD["data"]["results"][0]],
            },
        }
        response.raise_for_status.return_value = None
        self.client.session.get.return_value = response

        tracks_payload = self.client.get_album_tracks("album1")

        assert tracks_payload is not None
        assert len(tracks_payload["items"]) == 1
        assert tracks_payload["items"][0]["artists"] == [{"name": "Arijit Singh"}, {"name": "Pritam"}]


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
