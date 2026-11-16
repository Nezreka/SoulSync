"""Same-named releases kept apart by their tagged year and folder (#2110).

Weezer has three albums called "Weezer" (1994, 2001, 2008). A file known only
by its tags (a media-server scan adopting it, a standalone refresh) carries no
provider or MusicBrainz release id, so the import writer matched it to the
first album of that name and all three landed on one row: three track 1s,
three track 2s.

The tags still tell them apart: each album sits in its own folder with its
own year. A same-titled row whose files all live in other folders under
another year is another release. Same folder, or the same year (a CD1/CD2
split), or no year to compare: still the same album, as before.

``find_split_plans`` / ``apply_split_plan`` heal rows merged before the fix.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.release_split")

_YEAR = re.compile(r"\s*(\d{4})")


def tag_year(value: Any) -> Optional[str]:
    """The four digit year a tag or column holds ("1994-05-10" -> "1994")."""
    match = _YEAR.match(str(value or ""))
    if not match or match.group(1) == "0000":
        return None
    return match.group(1)


def folder_key(path: Any) -> str:
    text = str(path or "").replace("\\", "/").rstrip("/")
    return text.casefold()


def _tags(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _album_files(conn, album_id: int) -> List[Any]:
    return conn.execute(
        "SELECT t.id AS track_id, t.track_number, t.disc_number, f.path, f.tags_json"
        "  FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id = t.id"
        " WHERE t.album_id = ? AND COALESCE(f.file_state, 'active') = 'active'"
        "   AND TRIM(COALESCE(f.path, '')) <> ''",
        (int(album_id),),
    ).fetchall()


def is_other_release_by_year(conn, album_id: int, year: Any,
                             folder: Optional[str]) -> bool:
    """True when a same-titled row is another release than this file's.

    Every one of the row's files sits in another folder, and none of them
    (or the row itself, when no file has a year) carries this file's year.
    """
    year = tag_year(year)
    if not year or not folder:
        return False
    rows = _album_files(conn, album_id)
    if not rows:
        return False  # an empty row (a discography release) takes the file
    target = folder_key(folder)
    years = set()
    for row in rows:
        if folder_key(os.path.dirname(str(row["path"]))) == target:
            return False
        found = tag_year(_tags(row["tags_json"]).get("year"))
        if found:
            years.add(found)
    if not years:
        held = conn.execute("SELECT year FROM lib2_albums WHERE id=?",
                            (int(album_id),)).fetchone()
        found = tag_year(held[0]) if held else None
        if found:
            years.add(found)
    return bool(years) and year not in years


def _norm_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _candidate_album_ids(conn) -> List[int]:
    """albums showing the merge: the same position owned twice."""
    return [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT album_id FROM ("
        "  SELECT t.album_id FROM lib2_tracks t"
        "   WHERE t.track_number IS NOT NULL AND EXISTS ("
        "     SELECT 1 FROM lib2_track_files f WHERE f.track_id = t.id"
        "        AND COALESCE(f.file_state, 'active') = 'active'"
        "        AND TRIM(COALESCE(f.path, '')) <> '')"
        "   GROUP BY t.album_id, COALESCE(t.disc_number, 1), t.track_number"
        "  HAVING COUNT(*) > 1)").fetchall()]


def plan_for_album(conn, album_id: int) -> Optional[Dict[str, Any]]:
    """How one merged row splits, or None when it is one release.

    Each folder must be one year, the years must all differ, every file must
    name this album in its tags, and a track may not have files in two
    folders. Anything less proven stays as it is.
    """
    album = conn.execute("SELECT id, title, year FROM lib2_albums WHERE id=?",
                         (int(album_id),)).fetchone()
    if not album:
        return None
    title = _norm_title(album["title"])
    groups: Dict[str, Dict[str, Any]] = {}
    track_folder: Dict[int, str] = {}
    for row in _album_files(conn, album_id):
        folder = folder_key(os.path.dirname(str(row["path"])))
        tags = _tags(row["tags_json"])
        if _norm_title(tags.get("album")) != title:
            return None  # a single's file filed here is another problem
        track_id = int(row["track_id"])
        if track_folder.setdefault(track_id, folder) != folder:
            return None
        group = groups.setdefault(folder, {"years": set(), "tracks": set()})
        group["years"].add(tag_year(tags.get("year")))
        group["tracks"].add(track_id)
    if len(groups) < 2:
        return None
    years = []
    for group in groups.values():
        if len(group["years"]) != 1 or None in group["years"]:
            return None
        years.append(next(iter(group["years"])))
    if len(set(years)) != len(years):
        return None
    held_year = tag_year(album["year"])
    ordered = sorted(groups.items(), key=lambda item: (
        next(iter(item[1]["years"])) != held_year, -len(item[1]["tracks"]), item[0]))
    return {
        "album_id": int(album_id),
        "keep": {"folder": ordered[0][0], "year": next(iter(ordered[0][1]["years"]))},
        "split": [{"folder": folder, "year": next(iter(group["years"])),
                   "track_ids": sorted(group["tracks"])}
                  for folder, group in ordered[1:]],
    }


def find_split_plans(conn, album_ids: Optional[Iterable[int]] = None) -> List[Dict[str, Any]]:
    """Read only: every merged row that splits cleanly."""
    ids = list(album_ids) if album_ids is not None else _candidate_album_ids(conn)
    plans = []
    for album_id in ids:
        plan = plan_for_album(conn, album_id)
        if plan:
            plans.append(plan)
    return plans


def apply_split_plan(conn, plan: Dict[str, Any]) -> List[int]:
    """Give each other release its own album row. Does not commit.

    The new row copies the artist, title, kind and profile; its provider ids
    are left for enrichment, since the merged row's ids can belong to any of
    the releases.
    """
    from core.library2 import locks
    source_id = int(plan["album_id"])
    if locks.blocks("album", source_id, "match", "Same-named release split", conn=conn):
        return []
    made = []
    for part in plan["split"]:
        cursor = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type, album_type_known,"
            "                        quality_profile_id, monitored, year, origin)"
            " SELECT primary_artist_id, title, album_type, album_type_known,"
            "        quality_profile_id, monitored, ?, 'library'"
            "   FROM lib2_albums WHERE id=?",
            (int(part["year"]), source_id))
        new_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
            " SELECT ?, artist_id, role FROM lib2_album_artists WHERE album_id=?",
            (new_id, source_id))
        # what each profile chose for the merged row holds for every release
        # in it, or a profile's watched album comes back unwatched
        conn.execute(
            "INSERT OR IGNORE INTO lib2_monitor_rules(entity_type, entity_id, profile_id,"
            "                                        monitored, provenance)"
            " SELECT 'album', ?, profile_id, monitored, provenance FROM lib2_monitor_rules"
            "  WHERE entity_type='album' AND entity_id=?",
            (new_id, source_id))
        track_ids = [int(t) for t in part["track_ids"]]
        conn.execute(
            f"UPDATE lib2_tracks SET album_id=?, updated_at=CURRENT_TIMESTAMP"
            f" WHERE album_id=? AND id IN ({','.join('?' * len(track_ids))})",
            (new_id, source_id, *track_ids))
        made.append(new_id)
    for album_id in (source_id, *made):
        conn.execute(
            "UPDATE lib2_albums SET track_count=(SELECT COUNT(*) FROM lib2_tracks"
            " WHERE album_id=?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (album_id, album_id))
    if made:
        logger.info("Album %s held %d releases of one name, split into %s",
                    source_id, len(made) + 1, made)
    return made


def split_same_named_releases(conn, album_ids: Optional[Iterable[int]] = None) -> Dict[str, int]:
    """Heal every merged row. Does not commit."""
    stats = {"albums_split": 0, "albums_made": 0}
    for plan in find_split_plans(conn, album_ids):
        made = apply_split_plan(conn, plan)
        if made:
            stats["albums_split"] += 1
            stats["albums_made"] += len(made)
    return stats
