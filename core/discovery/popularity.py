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


# Per-source calibration (log10 of the count that maps to 0 and to 100). Deezer's user base is smaller
# than Last.fm's, so its fan counts top out lower.
LASTFM_FLOOR_LOG, LASTFM_CEIL_LOG = 2.0, 7.0   # 100 listeners -> 0, 10M -> 100
DEEZER_FLOOR_LOG, DEEZER_CEIL_LOG = 1.0, 6.0   # 10 fans -> 0, 1M -> 100


def resolve_popularity(
    *,
    spotify: Optional[object] = None,
    lastfm_listeners: Optional[object] = None,
    deezer_fans: Optional[object] = None,
) -> Tuple[Optional[float], Optional[str]]:
    """Pick a 0..100 popularity from the first source that has one, keeping the column on one scale:
    Spotify (native 0-100) -> Last.fm listeners (log-normalized) -> Deezer fans (log-normalized).

    Returns ``(popularity, source)``; ``(None, None)`` when nothing usable. ``spotify=None`` means
    "not found" (fall through); a Spotify number — *including 0* — is a real value and wins. Pure.
    """
    if spotify is not None:
        sp = _coerce_float(spotify, -1.0)
        if sp >= 0:
            return max(0.0, min(100.0, sp)), "spotify"
    if lastfm_listeners is not None and _coerce_float(lastfm_listeners, 0.0) > 0:
        return log_normalize_popularity(
            lastfm_listeners, floor_log=LASTFM_FLOOR_LOG, ceil_log=LASTFM_CEIL_LOG), "lastfm"
    if deezer_fans is not None and _coerce_float(deezer_fans, 0.0) > 0:
        return log_normalize_popularity(
            deezer_fans, floor_log=DEEZER_FLOOR_LOG, ceil_log=DEEZER_CEIL_LOG), "deezer"
    return None, None
