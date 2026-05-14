"""Format track-number tags consistently across audio formats.

Discord report (Netti93): album tracks were tagged as ``TRCK = "6/0"``
instead of ``"6/13"``. Cause: many album-dict construction sites in
the codebase pass ``total_tracks: 0`` when the source data is
incomplete, and ``core/metadata/enrichment.py`` formatted the tag
unconditionally as ``f"{track_number}/{total_tracks}"`` — so 0
propagated straight to disk. The retag path was unaffected because
``core/tag_writer.py`` already does the right thing.

Per ``core/metadata/types.py``, ``total_tracks = 0`` is documented
as "unknown" — not an actual track count. Fix at the consumer
boundary so every album-dict constructor doesn't need to be touched.

This module provides one pure helper. Tests at the function boundary.
"""

from __future__ import annotations

from typing import Optional, Tuple


def format_track_number_tag(
    track_number: Optional[int],
    total_tracks: Optional[int],
) -> str:
    """Return the canonical TRCK / tracknumber tag string.

    - ``track_number=6, total_tracks=13`` → ``"6/13"``
    - ``track_number=6, total_tracks=0``  → ``"6"``  (total unknown)
    - ``track_number=6, total_tracks=None`` → ``"6"``
    - ``track_number=None, total_tracks=13`` → ``"1/13"`` (track defaults to 1)
    - ``track_number=None, total_tracks=None`` → ``"1"``

    ID3 spec allows ``TRCK`` to be either ``"N"`` or ``"N/M"``. Vorbis
    ``tracknumber`` follows the same convention. Avoiding the ``/0``
    suffix keeps the tag honest — most media servers and taggers
    interpret ``6/0`` as "track 6 of 0" which is nonsensical, while
    ``6`` reads as "track 6, total unknown".
    """
    num = _coerce_positive_int(track_number, default=1)
    total = _coerce_positive_int(total_tracks, default=0)
    if total > 0:
        return f"{num}/{total}"
    return str(num)


def format_track_number_tuple(
    track_number: Optional[int],
    total_tracks: Optional[int],
) -> Tuple[int, int]:
    """Return the MP4 ``trkn`` tuple ``(track, total)``.

    MP4 tag spec stores track-of as a 2-int tuple — convention is
    ``(N, 0)`` when the total is unknown. Same coercion rules as
    ``format_track_number_tag``: missing / None / non-positive
    ``track_number`` defaults to 1, missing / 0 / negative
    ``total_tracks`` returns 0 (the spec's "unknown" marker).
    """
    num = _coerce_positive_int(track_number, default=1)
    total = _coerce_positive_int(total_tracks, default=0)
    return (num, total)


def _coerce_positive_int(value, *, default: int) -> int:
    """Coerce to a non-negative int. Falls back to ``default`` for
    None / non-numeric / negative input. Floats truncate.
    """
    if value is None:
        return default
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    if coerced < 0:
        return default
    return coerced
