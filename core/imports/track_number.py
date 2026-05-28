"""Pure-function resolver for the import pipeline's track_number lookup.

Lifted from ``core/imports/pipeline.py`` so the multi-source fallback
chain can be unit-tested in isolation. The pipeline integration is
one call site that delegates to ``resolve_track_number`` and then
applies the >=1 floor as the last-resort default.

Resolution order (first valid positive int wins):

1. ``album_info.track_number`` — set by upstream album-info builders
   when they have authoritative track position data (e.g. the
   album-bundle dispatch from ``core/downloads/master.py``).
2. ``track_info.track_number`` — Spotify-shaped track dict carried
   on the per-task download context. Populated by the per-track
   flow when the wishlist payload still has Spotify's position.
3. ``track_info.spotify_data.track_number`` — nested spotify_data
   dict inside track_info; common for wishlist-loop payloads that
   wrapped the source spotify dict under an outer envelope.
4. ``extract_track_number_from_filename(file_path)`` — last resort
   when none of the metadata sources carried the value.

Pre-fix, the pipeline only consulted ``album_info`` and fell straight
to the filename when it was None. That broke for VA-collection
source files like ``417 Fountains of Wayne - Stacys Mom.flac`` where
the leading number isn't the album track position — extract returned
None or the wrong number, post-process defaulted to 1, and every
such wishlist import landed as ``01 - <title>`` regardless of the
real source position.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from core.imports.filename import extract_explicit_track_number


def _coerce_positive(value: Any) -> Optional[int]:
    """Coerce ``value`` to a positive int, or return None when the
    value is missing / non-numeric / non-positive. Centralised so
    every check in ``resolve_track_number`` applies the same rules."""
    try:
        v = int(value)
        return v if v >= 1 else None
    except (TypeError, ValueError):
        return None


def _coerce_spotify_data(track_info: Any) -> dict:
    """Extract the nested ``spotify_data`` dict from a track_info
    payload, coercing string-JSON shapes and bad inputs to an empty
    dict so the caller can use ``.get`` safely."""
    if not isinstance(track_info, dict):
        return {}
    raw = track_info.get('spotify_data')
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def resolve_track_number(
    album_info: Any,
    track_info: Any,
    file_path: str,
) -> Optional[int]:
    """Walk the resolution chain and return the first valid positive
    int found, or None when every source is missing / unusable.

    Caller is responsible for the final default-1 floor — leaving
    that out of this function so tests can pin "everything missing
    returns None" separate from the floor behaviour.
    """
    album_info = album_info if isinstance(album_info, dict) else {}
    track_info = track_info if isinstance(track_info, dict) else {}
    spotify_data = _coerce_spotify_data(track_info)

    resolved = (
        _coerce_positive(album_info.get('track_number'))
        or _coerce_positive(track_info.get('track_number'))
        or _coerce_positive(spotify_data.get('track_number'))
    )
    if resolved is not None:
        return resolved

    # Filename fallback — use the EXPLICIT extractor variant which
    # returns 0 when no numeric prefix is recognised (vs. the default
    # variant that silently returns 1 for the unknown case). We want
    # "unknown" to stay unknown here so the pipeline's final
    # default-1 floor is the single source of that fallback —
    # otherwise this resolver would silently fill 1 and the
    # downstream floor logic would have no effect.
    if not file_path:
        return None
    try:
        from_filename = extract_explicit_track_number(file_path)
    except Exception:
        from_filename = None
    return _coerce_positive(from_filename)
