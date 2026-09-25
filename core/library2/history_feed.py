"""Unified, read-only history feed for one artist/album/track (§A6/C3).

The artist History modal used to read only ``track_downloads`` (by a fuzzy
artist-name match). Three richer journals already exist but were never
surfaced there: ``acquisition_history`` (grabs, checks, retries,
quarantine, import outcomes), ``lib2_entity_history`` (canonical link/relink,
file moves) and ``lib2_file_delete_operations`` (ADR-05 physical deletes).
This module merges all four into one ``{date, event_type, category, title,
detail, source}`` shape. No new persistence — pure reads/joins.

The one real complication: ``acquisition_requests.scope`` is NOT 1:1 with a
lib2 artist/album/track id (``scope`` is ``recording`` / ``release_group`` /
``release_edition`` / ``artist_missing`` — MusicBrainz-shaped content scopes,
not lib2 entity kinds), so a naive ``entity_id = <lib2 id>`` join would
silently misattribute rows. ``core.acquisition.catalog`` already resolves
scope+entity_id to lib2 relationship ids for the search path; this module
walks the *same* relationships in reverse (lib2 id -> matching recording /
release_group / release_edition ids -> matching request ids) instead of
duplicating a second resolver. ``upgrade`` scope is deliberately not handled:
nothing in the codebase creates an upgrade-scoped request yet (no entity_type
convention exists to test against), so resolving it now would be speculative.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence

# `YYYY-MM-DD HH:MM:SS[.ffffff]` with no timezone — SQLite's own
# CURRENT_TIMESTAMP shape, which is always UTC.
_NAIVE_SQLITE_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?$"
)


def _iso_utc(value: Any) -> Any:
    """Normalise a stored timestamp into an unambiguous ISO-8601 UTC string.

    iss29-C01. Most of these columns default to SQLite's ``CURRENT_TIMESTAMP``,
    which is **UTC** written as ``"YYYY-MM-DD HH:MM:SS"`` — a space separator and
    no zone. Handed to a browser unchanged, ``Date.parse`` reads that as LOCAL
    time (ECMA-262 only treats the date-time form as UTC when it is
    ``T``-separated *and* zoneless, and the space form falls through to
    implementation-defined local parsing). In Europe/Zurich — this project's
    timezone — every such timestamp arrived two hours in the past.

    That is not only a display bug: the interactive-search grab watcher filters
    quarantine events by "newer than when I started", so east of UTC the event
    never looked fresh, every poll stayed ``pending``, and the UI finally
    reported **Grabbed ✓** for a file that never arrived.

    Anything already carrying a zone (or that is not a timestamp at all) is
    passed through untouched.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not _NAIVE_SQLITE_TIMESTAMP.match(text):
        return value
    return text.replace(" ", "T") + "Z"


EVENT_CATEGORY = {
    "request_created": ("info", "Search requested"),
    "search_started": ("info", "Search started"),
    "search_completed": ("info", "Search completed"),
    "search_failed": ("failed", "Search failed"),
    "candidates_evaluated": ("info", "Candidates evaluated"),
    "no_candidate": ("failed", "No candidate found"),
    "grab_prepared": ("grabbed", "Grab prepared"),
    "grab_submitted": ("grabbed", "Grabbed"),
    "grab_submission_uncertain": ("grabbed", "Grab uncertain"),
    "manual_grab_correlated": ("grabbed", "Grabbed (manual)"),
    "scheduled_grab_correlated": ("grabbed", "Grabbed (scheduled)"),
    "client_job_adopted": ("grabbed", "Download adopted"),
    "force_grab": ("grabbed", "Force grabbed"),
    "force_quarantine_auto_approved": ("quarantined", "Force-quarantine approved"),
    "grab_completed": ("imported", "Download completed"),
    "grab_failed": ("failed", "Grab failed"),
    "candidate_blocklisted": ("blocklist", "Candidate blocklisted"),
    "candidate_unblocked": ("blocklist", "Candidate unblocked"),
    "retry_started": ("grabbed", "Retry started"),
    "cancelled": ("failed", "Cancelled"),
    "import_started": ("info", "Import started"),
    "quality_checked": ("info", "Quality checked"),
    "acoustic_id_checked": ("info", "Acoustic ID checked"),
    "import_needs_review": ("quarantined", "Needs review"),
    "import_resolved_manually": ("imported", "Resolved manually"),
    "import_file_quarantined": ("quarantined", "Quarantined"),
    "recovered_to_staging": ("quarantined", "Recovered to staging"),
    "import_completed": ("imported", "Imported"),
    "import_failed": ("failed", "Import failed"),
    "previous_file_replaced": ("imported", "Previous file replaced"),
    "human_verified": ("imported", "Verified by you"),
    "rejected": ("failed", "Rejected by you"),
}

