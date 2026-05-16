"""Unit tests for core/amazon_client.py.

All network I/O is mocked via a fake session — no real T2Tunes instance needed.

Run from project root:
    python -m pytest tests/tools/test_amazon_client.py -v
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

# Make sure project root is importable when running tests directly.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.amazon_client import (
    Album,
    AmazonClient,
    AmazonClientError,
    Artist,
    T2TunesSearchItem,
    T2TunesStreamInfo,
    Track,
    _rate_limit,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TRACK_DOC = {
    "asin": "B09XYZ1234",
    "title": "Not Like Us",
    "artistName": "Kendrick Lamar",
    "__type": "track",
    "albumName": "GNX",
    "albumAsin": "B0ABCDE123",
    "duration": 217,
    "isrc": "USRC12345678",
}

ALBUM_DOC = {
    "asin": "B0ABCDE123",
    "albumAsin": "B0ABCDE123",
    "title": "GNX",
    "albumName": "GNX",
    "artistName": "Kendrick Lamar",
    "__type": "album",
    "duration": 0,
}

SEARCH_RESPONSE_TRACKS = {
    "results": [
        {
            "hits": [
                {"document": TRACK_DOC},
                {
                    "document": {
                        "asin": "B09XYZ5678",
                        "title": "euphoria",
                        "artistName": "Kendrick Lamar",
                        "__type": "track",
                        "albumName": "euphoria",
                        "albumAsin": "B0ABCDE456",
                        "duration": 480,
                        "isrc": "USRC87654321",
                    }
                },
            ]
        }
    ]
}

SEARCH_RESPONSE_ALBUMS = {
    "results": [{"hits": [{"document": ALBUM_DOC}]}]
}

SEARCH_RESPONSE_MIXED = {
    "results": [
        {
            "hits": [
                {"document": TRACK_DOC},
                {"document": ALBUM_DOC},
            ]
        }
    ]
}

ALBUM_METADATA_RESPONSE = {
    "albumList": [
        {
            "asin": "B0ABCDE123",
            "title": "GNX",
            "image": "https://example.com/cover.jpg",
            "trackCount": 12,
            "label": "pgLang/Interscope",
            "artistName": "Kendrick Lamar",
        }
    ]
}

MEDIA_RESPONSE_FLAC = {
    "asin": "B09XYZ1234",
    "streamable": True,
    "decryptionKey": None,
    "streamInfo": {
        "codec": "FLAC",
        "format": "FLAC",
        "sampleRate": 44100,
        "streamUrl": "https://cdn.example.com/track.flac",
    },
    "tags": {
        "title": "Not Like Us",
        "artist": "Kendrick Lamar",
        "album": "GNX",
        "isrc": "USRC12345678",
        "trackNumber": "3",
        "discNumber": "1",
    },
}

MEDIA_RESPONSE_HIRES = {
    "asin": "B09XYZ1234",
    "streamable": True,
    "decryptionKey": "somekey",
    "streamInfo": {
        "codec": "FLAC",
        "format": "FLAC",
        "sampleRate": 96000,
        "streamUrl": "https://cdn.example.com/track-hires.flac",
    },
    "tags": {
        "title": "Not Like Us",
        "artist": "Kendrick Lamar",
        "album": "GNX",
        "isrc": "USRC12345678",
    },
}

STATUS_UP = {"amazonMusic": "up", "version": "1.0"}
STATUS_DOWN = {"amazonMusic": "down", "version": "1.0"}


def _mock_response(data: Any, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 400
    resp.json.return_value = data
    resp.text = json.dumps(data)
    if status_code >= 400:
        from requests import HTTPError
        exc = HTTPError(response=resp)
        resp.raise_for_status.side_effect = exc
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_client(response_map: Optional[Dict[str, Any]] = None) -> AmazonClient:
    """Build an AmazonClient with a fake session.

    response_map: path substring → response data (first match wins).
    """
    session = MagicMock()

    def _get(url, params=None, timeout=None, **_):
        if response_map:
            for key, data in response_map.items():
                if key in url:
                    if isinstance(data, Exception):
                        raise data
                    return _mock_response(data)
        return _mock_response({"error": "no mock for " + url}, 404)

    session.get.side_effect = _get
    with patch("core.amazon_client._rate_limit"):
        with patch("core.amazon_client.config_manager") as cfg:
            cfg.get.return_value = ""
            client = AmazonClient(
                base_url="https://test.t2tunes.local",
                country="US",
                session=session,
            )
    client.session = session
    return client


# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------

class TestTrackDataclass:
    def test_from_search_hit_basic(self):
        t = Track.from_search_hit(TRACK_DOC)
        assert t.id == "B09XYZ1234"
        assert t.name == "Not Like Us"
        assert t.artists == ["Kendrick Lamar"]
        assert t.album == "GNX"
        assert t.duration_ms == 217_000
        assert t.isrc == "USRC12345678"
        assert t.popularity == 0

    def test_from_search_hit_missing_fields(self):
        t = Track.from_search_hit({})
        assert t.id == ""
        assert t.name == ""
        assert t.artists == ["Unknown Artist"]
        assert t.duration_ms == 0
        assert t.isrc is None

    def test_from_stream_info(self):
        stream = T2TunesStreamInfo(
            asin="B09XYZ1234",
            streamable=True,
            codec="FLAC",
            format="FLAC",
            sample_rate=44100,
            stream_url="https://cdn.example.com/track.flac",
            decryption_key=None,
            title="Not Like Us",
            artist="Kendrick Lamar",
            album="GNX",
            isrc="USRC12345678",
        )
        t = Track.from_stream_info(stream)
        assert t.id == "B09XYZ1234"
        assert t.name == "Not Like Us"
        assert t.artists == ["Kendrick Lamar"]
        assert t.isrc == "USRC12345678"

    def test_from_stream_info_empty_artist(self):
        stream = T2TunesStreamInfo(
            asin="B1",
            streamable=True,
            codec="FLAC",
            format="FLAC",
            sample_rate=44100,
            stream_url="https://cdn.example.com/t.flac",
            decryption_key=None,
        )
        t = Track.from_stream_info(stream)
        assert t.artists == ["Unknown Artist"]


class TestArtistDataclass:
    def test_from_name(self):
        a = Artist.from_name("Kendrick Lamar")
        assert a.id == "kendrick_lamar"
        assert a.name == "Kendrick Lamar"
        assert a.genres == []
        assert a.followers == 0

    def test_from_name_special_chars(self):
        a = Artist.from_name("AC/DC")
        assert "ac" in a.id


class TestAlbumDataclass:
    def test_from_search_hit(self):
        al = Album.from_search_hit(ALBUM_DOC)
        assert al.id == "B0ABCDE123"
        assert al.name == "GNX"
        assert al.artists == ["Kendrick Lamar"]
        assert al.album_type == "album"

    def test_from_search_hit_fallback_asin(self):
        al = Album.from_search_hit({"asin": "B0001", "albumName": "Test", "artistName": "X"})
        assert al.id == "B0001"

    def test_from_metadata(self):
        meta = ALBUM_METADATA_RESPONSE["albumList"][0]
        al = Album.from_metadata(meta, asin="B0ABCDE123")
        assert al.id == "B0ABCDE123"
        assert al.name == "GNX"
        assert al.total_tracks == 12
        assert al.image_url == "https://example.com/cover.jpg"


# ---------------------------------------------------------------------------
# T2TunesSearchItem helpers
# ---------------------------------------------------------------------------

class TestT2TunesSearchItem:
    def test_is_track(self):
        item = T2TunesSearchItem(
            asin="A1", title="T", artist_name="X", item_type="MusicTrack"
        )
        assert item.is_track is True
        assert item.is_album is False

    def test_is_album(self):
        item = T2TunesSearchItem(
            asin="A1", title="T", artist_name="X", item_type="MusicAlbum"
        )
        assert item.is_album is True
        assert item.is_track is False

    def test_ambiguous_type(self):
        item = T2TunesSearchItem(
            asin="A1", title="T", artist_name="X", item_type="Unknown"
        )
        assert item.is_track is False
        assert item.is_album is False


# ---------------------------------------------------------------------------
# _iter_search_items static method
# ---------------------------------------------------------------------------

class TestIterSearchItems:
    def test_parses_tracks(self):
        items = list(AmazonClient._iter_search_items(SEARCH_RESPONSE_TRACKS))
        assert len(items) == 2
        assert items[0].asin == "B09XYZ1234"
        assert items[0].title == "Not Like Us"
        assert items[0].is_track

    def test_parses_albums(self):
        items = list(AmazonClient._iter_search_items(SEARCH_RESPONSE_ALBUMS))
        assert len(items) == 1
        assert items[0].is_album

    def test_skips_missing_asin(self):
        resp = {"results": [{"hits": [{"document": {"title": "No ASIN"}}]}]}
        items = list(AmazonClient._iter_search_items(resp))
        assert items == []

    def test_empty_results(self):
        items = list(AmazonClient._iter_search_items({"results": []}))
        assert items == []

    def test_wrong_type_raises(self):
        with pytest.raises(AmazonClientError):
            list(AmazonClient._iter_search_items(["not", "a", "dict"]))

    def test_skips_malformed_hits(self):
        resp = {
            "results": [
                {
                    "hits": [
                        "not_a_dict",
                        {"document": None},
                        {"document": {"asin": "B1", "__type": "track", "title": "T", "artistName": "A"}},
                    ]
                }
            ]
        }
        items = list(AmazonClient._iter_search_items(resp))
        assert len(items) == 1
        assert items[0].asin == "B1"


# ---------------------------------------------------------------------------
# _parse_stream_info static method
# ---------------------------------------------------------------------------

class TestParseStreamInfo:
    def test_flac_stream(self):
        s = AmazonClient._parse_stream_info(MEDIA_RESPONSE_FLAC)
        assert s.asin == "B09XYZ1234"
        assert s.streamable is True
        assert s.codec == "FLAC"
        assert s.sample_rate == 44100
        assert s.stream_url == "https://cdn.example.com/track.flac"
        assert s.has_decryption_key is False
        assert s.title == "Not Like Us"
        assert s.isrc == "USRC12345678"

    def test_hires_with_key(self):
        s = AmazonClient._parse_stream_info(MEDIA_RESPONSE_HIRES)
        assert s.sample_rate == 96000
        assert s.has_decryption_key is True

    def test_typo_stremeable(self):
        data = {
            "asin": "B1",
            "stremeable": True,  # typo variant
            "streamInfo": {"codec": "OPUS", "format": "OPUS", "streamUrl": "https://x.com/t.opus"},
            "tags": {},
        }
        s = AmazonClient._parse_stream_info(data)
        assert s.streamable is True
        assert s.codec == "OPUS"

    def test_missing_stream_info(self):
        s = AmazonClient._parse_stream_info({"asin": "B1"})
        assert s.stream_url == ""
        assert s.codec == ""
        assert s.sample_rate is None
        assert s.has_decryption_key is False


# ---------------------------------------------------------------------------
# AmazonClient — HTTP layer
# ---------------------------------------------------------------------------

class TestStatus:
    def test_success(self):
        client = _make_client({"/api/status": STATUS_UP})
        with patch("core.amazon_client._rate_limit"):
            result = client.status()
        assert result["amazonMusic"] == "up"

    def test_http_error_raises(self):
        client = _make_client()
        client.session.get.side_effect = None
        client.session.get.return_value = _mock_response({}, 503)
        with pytest.raises(AmazonClientError, match="HTTP 503"):
            client.status()

    def test_non_json_raises(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("not json")
        resp.text = "<html>error</html>"
        client = _make_client()
        client.session.get.side_effect = None
        client.session.get.return_value = resp
        with pytest.raises(AmazonClientError, match="not JSON"):
            client.status()


class TestIsAuthenticated:
    def test_true_when_up(self):
        client = _make_client({"/api/status": STATUS_UP})
        assert client.is_authenticated() is True

    def test_false_when_down(self):
        client = _make_client({"/api/status": STATUS_DOWN})
        assert client.is_authenticated() is False

    def test_false_on_error(self):
        from requests import RequestException
        client = _make_client()
        client.session.get.side_effect = RequestException("network error")
        assert client.is_authenticated() is False


class TestReloadConfig:
    def test_reloads_fields(self):
        with patch("core.amazon_client.config_manager") as cfg:
            cfg.get.side_effect = lambda key, default="": {
                "amazon.base_url": "https://new.instance.local",
                "amazon.country": "GB",
                "amazon.preferred_codec": "opus",
            }.get(key, default)
            client = AmazonClient(session=MagicMock())
            client.reload_config()
        assert "new.instance.local" in client.base_url
        assert client.country == "GB"
        assert client.preferred_codec == "opus"


# ---------------------------------------------------------------------------
# AmazonClient — search_raw
# ---------------------------------------------------------------------------

class TestSearchRaw:
    def test_returns_items(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            items = client.search_raw("Kendrick Lamar")
        assert len(items) == 2

    def test_passes_country(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            client.search_raw("test", types="track")
        call_kwargs = client.session.get.call_args
        assert "country" in str(call_kwargs)

    def test_network_error_raises(self):
        from requests import RequestException
        client = _make_client()
        client.session.get.side_effect = RequestException("timeout")
        with pytest.raises(AmazonClientError):
            client.search_raw("test")


# ---------------------------------------------------------------------------
# AmazonClient — search_tracks / search_artists / search_albums
# ---------------------------------------------------------------------------

class TestSearchTracks:
    def test_returns_track_list(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            tracks = client.search_tracks("Kendrick Lamar")
        assert len(tracks) == 2
        assert all(isinstance(t, Track) for t in tracks)

    def test_respects_limit(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            tracks = client.search_tracks("Kendrick Lamar", limit=1)
        assert len(tracks) == 1

    def test_ignores_album_hits(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_ALBUMS})
        with patch("core.amazon_client._rate_limit"):
            tracks = client.search_tracks("GNX")
        assert tracks == []

    def test_track_fields(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            tracks = client.search_tracks("Kendrick")
        t = tracks[0]
        assert t.name == "Not Like Us"
        assert t.artists == ["Kendrick Lamar"]
        assert t.album == "GNX"
        assert t.duration_ms == 217_000
        assert t.isrc == "USRC12345678"


class TestSearchArtists:
    def test_returns_unique_artists(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            artists = client.search_artists("Kendrick")
        # Both tracks are by Kendrick Lamar — should deduplicate
        assert len(artists) == 1
        assert artists[0].name == "Kendrick Lamar"

    def test_returns_artist_dataclass(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            artists = client.search_artists("Kendrick")
        assert isinstance(artists[0], Artist)

    def test_artist_image_from_album(self):
        resp = {
            "results": [{"hits": [
                {"document": {"asin": "A1", "title": "T1", "artistName": "Kendrick Lamar",
                               "__type": "track", "albumAsin": "B0ABCDE123"}},
            ]}]
        }
        client = _make_client({
            "amazon-music/search": resp,
            "amazon-music/metadata": ALBUM_METADATA_RESPONSE,
        })
        with patch("core.amazon_client._rate_limit"):
            artists = client.search_artists("Kendrick")
        assert artists[0].image_url == "https://example.com/cover.jpg"

    def test_deduplicates_feat_credits(self):
        resp = {
            "results": [
                {
                    "hits": [
                        {"document": {"asin": "A1", "title": "T1", "artistName": "Kendrick Lamar", "__type": "track"}},
                        {"document": {"asin": "A2", "title": "T2", "artistName": "Kendrick Lamar feat. SZA", "__type": "track"}},
                        {"document": {"asin": "A3", "title": "T3", "artistName": "Kendrick Lamar ft. Drake", "__type": "track"}},
                        {"document": {"asin": "A4", "title": "T4", "artistName": "SZA featuring Kendrick Lamar", "__type": "track"}},
                    ]
                }
            ]
        }
        client = _make_client({"amazon-music/search": resp})
        with patch("core.amazon_client._rate_limit"):
            artists = client.search_artists("Kendrick")
        names = [a.name for a in artists]
        assert "Kendrick Lamar" in names
        assert "SZA" in names
        assert len(artists) == 2

    def test_respects_limit(self):
        resp = {
            "results": [
                {
                    "hits": [
                        {
                            "document": {
                                "asin": f"B{i}",
                                "title": f"Song {i}",
                                "artistName": f"Artist {i}",
                                "__type": "track",
                            }
                        }
                        for i in range(10)
                    ]
                }
            ]
        }
        client = _make_client({"amazon-music/search": resp})
        with patch("core.amazon_client._rate_limit"):
            artists = client.search_artists("Various", limit=3)
        assert len(artists) == 3


class TestSearchAlbums:
    def test_returns_albums(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_ALBUMS})
        with patch("core.amazon_client._rate_limit"):
            albums = client.search_albums("GNX")
        assert len(albums) == 1
        assert isinstance(albums[0], Album)
        assert albums[0].id == "B0ABCDE123"

    def test_deduplicates_by_asin(self):
        resp = {
            "results": [
                {
                    "hits": [
                        {"document": {**ALBUM_DOC}},
                        {"document": {**ALBUM_DOC}},  # duplicate
                    ]
                }
            ]
        }
        client = _make_client({"amazon-music/search": resp})
        with patch("core.amazon_client._rate_limit"):
            albums = client.search_albums("GNX")
        assert len(albums) == 1

    def test_ignores_track_hits(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            albums = client.search_albums("Kendrick")
        assert albums == []

    def test_strips_explicit_from_album_name(self):
        resp = {
            "results": [{"hits": [
                {"document": {**ALBUM_DOC, "albumName": "GNX (Explicit)", "title": "GNX (Explicit)"}},
            ]}]
        }
        client = _make_client({"amazon-music/search": resp})
        with patch("core.amazon_client._rate_limit"):
            albums = client.search_albums("GNX")
        assert albums[0].name == "GNX"

    def test_keeps_clean_suffix(self):
        resp = {
            "results": [{"hits": [
                {"document": {**ALBUM_DOC, "albumName": "GNX [Clean]", "title": "GNX [Clean]"}},
            ]}]
        }
        client = _make_client({"amazon-music/search": resp})
        with patch("core.amazon_client._rate_limit"):
            albums = client.search_albums("GNX")
        assert albums[0].name == "GNX [Clean]"

    def test_deduplicates_explicit_clean_as_separate(self):
        resp = {
            "results": [{"hits": [
                {"document": {**ALBUM_DOC, "asin": "B1", "albumAsin": "B1", "albumName": "GNX (Explicit)", "title": "GNX (Explicit)"}},
                {"document": {**ALBUM_DOC, "asin": "B2", "albumAsin": "B2", "albumName": "GNX [Clean]", "title": "GNX [Clean]"}},
            ]}]
        }
        client = _make_client({"amazon-music/search": resp})
        with patch("core.amazon_client._rate_limit"):
            albums = client.search_albums("GNX")
        names = [a.name for a in albums]
        assert "GNX" in names        # explicit stripped
        assert "GNX [Clean]" in names
        assert len(albums) == 2


# ---------------------------------------------------------------------------
# AmazonClient — album_metadata / media_from_asin
# ---------------------------------------------------------------------------

class TestAlbumMetadata:
    def test_returns_dict(self):
        client = _make_client({"amazon-music/metadata": ALBUM_METADATA_RESPONSE})
        with patch("core.amazon_client._rate_limit"):
            meta = client.album_metadata("B0ABCDE123")
        assert "albumList" in meta
        assert meta["albumList"][0]["title"] == "GNX"


class TestMediaFromAsin:
    def test_list_response(self):
        client = _make_client({"amazon-music/media-from-asin": [MEDIA_RESPONSE_FLAC]})
        with patch("core.amazon_client._rate_limit"):
            streams = client.media_from_asin("B09XYZ1234")
        assert len(streams) == 1
        assert isinstance(streams[0], T2TunesStreamInfo)
        assert streams[0].codec == "FLAC"

    def test_single_dict_response(self):
        client = _make_client({"amazon-music/media-from-asin": MEDIA_RESPONSE_FLAC})
        with patch("core.amazon_client._rate_limit"):
            streams = client.media_from_asin("B09XYZ1234")
        assert len(streams) == 1

    def test_invalid_response_raises(self):
        client = _make_client({"amazon-music/media-from-asin": "not a list or dict"})
        with pytest.raises(AmazonClientError, match="Unexpected media"):
            client.media_from_asin("B09XYZ1234")

    def test_uses_preferred_codec(self):
        client = _make_client({"amazon-music/media-from-asin": [MEDIA_RESPONSE_FLAC]})
        client.preferred_codec = "opus"
        with patch("core.amazon_client._rate_limit"):
            client.media_from_asin("B09XYZ1234")
        call_kwargs = str(client.session.get.call_args)
        assert "opus" in call_kwargs

    def test_codec_override(self):
        client = _make_client({"amazon-music/media-from-asin": [MEDIA_RESPONSE_FLAC]})
        with patch("core.amazon_client._rate_limit"):
            client.media_from_asin("B09XYZ1234", codec="eac3")
        call_kwargs = str(client.session.get.call_args)
        assert "eac3" in call_kwargs


# ---------------------------------------------------------------------------
# AmazonClient — higher-level get_* methods
# ---------------------------------------------------------------------------

class TestGetTrackDetails:
    def _client(self):
        return _make_client({
            "amazon-music/media-from-asin": [MEDIA_RESPONSE_FLAC],
            "amazon-music/metadata": ALBUM_METADATA_RESPONSE,
        })

    def test_returns_spotify_compat_dict(self):
        client = self._client()
        with patch("core.amazon_client._rate_limit"):
            details = client.get_track_details("B09XYZ1234")
        assert details is not None
        assert details["id"] == "B09XYZ1234"
        assert details["name"] == "Not Like Us"
        assert "artists" in details
        assert "album" in details
        assert details["is_album_track"] is True

    def test_album_image_populated(self):
        client = self._client()
        with patch("core.amazon_client._rate_limit"):
            details = client.get_track_details("B09XYZ1234")
        assert details["album"]["images"][0]["url"] == "https://example.com/cover.jpg"

    def test_raw_data_present(self):
        client = self._client()
        with patch("core.amazon_client._rate_limit"):
            details = client.get_track_details("B09XYZ1234")
        assert "raw_data" in details
        assert details["raw_data"]["codec"] == "FLAC"
        assert details["raw_data"]["sample_rate"] == 44100

    def test_returns_none_on_empty_streams(self):
        client = _make_client({"amazon-music/media-from-asin": []})
        with patch("core.amazon_client._rate_limit"):
            assert client.get_track_details("B09XYZ1234") is None

    def test_returns_none_on_api_error(self):
        client = _make_client()
        client.session.get.return_value = _mock_response({}, 500)
        with patch("core.amazon_client._rate_limit"):
            assert client.get_track_details("B09XYZ1234") is None

    def test_graceful_when_metadata_fails(self):
        session = MagicMock()
        call_count = {"n": 0}

        def _get(url, params=None, timeout=None, **_):
            call_count["n"] += 1
            if "media-from-asin" in url:
                return _mock_response([MEDIA_RESPONSE_FLAC])
            return _mock_response({}, 500)

        session.get.side_effect = _get
        client = AmazonClient(base_url="https://test.local", session=session)
        with patch("core.amazon_client._rate_limit"):
            details = client.get_track_details("B09XYZ1234")
        assert details is not None
        assert details["album"]["images"] == []


class TestGetAlbum:
    def _client(self):
        return _make_client({"amazon-music/metadata": ALBUM_METADATA_RESPONSE,
                              "amazon-music/media-from-asin": [MEDIA_RESPONSE_FLAC]})

    def test_returns_album_dict(self):
        client = self._client()
        with patch("core.amazon_client._rate_limit"):
            album = client.get_album("B0ABCDE123")
        assert album is not None
        assert album["id"] == "B0ABCDE123"
        assert album["name"] == "GNX"
        assert album["total_tracks"] == 12
        assert album["label"] == "pgLang/Interscope"

    def test_includes_tracks_by_default(self):
        client = self._client()
        with patch("core.amazon_client._rate_limit"):
            album = client.get_album("B0ABCDE123")
        assert "tracks" in album
        assert isinstance(album["tracks"], dict)
        assert "items" in album["tracks"]

    def test_excludes_tracks_when_flag_false(self):
        client = _make_client({"amazon-music/metadata": ALBUM_METADATA_RESPONSE})
        with patch("core.amazon_client._rate_limit"):
            album = client.get_album("B0ABCDE123", include_tracks=False)
        assert "tracks" not in album

    def test_returns_none_on_empty_albumlist(self):
        client = _make_client({"amazon-music/metadata": {"albumList": []}})
        with patch("core.amazon_client._rate_limit"):
            assert client.get_album("B0ABCDE123") is None

    def test_returns_none_on_api_error(self):
        client = _make_client()
        client.session.get.return_value = _mock_response({}, 500)
        with patch("core.amazon_client._rate_limit"):
            assert client.get_album("B0ABCDE123") is None


class TestGetAlbumTracks:
    def test_returns_spotify_pagination(self):
        client = _make_client({"amazon-music/media-from-asin": [MEDIA_RESPONSE_FLAC]})
        with patch("core.amazon_client._rate_limit"):
            result = client.get_album_tracks("B09XYZ1234")
        assert result is not None
        assert "items" in result
        assert result["total"] == 1
        assert result["next"] is None
        assert result["limit"] == 50

    def test_item_fields(self):
        client = _make_client({"amazon-music/media-from-asin": [MEDIA_RESPONSE_FLAC]})
        with patch("core.amazon_client._rate_limit"):
            result = client.get_album_tracks("B09XYZ1234")
        item = result["items"][0]
        assert item["id"] == "B09XYZ1234"
        assert item["name"] == "Not Like Us"
        assert item["isrc"] == "USRC12345678"
        assert item["track_number"] == 3
        assert item["disc_number"] == 1

    def test_returns_none_on_api_error(self):
        client = _make_client()
        client.session.get.return_value = _mock_response({}, 500)
        with patch("core.amazon_client._rate_limit"):
            assert client.get_album_tracks("B09XYZ1234") is None


class TestGetArtist:
    def test_returns_artist_dict(self):
        client = _make_client({"amazon-music/search": SEARCH_RESPONSE_TRACKS})
        with patch("core.amazon_client._rate_limit"):
            artist = client.get_artist("Kendrick Lamar")
        assert artist is not None
        assert artist["name"] == "Kendrick Lamar"
        assert "genres" in artist
        assert "followers" in artist

    def test_exact_match_preferred(self):
        resp = {
            "results": [
                {
                    "hits": [
                        {"document": {"asin": "A1", "title": "T1", "artistName": "Kendrick Lamar", "__type": "track"}},
                        {"document": {"asin": "A2", "title": "T2", "artistName": "Kendrick Lamar Jr.", "__type": "track"}},
                    ]
                }
            ]
        }
        client = _make_client({"amazon-music/search": resp})
        with patch("core.amazon_client._rate_limit"):
            artist = client.get_artist("Kendrick Lamar")
        assert artist["name"] == "Kendrick Lamar"

    def test_returns_none_when_no_match(self):
        client = _make_client({"amazon-music/search": {"results": []}})
        with patch("core.amazon_client._rate_limit"):
            assert client.get_artist("Nobody") is None

    def test_returns_none_on_error(self):
        client = _make_client()
        client.session.get.return_value = _mock_response({}, 500)
        with patch("core.amazon_client._rate_limit"):
            assert client.get_artist("Kendrick") is None


class TestGetArtistAlbums:
    def test_returns_filtered_albums(self):
        resp = {
            "results": [
                {
                    "hits": [
                        {
                            "document": {
                                "asin": "B0ABCDE123",
                                "albumAsin": "B0ABCDE123",
                                "title": "GNX",
                                "albumName": "GNX",
                                "artistName": "Kendrick Lamar",
                                "__type": "album",
                            }
                        },
                        {
                            "document": {
                                "asin": "B0ZZZ",
                                "albumAsin": "B0ZZZ",
                                "title": "Other Album",
                                "albumName": "Other Album",
                                "artistName": "Another Artist",
                                "__type": "album",
                            }
                        },
                    ]
                }
            ]
        }
        client = _make_client({"amazon-music/search": resp})
        with patch("core.amazon_client._rate_limit"):
            albums = client.get_artist_albums("Kendrick Lamar")
        assert len(albums) == 1
        assert albums[0].name == "GNX"

    def test_respects_limit(self):
        hits = [
            {
                "document": {
                    "asin": f"B{i}",
                    "albumAsin": f"B{i}",
                    "albumName": f"Album {i}",
                    "artistName": "Kendrick Lamar",
                    "__type": "album",
                }
            }
            for i in range(20)
        ]
        client = _make_client({"amazon-music/search": {"results": [{"hits": hits}]}})
        with patch("core.amazon_client._rate_limit"):
            albums = client.get_artist_albums("Kendrick Lamar", limit=5)
        assert len(albums) == 5

    def test_returns_empty_on_error(self):
        client = _make_client()
        client.session.get.return_value = _mock_response({}, 500)
        with patch("core.amazon_client._rate_limit"):
            assert client.get_artist_albums("Kendrick") == []


class TestGetTrackFeatures:
    def test_always_none(self):
        client = AmazonClient(session=MagicMock())
        assert client.get_track_features("B09XYZ1234") is None


# ---------------------------------------------------------------------------
# Rate-limit enforcement
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_enforces_min_interval(self):
        import core.amazon_client as mod
        original = mod._last_api_call
        sleeps = []

        def fake_sleep(t):
            sleeps.append(t)

        with patch("core.amazon_client.time") as mock_time:
            mock_time.monotonic.return_value = mod._last_api_call + 0.1
            mock_time.sleep = fake_sleep
            with patch("core.amazon_client.api_call_tracker"):
                _rate_limit()
        # Should have slept since interval not elapsed
        assert len(sleeps) > 0
        mod._last_api_call = original

    def test_no_sleep_when_interval_elapsed(self):
        import core.amazon_client as mod
        original = mod._last_api_call
        sleeps = []

        with patch("core.amazon_client.time") as mock_time:
            mock_time.monotonic.return_value = mod._last_api_call + 10.0
            mock_time.sleep = lambda t: sleeps.append(t)
            with patch("core.amazon_client.api_call_tracker"):
                _rate_limit()
        assert sleeps == []
        mod._last_api_call = original
