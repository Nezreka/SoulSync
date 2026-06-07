"""Enrichment-worker yield policy: who pauses while the user's foreground
work is running.

Background enrichment workers share external API budgets with the foreground
pipelines — most painfully MusicBrainz (~1 req/s per IP), where a worker
grinding through the library can starve the import pipeline's per-track
lookups into multi-minute crawls (measured: ~4m15s/track vs the normal ~20s).

Policy (set with Boulder, 2026-06-06):
  - downloads active   -> EVERYTHING yields (post-processing touches every
                          metadata source: MusicBrainz, Spotify, iTunes,
                          Deezer, Discogs, Last.fm, Genius, ...)
  - discovery active   -> the API-contention five yield (discovery hammers
                          the track-matching sources only)
Workers the user explicitly resumed mid-yield are honored upstream (the
override set lives in web_server's loop, as does the user-paused bookkeeping).
"""

from __future__ import annotations

from typing import Optional

# Everything that yields during active downloads. listening-stats (talks only
# to the local media server) and repair (user-scheduled job runner, not a
# background API drip) intentionally keep running.
ALL_YIELD_WORKERS = (
    'musicbrainz', 'audiodb', 'discogs', 'deezer',
    'spotify-enrichment', 'itunes-enrichment', 'lastfm-enrichment',
    'genius-enrichment', 'tidal-enrichment', 'qobuz-enrichment',
    'amazon-enrichment', 'similar_artists', 'hydrabase', 'soulid',
)

# The sources discovery contends with (track matching APIs).
API_CONTENTION_WORKERS = frozenset({
    'spotify-enrichment', 'itunes-enrichment', 'deezer', 'discogs', 'hydrabase',
})

# Discovery state phases that mean "nothing running" (idle or terminal).
_INACTIVE_PHASES = frozenset({'', 'idle', 'discovered', 'error', 'failed', 'cancelled'})


def worker_yield_reason(name: str, downloading: bool, discovering: bool) -> Optional[str]:
    """Why ``name`` should be paused right now, or None to run.
    Downloads outrank discovery so the label reflects the stronger cause."""
    if name not in ALL_YIELD_WORKERS:
        return None
    if downloading:
        return 'downloads'
    if discovering and name in API_CONTENTION_WORKERS:
        return 'discovery'
    return None


def discovery_state_active(state: dict) -> bool:
    """True when a per-playlist discovery state dict represents live work."""
    phase = str((state or {}).get('phase', '') or '').lower()
    return phase not in _INACTIVE_PHASES
