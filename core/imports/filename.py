"""Filename parsing helpers used by import flows."""

from __future__ import annotations

import os
import re
from typing import Any, Dict


_TRACK_PATTERNS = (
    r"^(\d+)\s*[-\.]\s*(.+?)\s*[-–]\s*(.+)$",
    r"^(.+?)\s*[-–]\s*(.+)$",
    r"^(\d+)\s*[-\.]\s*(.+)$",
)


def extract_track_number_from_filename(filename: str, title: str = None) -> int:
    """Extract track number from a filename. Returns 1 if not found.

    Use ``extract_explicit_track_number`` instead when the caller needs
    to distinguish "track 1" from "unknown" — staging-file readers in
    particular MUST NOT conflate a bare title (no numeric prefix) with
    track 1, or every untagged album-bundle file gets imported as
    ``track_number=1`` and downstream callers can't recover the real
    number from authoritative metadata (Spotify track list, etc.).
    """
    num = extract_explicit_track_number(filename)
    return num if num > 0 else 1


def extract_explicit_track_number(filename: str) -> int:
    """Extract a track number only when the filename visibly carries one.

    Returns the parsed track number when the basename starts with a
    recognizable numeric prefix (``"01 - Title"``, ``"1-03 Title"``,
    ``"(01) Title"``, ``"[01] Title"``); returns ``0`` when no such
    prefix is present. This is the contract staging readers want —
    "unknown" must stay unknown so a downstream consumer with better
    info (Spotify metadata, MusicBrainz, etc.) can fill it in.
    """
    basename = os.path.splitext(os.path.basename(str(filename or "")))[0].strip()
    if not basename:
        return 0

    match = re.match(r"^\d[\-\.](\d{1,2})\s*[\-\.]\s*", basename)
    if match:
        num = int(match.group(1))
        if 1 <= num <= 99:
            return num

    match = re.match(r"^\(?(\d{1,3})\)?\s*[\-\.)\]]\s*", basename)
    if match:
        num = int(match.group(1))
        if 1 <= num <= 999:
            return num

    return 0


def parse_filename_metadata(filename: str) -> Dict[str, Any]:
    """Extract artist/title/album hints from a loose filename."""
    raw_path = str(filename or "")
    normalized_path = raw_path.replace("\\", "/")
    base_name = os.path.splitext(os.path.basename(normalized_path))[0]

    result: Dict[str, Any] = {
        "artist": "",
        "title": "",
        "album": "",
        "track_number": None,
    }

    if not base_name:
        return result

    for pattern in _TRACK_PATTERNS:
        match = re.match(pattern, base_name)
        if not match:
            continue

        groups = match.groups()
        if len(groups) == 3:
            try:
                result["track_number"] = int(groups[0])
                result["artist"] = result["artist"] or groups[1].strip()
                result["title"] = result["title"] or groups[2].strip()
            except ValueError:
                result["artist"] = result["artist"] or groups[0].strip()
                result["title"] = result["title"] or f"{groups[1]} - {groups[2]}".strip()
        elif len(groups) == 2:
            if groups[0].isdigit():
                try:
                    result["track_number"] = int(groups[0])
                    result["title"] = result["title"] or groups[1].strip()
                except ValueError:
                    pass
            else:
                result["artist"] = result["artist"] or groups[0].strip()
                result["title"] = result["title"] or groups[1].strip()
        break

    if not result["title"]:
        result["title"] = base_name

    if not result["album"] and "/" in normalized_path:
        path_parts = normalized_path.split("/")
        for part in reversed(path_parts[:-1]):
            if not part or part.startswith("@"):
                continue

            cleaned = re.sub(r"^\d+\s*[-\.]\s*", "", part).strip()
            if len(cleaned) > 3:
                result["album"] = cleaned
                break

    return result
