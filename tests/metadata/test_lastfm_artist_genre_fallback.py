"""Tests for #1513: Last.fm artist tags as a genre fallback (opt-in)."""

import pytest

from core.metadata.source import (
    _lastfm_artist_genre_fallback,
    lfm_artist_tags_cache,
)


class FakeLFClient:
    """Fake LastFMClient.get_artist_top_tags."""

    def __init__(self, tags):
        self.tags = tags
        self.calls = 0

    def get_artist_top_tags(self, artist_name):
        self.calls += 1
        return self.tags


class FakeCfg:
    def __init__(self, data=None):
        self.data = data or {}

    def get(self, key, default=None):
        return self.data.get(key, default)


@pytest.fixture(autouse=True)
def clear_cache():
    lfm_artist_tags_cache.clear()
    yield
    lfm_artist_tags_cache.clear()


def test_fallback_returns_whitelisted_genres():
    client = FakeLFClient([
        {"name": "Rock", "count": "50"},
        {"name": "Alternative Rock", "count": "30"},
        {"name": "seen live", "count": "90"},  # high weight, not a genre
        {"name": "awesome", "count": "80"},    # high weight, not a genre
    ])
    result = _lastfm_artist_genre_fallback(client, "Scalene", FakeCfg())
    assert "Rock" in result
    assert "Alternative Rock" in result
    assert "seen live" not in result
    assert "awesome" not in result


def test_fallback_respects_weight_threshold():
    client = FakeLFClient([
        {"name": "Rock", "count": "5"},   # below threshold
        {"name": "Emo", "count": "15"},   # above threshold
    ])
    result = _lastfm_artist_genre_fallback(client, "Fresno", FakeCfg())
    assert "Emo" in result
    assert "Rock" not in result


def test_fallback_caches_per_artist():
    client = FakeLFClient([{"name": "Rock", "count": "50"}])
    _lastfm_artist_genre_fallback(client, "Scalene", FakeCfg())
    _lastfm_artist_genre_fallback(client, "Scalene", FakeCfg())
    _lastfm_artist_genre_fallback(client, "scalene", FakeCfg())  # normalized same
    assert client.calls == 1


def test_fallback_caches_empty_results():
    """Negative caching: a failed/empty lookup doesn't re-hit the network."""
    client = FakeLFClient([])
    _lastfm_artist_genre_fallback(client, "Unknown", FakeCfg())
    _lastfm_artist_genre_fallback(client, "Unknown", FakeCfg())
    assert client.calls == 1


def test_fallback_cache_keyed_by_whitelist():
    """Changing the whitelist doesn't serve stale cached genres."""
    client = FakeLFClient([{"name": "Rock", "count": "50"}])
    cfg1 = FakeCfg({"genre_whitelist.genres": ["Rock"]})
    cfg2 = FakeCfg({"genre_whitelist.genres": ["Pop"]})
    r1 = _lastfm_artist_genre_fallback(client, "Band", cfg1)
    r2 = _lastfm_artist_genre_fallback(client, "Band", cfg2)
    assert r1 == ["Rock"]
    assert r2 == []  # Rock not in Pop-only whitelist
    assert client.calls == 2


def test_fallback_empty_when_no_client():
    result = _lastfm_artist_genre_fallback(None, "Scalene", FakeCfg())
    assert result == []


def test_fallback_empty_when_no_artist():
    client = FakeLFClient([{"name": "Rock", "count": "50"}])
    assert _lastfm_artist_genre_fallback(client, "", FakeCfg()) == []
    assert _lastfm_artist_genre_fallback(client, None, FakeCfg()) == []


def test_fallback_empty_when_no_tags():
    client = FakeLFClient([])
    assert _lastfm_artist_genre_fallback(client, "Unknown", FakeCfg()) == []


def test_fallback_handles_client_error():
    class BrokenClient:
        def get_artist_top_tags(self, artist_name):
            raise RuntimeError("network down")

    result = _lastfm_artist_genre_fallback(BrokenClient(), "Scalene", FakeCfg())
    assert result == []


def test_fallback_caps_at_five():
    client = FakeLFClient([
        {"name": g, "count": "50"}
        for g in ["Rock", "Pop", "Jazz", "Blues", "Country", "Folk", "Metal"]
    ])
    result = _lastfm_artist_genre_fallback(client, "Various", FakeCfg())
    assert len(result) <= 5


def test_fallback_dedupes():
    client = FakeLFClient([
        {"name": "Rock", "count": "50"},
        {"name": "rock", "count": "40"},
        {"name": "ROCK", "count": "30"},
    ])
    result = _lastfm_artist_genre_fallback(client, "Band", FakeCfg())
    assert len(result) == 1
