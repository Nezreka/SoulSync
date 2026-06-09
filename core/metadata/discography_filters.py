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

import re
from typing import Any, Dict, List, Optional

# Split a combined artist credit into its individual artists. Sources like
# iTunes return collabs as ONE string ("TRVNSPORTER, Narvent & SKVLENT"), not a
# list — so an exact full-string compare drops every collaborator's discography
# entry (#830). Split on the common credit separators; " and " / " with " are
# deliberately excluded (too many real band names contain them).
_ARTIST_CREDIT_SPLIT_RE = re.compile(
    r"\s*[,&;/]\s*|\s+(?:feat\.?|ft\.?|featuring|vs\.?|x)\s+",
    re.IGNORECASE,
)


def _artist_credit_components(name: str) -> List[str]:
    """Return the individual artist names within a (possibly combined) credit,
    always including the full string itself (so exact band names with internal
    separators still match)."""
    name = (name or "").strip()
    if not name:
        return []
    parts = [name]
    parts.extend(p.strip() for p in _ARTIST_CREDIT_SPLIT_RE.split(name) if p.strip())
    return parts

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

    Returns True for primary-artist tracks AND feature/collab appearances —
    the requested artist need only be one of the credited artists, INCLUDING
    when a source (iTunes, etc.) packs the collab into one combined string like
    "TRVNSPORTER, Narvent & SKVLENT" (#830). Only drops tracks where the
    requested artist isn't credited at all (the cross-artist compilation case
    from #559).
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
        # Match the requested artist as a component of the credit, so combined
        # collab strings ("A, B & C") keep B's discography entry. Component
        # matching is still exact per-name, so true contamination (the artist
        # genuinely absent) is dropped exactly as before.
        for component in _artist_credit_components(name):
            if component.strip().lower() == target:
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


def track_already_owned(
    db: Any,
    track_name: str,
    requested_artist: str,
    album_name: str,
    server_source: Optional[str],
    confidence_threshold: float = 0.7,
) -> bool:
    """Return True if the track is already in the user's library.

    Discord report (Skowl): clicking "Download Discography" twice on
    the same artist re-queued every track instead of skipping the
    half already on disk. Trace: the endpoint added each track to the
    wishlist via ``db.add_to_wishlist``, which only dedups against the
    wishlist itself — once a wishlist track downloads it leaves the
    wishlist, so the second discography click re-inserted everything.

    The discography backfill repair job already runs the same check
    via ``db.check_track_exists`` — this helper centralises the
    contract so the user-facing endpoint matches that behavior.

    `check_track_exists` is name+artist+album based, format-agnostic.
    Skowl's "Blasphemy mode" library (FLAC converted to MP3 then
    original deleted) matches just fine — track_name + artist + album
    don't change with format.

    Returns False on any exception so a transient DB hiccup doesn't
    silently nuke a discography fetch — a redundant wishlist add is
    much cheaper to recover from than a missed track.
    """
    if not requested_artist or not track_name:
        return False
    try:
        match, confidence = db.check_track_exists(
            track_name, requested_artist,
            confidence_threshold=confidence_threshold,
            server_source=server_source,
            album=album_name or None,
        )
    except Exception:
        return False
    return bool(match) and confidence >= confidence_threshold


__all__ = [
    'track_artist_matches',
    'content_type_skip_reason',
    'load_global_content_filter_settings',
    'track_already_owned',
]
