"""Build (and optionally write) an extended-M3U playlist from library track entries.

``build_m3u`` is pure + side-effect free: the caller enumerates tracks (converting the schema's
millisecond durations to seconds) and hands entries here; it only formats, so it's unit-testable
without a database or Flask. ``write_library_m3u`` is its thin I/O sibling used by the scan-sync hook.

Each entry is a dict with:
- ``path``     — the track file path (required; entries without one are skipped)
- ``title``    — track title (optional)
- ``artist``   — artist name (optional)
- ``duration`` — length in SECONDS (optional; ``-1`` / unknown is emitted per the M3U spec)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library.m3u_export")

DEFAULT_M3U_FILENAME = "soulsync_library.m3u"


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


def build_m3u(entries: Iterable[Dict[str, Any]], entry_base_path: str = "") -> str:
    """Return an extended-M3U playlist string for ``entries``.

    Emits ``#EXTM3U`` then, per track with a non-empty ``path``, an ``#EXTINF:<secs>,<label>`` line
    followed by the path. Entries without a path are skipped. Always ends with a trailing newline.

    ``entry_base_path`` is an optional prefix prepended to every track path (same knob the playlist
    M3U export uses) — for media servers that need a rewritten/absolute base. Empty = paths as stored.
    """
    base = (entry_base_path or "").rstrip("/\\")
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
        lines.append(f"{base}/{path}" if base else path)
    return "\n".join(lines) + "\n"


def write_library_m3u(
    entries: List[Dict[str, Any]],
    folder: str,
    filename: str = DEFAULT_M3U_FILENAME,
    entry_base_path: str = "",
) -> Optional[str]:
    """Write the library M3U into ``folder`` (created if missing). Returns the path written, or None
    on failure — the scan-sync hook must never raise into the scan-completion callback."""
    if not folder:
        return None
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build_m3u(entries, entry_base_path=entry_base_path))
        return path
    except Exception as exc:
        logger.warning("Failed to write library M3U to %s: %s", folder, exc)
        return None
