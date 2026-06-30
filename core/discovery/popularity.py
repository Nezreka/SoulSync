"""Popularity normalization + source cascade for Discover (pure, side-effect-free).

The adventurousness dial penalises popular artists, but only ~2% of ``similar_artists`` rows carried a
popularity (Spotify-scale 0-100). To fill the rest we cascade **Spotify Free -> Last.fm listeners ->
Deezer fans**, mapping each onto the same 0-100 scale so the dial treats a backfilled value exactly
like an existing one. The raw-count sources are log-scaled, so a megastar lands near 100 and an obscure
act near 0 — roughly comparable to Spotify's index. No DB / network / config here; the worker supplies
the fetched values and this decides the number.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def log_normalize_popularity(value: object, *, floor_log: float = 2.0, ceil_log: float = 7.0) -> float:
    """Map a raw count (Last.fm listeners / Deezer fans) to a 0..100 popularity, log-scaled.

    ``floor_log`` / ``ceil_log`` are the log10 of the counts that map to 0 / 100 (defaults: 100 -> 0,
    10M -> 100). A count of 0 / negative -> 0.0. Result is clamped to [0, 100]. Pure.
    """
    v = _coerce_float(value, 0.0)
    if v <= 0:
        return 0.0
    lo, hi = float(floor_log), float(ceil_log)
    if hi <= lo:
        return 0.0
    p = (math.log10(v) - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, p))


# Per-source calibration (log10 of the count that maps to 0 and to 100). Spotify Free returns raw
# FOLLOWERS (not the official 0-100 index), Last.fm raw listeners, Deezer raw fans — each on its own
# magnitude (Spotify follower counts run highest, Deezer's smaller user base lowest).
SPOTIFY_FLOOR_LOG, SPOTIFY_CEIL_LOG = 3.0, 7.0   # 1k followers -> 0, 10M -> 100
LASTFM_FLOOR_LOG, LASTFM_CEIL_LOG = 2.0, 7.0     # 100 listeners -> 0, 10M -> 100
DEEZER_FLOOR_LOG, DEEZER_CEIL_LOG = 1.0, 6.0     # 10 fans -> 0, 1M -> 100


def resolve_popularity(
    *,
    spotify_popularity: Optional[object] = None,
    spotify_followers: Optional[object] = None,
    lastfm_listeners: Optional[object] = None,
    deezer_fans: Optional[object] = None,
) -> Tuple[Optional[float], Optional[str]]:
    """Pick a 0..100 popularity from the first source that has one:
    official Spotify popularity (native 0-100, if available) -> Spotify Free followers (log-norm) ->
    Last.fm listeners (log-norm) -> Deezer fans (log-norm).

    Returns ``(popularity, source)``; ``(None, None)`` when nothing usable. ``spotify_popularity`` is
    the curated 0-100 index (MusicMap's existing values) — *including 0* it's a real value and wins.
    The follower/listener/fan sources are raw counts, log-scaled to be roughly comparable. Pure.
    """
    if spotify_popularity is not None:
        sp = _coerce_float(spotify_popularity, -1.0)
        if sp >= 0:
            return max(0.0, min(100.0, sp)), "spotify"
    if spotify_followers is not None and _coerce_float(spotify_followers, 0.0) > 0:
        return log_normalize_popularity(
            spotify_followers, floor_log=SPOTIFY_FLOOR_LOG, ceil_log=SPOTIFY_CEIL_LOG), "spotify_free"
    if lastfm_listeners is not None and _coerce_float(lastfm_listeners, 0.0) > 0:
        return log_normalize_popularity(
            lastfm_listeners, floor_log=LASTFM_FLOOR_LOG, ceil_log=LASTFM_CEIL_LOG), "lastfm"
    if deezer_fans is not None and _coerce_float(deezer_fans, 0.0) > 0:
        return log_normalize_popularity(
            deezer_fans, floor_log=DEEZER_FLOOR_LOG, ceil_log=DEEZER_CEIL_LOG), "deezer"
    return None, None


def _dig(obj, *path):
    """Walk a dict path, tolerating missing keys / non-dicts -> None."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def fetch_artist_popularity(
    name: str, *, spotify_id: Optional[str] = None, deezer_id: Optional[str] = None,
    spotify_free=None, lastfm=None, deezer=None,
) -> Tuple[Optional[float], Optional[str]]:
    """Run the popularity cascade for ONE artist over whatever clients are passed (all optional). Each
    source is wrapped so one failing just falls through to the next. The clients are INJECTED — no
    imports here — so this is unit-testable without the network. Returns ``(0..100, source)`` or
    ``(None, None)``. Shapes: Spotify Free ``get_artist``/``search_artists`` -> ``followers.total``;
    Last.fm ``get_artist_info`` -> ``stats.listeners``; Deezer ``get_artist_info`` -> ``followers.total``.
    """
    def _safe(fn):
        try:
            return fn()
        except Exception:
            return None

    sp_followers = None
    if spotify_free is not None and (spotify_id or name):
        art = _safe(lambda: spotify_free.get_artist(spotify_id)) if spotify_id else \
            _safe(lambda: (spotify_free.search_artists(name) or [None])[0])
        sp_followers = _dig(art, "followers", "total")

    lf_listeners = None
    if lastfm is not None and name:
        info = _safe(lambda: lastfm.get_artist_info(name))
        lf_listeners = _dig(info, "stats", "listeners")

    dz_fans = None
    if deezer is not None and deezer_id:
        info = _safe(lambda: deezer.get_artist_info(deezer_id))
        dz_fans = _dig(info, "followers", "total")

    return resolve_popularity(
        spotify_followers=sp_followers, lastfm_listeners=lf_listeners, deezer_fans=dz_fans)
