"""Build an extended-M3U playlist string from library track entries.

Pure + side-effect free: the caller enumerates tracks (converting the schema's millisecond durations
to seconds) and hands entries here; this only formats. Kept separate from the DB/endpoint so the
formatting is unit-testable without a database or Flask.

Each entry is a dict with:
- ``path``     — the track file path (required; entries without one are skipped)
- ``title``    — track title (optional)
- ``artist``   — artist name (optional)
- ``duration`` — length in SECONDS (optional; ``-1`` / unknown is emitted per the M3U spec)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable


def _extinf_seconds(duration: Any) -> int:
    """Whole seconds for an ``#EXTINF`` line, or ``-1`` when unknown (the M3U convention)."""
    try:
        secs = int(duration)
    except (TypeError, ValueError):
        return -1
    return secs if secs > 0 else -1


def _entry_label(artist: str, title: str, path: str) -> str:
    """The ``Artist - Title`` label, degrading to title, then the filename."""
    if artist and title:
        return f"{artist} - {title}"
    if title:
        return title
    return os.path.basename(path)


def build_m3u(entries: Iterable[Dict[str, Any]]) -> str:
    """Return an extended-M3U playlist string for ``entries``.

    Emits ``#EXTM3U`` then, per track with a non-empty ``path``, an ``#EXTINF:<secs>,<label>`` line
    followed by the path. Entries without a path are skipped. Always ends with a trailing newline.
    """
    lines = ["#EXTM3U"]
    for entry in entries:
        e = entry or {}
        path = str(e.get("path") or "").strip()
        if not path:
            continue
        secs = _extinf_seconds(e.get("duration"))
        label = _entry_label(
            str(e.get("artist") or "").strip(),
            str(e.get("title") or "").strip(),
            path,
        )
        lines.append(f"#EXTINF:{secs},{label}")
        lines.append(path)
    return "\n".join(lines) + "\n"