ENTITY_EVENT_LABEL = {
    "canonical_linked": "Linked as duplicate",
    "canonical_unlinked": "Unlinked from canonical",
    "canonical_relinked": "Re-linked to a different canonical track",
    "file_moved": "File moved to another track",
    "recording_moved": "Recording re-matched",
    "release_track_moved": "Moved to another edition",
    "entity_merged": "Merged",
    "entity_moved": "Moved",
}

SCOPES = ("artist", "album", "track")
PIPELINE_CHECK_EVENTS = frozenset({"quality_checked", "acoustic_id_checked"})

MAINTENANCE_EVENT_LABEL = {
    "applied_replaygain": "ReplayGain added",
    "applied_lyrics": "Lyrics added",
    "applied_cover_art": "Cover art updated",
    "applied_artist_art": "Artist image updated",
    "fixed_track_number": "Track number repaired",
    "verification_status_updated": "Acoustic ID status updated",
    "library_retag": "File tags rewritten",
    "retagged": "File tags updated",
    "moved_file": "File reorganized",
    "fixed_unknown_artist": "Artist identity repaired",
    "canonical_version_pinned": "Canonical album version selected",
    "pinned_canonical": "Canonical album version selected",
    "added_to_wishlist": "Replacement requested",
    "auto_fill_album": "Missing album tracks processed",
    "relocated": "Moved to staging for re-import",
    "converted": "Lossy copy created",
    "converted_and_deleted": "Lossy copy replaced original",
    "deleted_expired": "Expired download removed",
    "deleted_file": "File removed by maintenance",
}


def _rows(conn, sql: str, params: Sequence[Any]) -> List[Any]:
    return conn.execute(sql, params).fetchall()


def _in_clause(values: Sequence[int]) -> str:
    return ",".join("?" * len(values))


def _album_ids_for_artists(conn, artist_ids: Sequence[int]) -> List[int]:
    """Owned AND featured-on albums (junction table, not just primary_artist_id —
    a primary-only filter silently misses linked/featured releases, see §30/G8)."""
    if not artist_ids:
        return []
    rows = _rows(
        conn,
        f"SELECT DISTINCT album_id FROM lib2_album_artists "
        f"WHERE artist_id IN ({_in_clause(artist_ids)})",
        artist_ids,
    )
    return [int(r[0]) for r in rows]


def _edition_ids_for_albums(conn, album_ids: Sequence[int]) -> List[int]:
    if not album_ids:
        return []
    rows = _rows(
        conn,
        f"SELECT id FROM lib2_release_editions WHERE release_group_id IN ({_in_clause(album_ids)})",
        album_ids,
    )
    return [int(r[0]) for r in rows]


def _recording_ids_for_editions(conn, edition_ids: Sequence[int]) -> List[int]:
    if not edition_ids:
        return []
    rows = _rows(
        conn,
        "SELECT DISTINCT recording_id FROM lib2_release_tracks "
        f"WHERE release_edition_id IN ({_in_clause(edition_ids)})",
        edition_ids,
    )
    return [int(r[0]) for r in rows]


def _recording_ids_for_track(conn, track_id: int) -> List[int]:
    rows = _rows(
        conn,
        "SELECT DISTINCT recording_id FROM lib2_release_tracks WHERE track_id=?",
        (track_id,),
    )
    return [int(r[0]) for r in rows]


def _track_ids_for_albums(conn, album_ids: Sequence[int]) -> List[int]:
    if not album_ids:
        return []
    rows = _rows(
        conn, f"SELECT id FROM lib2_tracks WHERE album_id IN ({_in_clause(album_ids)})", album_ids
    )
    return [int(r[0]) for r in rows]


