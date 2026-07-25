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


def finalize_m3u_entry(line: str, rewrite_from: str = "", rewrite_to: str = "") -> str:
    """Final per-entry transforms, shared by every m3u writer.

    1. Prefix hot-swap (wolf39us): when ``rewrite_from`` is set and the line
       starts with it, that prefix is replaced with ``rewrite_to`` — so an m3u
       generated inside a container (``/data/media/...``) plays directly on
       the machine that mounts the same storage elsewhere (``M:/media/...``).
       Non-matching lines pass through untouched; empty settings = no-op.
    2. The '#' guard: an entry line must never START with '#' (players parse
       it as a comment and silently skip the track — the $artistletter '#'
       catch-all made this a real case, #1072).
    """
    out = str(line or "")
    rf = str(rewrite_from or "")
    if rf and out.startswith(rf):
        out = str(rewrite_to or "") + out[len(rf):]
    if out.startswith("#"):
        out = "./" + out
    return out


def build_m3u(entries: Iterable[Dict[str, Any]], entry_base_path: str = "",
              rewrite_from: str = "", rewrite_to: str = "") -> str:
    """Return an extended-M3U playlist string for ``entries``.

    Emits ``#EXTM3U`` then, per track with a non-empty ``path``, an ``#EXTINF:<secs>,<label>`` line
    followed by the path. Entries without a path are skipped. Always ends with a trailing newline.

    ``entry_base_path`` is an optional prefix prepended to every track path (same knob the playlist
    M3U export uses) — for media servers that need a rewritten/absolute base. Empty = paths as stored.

    ``rewrite_from``/``rewrite_to`` hot-swap a path prefix on the FINAL entry line (after the base
    prepend) — see ``finalize_m3u_entry``. Both empty = byte-identical output to before.
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
        lines.append(finalize_m3u_entry(f"{base}/{path}" if base else path,
                                        rewrite_from, rewrite_to))
    return "\n".join(lines) + "\n"


def write_library_m3u(
    entries: List[Dict[str, Any]],
    folder: str,
    filename: str = DEFAULT_M3U_FILENAME,
    entry_base_path: str = "",
    rewrite_from: str = "",
    rewrite_to: str = "",
) -> Optional[str]:
    """Write the library M3U into ``folder`` (created if missing). Returns the path written, or None
    on failure — the scan-sync hook must never raise into the scan-completion callback."""
    if not folder:
        return None
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build_m3u(entries, entry_base_path=entry_base_path,
                               rewrite_from=rewrite_from, rewrite_to=rewrite_to))
        return path
    except Exception as exc:
        logger.warning("Failed to write library M3U to %s: %s", folder, exc)
        return None
