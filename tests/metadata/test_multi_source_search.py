"""Unit tests for multi-source parallel metadata search and timeout enforcement."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List

from core.metadata.multi_source_search import (
    TrackQuery,
    search_all_sources,
)


@dataclass
class _DummyTrack:
    id: str
    name: str
    artists: List[str]
    album: str = "Test Album"
    duration_ms: int = 180000
    image_url: str = "http://example.com/art.jpg"


class _FastClient:
    def __init__(self, tracks: List[_DummyTrack]):
        self.tracks = tracks

    def search_tracks(self, query: str, limit: int = 10):
        return self.tracks


class _HangingClient:
    def __init__(self, delay_seconds: float = 3.0):
        self.delay_seconds = delay_seconds

    def search_tracks(self, query: str, limit: int = 10):
        time.sleep(self.delay_seconds)
        return [_DummyTrack(id="slow-1", name="Slow Track", artists=["Slow Artist"])]


def test_search_all_sources_fast_completion():
    query = TrackQuery(title="Come Together", artist="The Beatles", duration_ms=259000)
    spotify_track = _DummyTrack(id="sp-1", name="Come Together", artists=["The Beatles"], duration_ms=259000)
    itunes_track = _DummyTrack(id="it-1", name="Come Together", artists=["The Beatles"], duration_ms=258000)

    sources = [
        ("spotify", _FastClient([spotify_track])),
        ("itunes", _FastClient([itunes_track])),
    ]

    result = search_all_sources(query, sources, timeout_seconds=5.0)

    assert "spotify" in result.metadata_results
    assert "itunes" in result.metadata_results
    assert len(result.metadata_results["spotify"]) == 1
    assert len(result.metadata_results["itunes"]) == 1
    assert result.best_match is not None
    assert result.best_match["source"] in ("spotify", "itunes")


def test_search_all_sources_timeout_omits_slow_source_gracefully():
    query = TrackQuery(title="Come Together", artist="The Beatles", duration_ms=259000)
    fast_track = _DummyTrack(id="sp-1", name="Come Together", artists=["The Beatles"], duration_ms=259000)

    sources = [
        ("spotify", _FastClient([fast_track])),
        ("slow_provider", _HangingClient(delay_seconds=3.0)),
    ]

    t0 = time.time()
    result = search_all_sources(query, sources, timeout_seconds=0.3)
    elapsed = time.time() - t0

    # Ensure function did not wait for the 3.0s hanging client
    assert elapsed < 1.0, f"Search took {elapsed:.2f}s; expected < 1.0s"

    # Fast source returned results
    assert len(result.metadata_results["spotify"]) == 1
    assert result.metadata_results["spotify"][0]["id"] == "sp-1"

    # Slow source timed out and has empty results list, NOT missing key
    assert "slow_provider" in result.metadata_results
    assert result.metadata_results["slow_provider"] == []
    assert result.raw_tracks["slow_provider"] == []

    # Best match correctly chosen from completed provider
    assert result.best_match is not None
    assert result.best_match["source"] == "spotify"


def test_search_all_sources_all_slow_returns_empty_results():
    query = TrackQuery(title="Song", artist="Artist")
    sources = [
        ("slow_1", _HangingClient(delay_seconds=2.0)),
        ("slow_2", _HangingClient(delay_seconds=2.0)),
    ]

    t0 = time.time()
    result = search_all_sources(query, sources, timeout_seconds=0.2)
    elapsed = time.time() - t0

    assert elapsed < 0.8
    assert result.metadata_results["slow_1"] == []
    assert result.metadata_results["slow_2"] == []
    assert result.best_match is None


def test_search_all_sources_empty_sources():
    query = TrackQuery(title="Song", artist="Artist")
    result = search_all_sources(query, [])
    assert result.metadata_results == {}
    assert result.raw_tracks == {}
    assert result.best_match is None