def _acquisition_request_ids(
    conn,
    *,
    artist_ids: Sequence[int] = (),
    album_ids: Sequence[int] = (),
    edition_ids: Sequence[int] = (),
    recording_ids: Sequence[int] = (),
) -> List[str]:
    clauses: List[str] = []
    params: List[Any] = []
    if artist_ids:
        clauses.append(
            f"(scope='artist_missing' AND entity_id IN ({_in_clause(artist_ids)}))"
        )
        params.extend(artist_ids)
    if album_ids:
        clauses.append(f"(scope='release_group' AND entity_id IN ({_in_clause(album_ids)}))")
        params.extend(album_ids)
    if edition_ids:
        clauses.append(f"(scope='release_edition' AND entity_id IN ({_in_clause(edition_ids)}))")
        params.extend(edition_ids)
    if recording_ids:
        clauses.append(f"(scope='recording' AND entity_id IN ({_in_clause(recording_ids)}))")
        params.extend(recording_ids)
    if not clauses:
        return []
    try:
        rows = _rows(
            conn,
            f"SELECT id FROM acquisition_requests WHERE {' OR '.join(clauses)}",
            params,
        )
    except Exception:  # noqa: BLE001 — table may not exist on a fresh DB
        return []
    return [str(r[0]) for r in rows]


def _acquisition_events(conn, request_ids: Sequence[str], limit: int) -> List[Dict[str, Any]]:
    if not request_ids:
        return []
    from core.acquisition.history import ensure_acquisition_history_schema

    try:
        ensure_acquisition_history_schema(conn)
        rows = _rows(
            conn,
            f"""SELECT event_type, reason_code, message, payload_json, created_at
                  FROM acquisition_history WHERE request_id IN ({_in_clause(request_ids)})
                 ORDER BY created_at DESC, rowid DESC LIMIT ?""",
            (*request_ids, limit),
        )
    except Exception:  # noqa: BLE001
        return []
    events = []
    for r in rows:
        category, label = EVENT_CATEGORY.get(r["event_type"], ("info", r["event_type"]))
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        status = payload.get("status")
        if r["event_type"] in PIPELINE_CHECK_EVENTS:
            if status in {"failed", "error"}:
                category = "failed"
            elif status == "skipped":
                category = "override"
            parts = [str(status).replace("_", " ") if status else None]
            reason = r["message"] or payload.get("reason")
            if reason:
                parts.append(str(reason))
            before_quality = payload.get("before_quality")
            after_quality = payload.get("after_quality")
            if before_quality and after_quality:
                parts.append(f"{before_quality} → {after_quality}")
            elif after_quality:
                parts.append(str(after_quality))
            elif before_quality:
                parts.append(f"from {before_quality}")
            if payload.get("quality_profile_id"):
                parts.append(f"profile {payload['quality_profile_id']}")
            detail = " · ".join(part for part in parts if part)
        elif r["event_type"] in ("manual_grab_correlated", "scheduled_grab_correlated"):
            # Reported: this said just "youtube" — no sign that a human
            # overrode a gate rejection to get here versus the ordinary path.
            # The rejections/warnings the automatic gates raised are exactly
            # why the grab needed a human (or a scheduled fallback) at all.
            parts = [payload.get("source")]
            rejections = payload.get("rejections") or []
            if rejections:
                parts.append(
                    "overrode: " + ", ".join(str(x).replace("_", " ") for x in rejections)
                )
            warnings = payload.get("warnings") or []
            if warnings:
                parts.append(", ".join(str(x).replace("_", " ") for x in warnings))
            detail = " · ".join(p for p in parts if p) or r["message"] or r["reason_code"]
        elif r["event_type"] == "grab_completed":
            parts = [payload.get("source")]
            if payload.get("failure_kind"):
                parts.append(str(payload["failure_kind"]).replace("_", " "))
            elif payload.get("has_output_path"):
                parts.append("file received")
            detail = " · ".join(p for p in parts if p) or r["message"] or r["reason_code"]
        elif r["event_type"] == "grab_failed":
            parts = [payload.get("source"), payload.get("failure_kind")]
            detail = (
                " · ".join(str(p).replace("_", " ") for p in parts if p)
                or r["message"] or r["reason_code"]
            )
        else:
            detail = payload.get("reason") or payload.get("source")
            detail = detail or r["message"] or r["reason_code"]
        events.append({
            "date": _iso_utc(r["created_at"]),
            "event_type": r["event_type"],
            "category": category,
            "title": label,
            "detail": detail,
            "source": "acquisition",
            "status": status,
            "payload": payload,
        })
    return events


