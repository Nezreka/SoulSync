"""§52.5: the manual-match candidate list surfaces follower/popularity data
for the providers that actually supply it (Spotify: both; Deezer: fan count
as followers) — data every Artist search hit already carries, just not
previously projected into the shared ``_search_service`` response."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import core.library.service_search as ss


def test_spotify_artist_search_includes_followers_and_popularity(monkeypatch):
    from core.spotify_client import Artist

    client = MagicMock()
    client.search_artists.return_value = [
        Artist(id="sp1", name="Drake", popularity=97, genres=["rap"],
               followers=54_000_000, image_url="https://img/drake.jpg"),
    ]
    client._fallback_source = "itunes"
    monkeypatch.setattr(ss, "spotify_enrichment_worker", SimpleNamespace(client=client))

    results = ss._search_service("spotify", "artist", "Drake")

    assert len(results) == 1
    assert results[0]["followers"] == 54_000_000
    assert results[0]["popularity"] == 97
    assert results[0]["provider"] == "spotify"


def test_spotify_artist_search_zero_stats_when_fallback_serves_the_hit(monkeypatch):
    """The shared Artist dataclass defaults followers/popularity to 0 when a
    non-Spotify fallback (iTunes here) actually served the search — the
    numeric id is how the existing `_detect_provider` already tells them
    apart; this just confirms the new fields don't break on that path."""
    from core.spotify_client import Artist

    client = MagicMock()
    client.search_artists.return_value = [
        Artist(id="123456", name="Drake", popularity=0, genres=[],
               followers=0, image_url=None),
    ]
    client._fallback_source = "itunes"
    monkeypatch.setattr(ss, "spotify_enrichment_worker", SimpleNamespace(client=client))

    results = ss._search_service("spotify", "artist", "Drake")

    assert results[0]["provider"] == "itunes"
    assert results[0]["followers"] == 0
    assert results[0]["popularity"] == 0


def test_deezer_artist_search_includes_fan_count_as_followers(monkeypatch):
    class _FakeResponse:
        def json(self):
            return {"data": [{"id": 123, "name": "Drake", "picture_medium": "https://img/dz.jpg",
                               "nb_fan": 12_000_000}]}

    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse())

    results = ss._search_service("deezer", "artist", "Drake")

    assert len(results) == 1
    assert results[0]["followers"] == 12_000_000
    assert results[0]["extra"] == "12000000 fans"
