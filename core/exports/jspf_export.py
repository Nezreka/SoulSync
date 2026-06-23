"""Build a JSPF playlist (ListenBrainz-compatible) from resolved SoulSync tracks.

ListenBrainz's ``POST /1/playlist/create`` requires JSPF where **every track carries a
``identifier`` of ``https://musicbrainz.org/recording/<recording-mbid>``** — text-only
entries (title/creator alone) are rejected. So a track can only be exported once we've
resolved its MusicBrainz *recording* MBID (see ``mbid_resolver``); tracks without one are
dropped here and surfaced to the user as "unmatched".

Pure + I/O-free: callers pass already-resolved track dicts, this returns the JSPF dict
(and a small coverage summary). The same JSPF is used for both the downloadable ``.jspf``
file and the direct create-playlist POST, so there's one source of truth for the shape.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

MB_RECORDING_PREFIX = "https://musicbrainz.org/recording/"

# A MusicBrainz MBID is a canonical UUID. Validate to avoid emitting garbage identifiers
# that LB would reject (or, worse, that silently point nowhere).
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def is_valid_recording_mbid(mbid: Any) -> bool:
    """True when ``mbid`` is a well-formed MusicBrainz UUID."""
    return bool(mbid) and isinstance(mbid, str) and bool(_UUID_RE.match(mbid.strip()))


def _track_entry(track: Dict[str, Any]) -> Dict[str, Any] | None:
    """Build one JSPF track entry, or None if the track has no valid recording MBID."""
    mbid = (track.get("recording_mbid") or "").strip() if isinstance(track.get("recording_mbid"), str) else ""
    if not is_valid_recording_mbid(mbid):
        return None
    entry: Dict[str, Any] = {"identifier": f"{MB_RECORDING_PREFIX}{mbid}"}
    # Optional, human-friendly fields — LB ignores them on create but they make the
    # downloaded .jspf readable and round-trippable.
    if track.get("title"):
        entry["title"] = str(track["title"])
    if track.get("artist"):
        entry["creator"] = str(track["artist"])
    if track.get("album"):
        entry["album"] = str(track["album"])
    return entry


def build_jspf(
    title: str,
    tracks: List[Dict[str, Any]],
    *,
    creator: str = "",
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Build a ListenBrainz-compatible JSPF dict from resolved tracks.

    ``tracks`` is an ordered list of dicts with ``recording_mbid`` (required to be
    included), plus optional ``title`` / ``artist`` / ``album``. Tracks without a valid
    recording MBID are skipped (LB rejects them).

    Returns ``(jspf, summary)`` where ``jspf`` is ``{"playlist": {...}}`` and ``summary``
    is ``{"total", "included", "skipped"}`` for the coverage display.
    """
    jspf_tracks: List[Dict[str, Any]] = []
    for t in tracks or []:
        if not isinstance(t, dict):
            continue
        entry = _track_entry(t)
        if entry is not None:
            jspf_tracks.append(entry)

    playlist: Dict[str, Any] = {
        "title": (title or "SoulSync Export").strip() or "SoulSync Export",
        "track": jspf_tracks,
    }
    if creator:
        playlist["creator"] = str(creator)

    total = sum(1 for t in (tracks or []) if isinstance(t, dict))
    summary = {
        "total": total,
        "included": len(jspf_tracks),
        "skipped": total - len(jspf_tracks),
    }
    return {"playlist": playlist}, summary


__all__ = ["build_jspf", "is_valid_recording_mbid", "MB_RECORDING_PREFIX"]
