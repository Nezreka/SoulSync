"""Compare library path aliases only when the filesystem proves identity."""
from __future__ import annotations

import os


def same_file(left, right):
    if not left or not right:
        return False
    if os.path.abspath(left) == os.path.abspath(right):
        return True
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError, TypeError):
        return False


def planned_metadata(updates, track, resolved):
    """Prefer the stable library ID, with verified aliases for older callers."""
    updates = updates or {}
    for key in (f"track:{track.get('id')}", resolved, track.get('file_path')):
        if key in updates:
            return updates[key]
    for path, value in updates.items():
        if not str(path).startswith('track:') and same_file(path, resolved):
            return value
    return None
