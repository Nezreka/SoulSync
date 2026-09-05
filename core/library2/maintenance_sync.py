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


# entity_type -> (native table, legacy backref column). ``file`` is absent on
# purpose: ``lib2_track_files.legacy_track_id`` points at a legacy *track*, so a
# legacy id can name several file rows and is not a file identity.
_LEGACY_BACKREFS = {
    "artist": ("lib2_artists", "legacy_artist_id"),
    "album": ("lib2_albums", "legacy_album_id"),
    "track": ("lib2_tracks", "legacy_track_id"),
}


def _legacy_backref_ids(conn: Any, entity_type: str, entity_id: Any) -> List[int]:
    """Native row ids for a finding that names a *legacy* catalogue row.

    Findings written before the P3 cutover — and by the jobs that still scan
    legacy tables — carry a bare legacy id and no ``details['library_v2']``
    block, so every other lookup in :func:`_resolve_links` misses and the repair
    converges nowhere (issues.md T-01).  The importer stored the backref on the
    native row, so this is a hard stored id, not a name heuristic (Guide §2.5).

    Legacy ids are opaque ``TEXT`` (Guide §5) — compared as text, never
    ``int()``-coerced.
    """
    lookup = _LEGACY_BACKREFS.get(entity_type)
    text = str(entity_id or "").strip()
    if not lookup or not text or text.startswith("lib2:"):
        return []
    table, column = lookup
    try:
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE CAST({column} AS TEXT)=?", (text,)
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 — pre-migration schema
        logger.debug("legacy backref lookup failed (%s.%s): %s", table, column, exc)
        return []
    return _as_ints(row[0] for row in rows)


def _may_have_catalogue_row(entity_type: str, entity_id: Any) -> bool:
    """Can this subject possibly correspond to a ``lib2_track_files`` row?

    Two finding shapes provably cannot, and both used to fall through into the
    path-mapping fallback below and pay for a whole-library filesystem walk to
    prove the obvious:

    - ``orphan_file`` (``entity_type='file'``, ``entity_id=None``) — an orphan
      is *defined* as a file on disk that the catalogue does not know. The
      exact-path query above having found nothing IS the finding.
    - ``empty_folder`` (``entity_type='folder'``) — the subject is a directory,
      and ``lib2_track_files.path`` only ever holds files.

    A scan producing 2,000 orphans against 50,000 file rows did 100 million
    path resolutions, each with at least one ``stat``, to return an empty link
    set every time (perf/bug-audit BUG-01).
    """
    if entity_type == "folder":
        return False
    if entity_type == "file" and _native_entity_id(entity_id) is None \
            and not str(entity_id or "").strip():
        return False
    return True


