"""A premium-required 403 rests the official Spotify API for hours.

Spotify refuses every official call when the dev app's owner has no Premium,
while the token still refreshes, so the client believed it was connected.
Every artist search during an upgrade's credit pass paid a failed official
call and an error line before falling back. Now the first such 403 counts
the official API out, the way a rate-limit ban does, and Spotify Free serves.
"""

from unittest.mock import MagicMock

import pytest

import core.spotify_client as SC


class _Premium403(Exception):
    http_status = 403

    def __str__(self):
        return ("http status: 403, code: -1 - Active premium subscription required for the "
                "owner of the app. When the subscription status changes, it can take a few "
                "hours before requests are allowed again.")


class _Other403(Exception):
    http_status = 403

    def __str__(self):
        return "http status: 403 - Insufficient client scope"


@pytest.fixture(autouse=True)
def _reset_wall(monkeypatch):
    monkeypatch.setattr(SC, "_premium_wall_until", 0)
    yield


def _client():
    client = SC.SpotifyClient.__new__(SC.SpotifyClient)
    client.sp = MagicMock()
    return client


def test_the_premium_403_rests_the_official_api():
    assert SC._detect_and_set_rate_limit(_Premium403(), "search_artists") is False
    assert SC._premium_wall_active()
    assert _client().is_spotify_authenticated() is False


def test_another_403_does_not():
    SC._detect_and_set_rate_limit(_Other403(), "search_artists")
    assert not SC._premium_wall_active()


def test_the_wall_lifts_after_its_hours(monkeypatch):
    SC._detect_and_set_rate_limit(_Premium403(), "search_artists")
    monkeypatch.setattr(SC.time, "time", lambda: SC._premium_wall_until + 1)
    assert not SC._premium_wall_active()


def test_a_search_after_the_wall_never_asks_the_official_api_again(monkeypatch):
    client = _client()
    client.sp.search.side_effect = _Premium403()
    monkeypatch.setattr(client, "_free_active", lambda: SC._premium_wall_active())
    monkeypatch.setattr(client, "_free_available", lambda: False)
    monkeypatch.setattr(client, "_free_installed", lambda: False)
    fallback = MagicMock()
    fallback.search_artists.return_value = []
    monkeypatch.setattr(SC.SpotifyClient, "_fallback", property(lambda self: fallback))
    monkeypatch.setattr(SC, "get_metadata_cache",
                        lambda: MagicMock(get_search_results=lambda *a, **k: None))
    real_auth = SC.SpotifyClient.is_spotify_authenticated

    def authed(self):
        # authenticated as far as the token goes, until the wall says otherwise
        return False if SC._premium_wall_active() else True

    monkeypatch.setattr(SC.SpotifyClient, "is_spotify_authenticated", authed)
    client.search_artists("Treyy G, Trifo")
    client.search_artists("Mr.B, Weela")
    assert client.sp.search.call_count == 1
    monkeypatch.setattr(SC.SpotifyClient, "is_spotify_authenticated", real_auth)
