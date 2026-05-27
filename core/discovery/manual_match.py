"""Helpers for Fix-popup manual match persistence.

When the user manually fixes a mirrored-playlist discovery via the Fix
popup, two questions land at the web_server route layer that are easier
to test in isolation:

1. *Which metadata source did the manual match come from?* — the popup
   cascade queries the user's primary source first, then Spotify /
   Deezer / iTunes / MusicBrainz as fallbacks; each search endpoint
   stamps `source` on its rows but the MBID-paste lookup uses a lean
   flat shape that doesn't carry it. `derive_manual_match_provider`
   collapses the fallback chain into a single string.

2. *Should the discovery layer re-run for this track when the current
   active provider differs from the cached one?* — re-running silently
   overwrites the user's deliberate pick with whatever the auto-search
   ranks first, so manual matches are exempt regardless of provider
   drift. `is_drifted_for_redo` encapsulates the decision.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def derive_manual_match_provider(
    payload_track: Dict[str, Any],
    active_provider: Optional[str],
) -> str:
    """Return the provider string to stamp on a manually-fixed match.

    Resolution order:
        1. ``payload_track['source']`` — every *_search_tracks endpoint
           sets this; the MBID-paste path doesn't.
        2. ``active_provider`` — what the user has configured as their
           primary discovery source.
        3. ``'spotify'`` — last-ditch default matching the historic
           hardcode (so behaviour is identical when both upstream
           signals are absent).
    """
    if not isinstance(payload_track, dict):
        payload_track = {}
    source = payload_track.get('source')
    if source:
        return source
    if active_provider:
        return active_provider
    return 'spotify'


def is_drifted_for_redo(
    extra_data: Optional[Dict[str, Any]],
    active_provider: Optional[str],
) -> bool:
    """Return True when a cached discovery entry should be treated as
    stale because the user's active provider has changed since it was
    cached AND the entry isn't a manual match.

    Manual matches are *always* considered fresh: re-running discovery
    against the current source would overwrite the user's deliberate
    pick with whatever auto-search ranks first. The first Playlist
    Pipeline run after a manual fix used to clobber it for exactly
    this reason — the check lives here now so it's pinned by tests.
    """
    if not isinstance(extra_data, dict):
        return False
    if extra_data.get('manual_match'):
        return False
    cached_provider = extra_data.get('provider', 'spotify')
    return cached_provider != active_provider
