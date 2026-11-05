"""Issues reported before the upgrade point at the catalogue rows again.

An issue keeps the id of what it is about (``library_issues.entity_id``) and
a snapshot with more ids (``artist_id``, ``album_id``, an album's ``tracks``).
Reported at 3.6.1 those were legacy (media-server) ids; read on Library v2
they name nothing (Jellyfin/Navidrome text ids: the issue says the item is
gone and its fix buttons are dead) or another row (Plex numbers that equal
some catalogue id). This translates them through the rows' legacy
back-references, once per database.

An id is only translated on evidence: the catalogue row whose legacy id it
is carries the snapshot's own name for it, and the row that currently holds
that id does not. An issue reported on Library v2 already points at its row
and its snapshot names that row, so it is never touched.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.legacy_issues")

MARKER = "legacy_issue_ids_v1"

_KINDS = {
    "track": ("lib2_tracks", "legacy_track_id", "title"),
    "album": ("lib2_albums", "legacy_album_id", "title"),
    "artist": ("lib2_artists", "legacy_artist_id", "name"),
}


def _fold(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _translate(conn, kind: str, old_id: Any, name: Any) -> Optional[int]:
    """The catalogue id ``old_id`` named before the upgrade, or None."""
    if old_id in (None, "") or not _fold(name):
        return None
    table, back_ref, name_col = _KINDS[kind]
    rows = conn.execute(
        f"SELECT id, {name_col} FROM {table} WHERE CAST({back_ref} AS TEXT)=? LIMIT 2",
        (str(old_id),)).fetchall()
    if len(rows) != 1 or _fold(rows[0][1]) != _fold(name):
        return None
    new_id = int(rows[0][0])
    if str(new_id) == str(old_id):
        return None  # already the catalogue id
    current = conn.execute(f"SELECT {name_col} FROM {table} WHERE CAST(id AS TEXT)=?",
                           (str(old_id),)).fetchone()
    if current is not None and _fold(current[0]) == _fold(name):
        return None  # the id already names this very thing
    return new_id


def carry_legacy_issue_ids(conn: Any) -> Dict[str, int]:
    """Translate pre-upgrade issue ids onto catalogue ids. Once; never raises."""
    stats = {"issues": 0, "snapshot_ids": 0}
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.execute("CREATE TABLE IF NOT EXISTS lib2_upgrade_repairs "
                     "(name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        if conn.execute("SELECT 1 FROM lib2_upgrade_repairs WHERE name=?", (MARKER,)).fetchone():
            return stats
        if "library_issues" in tables and all(t in tables for t, _b, _n in _KINDS.values()):
            for issue_id, kind, entity_id, raw in conn.execute(
                    "SELECT id, entity_type, entity_id, snapshot_data FROM library_issues").fetchall():
                if kind not in _KINDS:
                    continue
                try:
                    snap = json.loads(raw or "{}")
                except (TypeError, ValueError):
                    continue
                if not isinstance(snap, dict):
                    continue
                own_name = snap.get(_KINDS[kind][2])
                new_entity = _translate(conn, kind, entity_id, own_name)
                changed = 0
                if new_entity is not None:
                    snap["id"] = new_entity
                    changed += 1
                for key, other, name_key in (("artist_id", "artist", "artist_name"),
                                             ("album_id", "album", "album_title")):
                    new_ref = _translate(conn, other, snap.get(key), snap.get(name_key))
                    if new_ref is not None:
                        snap[key] = new_ref
                        changed += 1
                if kind == "album" and isinstance(snap.get("tracks"), list):
                    for item in snap["tracks"]:
                        if isinstance(item, dict):
                            new_ref = _translate(conn, "track", item.get("id"), item.get("title"))
                            if new_ref is not None:
                                item["id"] = new_ref
                                changed += 1
                if not changed:
                    continue
                snap["_ids_carried_from_3_6_1"] = True
                conn.execute(
                    "UPDATE library_issues SET entity_id=?, snapshot_data=? WHERE id=?",
                    (str(new_entity) if new_entity is not None else entity_id,
                     json.dumps(snap), issue_id))
                stats["issues"] += 1
                stats["snapshot_ids"] += changed - (1 if new_entity is not None else 0)
        conn.execute("INSERT OR IGNORE INTO lib2_upgrade_repairs(name) VALUES(?)", (MARKER,))
        conn.commit()
        if stats["issues"]:
            logger.info("Pointed %d issue(s) from before the upgrade at their catalogue rows",
                        stats["issues"])
    except Exception as exc:  # noqa: BLE001 - the next start retries
        logger.warning("issues from before the upgrade not translated yet: %s", exc)
        try:
            conn.rollback()
        except Exception as rollback_exc:  # noqa: BLE001
            logger.debug("Upgrade repair rollback failed: %s", rollback_exc)
    return stats


__all__ = ["carry_legacy_issue_ids"]
