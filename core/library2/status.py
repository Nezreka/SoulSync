"""Pure read helpers for Library v2: quality tiers and metadata-gap detection.

These are deliberately side-effect-free and do **not** read files from disk — the
request path must stay fast, so the API computes status from DB columns plus the
cached ``tags_json`` / ``metadata_gaps_json`` snapshots written at import/scan time.
A later background scan can refresh those snapshots by actually reading file tags
(reusing the repair-job tag readers); this module only interprets what's already
stored.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

# Tags the UI surfaces as "present / missing" for a track file. Order = display order.
EXPECTED_TAGS = (
    "title", "artist", "album", "albumartist", "track_number",
    "disc_number", "year", "genre", "cover",
)

# Lossless container formats.
_LOSSLESS_FORMATS = {"flac", "alac", "wav", "aiff", "ape", "wv", "m4a"}
# m4a is ambiguous (ALAC vs AAC); treated as lossless only when bit_depth is set.


def quality_tier(fmt: Optional[str], bitrate: Optional[int], bit_depth: Optional[int]) -> str:
    """Classify a file into a coarse quality tier for at-a-glance display.

    Returns one of: ``'lossless_hi'`` (hi-res), ``'lossless'``, ``'lossy_high'``
    (>=256 kbps), ``'lossy'`` (<256 kbps), or ``'unknown'``.
    """
    f = (fmt or "").lower().lstrip(".")
    is_lossless = f in _LOSSLESS_FORMATS and not (f == "m4a" and not bit_depth)
    if is_lossless:
        if bit_depth and bit_depth > 16:
            return "lossless_hi"
        return "lossless"
    if bitrate:
        # bitrate may be stored in bps or kbps; normalize to kbps.
        kbps = bitrate / 1000 if bitrate > 5000 else bitrate
        if kbps >= 256:
            return "lossy_high"
        return "lossy"
    if f:
        return "lossy"
    return "unknown"


def _coerce_tags(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass
    return {}


def _coerce_list(raw: Any) -> Optional[List[str]]:
    if raw in (None, ""):
        return None
    if isinstance(raw, list):
        return [str(v) for v in raw if str(v)]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if str(v)]
        except (ValueError, TypeError):
            return None
    return None


def compute_metadata_gaps(track: Mapping[str, Any], file_row: Optional[Mapping[str, Any]] = None,
                          artist_count: int = 0) -> List[str]:
    """Return the list of EXPECTED_TAGS that are missing for a track.

    Combines DB-level metadata (``track`` row fields + linked ``artist_count``) with
    the file's cached ``tags_json`` snapshot when available. A tag counts as present
    if either the DB knows it or the cached tag snapshot has a non-empty value.
    """
    # A scanned missing-tag snapshot is authoritative whenever present (even before
    # we have a resolved file path).
    explicit_missing = _coerce_list(file_row.get("missing_tags_json")) if file_row else None
    if explicit_missing is not None:
        return [tag for tag in EXPECTED_TAGS if tag in set(explicit_missing)]

    if not file_row or not file_row.get("path"):
        return []

    tags = _coerce_tags(file_row.get("tags_json")) if file_row else {}

    def present(tag_key: str, db_value: Any) -> bool:
        if db_value not in (None, "", 0):
            return True
        val = tags.get(tag_key)
        return val not in (None, "", [], {})

    gaps: List[str] = []
    checks = {
        "title": track.get("title"),
        "artist": artist_count if artist_count else None,
        "album": track.get("album_title"),
        "albumartist": track.get("album_artist_name"),
        "track_number": track.get("track_number"),
        "disc_number": track.get("disc_number"),
        "year": track.get("year") or track.get("album_year"),
        "genre": track.get("genre") or track.get("album_genres"),
        "cover": track.get("image_url") or track.get("album_image_url"),
    }
    for tag in EXPECTED_TAGS:
        if not present(tag, checks.get(tag)):
            gaps.append(tag)
    return gaps


def file_status(file_row: Optional[Mapping[str, Any]], canonical_track_id: Optional[int]) -> str:
    """Coarse per-track file status for the UI.

    ``'missing'`` (no file), ``'duplicate_single'`` (linked to a canonical album
    track, i.e. a single that also exists on an album), or ``'present'``.
    """
    if not file_row or not file_row.get("path"):
        return "missing"
    if canonical_track_id:
        return "duplicate_single"
    return "present"


__all__ = [
    "EXPECTED_TAGS",
    "quality_tier",
    "compute_metadata_gaps",
    "file_status",
]
