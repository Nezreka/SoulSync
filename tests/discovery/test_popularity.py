"""Popularity normalization + source cascade (pure)."""

from __future__ import annotations

from core.discovery.popularity import (
    log_normalize_popularity,
    resolve_popularity,
)


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
