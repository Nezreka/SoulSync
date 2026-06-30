"""Popularity normalization + source cascade (pure)."""

from __future__ import annotations

from core.discovery.popularity import (
    fetch_artist_popularity,
    log_normalize_popularity,
    resolve_popularity,
)


class _SpotifyFree:
    def get_artist(self, aid):
        return {"followers": {"total": 5_000_000}}

    def search_artists(self, name):
        return [{"followers": {"total": 5_000_000}}]


class _LastFm:
    def get_artist_info(self, name):
        return {"stats": {"listeners": 1_000_000}}


class _Deezer:
    def get_artist_info(self, did):
        return {"followers": {"total": 500_000}}


# ── log_normalize_popularity ─────────────────────────────────────────────────
def test_log_normalize_endpoints_and_clamp():
    assert log_normalize_popularity(0) == 0.0
    assert log_normalize_popularity(-100) == 0.0
    assert log_normalize_popularity(None) == 0.0
    assert log_normalize_popularity(100) == 0.0            # floor (10^2)
    assert log_normalize_popularity(10_000_000) == 100.0   # ceiling (10^7)
    assert log_normalize_popularity(10**9) == 100.0        # above ceiling clamps to 100


def test_log_normalize_is_monotonic_and_midscale():
    # obscure stays low, megastar high — roughly Spotify-shaped
    assert log_normalize_popularity(1_000) < log_normalize_popularity(100_000) < log_normalize_popularity(5_000_000)
    assert log_normalize_popularity(1_000) < 25      # 1k listeners is still pretty obscure
    assert log_normalize_popularity(1_000_000) > 70  # 1M listeners is clearly popular


def test_log_normalize_custom_calibration():
    # 10 fans -> 0, 1M -> 100 (Deezer-style)
    assert log_normalize_popularity(10, floor_log=1.0, ceil_log=6.0) == 0.0
    assert log_normalize_popularity(1_000_000, floor_log=1.0, ceil_log=6.0) == 100.0


# ── resolve_popularity (the cascade) ─────────────────────────────────────────
def test_cascade_official_spotify_popularity_wins_including_zero():
    pop, src = resolve_popularity(spotify_popularity=86, spotify_followers=5_000_000,
                                  lastfm_listeners=5_000_000, deezer_fans=900_000)
    assert (pop, src) == (86.0, "spotify")
    # the curated 0-100 index, including 0, is a real value and wins over the raw-count sources.
    assert resolve_popularity(spotify_popularity=0, lastfm_listeners=5_000_000) == (0.0, "spotify")


def test_cascade_spotify_free_then_lastfm_then_deezer():
    # no official popularity -> Spotify Free followers (log-normalized)
    pop, src = resolve_popularity(spotify_followers=5_000_000, lastfm_listeners=1_000)
    assert src == "spotify_free" and pop > 70
    # no spotify at all -> Last.fm
    pop, src = resolve_popularity(lastfm_listeners=1_000_000, deezer_fans=10)
    assert src == "lastfm" and pop > 70
    # only deezer left
    pop, src = resolve_popularity(deezer_fans=1_000_000)
    assert src == "deezer" and pop == 100.0


def test_cascade_returns_none_when_nothing_usable():
    assert resolve_popularity() == (None, None)
    assert resolve_popularity(spotify_followers=0, lastfm_listeners=0, deezer_fans=0) == (None, None)
    assert resolve_popularity(lastfm_listeners=-5) == (None, None)


# ── fetch_artist_popularity (injected-client orchestration) ──────────────────
def test_fetch_uses_spotify_free_first():
    pop, src = fetch_artist_popularity("X", spotify_id="sp1", spotify_free=_SpotifyFree(), lastfm=_LastFm())
    assert src == "spotify_free" and pop > 70


def test_fetch_falls_through_when_a_source_errors():
    class _Boom:
        def get_artist(self, aid):
            raise RuntimeError("down")
    pop, src = fetch_artist_popularity("X", spotify_id="sp1", spotify_free=_Boom(), lastfm=_LastFm())
    assert src == "lastfm"     # spotify raised -> fell through, no crash


def test_fetch_deezer_needs_an_id():
    # no spotify/lastfm, deezer client present but no deezer_id -> nothing fetched
    assert fetch_artist_popularity("X", deezer=_Deezer()) == (None, None)
    pop, src = fetch_artist_popularity("X", deezer_id="dz1", deezer=_Deezer())
    assert src == "deezer"


def test_fetch_none_with_no_clients():
    assert fetch_artist_popularity("X", spotify_id="sp1", deezer_id="dz1") == (None, None)
