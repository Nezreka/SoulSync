"""Which of an artist's files Preview rename would move (#2141).

the artist page shows a count per album so nobody has to open Preview rename
to find out. the count is read off the same plan the preview shows
(``preview_album_reorganize``), never a second path builder, so the hint and
the preview can't disagree.

planning reads the disk, so an artist is checked in slices: each call plans
albums until its time budget runs out and says where the next slice starts.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.rename_check")

# a slice stops planning once this much time has gone, so one request never
# holds a worker for a big artist
SLICE_SECONDS = 4.0

# the music templates an album's files are named by
_MUSIC_TEMPLATES = ("album_path", "single_path", "compilation_path")


def naming_problem(config_manager: Any) -> Optional[str]:
    """None when files are organized by a template that renders, else why not.

    the settings page refuses to save a bad template, but config can be
    edited by hand. each template renders through the settings preview (the
    same code an import uses) against a made-up release with no ``$`` in it,
    so a ``$`` left over is a variable the renderer doesn't know."""
    get = config_manager.get if config_manager else (lambda _key, default=None: default)
    if get("file_organization.enabled", True) is False:
        return "off"
    from core.imports.path_preview import preview_paths

    org = get("file_organization", {}) or {}
    templates = get("file_organization.templates", {}) or {}
    try:
        paths = preview_paths(templates, org if isinstance(org, dict) else {})["paths"]
    except Exception as exc:  # noqa: BLE001 - a template that can't render is invalid
        logger.debug("naming preview failed: %s", exc)
        return "template"
    for key in _MUSIC_TEMPLATES:
        # the shape rules the settings page holds a save back on
        raw = str(templates.get(key) or "").strip() if isinstance(templates, dict) else ""
        if raw.startswith("/") or raw.endswith("/") or "//" in raw:
            return "template"
        path = paths.get(key)
        if not isinstance(path, str) or not path or "$" in path:
            return "template"
    if "/" not in paths["album_path"]:
        return "template"
    return None


def files_to_rename(preview: Dict[str, Any]) -> List[Dict[str, Any]]:
    """the files of one plan that sit somewhere other than their template path.

    the same test the reorganize repair job uses for a path mismatch: a file
    that exists, got a destination, and isn't already there."""
    return [
        track for track in preview.get("tracks") or []
        if track.get("matched") and track.get("new_path")
        and not track.get("unchanged") and track.get("file_exists")
    ]


def check_artist_renames(
    db: Any,
    config_manager: Any,
    artist_id: int,
    *,
    start: int = 0,
    preview_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    clock: Callable[[], float] = time.monotonic,
    budget: float = SLICE_SECONDS,
) -> Dict[str, Any]:
    """{album id: files to rename} for one slice of an artist's albums, and
    the tracks those files belong to.

    ``next`` is where the following slice starts, None once every album with
    files was planned. an album the planner refuses (placeholder metadata,
    gone since) is left out rather than counted."""
    from core.library2.reorganize_bridge import (
        ReorganizeBridgeError,
        artist_reorganize_albums,
        preview_album_reorganize,
    )

    problem = naming_problem(config_manager)
    if problem:
        return {"enabled": False, "reason": problem, "albums": {}, "tracks": [],
                "next": None, "total": 0}
    preview = preview_fn or (lambda album_id: preview_album_reorganize(db, config_manager, album_id))
    try:
        album_ids: List[int] = [int(a["album_id"]) for a in artist_reorganize_albums(db, artist_id)]
    except ReorganizeBridgeError:
        album_ids = []
    start = max(0, int(start or 0))
    deadline = clock() + budget
    counts: Dict[int, int] = {}
    tracks: List[int] = []
    index = start
    while index < len(album_ids):
        album_id = album_ids[index]
        index += 1
        try:
            moving = files_to_rename(preview(album_id))
            counts[album_id] = len(moving)
            tracks.extend(int(t["track_id"]) for t in moving if t.get("track_id"))
        except ReorganizeBridgeError as exc:
            logger.debug("rename check skipped album %s: %s", album_id, exc)
        except Exception as exc:  # noqa: BLE001 - one album never fails the page
            logger.warning("rename check failed for album %s: %s", album_id, exc)
        # at least one album per slice, so a slow one can't stall the walk
        if clock() >= deadline:
            break
    return {
        "enabled": True,
        "albums": counts,
        "tracks": tracks,
        "next": index if index < len(album_ids) else None,
        "total": len(album_ids),
    }


__all__ = ["SLICE_SECONDS", "naming_problem", "files_to_rename", "check_artist_renames"]
