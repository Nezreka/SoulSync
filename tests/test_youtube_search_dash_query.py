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

    tracks, albums = asyncio.run(client.search("-4WUHJRhvrM"))

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

    results = asyncio.run(client.search_videos("-4WUHJRhvrM", max_results=8))

    assert captured == [r"ytsearch8:\-4WUHJRhvrM"]
    assert [r.video_id for r in results] == ["-4WUHJRhvrM"]
