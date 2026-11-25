"""Move a track you already have into an album's missing slot (#2193).

"Go to the target album, find the track that's missing, find the existing
track, move the file, in the library, retag. It shouldn't copy, it should
move." (Vidariondr)

"I Have This" copies a file through the import pipeline and leaves the
original where it was, and album reassign re-imports a copy too. This is the
move: no staging, no import, no second file.

1. the library: the existing track's file rows move onto the missing track,
   the existing track's plays, history, rules, playlist and wishlist links
   carry over the way a twin fold carries them (``_carry_folded_track``), and
   its row goes. its album goes too when nothing is left on it and nobody
   wants it, the way removing an album does.
2. the tags: the file is retagged from the target track (``retag.write_tags``).
3. the folder: the file is moved where the naming template puts it in the
   target album, by the reorganize queue limited to that one track (the
   executor moves, it never copies, and it repoints the catalogue so a scan
   finds a known file, not a new one). skipped when file organization is off.

``fold_track_into`` and ``retire_emptied_album`` are shared with the
re-identify/reassign replace (``rematch_hints.delete_replaced_track``), so a
reassign leaves no empty row or album behind either.
"""

from __future__ import annotations

import os
from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.track_slot_move")

_LIVE = "COALESCE(file_state,'active') NOT IN ('deleted','missing_confirmed')"


class SlotMoveError(ValueError):
    """A move that can't happen, with a message a person reads."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# -- shared folds -------------------------------------------------------------

def fold_track_into(conn: Any, source_id: int, target_id: int) -> List[int]:
    """``source_id``'s files and everything hanging off it move to
    ``target_id``, then the source row goes. Returns the moved file ids.
    Does not commit.

    the same steps the Other versions merge runs for a song both albums have
    (``album_versions.merge_album_into``): every file row moves (deleted ones
    travel as history), the twin fold's carry takes plays, rules, mappings,
    listening history, wishlist links, edits and a redirect, and the primary
    file is elected again. the source's credits are not carried: the target
    slot's credits are the ones its album lists."""
    from core.library2.dedup_repair import _carry_folded_track, _delete_placeholder_track
    from core.library2.recording_links import settle_file_home
    from core.library2.track_files import elect_primary_file

    source_id, target_id = int(source_id), int(target_id)
    if source_id == target_id:
        raise SlotMoveError("A track can't be moved onto itself")
    cursor = conn.cursor()
    moved = [int(r[0]) for r in conn.execute(
        f"SELECT id FROM lib2_track_files WHERE track_id=? AND {_LIVE}", (source_id,))]
    cursor.execute("UPDATE lib2_track_files SET track_id=?, is_primary=0,"
                   " updated_at=CURRENT_TIMESTAMP WHERE track_id=?", (target_id, source_id))
    _carry_folded_track(conn, source_id, target_id)
    cursor.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (source_id,))
    _delete_placeholder_track(cursor, source_id)
    elect_primary_file(conn, target_id)
    # the person put the file here: the start-up re-home leaves it be
    for file_id in moved:
        settle_file_home(conn, file_id)
    return moved


def _provider_listed(conn: Any, album_id: int) -> bool:
    """a release a metadata provider lists (the album delete's own test)"""
    row = conn.execute("SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_albums WHERE id=?",
                       (int(album_id),)).fetchone()
    if not row:
        return False
    if row["spotify_id"] or row["musicbrainz_id"]:
        return True
    from core.library2.provider_ids import parse_external_ids, provider_only
    return bool(provider_only(parse_external_ids(row["external_ids"])))


def album_left_empty(conn: Any, album_id: int) -> bool:
    """nothing of the album is on disk any more and nobody wants what's left"""
    album_id = int(album_id)
    if conn.execute("SELECT 1 FROM lib2_albums WHERE id=?", (album_id,)).fetchone() is None:
        return False
    if conn.execute(
            "SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id"
            " WHERE t.album_id=? AND COALESCE(f.file_state,'active')"
            " NOT IN ('deleted','missing_confirmed') LIMIT 1", (album_id,)).fetchone():
        return False
    return conn.execute(
        "SELECT 1 FROM lib2_tracks t JOIN lib2_wanted_tracks w ON w.track_id=t.id"
        " WHERE t.album_id=? AND w.wanted=1 LIMIT 1", (album_id,)).fetchone() is None


