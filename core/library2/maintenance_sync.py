"""Native Library-v2 maintenance change boundary.

P3 removes the legacy-catalogue projection from repair tools. Jobs mutate the
Library-v2 model directly and report successful changes here so file snapshots,
artwork caches, wanted state and entity history converge through one boundary.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from core.library2.maintenance_subjects import (
    active_album_subjects,
    active_file_subjects,
    subject_details,
)
from utils.logging_config import get_logger

logger = get_logger("library2.maintenance_sync")


LIB2_MAINTENANCE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_maintenance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    finding_type TEXT,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    lib2_artist_id INTEGER,
    lib2_album_id INTEGER,
    lib2_track_id INTEGER,
    lib2_file_id INTEGER,
    changed_fields_json TEXT NOT NULL DEFAULT '[]',
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_DELETE_ACTIONS = frozenset({
    "deleted",
    "deleted_file",
    "removed",
    "removed_content",
    "removed_duplicates",
    "removed_single",
    "converted_and_deleted",
    "redownload",
    "relocated",
})
_ARTWORK_FINDINGS = frozenset({"missing_cover_art"})


def ensure_maintenance_event_schema(cursor: Any) -> None:
    cursor.execute(LIB2_MAINTENANCE_EVENTS_DDL)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_maintenance_events_track "
        "ON lib2_maintenance_events(lib2_track_id, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_maintenance_events_album "
        "ON lib2_maintenance_events(lib2_album_id, id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_maintenance_events_artist "
        "ON lib2_maintenance_events(lib2_artist_id, id)"
    )


def library_v2_enabled(config_manager: Any) -> bool:
    if config_manager is None:
        return False
    try:
        return config_manager.get("features.library_v2", True) is True
    except Exception:  # noqa: BLE001
        return False


def _table_exists(conn: Any, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _as_ints(values: Iterable[Any]) -> List[int]:
    result: set[int] = set()
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            result.add(number)
    return sorted(result)


def _marks(values: Sequence[Any]) -> str:
    return ",".join("?" for _ in values)


def _normal_path(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return os.path.normcase(os.path.abspath(os.path.normpath(text)))


def _details_lib2_ids(details: Mapping[str, Any]) -> Dict[str, List[int]]:
    linked = details.get("library_v2")
    if not isinstance(linked, Mapping):
        return {"artists": [], "albums": [], "tracks": [], "files": []}
    return {
        "artists": _as_ints(linked.get("artist_ids") or [linked.get("artist_id")]),
        "albums": _as_ints(linked.get("album_ids") or [linked.get("album_id")]),
        "tracks": _as_ints(linked.get("track_ids") or [linked.get("track_id")]),
        "files": _as_ints(linked.get("file_ids") or [linked.get("file_id")]),
    }


def _native_entity_id(value: Any) -> Optional[int]:
    """Parse an explicitly native ``lib2:<id>`` finding identity.

    Bare numeric IDs remain ambiguous with historical findings and are never
    interpreted as native row IDs.
    """

    text = str(value or "").strip()
    if not text.startswith("lib2:"):
        return None
    try:
        number = int(text.split(":", 1)[1])
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _resolve_links(
    conn: Any,
    *,
    entity_type: Optional[str],
    entity_id: Any,
    file_path: Optional[str],
    details: Mapping[str, Any],
    config_manager: Any,
) -> Dict[str, List[int]]:
    ids = _details_lib2_ids(details)
    artists, albums = set(ids["artists"]), set(ids["albums"])
    tracks, files = set(ids["tracks"]), set(ids["files"])

    entity_type = str(entity_type or "").strip().lower()
    native_id = _native_entity_id(entity_id)
    entity_tables = {
        "artist": ("lib2_artists", artists),
        "album": ("lib2_albums", albums),
        "track": ("lib2_tracks", tracks),
        "file": ("lib2_track_files", files),
    }
    if native_id is not None and entity_type in entity_tables:
        table, target = entity_tables[entity_type]
        if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (native_id,)).fetchone():
            target.add(native_id)

    candidate_paths = {
        str(value).strip()
        for value in (
            file_path,
            details.get("file_path"),
            details.get("original_path"),
            details.get("from_abs"),
            details.get("to_abs"),
        )
        if str(value or "").strip()
    }
    if candidate_paths:
        path_list = sorted(candidate_paths)
        rows = conn.execute(
            f"SELECT id FROM lib2_track_files WHERE path IN ({_marks(path_list)})",
            path_list,
        ).fetchall()
        files.update(int(row[0]) for row in rows)
    if candidate_paths and not files:
        wanted = {_normal_path(path) for path in candidate_paths}
        wanted.discard(None)
        try:
            from core.library2.paths import resolve_lib2_path

            for row in conn.execute(
                "SELECT id, path FROM lib2_track_files WHERE path IS NOT NULL "
                "AND path<>'' AND COALESCE(file_state,'active')<>'deleted'"
            ).fetchall():
                resolved = resolve_lib2_path(row["path"], config_manager=config_manager)
                if _normal_path(row["path"]) in wanted or _normal_path(resolved) in wanted:
                    files.add(int(row["id"]))
        except Exception as exc:  # noqa: BLE001
            logger.debug("mapped path subject resolution failed: %s", exc)

    if files:
        rows = conn.execute(
            f"SELECT DISTINCT track_id FROM lib2_track_files WHERE id IN "
            f"({_marks(sorted(files))}) AND track_id IS NOT NULL",
            sorted(files),
        ).fetchall()
        tracks.update(int(row[0]) for row in rows)
    if tracks:
        rows = conn.execute(
            f"SELECT DISTINCT album_id FROM lib2_tracks WHERE id IN "
            f"({_marks(sorted(tracks))}) AND album_id IS NOT NULL",
            sorted(tracks),
        ).fetchall()
        albums.update(int(row[0]) for row in rows)
    if albums:
        rows = conn.execute(
            f"SELECT DISTINCT artist_id FROM lib2_album_artists WHERE album_id IN "
            f"({_marks(sorted(albums))})",
            sorted(albums),
        ).fetchall()
        artists.update(int(row[0]) for row in rows)
        if entity_type == "album":
            rows = conn.execute(
                f"SELECT id FROM lib2_tracks WHERE album_id IN "
                f"({_marks(sorted(albums))})",
                sorted(albums),
            ).fetchall()
            tracks.update(int(row[0]) for row in rows)
            if tracks:
                rows = conn.execute(
                    f"SELECT id FROM lib2_track_files WHERE track_id IN "
                    f"({_marks(sorted(tracks))}) AND "
                    "COALESCE(file_state,'active')<>'deleted'",
                    sorted(tracks),
                ).fetchall()
                files.update(int(row[0]) for row in rows)
    return {
        "artists": sorted(artists),
        "albums": sorted(albums),
        "tracks": sorted(tracks),
        "files": sorted(files),
    }


def annotate_finding_details(
    database: Any,
    config_manager: Any,
    *,
    entity_type: Optional[str],
    entity_id: Any,
    file_path: Optional[str],
    details: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Attach stable native identities without creating catalogue rows."""

    payload = dict(details or {})
    if not library_v2_enabled(config_manager):
        return payload
    conn = database._get_connection()
    try:
        if not _table_exists(conn, "lib2_track_files"):
            return payload
        links = _resolve_links(
            conn,
            entity_type=entity_type,
            entity_id=entity_id,
            file_path=file_path,
            details=payload,
            config_manager=config_manager,
        )
    finally:
        conn.close()
    if any(links.values()):
        payload["library_v2"] = {
            "artist_id": links["artists"][0] if links["artists"] else None,
            "album_id": links["albums"][0] if links["albums"] else None,
            "track_id": links["tracks"][0] if links["tracks"] else None,
            "file_id": links["files"][0] if links["files"] else None,
            "artist_ids": links["artists"][:100],
            "album_ids": links["albums"][:100],
            "track_ids": links["tracks"][:500],
            "file_ids": links["files"][:500],
        }
    return payload


