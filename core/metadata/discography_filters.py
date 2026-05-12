"""Track-level filters for the user-facing Download Discography flow.

GitHub issue #559 (trackhacs): clicking "Download Discography" on an
artist also pulled in tracks where the artist's name appeared in the
title of someone else's song. Two failure modes underneath:

1. **Cross-artist tracks.** Spotify's `artist_albums` endpoint returns
   compilation / appears_on / various-artists albums where the requested
   artist is featured on one or two tracks. The endpoint then added
   *every* track from those albums to the wishlist, including tracks by
   unrelated artists that just happened to mention the requested artist
   in the title.

2. **Remix / live / acoustic / instrumental versions.** The watchlist
   scanner has user-toggleable filters for these (default: exclude),
   stored at `watchlist.global_include_*`. The discography backfill
   repair job already honors them. The user-facing Download Discography
   endpoint did not — those filters never fired for one-off discography
   downloads, so users got remix-ladder bloat.

These helpers live alongside the existing `core.metadata.discography`
because they belong to the same conceptual layer (discography fetch
results, pre-wishlist) and are independently testable. The watchlist
content-type detectors (``is_remix_version`` etc.) are reused from
``core.watchlist_scanner`` rather than re-implemented — same patterns,
single source of truth.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.watchlist_scanner import (
    is_acoustic_version,
    is_instrumental_version,
    is_live_version,
    is_remix_version,
)


def track_artist_matches(track_artists: Any, requested_artist_name: str) -> bool:
    """Return True if the requested artist appears in the track's
    artists list (case-insensitive exact-name membership).

    `track_artists` can be the list-of-strings shape produced by
    ``core.metadata.album_tracks._normalize_track_artists`` (which is
    what the discography fetch returns), or the list-of-dicts shape
    that some upstreams pass directly. Both are accepted.

    Returns True for primary-artist tracks AND feature appearances —
    the requested artist need only be one of the listed artists. Only
    drops tracks where the requested artist isn't named at all (the
    cross-artist compilation case from #559).
    """
    if not requested_artist_name:
        # No artist to compare against — don't filter; let the caller
        # decide. Defensive: avoids dropping every track when the
        # caller forgot to pass the artist name.
        return True

    target = requested_artist_name.strip().lower()
    if not target:
        return True

    if not track_artists:
        return False

    for entry in track_artists:
        if isinstance(entry, dict):
            name = entry.get('name', '') or ''
        else:
            name = str(entry or '')
        if name.strip().lower() == target:
            return True

    return False


def content_type_skip_reason(
    track_name: str,
    album_name: str,
    settings: Dict[str, Any],
) -> Optional[str]:
    """Return a short skip-reason string if the track is a content type
    the user has chosen to exclude, else None.

    `settings` is a dict keyed by the same names as the watchlist
    globals (``include_live`` / ``include_remixes`` / ``include_acoustic``
    / ``include_instrumentals``). All default to False — i.e. exclude
    by default — matching the watchlist scanner's default contract.
    """
    if not settings.get('include_live', False) and is_live_version(track_name, album_name):
        return 'live'
    if not settings.get('include_remixes', False) and is_remix_version(track_name, album_name):
        return 'remix'
    if not settings.get('include_acoustic', False) and is_acoustic_version(track_name, album_name):
        return 'acoustic'
    if not settings.get('include_instrumentals', False) and is_instrumental_version(track_name, album_name):
        return 'instrumental'
    return None


def load_global_content_filter_settings(config_manager: Any) -> Dict[str, Any]:
    """Read the four watchlist content-type globals from config.

    Centralises the key names so the endpoint and the helper agree on
    where the settings live. All four default to False (exclude) — same
    contract as the watchlist scanner.
    """
    if config_manager is None:
        return {
            'include_live': False,
            'include_remixes': False,
            'include_acoustic': False,
            'include_instrumentals': False,
        }
    try:
        return {
            'include_live': bool(config_manager.get('watchlist.global_include_live', False)),
            'include_remixes': bool(config_manager.get('watchlist.global_include_remixes', False)),
            'include_acoustic': bool(config_manager.get('watchlist.global_include_acoustic', False)),
            'include_instrumentals': bool(config_manager.get('watchlist.global_include_instrumentals', False)),
        }
    except Exception:
        return {
            'include_live': False,
            'include_remixes': False,
            'include_acoustic': False,
            'include_instrumentals': False,
        }


__all__ = [
    'track_artist_matches',
    'content_type_skip_reason',
    'load_global_content_filter_settings',
]