def _files_by_mapped_path(
    conn: Any, candidate_paths: set, *, config_manager: Any,
) -> List[int]:
    """File ids whose stored path *resolves* onto one of ``candidate_paths``.

    The stored path is the legacy/media-server view of the filesystem, so on a
    path-mapped setup it never equals the path a repair job observed — hence a
    resolver pass rather than a second equality test.

    Bounded on purpose. This used to read every non-deleted row in
    ``lib2_track_files`` and call ``resolve_lib2_path`` on each, per finding.
    A rename or a path mapping changes directories, essentially never the
    basename, so probing by basename narrows the candidate set from the whole
    library to a handful of rows before any filesystem call happens.
    """
    wanted = {_normal_path(path) for path in candidate_paths}
    wanted.discard(None)
    if not wanted:
        return []
    found: List[int] = []
    try:
        from core.library2.paths import resolve_lib2_path

        # Stored paths keep whichever separator the writer used, so probe both
        # -- the same two-pattern idiom track_files.find_track_id_by_path uses.
        basenames = {
            os.path.basename(str(path).replace("\\", "/").rstrip("/"))
            for path in candidate_paths
        }
        basenames.discard("")
        if not basenames:
            return []
        rows = []
        for name in sorted(basenames):
            escaped = _like_escape(name)
            rows.extend(conn.execute(
                "SELECT id, path FROM lib2_track_files "
                "WHERE path IS NOT NULL AND path<>'' "
                "  AND COALESCE(file_state,'active')<>'deleted' "
                "  AND (path=? OR path LIKE ? ESCAPE '^' OR path LIKE ? ESCAPE '^')",
                (name, f"%/{escaped}", f"%\\{escaped}"),
            ).fetchall())
        for row in rows:
            if _normal_path(row["path"]) in wanted:
                found.append(int(row["id"]))
                continue
            resolved = resolve_lib2_path(row["path"], config_manager=config_manager)
            if _normal_path(resolved) in wanted:
                found.append(int(row["id"]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("mapped path subject resolution failed: %s", exc)
    return found


def _like_escape(value: str) -> str:
    """Escape LIKE wildcards so a filename containing % or _ still matches.

    Uses ``^`` as the escape character rather than a backslash, because the
    patterns themselves contain a literal backslash (the Windows separator) and
    a backslash escape would then have to escape itself inside a Python string
    inside a SQL string. ``^`` is not special to LIKE and is vanishingly rare in
    a filename -- and it is escaped here too, so even that case is correct.
    """
    return (value.replace("^", "^^").replace("%", "^%").replace("_", "^_"))


def _resolve_links(
    conn: Any,
    *,
    entity_type: Optional[str],
    entity_id: Any,
    file_path: Optional[str],
    details: Mapping[str, Any],
    config_manager: Any,
    artist_files: bool = False,
) -> Dict[str, List[int]]:
    ids = _details_lib2_ids(details)
    artists, albums = set(ids["artists"]), set(ids["albums"])
    tracks, files = set(ids["tracks"]), set(ids["files"])
    # Files the change named CONCRETELY, as opposed to files pulled in by a
    # fan-out for rescan purposes — see iss29-E03 below.
    direct_files: set = set()

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
    elif native_id is None and entity_type in entity_tables:
        _, target = entity_tables[entity_type]
        target.update(_legacy_backref_ids(conn, entity_type, entity_id))

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
    if candidate_paths and not files and _may_have_catalogue_row(entity_type, entity_id):
        files.update(
            _files_by_mapped_path(conn, candidate_paths, config_manager=config_manager)
        )

    # iss29-E03: everything identified up to HERE was named concretely — an
    # explicit lib2 file id in the finding details, or a path the change itself
    # carried. Everything the fan-outs below add is rescan scope: files the
    # repair may have touched indirectly. Only this narrow set may be marked
    # deleted, because a repair that removed one file must not retire the rest
    # of the album with it.
    direct_files.update(files)

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
        # Only the primary album artist becomes its own maintenance-event
        # subject here — matching _record_events' album_to_artist (which
        # reads lib2_albums.primary_artist_id). Pulling in every featured/
        # secondary lib2_album_artists row instead produced a spurious extra
        # "artist-only" event for guest artists whenever a track they merely
        # feature on was repaired (e.g. an acoustid re-verify on a single
        # file spawned an unrelated event for the featured artist alone).
        rows = conn.execute(
            f"SELECT DISTINCT artist_id FROM lib2_album_artists WHERE album_id IN "
            f"({_marks(sorted(albums))}) AND role='primary'",
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
    if artist_files and artists and not (albums or tracks or files):
        # An artist-only subject whose repair rewrote files on disk (T-11's
        # comma-artist split re-tags every file credited to the artist). The
        # artist is the narrowest thing the finding named, so its files are the
        # convergence scope. Gated on the job's declared effects — a catalogue-
        # only repair such as genre_cleanup must not drag a whole discography
        # into a rescan (BR-08).
        rows = conn.execute(
            f"""SELECT f.id FROM lib2_track_files f
                  JOIN lib2_tracks t ON t.id=f.track_id
             LEFT JOIN lib2_albums al ON al.id=t.album_id
                 WHERE COALESCE(f.file_state,'active')<>'deleted'
                   AND (al.primary_artist_id IN ({_marks(sorted(artists))})
                        OR EXISTS (SELECT 1 FROM lib2_track_artists ta
                                    WHERE ta.track_id=t.id
                                      AND ta.artist_id IN
                                          ({_marks(sorted(artists))})))""",
            [*sorted(artists), *sorted(artists)],
        ).fetchall()
        files.update(int(row[0]) for row in rows)
        if files:
            rows = conn.execute(
                f"SELECT DISTINCT track_id FROM lib2_track_files WHERE id IN "
                f"({_marks(sorted(files))}) AND track_id IS NOT NULL",
                sorted(files),
            ).fetchall()
            tracks.update(int(row[0]) for row in rows)

    if tracks and not files:
        # Nothing named a concrete file, so the track rows are the narrowest
        # subject we have — take their files so a tags/path repair still gets
        # its snapshot refresh.  Guarded on ``not files`` so a finding that DID
        # name a file is never widened to its track's other files (ADR-03).
        rows = conn.execute(
            f"SELECT id FROM lib2_track_files WHERE track_id IN "
            f"({_marks(sorted(tracks))}) AND "
            "COALESCE(file_state,'active')<>'deleted'",
            sorted(tracks),
        ).fetchall()
        files.update(int(row[0]) for row in rows)
        # A track named without a concrete file is still a narrow subject: the
        # track's own files are what a delete of that track would mean.
        direct_files.update(files)
    return {
        "artists": sorted(artists),
        "albums": sorted(albums),
        "tracks": sorted(tracks),
        "files": sorted(files),
        "direct_files": sorted(direct_files & files),
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
    from core.quality.model import AudioQuality
    from core.quality.retention import quality_json, transforms_json
    raw_acquired = result.get("acquired_quality")
    try:
        acquired_json = quality_json(
            AudioQuality.from_dict(raw_acquired)
            if isinstance(raw_acquired, dict) else raw_acquired
        )
    except (AttributeError, TypeError, ValueError):
        acquired_json = None
    retention_json = transforms_json(result.get("retention_transforms"))
    derived_from = result.get("derived_from_file_id")
    try:
        derived_from = int(derived_from) if derived_from else None
    except (TypeError, ValueError):
        derived_from = None
    file_role = str(result.get("file_role") or "derivative")
    if existing:
        conn.execute(
            """UPDATE lib2_track_files
                  SET file_state='active', file_role=?,
                      derived_from_file_id=COALESCE(?, derived_from_file_id),
                      acquired_quality_json=COALESCE(?, acquired_quality_json),
                      retention_json=COALESCE(?, retention_json),
                      updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (file_role, derived_from, acquired_json, retention_json,
             int(existing[0])),
        )
        return int(existing[0])
    cursor = conn.execute(
        """INSERT INTO lib2_track_files(
               track_id, path, size, format, import_status, file_state, source,
               file_role, derived_from_file_id, acquired_quality_json,
               retention_json)
           VALUES(?,?,?,?, 'imported', 'active', 'repair_job',?,?,?,?)""",
        (
            links["tracks"][0],
            output_path,
            os.path.getsize(output_path),
            output_path.rsplit(".", 1)[-1].lower() if "." in output_path else None,
            file_role,
            derived_from,
            acquired_json,
            retention_json,
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

    details, result = dict(details or {}), dict(result or {})
    conn = database._get_connection()
    try:
        if not _table_exists(conn, "lib2_track_files"):
            return {"enabled": True, "reason": "schema_missing", "converged": False}

        from core.repair_jobs import JOB_LIBRARY_V2_EFFECTS

        effects = set(JOB_LIBRARY_V2_EFFECTS.get(job_id, frozenset({"observe"})))
        links = _resolve_links(
            conn,
            entity_type=entity_type,
            entity_id=entity_id,
            file_path=file_path,
            details=details,
            config_manager=config_manager,
            # Only a repair that declares it writes files earns the artist-wide
            # file fan-out.
            artist_files=bool(effects & {"tags", "path", "new_file"}),
        )
        if not any(links.values()):
            return {"enabled": True, "reason": "subject_unlinked", "converged": False}

        changed_fields: set[str] = set(effects - {"observe", "none"})
        deleting = (
            action in _DELETE_ACTIONS
            or result.get("library_v2_file_deleted") is True
        ) and result.get("library_v2_source_replaced") is not True
        # iss29-E03: retire only the files the change actually named, never the
        # album-wide fan-out that `_resolve_links` builds for rescan purposes.
        #
        # `_fix_unwanted_content` deletes exactly ONE file and reports
        # `removed_content`, which is a delete action. Its findings carry
        # `entity_type='album'` (that is how `live_commentary_cleaner` creates
        # them, and the job is not retired), so the fan-out expanded the subject
        # to every track and every live file of the album and the loop marked
        # them all deleted. The album then looked file-less, `recompute_wanted`
        # wanted the whole thing back, and the wishlist re-downloaded an album
        # that was sitting complete on disk while the real files looked like
        # orphans.
        delete_scope = links.get("direct_files") or []
        if deleting and delete_scope:
            from core.library2.track_files import set_file_state

            for file_id in delete_scope:
                if set_file_state(conn, file_id, "deleted"):
                    changed_fields.add("file_state")
        repair_intent = str(result.get("repair_intent") or "").strip().lower()
        if repair_intent in {"remove", "redownload"} and links["tracks"]:
            from core.library2 import ADMIN_PROFILE_ID
            from core.library2.monitor_rules import PROVENANCE_USER, record_rule

            wanted = repair_intent == "redownload"
            marks = _marks(links["tracks"])
            conn.execute(
                f"UPDATE lib2_tracks SET monitored=?, updated_at=CURRENT_TIMESTAMP "
                f"WHERE id IN ({marks})",
                [1 if wanted else 0, *links["tracks"]],
            )
            for track_id in links["tracks"]:
                record_rule(
                    conn, "track", track_id, wanted, PROVENANCE_USER,
                    profile_id=ADMIN_PROFILE_ID,
                )
            changed_fields.add("repair_intent")
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
    # dd28-20: a job whose declared effects do not include 'wanted' can still
    # empty a track — the AcoustID retag re-homes the file onto a new identity
    # and leaves the expected track fileless. A handler that knows it did that
    # says so explicitly rather than relying on the job-level effect set.
    forced_recompute = bool(result.get("library_v2_recompute_wanted"))
    if deleting or new_file_id is not None or "wanted" in effects or forced_recompute:
        conn = database._get_connection()
        try:
            from core.library2 import ADMIN_PROFILE_ID
            from core.library2.wanted import ensure_wanted_schema, recompute_wanted

            ensure_wanted_schema(conn.cursor())
            # Scope the recompute to the tracks this change can possibly have
            # touched. The unscoped call is a whole-library rebuild: its own
            # docstring measures that as "a multi-minute write lock" at 300k
            # tracks, plus a full prune -- and it ran once PER FIXED FINDING.
            # "Fix All" over 500 dead-file findings therefore held the single
            # SQLite writer for hours while every enrichment worker, config
            # save and media-server scan piled up behind it. A delete of one
            # file can only change the wanted state of its own tracks, and the
            # scoped variant already exists (track_file_move.py:129).
            # The global rebuild stays as the fallback for a change with no
            # resolved track subject, which is the only case that can be
            # library-wide.
            recompute_wanted(
                conn.cursor(),
                profile_id=ADMIN_PROFILE_ID,
                track_ids=links["tracks"] or None,
            )
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
        "converged": True,
        "artists": len(links["artists"]),
        "albums": len(links["albums"]),
        "tracks": len(links["tracks"]),
        "files": len(links["files"]),
        "events": events,
        "artwork_invalidated": artwork_invalidated,
        "wishlist_mirrored": mirrored,
        "repair_intent": repair_intent or None,
        "scan": scan_stats,
    }


__all__ = [
    "LIB2_MAINTENANCE_EVENTS_DDL",
    "annotate_finding_details",
    "ensure_maintenance_event_schema",
    "sync_repair_change",
]