def _entity_history_events(conn, track_ids: Sequence[int], limit: int) -> List[Dict[str, Any]]:
    if not track_ids:
        return []
    from core.library2.entity_history import ensure_entity_history_schema

    try:
        ensure_entity_history_schema(conn.cursor())
        ph = _in_clause(track_ids)
        rows = _rows(
            conn,
            f"""SELECT event_type, subject_type, subject_id,
                       from_entity_type, from_entity_id,
                       to_entity_type, to_entity_id, occurred_at
                  FROM lib2_entity_history
                 WHERE (subject_type='track' AND subject_id IN ({ph}))
                    OR (from_entity_type='track' AND from_entity_id IN ({ph}))
                    OR (to_entity_type='track' AND to_entity_id IN ({ph}))
                 ORDER BY id DESC LIMIT ?""",
            (*track_ids, *track_ids, *track_ids, limit),
        )
    except Exception:  # noqa: BLE001
        return []
    events = []
    for r in rows:
        label = ENTITY_EVENT_LABEL.get(r["event_type"], r["event_type"])
        detail = None
        if r["to_entity_type"] and r["to_entity_id"] is not None:
            detail = f"→ {r['to_entity_type']} #{r['to_entity_id']}"
        elif r["from_entity_type"] and r["from_entity_id"] is not None:
            detail = f"← {r['from_entity_type']} #{r['from_entity_id']}"
        events.append({
            "date": _iso_utc(r["occurred_at"]),
            "event_type": r["event_type"],
            "category": "moved",
            "title": label,
            "detail": detail,
            "source": "catalog",
        })
    return events


