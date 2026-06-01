"""Choosing which copy of a duplicate track to keep.

The keeper is the highest-quality copy, and **format/lossless is the primary
criterion**: a lossless FLAC must beat a lossy MP3 regardless of the recorded
bitrate number — a FLAC whose bitrate the library scan never populated (a
common gap) still has to win over a 282 kbps MP3. Only *within* the same format
tier do bitrate, then duration, then track number break the tie.

This was lifted out of the repair worker so the deletion path and the UI's
"Keep Best" recommendation rank identically (previously both ranked by bitrate
first, which kept the MP3 when the FLAC's bitrate was missing), and so the
ranking is unit-testable in isolation.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

# Format quality rank by file extension (higher = better). Mirrors
# ``auto_import_worker._quality_rank`` so lossless always outranks lossy.
_FORMAT_RANK = {
    ".flac": 10, ".wav": 9, ".aiff": 9, ".aif": 9, ".ape": 8,
    ".m4a": 7, ".ogg": 6, ".opus": 6, ".mp3": 5, ".aac": 5, ".wma": 3,
}


def format_rank_for_path(file_path: Optional[str]) -> int:
    """Quality rank for a file by extension (higher = better, unknown = 1)."""
    if not file_path:
        return 1
    ext = os.path.splitext(str(file_path))[1].lower()
    return _FORMAT_RANK.get(ext, 1)


def duplicate_keep_sort_key(track: Dict) -> Tuple[int, int, float, int]:
    """Sort key for picking the keeper — the higher tuple wins.

    Order of precedence: format/lossless tier, then bitrate, then duration,
    then track number (a real number over a placeholder ``01``). Putting format
    first is the whole point — it makes FLAC beat MP3 even when the FLAC's
    bitrate is 0/missing in the DB.
    """
    return (
        format_rank_for_path(track.get("file_path")),
        track.get("bitrate") or 0,
        track.get("duration") or 0,
        track.get("track_number") or 0,
    )


def pick_duplicate_to_keep(tracks: List[Dict]) -> Optional[Dict]:
    """Return the copy to keep from a duplicate group (highest sort key), or
    ``None`` if the group is empty."""
    if not tracks:
        return None
    return max(tracks, key=duplicate_keep_sort_key)
