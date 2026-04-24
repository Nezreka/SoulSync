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