def _file_delete_events(
    conn, *, artist_ids: Sequence[int] = (), album_ids: Sequence[int] = (), limit: int,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if artist_ids:
        clauses.append(
            f"(entity_type IN ('artist','artists') "
            f"AND entity_id IN ({_in_clause(artist_ids)}))"
        )
        params.extend(artist_ids)
    if album_ids:
        clauses.append(
            f"(entity_type IN ('release_group','albums') "
            f"AND entity_id IN ({_in_clause(album_ids)}))"
        )
        params.extend(album_ids)
    if not clauses:
        return []
    try:
        rows = _rows(
            conn,
            f"""SELECT status, file_count, created_at, completed_at,
                       COALESCE(mode, 'permanent') AS mode,
                       COALESCE(actor, 'user') AS actor
                  FROM lib2_file_delete_operations WHERE {' OR '.join(clauses)}
                 ORDER BY COALESCE(completed_at, created_at) DESC LIMIT ?""",
            (*params, limit),
        )
    except Exception:  # noqa: BLE001
        return []
    events = []
    for r in rows:
        completed = r["status"] == "completed"
        database_only = r["mode"] == "database_only"
        events.append({
            "date": _iso_utc(r["completed_at"] or r["created_at"]),
            "event_type": "file_records_removed" if database_only else "files_deleted",
            "category": "deleted",
            "title": (
                "Removed from library database"
                if database_only and completed
                else "Files permanently deleted"
                if completed
                else f"File removal {r['status']}"
            ),
            "detail": f"{r['file_count']} file(s) · actor {r['actor']}",
            "source": "library" if database_only else "filesystem",
        })
    return events


def _track_file_delete_events(
    conn, track_ids: Sequence[int], limit: int,
) -> List[Dict[str, Any]]:
    """Delete operations that took one of THESE tracks' files.

    The journal is keyed by artist/album, because that is the scope a user
    deletes in. A track's own timeline — the one behind the pencil — has to
    answer "was this deleted, and by whom" too, and the operation's ITEMS name
    the file ids it removed. Files keep their row after deletion
    (``file_state='deleted'``), so the join still resolves afterwards.
    """
    if not track_ids:
        return []
    try:
        rows = _rows(
            conn,
            f"""SELECT o.status, o.created_at, o.completed_at,
                       COALESCE(o.mode, 'permanent') AS mode,
                       COALESCE(o.actor, 'user') AS actor,
                       i.resolved_path, i.status AS item_status
                  FROM lib2_file_delete_items i
                  JOIN lib2_file_delete_operations o ON o.id = i.operation_id
                 WHERE EXISTS (
                       SELECT 1 FROM json_each(i.file_ids_json) fid
                        JOIN lib2_track_files tf ON tf.id = CAST(fid.value AS INTEGER)
                       WHERE tf.track_id IN ({_in_clause(track_ids)}))
                 ORDER BY COALESCE(o.completed_at, o.created_at) DESC LIMIT ?""",
            (*track_ids, limit),
        )
    except Exception:  # noqa: BLE001
        return []
    events = []
    for r in rows:
        database_only = r["mode"] == "database_only"
        gone = r["item_status"] in ("deleted", "removed", "missing")
        events.append({
            "date": _iso_utc(r["completed_at"] or r["created_at"]),
            "event_type": "file_records_removed" if database_only else "files_deleted",
            "category": "deleted",
            "title": (
                "Removed from library database" if database_only
                else "File deleted" if gone
                else f"File removal {r['status']}"
            ),
            "detail": f"{os.path.basename(r['resolved_path'] or '') or '—'} · actor {r['actor']}",
            "source": "library" if database_only else "filesystem",
        })
    return events


def _manual_skip_events(conn, track_ids: Sequence[int], limit: int) -> List[Dict[str, Any]]:
    if not track_ids:
        return []
    try:
        rows = _rows(
            conn,
            f"""SELECT s.skipped_checks, s.created_at
                  FROM lib2_manual_skips s
                  JOIN lib2_track_files tf ON tf.path = s.file_path AND tf.is_primary=1
                 WHERE tf.track_id IN ({_in_clause(track_ids)})
                 ORDER BY s.id DESC LIMIT ?""",
            (*track_ids, limit),
        )
    except Exception:  # noqa: BLE001
        return []
    events = []
    for r in rows:
        try:
            checks = json.loads(r["skipped_checks"] or "[]")
        except (TypeError, ValueError):
            checks = []
        events.append({
            "date": _iso_utc(r["created_at"]),
            "event_type": "manual_skip",
            "category": "override",
            "title": "Check overridden",
            "detail": ", ".join(checks) if checks else None,
            "source": "manual",
        })
    return events


def _maintenance_events(
    conn,
    *,
    artist_ids: Sequence[int] = (),
    album_ids: Sequence[int] = (),
    track_ids: Sequence[int] = (),
    limit: int,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if artist_ids:
        clauses.append(f"e.lib2_artist_id IN ({_in_clause(artist_ids)})")
        params.extend(artist_ids)
    if album_ids:
        clauses.append(f"e.lib2_album_id IN ({_in_clause(album_ids)})")
        params.extend(album_ids)
    if track_ids:
        clauses.append(f"e.lib2_track_id IN ({_in_clause(track_ids)})")
        params.extend(track_ids)
    if not clauses:
        return []
    try:
        rows = _rows(
            conn,
            """SELECT e.job_id, e.finding_type, e.action, e.changed_fields_json, e.occurred_at,
                      e.lib2_file_id, e.lib2_track_id, t.title AS track_title,
                      COALESCE(e.lib2_album_id, t.album_id) AS album_id,
                      a.title AS album_title
                 FROM lib2_maintenance_events e
                 LEFT JOIN lib2_tracks t ON t.id = e.lib2_track_id
                 LEFT JOIN lib2_albums a ON a.id = COALESCE(e.lib2_album_id, t.album_id)
                WHERE """ + " OR ".join(clauses) + " ORDER BY e.id DESC LIMIT ?",
            (*params, limit),
        )
    except Exception:  # noqa: BLE001 — table is additive on older databases
        return []
    events = []
    for row in rows:
        try:
            fields = json.loads(row["changed_fields_json"] or "[]")
        except (TypeError, ValueError):
            fields = []
        # The column is free-form JSON written by whatever repair job produced
        # the row, but this is served as `changed_fields: string[]`. A stored
        # object or scalar decodes to a dict/number and would reach the UI as a
        # shape its types promise cannot occur — and `', '.join` below would
        # silently render a dict's KEYS. Anything that is not a list is no list
        # of changed fields.
        if not isinstance(fields, list):
            fields = []
        detail = (
            f"{row['job_id']} · {', '.join(str(field) for field in fields)}"
            if fields else str(row["job_id"])
        )
        status = None
        if row["action"] == "verification_status_updated":
            verdict = _check_verdict(conn, row["lib2_file_id"], row["lib2_track_id"])
            if verdict:
                status, detail = verdict
        events.append({
            "date": _iso_utc(row["occurred_at"]),
            "event_type": row["action"],
            "category": "maintenance",
            "title": MAINTENANCE_EVENT_LABEL.get(
                row["action"], str(row["action"] or "Maintenance updated")
                    .replace("_", " ").capitalize(),
            ),
            "detail": detail,
            "source": "maintenance",
            "status": status,
            "status_basis": "current_file" if status else None,
            "track_id": row["lib2_track_id"],
            "track_title": row["track_title"],
            "album_id": row["album_id"],
            "album_title": row["album_title"],
            "changed_fields": fields,
            "job_id": row["job_id"],
        })
    return events


def _check_verdict(
    conn, file_id: Optional[int], track_id: Optional[int],
) -> Optional["tuple[str, str]"]:
    """The same verdict the Check column shows (T-09/T-10's
    ``TrackCheckBadge``), split into ``(status, reason)`` — for a scan-
    history row that otherwise only says which columns changed, not to what
    or why (§44/§45 follow-up: "unverified" on its own tells you nothing you
    couldn't already see in the table; the reasoning belongs in Detail, the
    verdict word in Status, matching every other event in this feed).
    ``status`` is exactly the word the Check column badge shows, so the UI
    can render one identical-looking badge in both places."""
    row = None
    if file_id:
        row = conn.execute(
            "SELECT verification_status, acoustid_status, file_state, "
            "pipeline_result_json FROM lib2_track_files WHERE id=?",
            (file_id,),
        ).fetchone()
    if row is None and track_id:
        row = conn.execute(
            "SELECT verification_status, acoustid_status, file_state, "
            "pipeline_result_json FROM lib2_track_files "
            "WHERE track_id=? ORDER BY is_primary DESC, id DESC LIMIT 1",
            (track_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        message = (json.loads(row["pipeline_result_json"] or "{}") or {}).get(
            "acoustid_message"
        )
    except (TypeError, ValueError):
        message = None
    verification_status = row["verification_status"]
    acoustid_status = row["acoustid_status"]
    if row["file_state"] in ("missing_confirmed", "deleted"):
        label = "File missing"
        default_reason = "The file is no longer on disk, so no check can run against it"
    elif verification_status == "human_verified":
        label = "Human verified"
        default_reason = "You approved this file, skipping AcoustID"
    elif acoustid_status == "fail":
        label = "Mismatch"
        default_reason = "The audio fingerprint matches a different recording"
    elif acoustid_status == "pass":
        label = "Verified"
        default_reason = "AcoustID fingerprint check passed"
    elif verification_status == "force_imported":
        # Administrative bypass — the check never ran. Kept distinct from
        # the branch below (ran, but couldn't confirm) for the same reason
        # TrackCheckBadge does: "Skipped" and "Unverified" answer different
        # questions.
        label = "Skipped"
        default_reason = "Check skipped by force/retry import"
    elif acoustid_status == "skip":
        label = "Unverified"
        default_reason = (
            "AcoustID ran but found no confident match — a low fingerprint "
            "score, an ambiguous cover/collab, or no match in its database"
        )
    elif verification_status == "verified":
        label = "Verified"
        default_reason = "Verified — no separate fingerprint verdict is recorded for this file"
    else:
        label = "Not scanned"
        default_reason = "No completed AcoustID check is recorded for this file"
    return label, (str(message) if message else default_reason)


def _track_downloads_to_events(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    return [{
        "date": _iso_utc(r["created_at"]),
        "event_type": "downloaded",
        "category": "imported" if r["status"] == "completed" else "info",
        "title": "Downloaded",
        "detail": f"{r['track_title'] or '—'} ({r['source_service'] or '—'})",
        "source": "download",
    } for r in rows]


def _track_download_events(
    conn, track_ids: Sequence[int], limit: int,
) -> "tuple[List[Dict[str, Any]], set]":
    """``track_downloads`` rows for these tracks — resolved by legacy id first
    (rename-proof, see ``source_info.py``), falling back to the primary file
    path whenever the legacy-id lookup itself finds nothing for that track —
    not only when the track has no legacy id at all. Real-DB finding:
    ``track_downloads.track_id`` is frequently left NULL/never backfilled even
    on a track whose own ``legacy_track_id`` IS set, so "has a legacy id"
    can't be trusted to mean "the legacy-id query will find it" —
    ``source_info.py`` already falls through on an empty legacy-id result for
    exactly this reason; this mirrors that per-track fallthrough instead of
    only checking presence/absence of the id. Also returns the matched row
    ids so callers can dedupe a broader fallback query against them."""
    if not track_ids:
        return [], set()
    ph = _in_clause(track_ids)
    try:
        link_rows = _rows(
            conn,
            f"""SELECT t.id AS track_id, COALESCE(t.legacy_track_id, tf.legacy_track_id) AS legacy_id,
                       tf.path AS file_path
                  FROM lib2_tracks t
                  LEFT JOIN lib2_track_files tf ON tf.track_id=t.id AND tf.is_primary=1
                 WHERE t.id IN ({ph})""",
            track_ids,
        )
    except Exception:  # noqa: BLE001
        return [], set()

    legacy_ids = sorted({str(r["legacy_id"]) for r in link_rows if r["legacy_id"] is not None})
    rows: List[Any] = []
    matched_legacy_ids: set = set()
    try:
        if legacy_ids:
            legacy_rows = _rows(
                conn,
                f"""SELECT id, track_id, track_title, track_album, source_service,
                           status, created_at
                      FROM track_downloads WHERE track_id IN ({_in_clause(legacy_ids)})
                     ORDER BY id DESC LIMIT ?""",
                (*legacy_ids, limit),
            )
            rows.extend(legacy_rows)
            matched_legacy_ids = {r["track_id"] for r in legacy_rows}
    except Exception:  # noqa: BLE001 — legacy table may be absent
        return [], set()

    fallback_paths = sorted({
        r["file_path"] for r in link_rows
        if r["file_path"]
        and (r["legacy_id"] is None or str(r["legacy_id"]) not in matched_legacy_ids)
    })
    if fallback_paths:
        try:
            path_rows = _rows(
                conn,
                f"""SELECT id, track_title, track_album, source_service, status, created_at
                      FROM track_downloads WHERE file_path IN ({_in_clause(fallback_paths)})
                     ORDER BY id DESC LIMIT ?""",
                (*fallback_paths, limit),
            )
            rows.extend(path_rows)
        except Exception:  # noqa: BLE001, S110 — the legacy-id pass above already succeeded
            pass

    if not rows:
        return [], set()
    deduped: Dict[Any, Any] = {}
    for r in rows:
        deduped.setdefault(r["id"], r)
    ordered = sorted(deduped.values(), key=lambda r: r["id"], reverse=True)[:limit]
    matched_ids = {r["id"] for r in ordered}
    return _track_downloads_to_events(ordered), matched_ids


def _artist_name_fallback_events(
    conn, artist_name: str, exclude_ids: set, limit: int,
) -> List[Dict[str, Any]]:
    """Catches ``track_downloads`` rows with no lib2 track to join through at
    all (deleted/replaced tracks, pre-lib2 downloads) — a legacy fallback only,
    per §A6/C3: entity-id joins are the source of truth, this only fills the
    gap they structurally can't cover."""
    if not artist_name:
        return []
    try:
        rows = _rows(
            conn,
            """SELECT id, track_title, track_album, source_service, status, created_at
                 FROM track_downloads
                WHERE lower(track_artist) = lower(?)
                   OR lower(track_artist) LIKE lower(?) || ' %'
                ORDER BY id DESC LIMIT ?""",
            (artist_name, artist_name, limit),
        )
    except Exception:  # noqa: BLE001
        return []
    return _track_downloads_to_events([r for r in rows if r["id"] not in exclude_ids])


def scoped_history(
    conn, *, scope: str, entity_id: int, limit: int = 100, artist_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Merged, newest-first history for one artist/album/track.

    ``scope`` is ``'artist'``, ``'album'`` or ``'track'`` (a lib2 entity kind —
    not to be confused with ``acquisition_requests.scope``, which this
    function resolves internally per relevant request). ``artist_name`` only
    applies to ``scope='artist'``: it adds the pre-existing name-match legacy
    fallback (§A6) for downloads no current track links back to.
    """
    scope = str(scope or "").strip().lower()
    if scope not in SCOPES:
        raise ValueError(f"unsupported history scope: {scope!r}")
    limit = int(limit)
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")

    events: List[Dict[str, Any]] = []
    if scope == "artist":
        from core.library2.artist_aliases import resolve_alias_group

        artist_ids = resolve_alias_group(conn, entity_id)
        album_ids = _album_ids_for_artists(conn, artist_ids)
        edition_ids = _edition_ids_for_albums(conn, album_ids)
        recording_ids = _recording_ids_for_editions(conn, edition_ids)
        track_ids = _track_ids_for_albums(conn, album_ids)
        request_ids = _acquisition_request_ids(
            conn, artist_ids=artist_ids, album_ids=album_ids,
            edition_ids=edition_ids, recording_ids=recording_ids,
        )
        download_events, matched_ids = _track_download_events(conn, track_ids, limit)
        events += _acquisition_events(conn, request_ids, limit)
        events += _entity_history_events(conn, track_ids, limit)
        events += _file_delete_events(
            conn, artist_ids=artist_ids, album_ids=album_ids, limit=limit
        )
        events += _manual_skip_events(conn, track_ids, limit)
        events += _maintenance_events(
            conn, artist_ids=artist_ids, album_ids=album_ids,
            track_ids=track_ids, limit=limit,
        )
        events += download_events
        events += _artist_name_fallback_events(conn, artist_name or "", matched_ids, limit)
    elif scope == "album":
        edition_ids = _edition_ids_for_albums(conn, [entity_id])
        recording_ids = _recording_ids_for_editions(conn, edition_ids)
        track_ids = _track_ids_for_albums(conn, [entity_id])
        request_ids = _acquisition_request_ids(
            conn, album_ids=[entity_id], edition_ids=edition_ids, recording_ids=recording_ids,
        )
        download_events, _matched_ids = _track_download_events(conn, track_ids, limit)
        events += _acquisition_events(conn, request_ids, limit)
        events += _entity_history_events(conn, track_ids, limit)
        events += _file_delete_events(conn, album_ids=[entity_id], limit=limit)
        events += _manual_skip_events(conn, track_ids, limit)
        events += _maintenance_events(
            conn, album_ids=[entity_id], track_ids=track_ids, limit=limit,
        )
        events += download_events
    else:  # track
        recording_ids = _recording_ids_for_track(conn, entity_id)
        request_ids = _acquisition_request_ids(conn, recording_ids=recording_ids)
        download_events, _matched_ids = _track_download_events(conn, [entity_id], limit)
        acquisition_events = _acquisition_events(conn, request_ids, limit)
        events += acquisition_events
        events += _entity_history_events(conn, [entity_id], limit)
        events += _track_file_delete_events(conn, [entity_id], limit)
        events += _manual_skip_events(conn, [entity_id], limit)
        events += _maintenance_events(conn, track_ids=[entity_id], limit=limit)
        # The legacy track_downloads feed and the richer acquisition pipeline
        # both journal "a download finished" independently — for a track the
        # acquisition system tracked, that is the SAME real event twice under
        # two different names ("Downloaded" / "Download completed"), not two
        # downloads. Drop the legacy row only where its timestamp lines up
        # with an acquisition-side completion, so a genuinely separate old
        # download (from before this track had acquisition coverage) still
        # shows up on its own.
        completed_at = {e["date"] for e in acquisition_events if e["category"] == "imported"}
        events += [e for e in download_events if e["date"] not in completed_at]

    events.sort(key=lambda e: e["date"] or "", reverse=True)
    return events[:limit]


__all__ = [
    "EVENT_CATEGORY",
    "ENTITY_EVENT_LABEL",
    "PIPELINE_CHECK_EVENTS",
    "SCOPES",
    "scoped_history",
]
