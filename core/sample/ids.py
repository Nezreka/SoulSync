"""Safe string keys for Sample Studio's native catalogue track IDs.

Library-v2 uses native row IDs and stores Plex/Jellyfin/Navidrome identifiers
in media mappings. HTTP/store/cache keys use a consistent string form. Plain
characters also keep cache paths safe; legacy derived-artifact keys remain
valid while native ownership checks decide which library files are usable.
"""

from __future__ import annotations

import re

_SAFE = re.compile(r"[A-Za-z0-9_-]{1,128}")


def track_key(track_id) -> str:
    """the id as text, or ValueError when it's empty or could escape a path.
    a plex id keeps its digits, so existing cache files still match."""
    if track_id is None or isinstance(track_id, bool):
        raise ValueError("track_id is required")
    if isinstance(track_id, float):
        if not track_id.is_integer():
            raise ValueError("track_id is required")
        track_id = int(track_id)
    key = str(track_id).strip()
    if not _SAFE.fullmatch(key):
        raise ValueError("track_id is required")
    return key
