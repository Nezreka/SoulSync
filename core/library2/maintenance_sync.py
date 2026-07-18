"""Library-v2 boundary for legacy repair/maintenance mutations.

Library v2 is optional while the repair worker still contains jobs whose
catalogue queries use the legacy ``artists``/``albums``/``tracks`` tables.
Those jobs are allowed to reuse the mature file/tag implementations, but a
successful mutation must not leave ``lib2_track_files`` or the Library-v2
history stale.  This module is the one transitional compatibility boundary:

* strict ``features.library_v2 is True`` gate;
* attach stable Library-v2 identities to newly-created repair findings;
* reconcile a successful fix's path, verification, tag and quality facts;
* invalidate managed artwork after out-of-band embedded-art writes;
* append a compact entity-visible maintenance event.

The bridge never makes Library v2 depend on a media server.  Once the legacy
catalogue is removed, callers can keep reporting the same change contract and
the legacy-row projection helpers in this file can be deleted in one place.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

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
    "deleted_expired",
    "deleted_file",
    "removed",
    "removed_content",
    "removed_duplicates",
    "removed_single",
    "converted_and_deleted",
    "redownload",
    "relocated",
})

_ARTWORK_FINDINGS = frozenset({"missing_cover_art", "library_retag", "unknown_artist"})


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
        return config_manager.get("features.library_v2", False) is True
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
    artists = set(ids["artists"])
    albums = set(ids["albums"])
    tracks = set(ids["tracks"])
    files = set(ids["files"])

    entity_type = str(entity_type or "").strip().lower()
    if entity_id not in (None, ""):
        if entity_type == "track":
            rows = conn.execute(
                "SELECT id FROM lib2_tracks WHERE legacy_track_id=?", (entity_id,)
            ).fetchall()
            tracks.update(int(row[0]) for row in rows)
            rows = conn.execute(
                "SELECT id FROM lib2_track_files WHERE legacy_track_id=?", (entity_id,)
            ).fetchall()
            files.update(int(row[0]) for row in rows)
        elif entity_type == "album":
            rows = conn.execute(
                "SELECT id FROM lib2_albums WHERE legacy_album_id=?", (entity_id,)
            ).fetchall()
            albums.update(int(row[0]) for row in rows)
        elif entity_type == "artist":
            rows = conn.execute(
                "SELECT id FROM lib2_artists WHERE legacy_artist_id=?", (entity_id,)
            ).fetchall()
            artists.update(int(row[0]) for row in rows)

    candidate_paths = {
        str(value).strip()
        for value in (
            file_path,
            details.get("file_path"),
            details.get("original_path"),
            details.get("from_abs"),
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

    # Cross-container/media-server paths only pay the resolver scan when the
    # indexed/identity fast paths found nothing.
    if candidate_paths and not files:
        wanted = {_normal_path(path) for path in candidate_paths}
        wanted.discard(None)
        try:
            from core.library2.paths import resolve_lib2_path

            for row in conn.execute(
                "SELECT id, path FROM lib2_track_files "
                "WHERE path IS NOT NULL AND path<>'' AND "
                "COALESCE(file_state,'active')<>'deleted'"
            ).fetchall():
                raw = _normal_path(row["path"])
                resolved = resolve_lib2_path(
                    row["path"], config_manager=config_manager,
                )
                if raw in wanted or _normal_path(resolved) in wanted:
                    files.add(int(row["id"]))
        except Exception as exc:  # noqa: BLE001
            logger.debug("mapped path subject resolution failed: %s", exc)

    if files:
        rows = conn.execute(
            f"SELECT DISTINCT track_id FROM lib2_track_files "
            f"WHERE id IN ({_marks(sorted(files))}) AND track_id IS NOT NULL",
            sorted(files),
        ).fetchall()
        tracks.update(int(row[0]) for row in rows)
    if tracks:
        rows = conn.execute(
            f"SELECT DISTINCT album_id FROM lib2_tracks "
            f"WHERE id IN ({_marks(sorted(tracks))}) AND album_id IS NOT NULL",
            sorted(tracks),
        ).fetchall()
        albums.update(int(row[0]) for row in rows)
    if albums:
        rows = conn.execute(
            f"SELECT DISTINCT artist_id FROM lib2_album_artists "
            f"WHERE album_id IN ({_marks(sorted(albums))})",
            sorted(albums),
        ).fetchall()
        artists.update(int(row[0]) for row in rows)

        # Album-level findings (cover art, album retag, completeness) need all
        # child tracks/files even without a representative path. A track/file
        # finding still keeps album+artist ancestry for cache/history, but must
        # not rescan or emit events for every sibling on the release.
        if entity_type == "album":
            rows = conn.execute(
                f"SELECT id FROM lib2_tracks WHERE album_id IN ({_marks(sorted(albums))})",
                sorted(albums),
            ).fetchall()
            tracks.update(int(row[0]) for row in rows)
            if tracks:
                rows = conn.execute(
                    f"SELECT id FROM lib2_track_files WHERE track_id IN "
                    f"({_marks(sorted(tracks))}) AND COALESCE(file_state,'active')<>'deleted'",
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
    """Return finding details enriched with stable Library-v2 subjects.

    Disabled features and pre-v2 databases return the original details.  The
    annotation is metadata only; creating a finding never creates lib2 rows.
    """
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


def v2_uncovered_file_subjects(database: Any, config_manager: Any) -> List[Dict[str, Any]]:
    """Return active V2 files a legacy ``tracks`` scan cannot enumerate.

    During the transition this is the shared P1 subject seam for mature
    file-oriented tools. A derivative/autolinked file may belong to a mapped
    V2 track while having no ``legacy_track_id`` of its own; it still needs
    ReplayGain, lyrics, verification and corruption checks. When the legacy
    table is eventually gone the same query naturally returns every active
    V2 file. Feature-off and pre-schema installs are strict no-ops.
    """
    if not library_v2_enabled(config_manager):
        return []
    conn = database._get_connection()
    try:
        if not _table_exists(conn, "lib2_track_files"):
            return []
        legacy_filter = ""
        if _table_exists(conn, "tracks"):
            legacy_filter = (
                "AND (f.legacy_track_id IS NULL OR NOT EXISTS "
                "(SELECT 1 FROM tracks legacy WHERE legacy.id=f.legacy_track_id))"
            )
        rows = conn.execute(
            f"""SELECT f.id AS file_id, f.track_id, f.path,
                       t.album_id, t.title, t.duration,
                       al.title AS album_title,
                       COALESCE(
                         (SELECT ar.name
                            FROM lib2_track_artists ta
                            JOIN lib2_artists ar ON ar.id=ta.artist_id
                           WHERE ta.track_id=t.id
                           ORDER BY CASE ta.role WHEN 'primary' THEN 0 ELSE 1 END,
                                    ta.position, ar.id
                           LIMIT 1),
                         primary_artist.name
                       ) AS artist_name,
                       al.primary_artist_id AS artist_id
                  FROM lib2_track_files f
                  JOIN lib2_tracks t ON t.id=f.track_id
                  JOIN lib2_albums al ON al.id=t.album_id
             LEFT JOIN lib2_artists primary_artist
                    ON primary_artist.id=al.primary_artist_id
                 WHERE f.path IS NOT NULL AND f.path<>''
                   AND COALESCE(f.file_state,'active')='active'
                   {legacy_filter}
              ORDER BY al.id, t.disc_number, t.track_number, f.id"""
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def v2_subject_details(subject: Mapping[str, Any]) -> Dict[str, Any]:
    """Stable finding payload for a row from :func:`v2_uncovered_file_subjects`."""
    return {
        "library_v2_native": True,
        "library_v2": {
            "artist_id": subject.get("artist_id"),
            "album_id": subject.get("album_id"),
            "track_id": subject.get("track_id"),
            "file_id": subject.get("file_id"),
            "artist_ids": _as_ints([subject.get("artist_id")]),
            "album_ids": _as_ints([subject.get("album_id")]),
            "track_ids": _as_ints([subject.get("track_id")]),
            "file_ids": _as_ints([subject.get("file_id")]),
        },
    }


def _columns(conn: Any, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _update_static_fields(
    conn: Any, table: str, row_id: int, values: Mapping[str, Any],
) -> List[str]:
    allowed = _columns(conn, table)
    updates = {key: value for key, value in values.items() if key in allowed}
    if not updates:
        return []
    assignments = ", ".join(f"{key}=?" for key in updates)
    conn.execute(
        f"UPDATE {table} SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (*updates.values(), int(row_id)),
    )
    return sorted(updates)


def _sync_legacy_projection(conn: Any, links: Mapping[str, List[int]]) -> set[str]:
    """Targeted transitional legacy→lib2 projection for linked subjects."""
    changed: set[str] = set()
    if not _table_exists(conn, "tracks"):
        return changed
    legacy_track_columns = _columns(conn, "tracks")
    track_map = {
        "title": "title",
        "track_number": "track_number",
        "disc_number": "disc_number",
        "duration": "duration",
        "isrc": "isrc",
        "musicbrainz_recording_id": "musicbrainz_id",
        "spotify_track_id": "spotify_id",
        "bpm": "bpm",
        "explicit": "explicit",
        "genius_lyrics": "genius_lyrics",
        "copyright": "copyright",
        "style": "style",
        "mood": "mood",
        "play_count": "play_count",
        "last_played": "last_played",
    }
    for track_id in links["tracks"]:
        lib2_row = conn.execute(
            "SELECT legacy_track_id FROM lib2_tracks WHERE id=?", (track_id,)
        ).fetchone()
        if not lib2_row or lib2_row[0] is None:
            continue
        legacy_id = lib2_row[0]
        legacy = conn.execute("SELECT * FROM tracks WHERE id=?", (legacy_id,)).fetchone()
        if legacy is None:
            continue
        values = {
            target: legacy[source]
            for source, target in track_map.items()
            if source in legacy_track_columns
        }
        changed.update(_update_static_fields(conn, "lib2_tracks", track_id, values))
        file_path = legacy["file_path"] if "file_path" in legacy_track_columns else None
        file_values: Dict[str, Any] = {}
        for source, target in (
            ("file_size", "size"),
            ("bitrate", "bitrate"),
            ("sample_rate", "sample_rate"),
            ("bit_depth", "bit_depth"),
            ("verification_status", "verification_status"),
        ):
            if source in legacy_track_columns:
                file_values[target] = legacy[source]
        if file_path:
            file_values["path"] = file_path
            file_values["format"] = (
                str(file_path).rsplit(".", 1)[-1].lower() if "." in str(file_path) else None
            )
            file_values["file_state"] = "active"
            file_values["missing_since"] = None
            file_values["missing_scan_count"] = 0
        if file_values:
            assignments = ", ".join(f"{key}=?" for key in file_values)
            conn.execute(
                f"UPDATE lib2_track_files SET {assignments}, "
                "updated_at=CURRENT_TIMESTAMP WHERE legacy_track_id=?",
                (*file_values.values(), legacy_id),
            )
            changed.update(file_values)

    if _table_exists(conn, "albums"):
        album_columns = _columns(conn, "albums")
        album_map = {
            "title": "title",
            "year": "year",
            "release_date": "release_date",
            "thumb_url": "image_url",
            "genres": "genres",
            "track_count": "track_count",
            "explicit": "explicit",
            "label": "label",
            "upc": "upc",
            "style": "style",
            "mood": "mood",
        }
        for album_id in links["albums"]:
            row = conn.execute(
                "SELECT legacy_album_id FROM lib2_albums WHERE id=?", (album_id,)
            ).fetchone()
            if not row or row[0] is None:
                continue
            legacy = conn.execute("SELECT * FROM albums WHERE id=?", (row[0],)).fetchone()
            if legacy is None:
                continue
            values = {
                target: legacy[source]
                for source, target in album_map.items()
                if source in album_columns
            }
            changed.update(_update_static_fields(conn, "lib2_albums", album_id, values))
    if _table_exists(conn, "artists"):
        artist_columns = _columns(conn, "artists")
        artist_map = {
            "name": "name",
            "sort_name": "sort_name",
            "thumb_url": "image_url",
            "genres": "genres",
            "summary": "summary",
            "style": "style",
            "mood": "mood",
            "label": "label",
            "banner_url": "banner_url",
        }
        for artist_id in links["artists"]:
            row = conn.execute(
                "SELECT legacy_artist_id FROM lib2_artists WHERE id=?", (artist_id,)
            ).fetchone()
            if not row or row[0] is None:
                continue
            legacy = conn.execute("SELECT * FROM artists WHERE id=?", (row[0],)).fetchone()
            if legacy is None:
                continue
            values = {
                target: legacy[source]
                for source, target in artist_map.items()
                if source in artist_columns
            }
            changed.update(_update_static_fields(conn, "lib2_artists", artist_id, values))
    return changed


def _link_new_output_file(
    conn: Any, links: Mapping[str, List[int]], result: Mapping[str, Any],
) -> Optional[int]:
    output_path = str(result.get("output_path") or "").strip()
    if not output_path or not os.path.isfile(output_path) or len(links["tracks"]) != 1:
        return None
    existing = conn.execute(
        "SELECT id FROM lib2_track_files WHERE path=?", (output_path,)
    ).fetchone()
    if existing:
        return int(existing[0])
    track_id = links["tracks"][0]
    cursor = conn.execute(
        """INSERT INTO lib2_track_files(
               track_id, path, size, format, import_status, file_state, source)
           VALUES(?,?,?,?, 'imported', 'active', 'repair_job')""",
        (
            track_id,
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
            for row in rows if row["track_id"] is not None
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
            for row in rows if row["album_id"] is not None
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
            for row in rows if row["primary_artist_id"] is not None
        }

    subjects: List[tuple[Optional[int], Optional[int], Optional[int], Optional[int]]] = []
    for file_id in links["files"]:
        track_id = file_to_track.get(file_id)
        album_id = track_to_album.get(track_id) if track_id else None
        subjects.append((album_to_artist.get(album_id) if album_id else None,
                         album_id, track_id, file_id))
    covered_tracks = {subject[2] for subject in subjects}
    for track_id in links["tracks"]:
        if track_id in covered_tracks:
            continue
        album_id = track_to_album.get(track_id)
        subjects.append((album_to_artist.get(album_id) if album_id else None,
                         album_id, track_id, None))
    if not subjects:
        for album_id in links["albums"] or [None]:
            subjects.append((album_to_artist.get(album_id) if album_id else None,
                             album_id, None, None))

    for artist_id, album_id, track_id, file_id in subjects:
        conn.execute(
            """INSERT INTO lib2_maintenance_events(
                   job_id, finding_type, action, entity_type, entity_id,
                   lib2_artist_id, lib2_album_id, lib2_track_id, lib2_file_id,
                   changed_fields_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                str(job_id), finding_type, str(action), entity_type,
                None if entity_id is None else str(entity_id),
                artist_id, album_id, track_id, file_id, payload,
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
    """Reconcile one successful repair mutation into Library v2.

    The return shape is intentionally diagnostic and JSON-safe.  No-op reasons
    distinguish disabled Library v2 from an unlinked legacy-only subject.
    """
    if not library_v2_enabled(config_manager):
        return {"enabled": False, "reason": "feature_disabled"}
    details = dict(details or {})
    result = dict(result or {})

    # Identity repairs can change album/artist junction ownership.  Reuse the
    # idempotent importer for this rare, high-risk transition; all ordinary
    # tag/path/file fixes stay targeted below.
    if job_id == "unknown_artist_fixer" and action == "fixed_unknown_artist":
        from core.library2 import ADMIN_PROFILE_ID
        from core.library2.importer import import_legacy_library

        import_legacy_library(database, profile_id=ADMIN_PROFILE_ID)
    elif job_id == "album_completeness" and int(result.get("fixed") or 0) > 0:
        # This mature repair can move a legacy single or create a copied legacy
        # track. Reusing the idempotent importer is the safe transition path:
        # the new row/file and changed album relationships become native V2
        # subjects before the targeted rescan/history work below.
        from core.library2 import ADMIN_PROFILE_ID
        from core.library2.importer import import_legacy_library

        import_legacy_library(database, profile_id=ADMIN_PROFILE_ID)

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
        changed_fields: set[str] = set()
        deleting = (
            action in _DELETE_ACTIONS
            or result.get("library_v2_file_deleted") is True
        )
        if deleting and links["files"]:
            from core.library2.track_files import set_file_state

            for file_id in links["files"]:
                if set_file_state(conn, file_id, "deleted"):
                    changed_fields.add("file_state")
        else:
            changed_fields.update(_sync_legacy_projection(conn, links))

        new_file_id = _link_new_output_file(conn, links, result)
        if new_file_id is not None:
            links["files"] = sorted(set(links["files"]) | {new_file_id})
            changed_fields.update({"new_file", "quality"})
        conn.commit()
    finally:
        conn.close()

    # Re-read only linked live files after all legacy/job transactions have
    # closed. This refreshes ReplayGain/lyrics/cover flags and quality facts.
    scan_stats = {"scanned": 0, "updated": 0, "missing": 0}
    if links["files"] and not deleting and effects.intersection(
        {"tags", "path", "new_file", "metadata", "artwork"}
    ):
        from core.library2.scan import rescan_files

        scan_stats = rescan_files(database, file_ids=links["files"])
        if scan_stats["scanned"]:
            changed_fields.add("file_snapshot")

    artwork_invalidated = 0
    if effects.intersection({"artwork"}) or finding_type in _ARTWORK_FINDINGS:
        try:
            from core.library2.artwork import invalidate_artwork

            for album_id in links["albums"]:
                artwork_invalidated += invalidate_artwork(database, "album", album_id)
            for artist_id in links["artists"]:
                artwork_invalidated += invalidate_artwork(database, "artist", artist_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Library-v2 artwork invalidation failed: %s", exc)

    if deleting or new_file_id is not None or "wanted" in effects:
        conn = database._get_connection()
        try:
            from core.library2 import ADMIN_PROFILE_ID
            from core.library2.wanted import ensure_wanted_schema, recompute_wanted

            ensure_wanted_schema(conn.cursor())
            recompute_wanted(conn.cursor(), profile_id=ADMIN_PROFILE_ID)
            conn.commit()
            changed_fields.add("wanted")
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
            changed_fields=sorted(changed_fields or effects - {"observe", "none"}),
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
        "scan": scan_stats,
    }


__all__ = [
    "LIB2_MAINTENANCE_EVENTS_DDL",
    "annotate_finding_details",
    "ensure_maintenance_event_schema",
    "library_v2_enabled",
    "sync_repair_change",
    "v2_subject_details",
    "v2_uncovered_file_subjects",
]
