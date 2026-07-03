"""Resolve a track's position WITHIN its album's track list.

The bug this fixes: a track auto-downloaded from the playlist pipeline / wishlist /
watchlist is identified as belonging to an album, but the per-track position is
unknown — Deezer's search/track and MusicBrainz's recording lookups don't carry a
track position (only their album endpoint does). ``detect_album_info_web`` then
leaves ``track_number = None``, the import pipeline falls through to the default-1
floor, and the file lands as ``01/1`` even though the album is known
(``core/imports/context.py``). Verified live: e.g. Deezer says "Obelisk" is track
9 of *The Grand Mirage*, but it was tagged 1/1.

This is the pure matcher: given the album's track list (fetched by the caller via
``core.metadata.album_tracks.get_album_tracks_for_source`` — so this stays
source-agnostic and I/O-free) plus the track's own identifiers, return its real
``(track_number, disc_number)``. Match priority is by reliability:

1. **ISRC** — an exact recording identity; trusted immediately.
2. **source track id** — exact within this album.
3. **normalized title** — last resort.

Returns ``(None, None)`` on no confident match, so the caller keeps its existing
behaviour (never worse than today).
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple


def _norm_title(value: Any) -> str:
    """Lower, strip punctuation, collapse whitespace — for tolerant title match."""
    s = re.sub(r"[^\w\s]", "", str(value or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _pos_int(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def resolve_track_position_in_album(
    album_tracks: List[dict],
    *,
    title: str = "",
    track_id: str = "",
    isrc: str = "",
) -> Tuple[Optional[int], Optional[int]]:
    """Return ``(track_number, disc_number)`` for this track within ``album_tracks``,
    or ``(None, None)`` when no confident match is found.

    ``album_tracks`` is the list under ``get_album_tracks_for_source(...)['tracks']``
    — each entry has ``track_number`` / ``disc_number`` / ``id`` / ``name`` / ``isrc``.
    Entries without a valid positive ``track_number`` are skipped. Pure: no I/O.
    """
    if not album_tracks:
        return (None, None)

    want_isrc = str(isrc or "").strip().upper()
    want_id = str(track_id or "").strip()
    want_title = _norm_title(title)

    by_id: Optional[Tuple[int, int]] = None
    by_title: Optional[Tuple[int, int]] = None

    for t in album_tracks:
        if not isinstance(t, dict):
            continue
        tn = _pos_int(t.get("track_number"))
        if tn is None:
            continue
        dn = _pos_int(t.get("disc_number")) or 1

        # 1) ISRC — exact recording. Win immediately.
        if want_isrc and str(t.get("isrc") or "").strip().upper() == want_isrc:
            return (tn, dn)
        # 2) source track id — exact within the album.
        if by_id is None and want_id and str(t.get("id") or "").strip() == want_id:
            by_id = (tn, dn)
        # 3) normalized title — last resort.
        if by_title is None and want_title and _norm_title(t.get("name")) == want_title:
            by_title = (tn, dn)

    if by_id is not None:
        return by_id
    if by_title is not None:
        return by_title
    return (None, None)


__all__ = ["resolve_track_position_in_album"]