def retire_emptied_album(conn: Any, album_id: int, *, profile_ids: Iterable[int] = ()) -> Optional[str]:
    """Take an album the move left empty out of the library, the way removing
    it from its page does (``DELETE /api/library/v2/albums/<id>``): a release
    a provider lists stays listed, not owned and not wanted, so the back
    catalogue's auto add doesn't wish it back; any other is deleted. Returns
    'forgotten', 'deleted' or None when the album still has something on it.
    Does not commit; queues the wishlist withdrawals on ``conn``."""
    from core.library2.mirror_outbox import enqueue_tracks, intent_profile_ids
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule

    album_id = int(album_id)
    if not album_left_empty(conn, album_id):
        return None
    track_ids = [int(r[0]) for r in conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=?", (album_id,))]
    profiles = (intent_profile_ids(conn, track_ids=track_ids, album_ids=[album_id])
                | {int(p) for p in profile_ids} | {1})
    if track_ids:
        for profile_id in sorted(profiles):
            enqueue_tracks(conn, track_ids, False, profile_id=profile_id)
    marks = ",".join("?" for _ in track_ids) or "NULL"
    if _provider_listed(conn, album_id):
        conn.execute(f"DELETE FROM lib2_wanted_tracks WHERE track_id IN ({marks})", track_ids)
        conn.execute(f"UPDATE lib2_tracks SET monitored=0, updated_at=CURRENT_TIMESTAMP"
                     f" WHERE id IN ({marks})", track_ids)
        conn.execute("UPDATE lib2_albums SET monitored=0, origin='discography',"
                     " updated_at=CURRENT_TIMESTAMP WHERE id=?", (album_id,))
        for profile in sorted(profiles):
            record_rule(conn, "album", album_id, False, PROVENANCE_USER, profile_id=profile)
            for track_id in track_ids:
                record_rule(conn, "track", track_id, False, PROVENANCE_USER, profile_id=profile)
        return "forgotten"
    conn.execute("DELETE FROM lib2_album_artists WHERE album_id=?", (album_id,))
    conn.execute(f"DELETE FROM lib2_track_artists WHERE track_id IN ({marks})", track_ids)
    # deleted rows are history; the tracks they hang off go with the album
    conn.execute(f"DELETE FROM lib2_track_files WHERE track_id IN ({marks})", track_ids)
    conn.execute(f"DELETE FROM lib2_wanted_tracks WHERE track_id IN ({marks})", track_ids)
    conn.execute(f"DELETE FROM lib2_release_tracks WHERE track_id IN ({marks})", track_ids)
    conn.execute("DELETE FROM lib2_monitor_rules WHERE entity_type='album' AND entity_id=?",
                 (album_id,))
    conn.execute(f"DELETE FROM lib2_monitor_rules WHERE entity_type='track'"
                 f" AND entity_id IN ({marks})", track_ids)
    conn.execute("DELETE FROM lib2_tracks WHERE album_id=?", (album_id,))
    conn.execute("DELETE FROM lib2_albums WHERE id=?", (album_id,))
    return "deleted"


def _touch_albums(conn: Any, album_ids: Iterable[int]) -> None:
    """track counts and the artist roll-ups the move changed"""
    has_rollup = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                              " AND name='lib2_artist_rollup'").fetchone() is not None
    for album_id in {int(a) for a in album_ids if a is not None}:
        row = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?",
                           (album_id,)).fetchone()
        if row is None:
            continue
        conn.execute("UPDATE lib2_albums SET track_count=(SELECT COUNT(*) FROM lib2_tracks"
                     " WHERE album_id=?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                     (album_id, album_id))
        if has_rollup:
            conn.execute("UPDATE lib2_artist_rollup SET computed_at=0 WHERE artist_id=?",
                         (row["primary_artist_id"],))


# -- the move -------------------------------------------------------------------

def _track(conn: Any, track_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT t.id, t.title, t.track_number, COALESCE(t.disc_number, 1) AS disc_number,
                  t.duration, t.album_id, al.title AS album_title, al.album_type,
                  al.primary_artist_id AS artist_id, ar.name AS artist_name,
                  t.track_artist
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id = t.album_id
             LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE t.id=?""", (int(track_id),)).fetchone()
    return dict(row) if row else None


def _files(conn: Any, track_id: int) -> List[Dict[str, Any]]:
    """the track's live files in the library being worked in, best first"""
    from core.library2.sql_util import owner_clause
    from core.library2.track_files import primary_order
    rows = conn.execute(
        f"SELECT id, path, format, bitrate, sample_rate, bit_depth, size, owner_profile_id"
        f" FROM lib2_track_files WHERE track_id=? AND path IS NOT NULL AND path<>''"
        f" AND {_LIVE}{owner_clause(column='owner_profile_id')}"
        f" ORDER BY {primary_order()}", (int(track_id),)).fetchall()
    return [dict(r) for r in rows]


def _locked(conn: Any, album_id: int) -> bool:
    from core.library2.locks import scopes_for
    return bool(scopes_for("album", album_id, conn))


def _validate(conn: Any, source_id: int, target_id: int) -> Dict[str, Any]:
    source_id, target_id = int(source_id), int(target_id)
    if source_id == target_id:
        raise SlotMoveError("Pick a different track: that's the missing one")
    target = _track(conn, target_id)
    if target is None:
        raise SlotMoveError("The missing track is gone, reload the album", 404)
    source = _track(conn, source_id)
    if source is None:
        raise SlotMoveError("That track is gone from the library, search again", 404)
    if conn.execute(f"SELECT 1 FROM lib2_track_files WHERE track_id=? AND path IS NOT NULL"
                    f" AND path<>'' AND {_LIVE} LIMIT 1", (target_id,)).fetchone():
        raise SlotMoveError("This track already has a file", 409)
    files = _files(conn, source_id)
    if not files:
        raise SlotMoveError("That track has no file in this library to move", 409)
    every = conn.execute(f"SELECT COUNT(*) FROM lib2_track_files WHERE track_id=? AND path IS NOT NULL"
                         f" AND path<>'' AND {_LIVE}", (source_id,)).fetchone()[0]
    if int(every) != len(files):
        # the whole row moves, so it would carry another library's copy along
        raise SlotMoveError("That track also has a copy in another library, move it from there", 409)
    for side, row in (("This album", target), ("The album of that track", source)):
        if _locked(conn, row["album_id"]):
            raise SlotMoveError(f"{side} is locked. Unlock it to move a track", 423)
    return {"source": source, "target": target, "files": files}


def _file_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    path = str(row.get("path") or "")
    ext = os.path.splitext(path)[1].lstrip(".").upper()
    return {"file_id": row["id"], "path": path,
            "format": (row.get("format") or ext or "").upper(),
            "bitrate": row.get("bitrate"), "sample_rate": row.get("sample_rate"),
            "bit_depth": row.get("bit_depth"), "size": row.get("size")}


def _organize_enabled(config_manager: Any) -> bool:
    if config_manager is None:
        from core.metadata.common import get_config_manager
        config_manager = get_config_manager()
    return config_manager.get("file_organization.enabled", True) is not False


def _tag_changes(conn: Any, source_files: List[Dict[str, Any]], target_id: int) -> Dict[str, Any]:
    """the tags the target track would write into the source's file"""
    from core.library2.retag import tag_preview, track_contexts
    contexts = track_contexts(conn, [int(target_id)], lyrics=False)
    if not contexts:
        return {"changes": [], "error": "Could not read the missing track"}
    context = contexts[0]
    context.update(file_id=source_files[0]["id"], file_path=source_files[0]["path"],
                   sibling_files=[{"id": f["id"], "path": f["path"]} for f in source_files[1:]])
    entry = tag_preview([context])[0]
    changes = [{"field": d.get("field"), "from": d.get("file_value"), "to": d.get("db_value")}
               for d in entry.get("diff") or []]
    return {"changes": changes, "error": entry.get("error")}


def _planned_path(database: Any, config_manager: Any, source_id: int, target: Dict[str, Any],
                  files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """where the file lands, from the reorganize planner itself: the move is
    tried on a connection that is rolled back, so the path is the one the
    queue will compute"""
    from core.imports.paths import build_final_path_for_track
    from core.library2.reorganize_bridge import _resolve_file_path_fn, _transfer_dir
    from core.library2.reorganize_plan import plan_album_reorganize
    from core.library_scope import any_own_library_exists, library_scope, scope_for_owner

    owner = files[0].get("owner_profile_id")
    scope, root = None, _transfer_dir(config_manager)
    if any_own_library_exists():
        from core.imports.paths import library_root_for_profile, shared_transfer_root
        scope = scope_for_owner(owner)
        root = (shared_transfer_root() if scope == "shared"
                else library_root_for_profile(scope, announce=False)) or root

    def _plan():
        with closing(database._get_connection()) as conn:
            try:
                conn.execute("UPDATE lib2_track_files SET track_id=? WHERE track_id=? AND "
                             f"{_LIVE}", (int(target["id"]), int(source_id)))
                return plan_album_reorganize(
                    conn, int(target["album_id"]),
                    build_final_path_fn=build_final_path_for_track, transfer_dir=root,
                    resolve_file_path_fn=_resolve_file_path_fn(config_manager))
            finally:
                conn.rollback()

    if scope is not None:
        with library_scope(scope):
            plan = _plan()
    else:
        plan = _plan()
    item = next((t for t in plan.get("tracks") or [] if int(t["track_id"]) == int(target["id"])), None)
    if not plan.get("success") or item is None:
        return {"current": files[0]["path"], "new": None,
                "reason": plan.get("error") or "No destination could be worked out"}
    return {"current": item.get("current_path") or files[0]["path"],
            "new": item.get("new_path") or None,
            "unchanged": bool(item.get("unchanged")),
            "collision": bool(item.get("collision")),
            "reason": item.get("reason")}


def preview_slot_move(database: Any, source_id: int, target_id: int, *,
                      config_manager: Any = None) -> Dict[str, Any]:
    """What a move would do, before anything changes."""
    with closing(database._get_connection()) as conn:
        found = _validate(conn, source_id, target_id)
        source, target, files = found["source"], found["target"], found["files"]
        same_album = source["album_id"] == target["album_id"]
        album_count = conn.execute("SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?",
                                   (source["album_id"],)).fetchone()[0]
        others_on_disk = conn.execute(
            "SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id"
            " WHERE t.album_id=? AND t.id<>? AND COALESCE(f.file_state,'active')"
            " NOT IN ('deleted','missing_confirmed') LIMIT 1",
            (source["album_id"], source["id"])).fetchone() is not None
        others_wanted = conn.execute(
            "SELECT 1 FROM lib2_tracks t JOIN lib2_wanted_tracks w ON w.track_id=t.id"
            " WHERE t.album_id=? AND t.id<>? AND w.wanted=1 LIMIT 1",
            (source["album_id"], source["id"])).fetchone() is not None
        tags = _tag_changes(conn, files, target["id"])
    organize = _organize_enabled(config_manager)
    try:
        path = (_planned_path(database, config_manager, source["id"], target, files)
                if organize else {"current": files[0]["path"], "new": None,
                                  "reason": "File organization is off, the file stays where it is"})
    except Exception as exc:  # noqa: BLE001 - the preview still shows the rest
        logger.warning("slot move path preview failed: %s", exc, exc_info=True)
        path = {"current": files[0]["path"], "new": None, "reason": "Couldn't work out the new path"}
    return {
        "success": True,
        "source": {**source, "files": [_file_summary(f) for f in files]},
        "target": target,
        "tags": tags,
        "path": path,
        "organize": organize,
        "source_album_left_empty": (not same_album and not others_on_disk
                                    and not others_wanted and album_count >= 1),
    }


def apply_slot_move(database: Any, source_id: int, target_id: int, *,
                    config_manager: Any = None, profile_id: int = 1,
                    write_tags_fn=None, enqueue_fn=None) -> Dict[str, Any]:
    """Move the track into the slot: library, tags, then folder."""
    from core.library2.entity_history import record_entity_move
    from core.library2.mirror_outbox import (
        drain_since, enqueue_tracks, intent_profile_ids, queue_mark)
    from core.library2.wanted import recompute_wanted

    with closing(database._get_connection()) as conn:
        found = _validate(conn, source_id, target_id)
        source, target, files = found["source"], found["target"], found["files"]
        mark = queue_mark(conn)
        profiles = (intent_profile_ids(conn, track_ids=[source["id"], target["id"]])
                    | {int(profile_id or 1), 1})
        moved = fold_track_into(conn, source["id"], target["id"])
        record_entity_move(conn, source_type="track", source_id=source["id"],
                           target_type="track", target_id=target["id"],
                           change_source="move_into_missing_slot",
                           context={"from_album_id": source["album_id"],
                                    "to_album_id": target["album_id"],
                                    "file_ids": moved})
        album_outcome = None
        if source["album_id"] != target["album_id"]:
            album_outcome = retire_emptied_album(conn, source["album_id"], profile_ids=profiles)
        for profile in sorted(profiles):
            recompute_wanted(conn, profile_id=profile, track_ids=[target["id"]])
            # the slot is filled: it is off every wishlist that asked for it
            enqueue_tracks(conn, [target["id"]], False, profile_id=profile)
        _touch_albums(conn, [source["album_id"], target["album_id"]])
        conn.commit()
    try:
        drain_since(database, mark)
    except Exception as exc:  # noqa: BLE001 - the mirror catches up later
        logger.debug("outbox drain after slot move skipped: %s", exc)
    if album_outcome == "deleted":
        _forget_artwork(database, source["album_id"])

    logger.info("Moved track %s ('%s', album %s) into missing slot %s ('%s', album %s), files %s",
                source["id"], source["title"], source["album_id"], target["id"],
                target["title"], target["album_id"], moved)

    # tags: what the target track says, written into the moved file(s)
    tags: Dict[str, Any]
    try:
        if write_tags_fn is None:
            from core.library2.retag import write_tags as write_tags_fn
        tags = write_tags_fn(database, [int(target["id"])])
    except Exception as exc:  # noqa: BLE001 - the library move stands
        logger.warning("retag after slot move failed for track %s: %s", target["id"], exc)
        tags = {"written": 0, "failed": 1, "errors": [str(exc)]}

    # folder: the reorganize queue, for this one track
    organize: Dict[str, Any]
    if not _organize_enabled(config_manager):
        organize = {"queued": False, "reason": "organization_off"}
    else:
        try:
            if enqueue_fn is None:
                enqueue_fn = _enqueue_track_reorganize
            organize = enqueue_fn(database, int(target["album_id"]), [int(target["id"])])
        except Exception as exc:  # noqa: BLE001
            logger.warning("reorganize after slot move failed for album %s: %s",
                           target["album_id"], exc)
            organize = {"queued": False, "reason": str(exc)}

    return {"success": True, "track_id": int(target["id"]), "removed_track_id": int(source["id"]),
            "moved_file_ids": moved, "source_album": album_outcome,
            "tags": {k: tags.get(k) for k in ("written", "skipped", "failed", "errors")},
            "organize": organize}


def _forget_artwork(database: Any, album_id: int) -> None:
    try:
        from core.library2.artwork import artwork_file, forget_artwork_versions, thumb_file
        for f in (artwork_file(database, "album", album_id), thumb_file(database, "album", album_id)):
            if f.exists():
                f.unlink()
        forget_artwork_versions(database, "album", album_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("artwork cleanup after slot move skipped: %s", exc)


def _enqueue_track_reorganize(database: Any, album_id: int, track_ids: List[int]) -> Dict[str, Any]:
    from core.reorganize_queue import get_queue
    meta = database.get_album_display_meta(album_id)
    if meta is None:
        return {"queued": False, "reason": "album_gone"}
    return get_queue().enqueue(album_id=str(album_id), album_title=meta["album_title"],
                               artist_id=meta["artist_id"], artist_name=meta["artist_name"],
                               track_ids=track_ids)


__all__ = ["SlotMoveError", "album_left_empty", "apply_slot_move", "fold_track_into",
           "preview_slot_move", "retire_emptied_album"]
