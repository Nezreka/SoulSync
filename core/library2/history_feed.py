"""Unified, read-only history feed for one artist/album/track (§A6/C3).

The artist History modal used to read only ``track_downloads`` (by a fuzzy
artist-name match). Three richer journals already exist but were never
surfaced there: ``acquisition_history`` (26 event types — grabs, retries,
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
from typing import Any, Dict, List, Optional, Sequence

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
    "import_needs_review": ("quarantined", "Needs review"),
    "import_resolved_manually": ("imported", "Resolved manually"),
    "import_file_quarantined": ("quarantined", "Quarantined"),
    "import_completed": ("imported", "Imported"),
    "import_failed": ("failed", "Import failed"),
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


def _rows(conn, sql: str, params: Sequence[Any]) -> List[Any]:
    return conn.execute(sql, params).fetchall()


def _in_clause(values: Sequence[int]) -> str:
    return ",".join("?" * len(values))


def _album_ids_for_artist(conn, artist_id: int) -> List[int]:
    """Owned AND featured-on albums (junction table, not just primary_artist_id —
    a primary-only filter silently misses linked/featured releases, see §30/G8)."""
    rows = _rows(
        conn, "SELECT album_id FROM lib2_album_artists WHERE artist_id=?", (artist_id,)
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
    artist_id: Optional[int] = None,
    album_ids: Sequence[int] = (),
    edition_ids: Sequence[int] = (),
    recording_ids: Sequence[int] = (),
) -> List[str]:
    clauses: List[str] = []
    params: List[Any] = []
    if artist_id is not None:
        clauses.append("(scope='artist_missing' AND entity_id=?)")
        params.append(artist_id)
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
                 ORDER BY id DESC LIMIT ?""",
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
        detail = None
        if isinstance(payload, dict):
            detail = payload.get("reason") or payload.get("source")
        detail = detail or r["message"] or r["reason_code"]
        events.append({
            "date": r["created_at"],
            "event_type": r["event_type"],
            "category": category,
            "title": label,
            "detail": detail,
            "source": "acquisition",
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
            "date": r["occurred_at"],
            "event_type": r["event_type"],
            "category": "moved",
            "title": label,
            "detail": detail,
            "source": "catalog",
        })
    return events


def _file_delete_events(
    conn, *, artist_id: Optional[int] = None, album_ids: Sequence[int] = (), limit: int,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if artist_id is not None:
        clauses.append("(entity_type IN ('artist','artists') AND entity_id=?)")
        params.append(artist_id)
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
            "date": r["completed_at"] or r["created_at"],
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
            "date": r["created_at"],
            "event_type": "manual_skip",
            "category": "override",
            "title": "Check overridden",
            "detail": ", ".join(checks) if checks else None,
            "source": "manual",
        })
    return events


def _track_downloads_to_events(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    return [{
        "date": r["created_at"],
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

    legacy_ids = sorted({str(int(r["legacy_id"])) for r in link_rows if r["legacy_id"] is not None})
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
        album_ids = _album_ids_for_artist(conn, entity_id)
        edition_ids = _edition_ids_for_albums(conn, album_ids)
        recording_ids = _recording_ids_for_editions(conn, edition_ids)
        track_ids = _track_ids_for_albums(conn, album_ids)
        request_ids = _acquisition_request_ids(
            conn, artist_id=entity_id, album_ids=album_ids,
            edition_ids=edition_ids, recording_ids=recording_ids,
        )
        download_events, matched_ids = _track_download_events(conn, track_ids, limit)
        events += _acquisition_events(conn, request_ids, limit)
        events += _entity_history_events(conn, track_ids, limit)
        events += _file_delete_events(conn, artist_id=entity_id, album_ids=album_ids, limit=limit)
        events += _manual_skip_events(conn, track_ids, limit)
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
        events += download_events
    else:  # track
        recording_ids = _recording_ids_for_track(conn, entity_id)
        request_ids = _acquisition_request_ids(conn, recording_ids=recording_ids)
        download_events, _matched_ids = _track_download_events(conn, [entity_id], limit)
        events += _acquisition_events(conn, request_ids, limit)
        events += _entity_history_events(conn, [entity_id], limit)
        events += _manual_skip_events(conn, [entity_id], limit)
        events += download_events

    events.sort(key=lambda e: e["date"] or "", reverse=True)
    return events[:limit]


__all__ = ["EVENT_CATEGORY", "ENTITY_EVENT_LABEL", "SCOPES", "scoped_history"]
