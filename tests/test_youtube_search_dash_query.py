"""Regression tests for YouTube searches whose query starts with ``-``.

YouTube video IDs can start with a dash. yt-dlp's ``ytsearchN:`` parser
interprets a leading dash as search syntax unless escaped, so manual
searches for those IDs used to fan out into unrelated results.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from core import youtube_client
from core.youtube_client import YouTubeClient

import pytest


@pytest.fixture(autouse=True)
def _reencode_off(monkeypatch):
    monkeypatch.setattr(
        youtube_client, "_youtube_transcode_settings",
        lambda: (False, "mp3", "320"),
    )


def _stub_catalog(monkeypatch, hits=None):
    monkeypatch.setattr(
        "core.youtube_music_meta.search_ytmusic_songs",
        lambda *a, **k: hits,
    )


def _run(coro):
    loop = asyncio.new_event_loop()

    async def _drain_with_heartbeat():
        task = loop.create_task(coro)
        while not task.done():
            await asyncio.sleep(0.01)
        return task.result()

    try:
        return loop.run_until_complete(_drain_with_heartbeat())
    finally:
        loop.close()


def test_escape_ytsearch_query_handles_leading_dash():
    assert YouTubeClient._escape_ytsearch_query("-4WUHJRhvrM") == r"\-4WUHJRhvrM"
    assert YouTubeClient._escape_ytsearch_query(r"\-4WUHJRhvrM") == r"\-4WUHJRhvrM"
    assert YouTubeClient._escape_ytsearch_query("Yo-Yo Ma") == "Yo-Yo Ma"


def test_search_escapes_leading_dash_before_yt_dlp(monkeypatch):
    captured = []

    class _FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, search_query, download=False):
            captured.append(search_query)
            return {"entries": [{"id": "-4WUHJRhvrM", "title": "Unaccompanied Cello"}]}

    monkeypatch.setattr(youtube_client.yt_dlp, "YoutubeDL", _FakeYoutubeDL)

    client = YouTubeClient.__new__(YouTubeClient)
    monkeypatch.setattr(client, "_get_best_audio_format", lambda formats: None)
    monkeypatch.setattr(
        client,
        "_youtube_to_track_result",
        lambda entry, best_audio: SimpleNamespace(filename=entry["title"]),
    )
    _stub_catalog(monkeypatch)

    tracks, albums = _run(client.search("-4WUHJRhvrM"))

    assert captured == [r"ytsearch50:\-4WUHJRhvrM"]
    assert len(tracks) == 1
    assert albums == []


def test_search_videos_escapes_leading_dash_before_yt_dlp(monkeypatch):
    captured = []

    class _FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, search_query, download=False):
            captured.append(search_query)
            return {
                "entries": [{
                    "id": "-4WUHJRhvrM",
                    "title": "Unaccompanied Cello",
                    "duration": 152,
                    "uploader": "Yo-Yo Ma",
                }]
            }

    monkeypatch.setattr(youtube_client.yt_dlp, "YoutubeDL", _FakeYoutubeDL)
    client = YouTubeClient.__new__(YouTubeClient)

    results = _run(client.search_videos("-4WUHJRhvrM", max_results=8))

    assert captured == [r"ytsearch8:\-4WUHJRhvrM"]
    assert [r.video_id for r in results] == ["-4WUHJRhvrM"]


_CATALOG_HIT = {
    "id": "vid1",
    "name": "Example Track",
    "artists": ["Example Artist"],
    "album": "Example Album",
    "duration_ms": 225_000,
    "video_type": "MUSIC_VIDEO_TYPE_ATV",
    "thumbnail": "https://i.ytimg.com/vi/vid1/high.jpg",
    "url": "https://www.youtube.com/watch?v=vid1",
}


def test_search_catalog_hits_skip_yt_dlp_and_set_album(monkeypatch):
    _stub_catalog(monkeypatch, [_CATALOG_HIT])

    def _boom(*_a, **_k):
        raise AssertionError("yt-dlp must not run when catalog search returns hits")

    monkeypatch.setattr(youtube_client.yt_dlp, "YoutubeDL", _boom)
    client = YouTubeClient.__new__(YouTubeClient)
    tracks, albums = asyncio.run(client.search("Example Artist - Example Track"))

    assert albums == []
    assert len(tracks) == 1
    track = tracks[0]
    assert track.album == "Example Album"
    assert track.artist == "Example Artist"
    assert track.title == "Example Track"
    assert track.filename == "vid1||Example Track"
    assert track.duration == 225_000
    assert track.quality == "opus"
    assert track.bitrate == 160
    assert track._source_metadata == {"source": "youtube", "catalog": True}


def test_search_catalog_with_cookies_still_claims_opus_160(monkeypatch):
    _stub_catalog(monkeypatch, [_CATALOG_HIT])
    monkeypatch.setattr(
        youtube_client, "_resolve_cookie_opts",
        lambda: {"cookiefile": "/tmp/youtube_cookies.txt"},
    )

    def _boom(*_a, **_k):
        raise AssertionError("yt-dlp must not run when catalog search returns hits")

    monkeypatch.setattr(youtube_client.yt_dlp, "YoutubeDL", _boom)
    client = YouTubeClient.__new__(YouTubeClient)
    tracks, _ = asyncio.run(client.search("Example Artist - Example Track"))
    assert tracks[0].quality == "opus"
    assert tracks[0].bitrate == 160


def test_search_catalog_cookies_plus_reencode_claim_mp3_not_premium_opus(monkeypatch):
    _stub_catalog(monkeypatch, [_CATALOG_HIT])
    monkeypatch.setattr(
        youtube_client, "_resolve_cookie_opts",
        lambda: {"cookiefile": "/tmp/youtube_cookies.txt"},
    )
    monkeypatch.setattr(
        youtube_client, "_youtube_transcode_settings",
        lambda: (True, "mp3", "320"),
    )

    def _boom(*_a, **_k):
        raise AssertionError("yt-dlp must not run when catalog search returns hits")

    monkeypatch.setattr(youtube_client.yt_dlp, "YoutubeDL", _boom)
    client = YouTubeClient.__new__(YouTubeClient)
    tracks, _ = asyncio.run(client.search("Example Artist - Example Track"))
    assert tracks[0].quality == "mp3"
    assert tracks[0].bitrate == 320


def test_search_catalog_reencode_claims_converted_mp3(monkeypatch):
    _stub_catalog(monkeypatch, [_CATALOG_HIT])
    monkeypatch.setattr(
        youtube_client, "_youtube_transcode_settings",
        lambda: (True, "mp3", "320"),
    )

    def _boom(*_a, **_k):
        raise AssertionError("yt-dlp must not run when catalog search returns hits")

    monkeypatch.setattr(youtube_client.yt_dlp, "YoutubeDL", _boom)
    client = YouTubeClient.__new__(YouTubeClient)
    tracks, _ = asyncio.run(client.search("Example Artist - Example Track"))
    assert tracks[0].quality == "mp3"
    assert tracks[0].bitrate == 320
    assert tracks[0].filename == "vid1||Example Track"


def test_search_catalog_none_falls_through_to_ytsearch(monkeypatch):
    captured = []

    class _FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, search_query, download=False):
            captured.append(search_query)
            return {"entries": [{"id": "yt1", "title": "Fallback Title"}]}

    _stub_catalog(monkeypatch)
    monkeypatch.setattr(youtube_client.yt_dlp, "YoutubeDL", _FakeYoutubeDL)
    client = YouTubeClient.__new__(YouTubeClient)
    monkeypatch.setattr(client, "_get_best_audio_format", lambda formats: None)
    monkeypatch.setattr(
        client,
        "_youtube_to_track_result",
        lambda entry, best_audio: SimpleNamespace(filename=entry["title"]),
    )

    tracks, albums = asyncio.run(client.search("Artist - Title"))
    assert captured == ["ytsearch50:Artist - Title"]
    assert len(tracks) == 1
    assert albums == []


def test_search_use_catalog_false_ignores_catalog_hits(monkeypatch):
    captured = []

    class _FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, search_query, download=False):
            captured.append(search_query)
            return {"entries": [{"id": "yt1", "title": "Remix Video"}]}

    _stub_catalog(monkeypatch, [_CATALOG_HIT])
    monkeypatch.setattr(youtube_client.yt_dlp, "YoutubeDL", _FakeYoutubeDL)
    client = YouTubeClient.__new__(YouTubeClient)
    monkeypatch.setattr(client, "_get_best_audio_format", lambda formats: None)
    monkeypatch.setattr(
        client,
        "_youtube_to_track_result",
        lambda entry, best_audio: SimpleNamespace(filename=entry["title"]),
    )

    tracks, albums = asyncio.run(
        client.search("Artist - Remix", use_catalog=False),
    )
    assert captured == ["ytsearch50:Artist - Remix"]
    assert [t.filename for t in tracks] == ["Remix Video"]
    assert albums == []
