"""Put a track back on its own number when it sits on another song's.

wolf39us: album V showed "1 Maps" (missing) and "1 Lost Stars" with the file
`2014 - V/15 - Lost Stars.flac`. The row had carried number 1 from another
release (the Lost Stars single, track 1 of 1) into this album: a fold or merge
moves a track "as it is", and a tracklist refresh only renumbers rows whose
title is on the tracklist, so a bonus track that isn't kept the foreign number
forever. Every later step that lines things up by position (Other versions,
reassign) then read "1. Lost Stars" as Maps.

The number a row should have here is this release's (the tracklist heal in
``completeness`` already does that for every title on the list) or, for a song
the list doesn't carry, the number its own file says. This module does the
second: when two rows share a position and their titles name different songs,
a row whose file tag (or filename) puts it somewhere else moves there, if that
position is free and the tracklist doesn't hold another song at it. A hand-set
number (an override) is never touched, nothing moves on a guess, nothing on
disk changes.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.track_numbers")

_LEADING = re.compile(r"^\s*(\d{1,4})")


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    match = _LEADING.match(str(value))
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def own_number(tags_json: Any, path: Any) -> Tuple[Optional[int], Optional[int]]:
    """(track, disc) the file says it is: its tag first, then its filename"""
    tags: Dict[str, Any] = {}
    try:
        parsed = json.loads(tags_json) if isinstance(tags_json, str) else (tags_json or {})
        tags = parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        tags = {}
    track, disc = _int(tags.get("track_number")), _int(tags.get("disc_number"))
    if track is None and path:
        from core.library2.path_drift import split_track_numbering
        stem = os.path.splitext(os.path.basename(str(path).replace("\\", "/")))[0]
        track, _rest = split_track_numbering(stem)
    return track, disc


def _tracklist(raw: Any) -> Dict[Tuple[int, int], str]:
    if isinstance(raw, list):
        entries = raw
    else:
        try:
            entries = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            return {}
    slots: Dict[Tuple[int, int], str] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        number = _int(entry.get("track_number"))
        if number is None:
            continue
        slots[(_int(entry.get("disc_number")) or 1, number)] = str(entry.get("title") or entry.get("name") or "")
    return slots


def _overridden(conn: Any, track_ids: List[int]) -> set:
    if not track_ids or not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lib2_metadata_overrides'").fetchone():
        return set()
    marks = ",".join("?" for _ in track_ids)
    return {int(r[0]) for r in conn.execute(
        f"SELECT entity_id FROM lib2_metadata_overrides WHERE entity_type='track'"
        f" AND field_name='track_number' AND entity_id IN ({marks})", track_ids)}


def heal_colliding_numbers(conn: Any, album_ids: Iterable[int], *,
                           tracklist: Optional[List[Dict[str, Any]]] = None) -> int:
    """Renumber rows that sit on another song's position, per the rule in the
    module docstring. ``tracklist``: the entries just persisted for a single
    album, else the album's cached one. Returns how many rows moved. Does not
    commit."""
    from core.imports.reassign_album import titles_conflict

    moved = 0
    for album_id in sorted({int(a) for a in album_ids or ()}):
        album = conn.execute("SELECT tracklist_json FROM lib2_albums WHERE id=?", (album_id,)).fetchone()
        if album is None:
            continue
        slots = _tracklist(tracklist if tracklist is not None else album[0])
        rows = [dict(r) for r in conn.execute(
            """SELECT t.id, t.title, t.track_number, COALESCE(t.disc_number, 1) AS disc,
                      (SELECT f.tags_json FROM lib2_track_files f
                        WHERE f.track_id=t.id AND COALESCE(f.file_state,'active')='active'
                        ORDER BY f.is_primary DESC, f.id LIMIT 1) AS tags_json,
                      (SELECT f.path FROM lib2_track_files f
                        WHERE f.track_id=t.id AND COALESCE(f.file_state,'active')='active'
                        ORDER BY f.is_primary DESC, f.id LIMIT 1) AS path
                 FROM lib2_tracks t WHERE t.album_id=? AND t.track_number IS NOT NULL""",
            (album_id,))]
        held: Dict[Tuple[int, int], List[Dict[str, Any]]] = {}
        for row in rows:
            held.setdefault((int(row["disc"]), int(row["track_number"])), []).append(row)
        clashing = [row for group in held.values() if len(group) > 1 for row in group
                    if row["path"] and any(titles_conflict(row["title"], other["title"])
                                           for other in group if other is not row)]
        if not clashing:
            continue
        locked = _overridden(conn, [int(r["id"]) for r in clashing])
        for row in clashing:
            if int(row["id"]) in locked:
                continue
            here = (int(row["disc"]), int(row["track_number"]))
            listed_here = slots.get(here)
            if listed_here is not None and not titles_conflict(row["title"], listed_here):
                continue  # the tracklist puts this very song here: the other row is the stray
            number, disc = own_number(row["tags_json"], row["path"])
            if number is None:
                continue
            slot = (disc or here[0], number)
            if slot == here or held.get(slot):
                continue
            listed = slots.get(slot)
            if listed is not None and titles_conflict(row["title"], listed):
                continue
            conn.execute(
                "UPDATE lib2_tracks SET track_number=?, disc_number=?,"
                " updated_at=CURRENT_TIMESTAMP WHERE id=?", (slot[1], slot[0], int(row["id"])))
            held[here] = [r for r in held[here] if r is not row]
            held[slot] = [row]
            moved += 1
            logger.info("Track %s (%r) moved off %s to %s, the number its file gives it",
                        row["id"], row["title"], here, slot)
    return moved


__all__ = ["heal_colliding_numbers", "own_number"]
