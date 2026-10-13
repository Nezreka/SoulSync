"""Library track ids as Sample Studio keys them.

tracks.id is TEXT: plex ids are digits, jellyfin ids are 32-hex guids and
navidrome ids are alphanumeric. the studio cast every id with int(), so on a
jellyfin or navidrome library every analysis, waveform and chop request was
a 400 and the page said "check your connection". ids are text everywhere now.
they also name cache files and stem folders, so only plain characters pass.
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
