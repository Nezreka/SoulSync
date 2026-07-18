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


def metadata_scan_status(file_row: Optional[Mapping[str, Any]]) -> str:
    """Whether ``file_row``'s tag snapshot reflects a real read of the file.

    ``persist_tag_cache`` is the only writer of ``tags_json``/``missing_tags_json``
    (docs §79, LV2-TAG-STATUS-01). On a successful read it always stores the full
    ``EXPECTED_TAGS``-keyed snapshot (never the literal ``'{}'``); on a failed read
    it resets ``tags_json`` back to ``'{}'`` and writes the explicit JSON ``null``
    sentinel into ``missing_tags_json``. The schema default for a file the tag
    reader has never touched is ``tags_json='{}'`` + ``missing_tags_json='[]'`` —
    numerically indistinguishable from a real "scanned, zero gaps" result, which
    is exactly the false "tags ✓" this function exists to prevent.

    Returns ``'scanned'``, ``'unreadable'`` (last read attempt failed), or
    ``'pending'`` (no file, or never read since import).
    """
    if not file_row or not file_row.get("path"):
        return "pending"
    raw_tags = file_row.get("tags_json")
    if isinstance(raw_tags, Mapping):
        if raw_tags:
            return "scanned"
    elif isinstance(raw_tags, str) and raw_tags.strip() not in ("", "{}"):
        return "scanned"
    raw_missing = file_row.get("missing_tags_json")
    if isinstance(raw_missing, str) and raw_missing.strip() == "null":
        return "unreadable"
    return "pending"


def compute_metadata_gaps(file_row: Optional[Mapping[str, Any]]) -> List[str]:
    """Return the EXPECTED_TAGS confirmed missing from the file's own tags.

    Only meaningful once the file has actually been read — call
    ``metadata_scan_status`` first. A track whose file was never scanned or
    whose last read failed also returns ``[]`` here, but that must not be
    read as "no gaps": this function reports the file's *physical* tag
    state and deliberately never falls back to DB/provider metadata (docs
    §79, LV2-TAG-STATUS-01) — DB knowledge (e.g. a provider ``image_url``)
    is not evidence that a tag is actually embedded in this file.
    """
    if not file_row:
        return []
    # A non-empty missing-tag snapshot is unambiguous evidence of a real scan
    # (the untouched schema default is always an empty list) — authoritative
    # even before a path is resolved.
    explicit_missing = _coerce_list(file_row.get("missing_tags_json"))
    if explicit_missing:
        return [tag for tag in EXPECTED_TAGS if tag in set(explicit_missing)]
    return []


def file_status(file_row: Optional[Mapping[str, Any]], canonical_track_id: Optional[int]) -> str:
    """Coarse per-track file status for the UI.

    ``'missing'`` (no file or a confirmed miss), ``'missing_suspected'``
    (the first healthy-root miss is visible but must not trigger a redownload),
    ``'duplicate_single'`` (linked to a canonical album track, i.e. a single
    that also exists on an album), or ``'present'``.
    """
    if not file_row or not file_row.get("path"):
        return "missing"
    if file_row.get("file_state") in ("missing_confirmed", "deleted"):
        return "missing"
    if file_row.get("file_state") == "missing_suspected":
        return "missing_suspected"
    if canonical_track_id:
        return "duplicate_single"
    return "present"


__all__ = [
    "EXPECTED_TAGS",
    "quality_tier",
    "compute_metadata_gaps",
    "metadata_scan_status",
    "file_status",
]