def _link_new_output_file(
    conn: Any, links: Mapping[str, List[int]], result: Mapping[str, Any]
) -> Optional[int]:
    output_path = str(result.get("output_path") or "").strip()
    if not output_path or not os.path.isfile(output_path) or len(links["tracks"]) != 1:
        return None
    existing = conn.execute(
        "SELECT id FROM lib2_track_files WHERE path=?", (output_path,)
    ).fetchone()
    if existing:
        return int(existing[0])
    cursor = conn.execute(
        """INSERT INTO lib2_track_files(
               track_id, path, size, format, import_status, file_state, source)
           VALUES(?,?,?,?, 'imported', 'active', 'repair_job')""",
        (
            links["tracks"][0],
            output_path,
            os.path.getsize(output_path),
            output_path.rsplit(".", 1)[-1].lower() if "." in output_path else None,
        ),
    )
    return int(cursor.lastrowid)


def _record_events(
    conn: Any,
    *,
    job_id: str,
    finding_type: Optional[str],
    action: str,
    entity_type: Optional[str],
    entity_id: Any,
    links: Mapping[str, List[int]],
    changed_fields: Sequence[str],
) -> int:
    ensure_maintenance_event_schema(conn.cursor())
    payload = json.dumps(sorted(set(changed_fields)), separators=(",", ":"))
    file_to_track: Dict[int, int] = {}
    if links["files"]:
        rows = conn.execute(
            f"SELECT id, track_id FROM lib2_track_files WHERE id IN "
            f"({_marks(links['files'])})",
            links["files"],
        ).fetchall()
        file_to_track = {
            int(row["id"]): int(row["track_id"])
            for row in rows
            if row["track_id"] is not None
        }
    track_to_album: Dict[int, int] = {}
    if links["tracks"]:
        rows = conn.execute(
            f"SELECT id, album_id FROM lib2_tracks WHERE id IN "
            f"({_marks(links['tracks'])})",
            links["tracks"],
        ).fetchall()
        track_to_album = {
            int(row["id"]): int(row["album_id"])
            for row in rows
            if row["album_id"] is not None
        }
    album_to_artist: Dict[int, int] = {}
    if links["albums"]:
        rows = conn.execute(
            f"SELECT id, primary_artist_id FROM lib2_albums WHERE id IN "
            f"({_marks(links['albums'])})",
            links["albums"],
        ).fetchall()
        album_to_artist = {
            int(row["id"]): int(row["primary_artist_id"])
            for row in rows
            if row["primary_artist_id"] is not None
        }

    subjects: List[tuple[Optional[int], Optional[int], Optional[int], Optional[int]]] = []
    for file_id in links["files"]:
        track_id = file_to_track.get(file_id)
        album_id = track_to_album.get(track_id) if track_id else None
        subjects.append(
            (album_to_artist.get(album_id) if album_id else None, album_id, track_id, file_id)
        )
    covered_tracks = {subject[2] for subject in subjects}
    for track_id in links["tracks"]:
        if track_id in covered_tracks:
            continue
        album_id = track_to_album.get(track_id)
        subjects.append(
            (album_to_artist.get(album_id) if album_id else None, album_id, track_id, None)
        )
    covered_albums = {subject[1] for subject in subjects}
    for album_id in links["albums"]:
        if album_id not in covered_albums:
            subjects.append((album_to_artist.get(album_id), album_id, None, None))
    covered_artists = {subject[0] for subject in subjects}
    for artist_id in links["artists"]:
        if artist_id not in covered_artists:
            subjects.append((artist_id, None, None, None))

    for artist_id, album_id, track_id, file_id in subjects:
        conn.execute(
            """INSERT INTO lib2_maintenance_events(
                   job_id, finding_type, action, entity_type, entity_id,
                   lib2_artist_id, lib2_album_id, lib2_track_id, lib2_file_id,
                   changed_fields_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                str(job_id),
                finding_type,
                str(action),
                entity_type,
                None if entity_id is None else str(entity_id),
                artist_id,
                album_id,
                track_id,
                file_id,
                payload,
            ),
        )
    return len(subjects)


def sync_repair_change(
    database: Any,
    config_manager: Any,
    *,
    job_id: str,
    finding_type: Optional[str] = None,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Any = None,
    file_path: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
    result: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Finalize one successful native repair mutation."""

    if not library_v2_enabled(config_manager):
        return {"enabled": False, "reason": "feature_disabled"}
    details, result = dict(details or {}), dict(result or {})
    conn = database._get_connection()
    try:
        if not _table_exists(conn, "lib2_track_files"):
            return {"enabled": True, "reason": "schema_missing"}
        links = _resolve_links(
            conn,
            entity_type=entity_type,
            entity_id=entity_id,
            file_path=file_path,
            details=details,
            config_manager=config_manager,
        )
        if not any(links.values()):
            return {"enabled": True, "reason": "subject_unlinked"}

        from core.repair_jobs import JOB_LIBRARY_V2_EFFECTS

        effects = set(JOB_LIBRARY_V2_EFFECTS.get(job_id, frozenset({"observe"})))
        changed_fields: set[str] = set(effects - {"observe", "none"})
        deleting = action in _DELETE_ACTIONS or result.get("library_v2_file_deleted") is True
        if deleting and links["files"]:
            from core.library2.track_files import set_file_state

            for file_id in links["files"]:
                if set_file_state(conn, file_id, "deleted"):
                    changed_fields.add("file_state")
        new_file_id = _link_new_output_file(conn, links, result)
        if new_file_id is not None:
            links["files"] = sorted(set(links["files"]) | {new_file_id})
            changed_fields.update({"new_file", "quality"})
        conn.commit()
    finally:
        conn.close()

    scan_stats = {"scanned": 0, "updated": 0, "missing": 0}
    if links["files"] and not deleting and effects.intersection(
        {"tags", "path", "new_file", "metadata", "artwork"}
    ):
        from core.library2.scan import rescan_files

        scan_stats = rescan_files(database, file_ids=links["files"])
        if scan_stats["scanned"]:
            changed_fields.add("file_snapshot")

    artwork_invalidated = 0
    if "artwork" in effects or finding_type in _ARTWORK_FINDINGS:
        try:
            from core.library2.artwork import invalidate_artwork

            for album_id in links["albums"]:
                artwork_invalidated += invalidate_artwork(database, "album", album_id)
            for artist_id in links["artists"]:
                artwork_invalidated += invalidate_artwork(database, "artist", artist_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Library-v2 artwork invalidation failed: %s", exc)

    mirrored = 0
    if deleting or new_file_id is not None or "wanted" in effects:
        conn = database._get_connection()
        try:
            from core.library2 import ADMIN_PROFILE_ID
            from core.library2.wanted import ensure_wanted_schema, recompute_wanted

            ensure_wanted_schema(conn.cursor())
            recompute_wanted(conn.cursor(), profile_id=ADMIN_PROFILE_ID)
            conn.commit()
            changed_fields.add("wanted")
            if links["tracks"]:
                from core.library2.wishlist_mirror import (
                    mirror_projected_tracks_wishlist,
                )
                mirrored = mirror_projected_tracks_wishlist(
                    database,
                    conn,
                    links["tracks"],
                    profile_id=ADMIN_PROFILE_ID,
                )
        finally:
            conn.close()

    conn = database._get_connection()
    try:
        events = _record_events(
            conn,
            job_id=job_id,
            finding_type=finding_type,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            links=links,
            changed_fields=sorted(changed_fields),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "enabled": True,
        "reason": "synchronized",
        "artists": len(links["artists"]),
        "albums": len(links["albums"]),
        "tracks": len(links["tracks"]),
        "files": len(links["files"]),
        "events": events,
        "artwork_invalidated": artwork_invalidated,
        "wishlist_mirrored": mirrored,
        "scan": scan_stats,
    }


__all__ = [
    "LIB2_MAINTENANCE_EVENTS_DDL",
    "annotate_finding_details",
    "ensure_maintenance_event_schema",
    "library_v2_enabled",
    "sync_repair_change",
]
