"""Re-read audio files' real properties into ``lib2_track_files``.

The importer seeds file rows from the legacy DB, which only reliably knows
format+bitrate. "Refresh & Scan" calls this to probe each file on disk
(``core/imports/file_ops.probe_audio_quality`` — mutagen, ground truth) so
sample-rate/bit-depth-based quality targets (hi-res FLAC tiers) evaluate
against real values instead of format-based fallbacks.

The same pass refreshes the tag/gap cache through ``core.tag_writer``'s
canonical reader. Tag and quality probes are independent: failure of one must
not keep the other stale.

A path that no longer resolves is not automatically a deleted file. It is
first offered to the stale-index-path matcher (``core.library2.path_drift``)
scoped to exactly those rows: a rename that only reached the filesystem is
repointed and rescanned, an ambiguous folder is left for a human, and only
what survives becomes a missing observation.

Missing paths then advance only while their library root is known healthy:
one miss is suspected, two are confirmed — or one, when a person pressed
"Refresh & Scan" (``manual=True``). Unhealthy/unknown mounts defer the
transition, and a recovered path returns to active.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.scan")

ProgressCb = Optional[Callable[[str, int, int], None]]
MISSING_CONFIRMATION_SCANS = 2


def _file_rows_in_scope(
    conn,
    *,
    album_ids: Optional[List[int]] = None,
    file_ids: Optional[List[int]] = None,
) -> List[Any]:
    # Scope contract: None = whole library, [] = nothing. An empty scope must
    # never widen to a full-library scan (an artist without albums would
    # otherwise probe every file in the database).
    if album_ids is not None and file_ids is not None:
        raise ValueError("album_ids and file_ids are mutually exclusive")
    if file_ids is not None:
        if not file_ids:
            return []
        rows = []
        ids = list(dict.fromkeys(int(file_id) for file_id in file_ids))
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f"""SELECT id, track_id, path, size, file_state, missing_scan_count
                      FROM lib2_track_files
                     WHERE id IN ({marks}) AND path IS NOT NULL AND path <> ''
                       AND COALESCE(file_state,'active')<>'deleted'""",
                chunk,
            ).fetchall())
        return rows
    if album_ids is not None:
        if not album_ids:
            return []
        rows = []
        ids = list(dict.fromkeys(int(album_id) for album_id in album_ids))
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f"""SELECT tf.id, tf.track_id, tf.path, tf.size, tf.file_state,
                             tf.missing_scan_count
                      FROM lib2_track_files tf
                   JOIN lib2_tracks t ON t.id = tf.track_id
                   WHERE t.album_id IN ({marks}) AND tf.path IS NOT NULL AND tf.path <> ''
                     AND COALESCE(tf.file_state,'active')<>'deleted'""",
                chunk,
            ).fetchall())
        return rows
    return conn.execute(
        """SELECT id, track_id, path, size, file_state, missing_scan_count
             FROM lib2_track_files WHERE path IS NOT NULL AND path <> ''
              AND COALESCE(file_state,'active')<>'deleted'"""
    ).fetchall()


def _persist_missing_observation(
    database, file_id: int, *, root_healthy: bool, allow_confirm: bool = True,
    force_confirm: bool = False, conn=None,
) -> tuple:
    """Persist one missing-path observation in a short transaction.

    Returns ``(state, changed)`` — the lifecycle state the row ended up in and
    whether that differs from the state it had. ``(None, False)`` when the
    observation was deferred. The two are not the same question: a scan of a
    library with two long-gone files should keep reporting two missing files,
    but only the scan that *first* concluded it may hand the track to
    acquisition.

    ``conn`` lets the caller supply a connection it owns.  The transaction stays
    exactly as short -- one ``commit()`` per observation -- but the scan loop no
    longer pays ``sqlite3.connect`` + a full schema parse per file.  That is
    ~3.8 ms on this project's 700-object schema, i.e. ~19 minutes of pure
    connection setup on a 300k-file rescan (perf-audit PERF-10).

    ``allow_confirm=False`` caps the row at ``missing_suspected`` no matter how
    many misses it has: pathdrift25-01: a stale *filename* leaves the parent
    directory perfectly healthy, so the miss looks credible and the row used to
    reach ``missing_confirmed`` while the song sat in that very folder under a
    slightly different name.  The miss is still counted — once the reconcile
    candidate disappears, the next scan confirms immediately.

    ``force_confirm`` skips the two-scan wait.  That wait protects the
    *unattended* scan, where a share that is briefly away must not flip half a
    library to missing; it is not a service to a person standing in front of
    the "Refresh & Scan" button, who asked a direct question and expects this
    pass to answer it.
    """
    if not root_healthy:
        return None, False
    from core.library2.track_files import set_file_state

    owned = conn is None
    conn = database._get_connection() if owned else conn
    try:
        row = conn.execute(
            "SELECT file_state, missing_scan_count FROM lib2_track_files WHERE id=?",
            (int(file_id),),
        ).fetchone()
        if not row or row["file_state"] not in (
            "active", "missing_suspected", "missing_confirmed"
        ):
            return None, False
        previous = row["file_state"]
        misses = int(row["missing_scan_count"] or 0) + 1
        confirmed = allow_confirm and (
            force_confirm or misses >= MISSING_CONFIRMATION_SCANS
        )
        state = "missing_confirmed" if confirmed else "missing_suspected"
        conn.execute(
            """UPDATE lib2_track_files
                  SET missing_scan_count=?,
                      missing_since=COALESCE(missing_since, CURRENT_TIMESTAMP),
                      updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (misses, int(file_id)),
        )
        set_file_state(conn, int(file_id), state)
        conn.commit()
        return state, state != previous
    finally:
        if owned:
            conn.close()


def _persist_verification_observation(conn, file_id: int, file_tags: Dict[str, Any]) -> None:
    """Adopt the file's own ``SOULSYNC_VERIFICATION`` tag into the catalogue.

    Every file the download pipeline finalizes gets this tag written
    (``core/imports/pipeline._persist_verification_status``) precisely so the
    information survives a DB reset and travels with the file. The rescan
    already reads it — dropping it left ``lib2_track_files.verification_status``
    NULL for everything imported outside the autolink callback, so the UI had
    nothing to show (issues.md T-09).

    This is an *observation*, not a new judgement:

    - an unknown tag value is ignored (never invent a fifth state);
    - a file without the tag never clears a status the catalogue already has;
    - ``human_verified`` is a person's decision and outranks any machine
      state stamped into the file, so it is never overwritten.
    """
    from core.matching.verification_status import ALL_STATUSES, HUMAN_VERIFIED

    status = str(file_tags.get("verification_status") or "").strip()
    if status not in ALL_STATUSES:
        return
    conn.execute(
        """UPDATE lib2_track_files
              SET verification_status=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND COALESCE(verification_status,'') NOT IN (?, ?)""",
        (status, int(file_id), status, HUMAN_VERIFIED),
    )


def _persist_present_observation(
    database,
    file_id: int,
    *,
    file_tags: Dict[str, Any],
    quality: Any = None,
    size: Optional[int] = None,
    tier: Optional[str] = None,
    conn=None,
) -> Dict[str, bool]:
    """Persist one completed file observation in a short transaction.

    Returns ``{"updated": ..., "recovered": ...}`` — whether the measured
    quality columns were written, and whether this file came back from a
    missing state.

    See ``_persist_missing_observation`` for why ``conn`` exists (PERF-10).
    """
    from core.library2.tag_cache import persist_tag_cache
    from core.library2.track_files import set_file_state

    owned = conn is None
    conn = database._get_connection() if owned else conn
    try:
        row = conn.execute(
            "SELECT file_state FROM lib2_track_files WHERE id=?", (int(file_id),)
        ).fetchone()
        if not row:
            return {"updated": False, "recovered": False}
        recovered = row["file_state"] in ("missing_suspected", "missing_confirmed")
        if recovered:
            set_file_state(conn, int(file_id), "active")
        conn.execute(
            """UPDATE lib2_track_files
                  SET missing_scan_count=0, missing_since=NULL
                WHERE id=? AND (missing_scan_count<>0 OR missing_since IS NOT NULL)""",
            (int(file_id),),
        )
        persist_tag_cache(conn, int(file_id), file_tags)
        _persist_verification_observation(conn, int(file_id), file_tags)
        if quality is not None:
            conn.execute(
                """UPDATE lib2_track_files SET
                       format = COALESCE(?, format),
                       bitrate = COALESCE(?, bitrate),
                       sample_rate = COALESCE(?, sample_rate),
                       bit_depth = COALESCE(?, bit_depth),
                       size = COALESCE(?, size),
                       quality_tier = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (
                    quality.format,
                    quality.bitrate,
                    quality.sample_rate,
                    quality.bit_depth,
                    size,
                    tier,
                    int(file_id),
                ),
            )
        conn.commit()
        return {"updated": quality is not None, "recovered": recovered}
    finally:
        if owned:
            conn.close()


def rescan_files(
    database,
    *,
    album_ids: Optional[List[int]] = None,
    file_ids: Optional[List[int]] = None,
    progress: ProgressCb = None,
    manual: bool = False,
    on_presence_change: Optional[Callable[[List[int]], None]] = None,
) -> Dict[str, int]:
    """Probe the files in scope and persist their measured audio properties.

    ``album_ids=None`` and ``file_ids=None`` scan the whole library; an empty
    supplied list scans nothing.  The two scopes are mutually exclusive.  The
    file-id scope is used by repair/change bridges so one ReplayGain or lyrics
    approval never turns into a full-library probe.

    ``on_presence_change`` receives the track ids whose availability actually
    changed — a file confirmed gone, one that came back. Acquisition is exactly
    the consumer of that event, so a caller can mirror the wanted projection
    for those tracks immediately instead of waiting for the hourly reconcile to
    rediscover it. Never called with an empty list.

    ``manual=True`` marks a scan a person asked for by pressing "Refresh &
    Scan".  Two things then differ from the unattended sweep: an unambiguous
    stale index path is repaired instead of merely reported, and a credible
    miss is confirmed on this pass instead of on the next one.  Both are
    deliberate — see ``_reconcile_drifted_paths`` and
    ``_persist_missing_observation``.

    Returns a stats dict: ``scanned``/``updated`` for files that were there,
    ``missing`` for paths that did not resolve, ``path_drift``/``path_repointed``
    for renames spotted and repaired, ``missing_suspected``/``missing_confirmed``
    for lifecycle transitions written, and ``recovered`` for files that came
    back.  Never raises for individual files — a broken file just stays on its
    imported values.

    Stored paths are the legacy DB's (often the media server's) view of the
    filesystem, so each one goes through the shared resolver — on path-mapped
    setups the raw path never exists here and a raw ``os.path.exists`` check
    would report the whole library "missing".
    """
    stats = {
        "scanned": 0, "updated": 0, "missing": 0, "path_drift": 0,
        "path_repointed": 0, "missing_suspected": 0, "missing_confirmed": 0,
        "recovered": 0,
    }
    conn = database._get_connection()
    try:
        # sqlite3.Row values remain tied to the result shape, so materialize
        # plain dicts before closing the read snapshot connection.
        rows = [
            dict(row)
            for row in _file_rows_in_scope(
                conn, album_ids=album_ids, file_ids=file_ids,
            )
        ]
    finally:
        conn.close()

    total = len(rows)
    presence: List[int] = []
    _rescan_loop(database, rows, total, progress=progress, stats=stats,
                 manual=manual, presence=presence)
    logger.info(
        "Library v2 file rescan: %(scanned)d probed, %(updated)d updated, "
        "%(missing)d paths absent, %(path_repointed)d repointed, "
        "%(missing_confirmed)d confirmed missing, %(recovered)d recovered",
        stats,
    )
    if presence and on_presence_change:
        # Deliberately after the log line and outside every write path: a
        # consumer that raises must not turn a completed scan into a failure.
        try:
            on_presence_change(sorted(set(presence)))
        except Exception as exc:  # noqa: BLE001
            logger.error("presence-change consumer failed: %s", exc, exc_info=True)
    return stats


#: How many files' observations are buffered before one connection flushes them.
#: Opening a connection costs a full parse of this project's ~700-object schema
#: (measured 3.8 ms), so a per-file connection spent ~19 minutes on connection
#: setup alone in a 300k-file rescan (perf-audit PERF-10).  Buffering keeps that
#: cost amortised WITHOUT holding a connection open across the file I/O, which
#: is the invariant `test_rescan_closes_snapshot_connection_before_file_io`
#: exists to protect: probing a file is slow and unpredictable (network mounts,
#: mutagen), and a connection open across it pins the WAL.
_OBSERVATION_FLUSH_BATCH = 100


def _flush_observations(database, pending, stats, presence=None) -> None:
    """Apply one buffered batch on a single connection, then close it.

    Each observation still commits on its own, so the write lock is held for
    exactly as long as it was per file — only the connection is shared.

    ``presence`` collects the tracks whose *availability* changed in this
    batch — a file confirmed gone, or one that came back. That is precisely
    the event acquisition cares about, and reporting it is what lets a scan
    hand its result straight to the wanted/Wishlist mirror instead of leaving
    it for the hourly reconcile to notice.
    """
    if not pending:
        return
    conn = database._get_connection()
    try:
        for kind, track_id, payload in pending:
            if kind == "missing":
                state, changed = _persist_missing_observation(
                    database, conn=conn, **payload)
                if state:
                    stats[state] = stats.get(state, 0) + 1
                if (changed and state == "missing_confirmed"
                        and presence is not None and track_id):
                    presence.append(int(track_id))
                continue
            outcome = _persist_present_observation(database, conn=conn, **payload)
            if outcome["updated"]:
                stats["updated"] += 1
            if outcome["recovered"]:
                stats["recovered"] += 1
                if presence is not None and track_id:
                    presence.append(int(track_id))
    finally:
        conn.close()
    pending.clear()


def _rescan_loop(database, rows, total, *, progress, stats, manual: bool = False,
                 presence=None) -> None:
    """The per-file body of :func:`rescan_files`, in three ordered phases.

    The order is the behaviour, not an implementation detail:

    1. probe everything that resolves — that is the actual refresh;
    2. give every path that did *not* resolve one chance to be a rename
       rather than a deletion;
    3. only what survives both becomes a missing observation.

    Reversing 2 and 3 is what made a renamed song walk towards
    ``missing_confirmed`` and into the redownload queue.

    File I/O happens with **no** database connection open; the resulting
    observations are buffered and flushed in batches (see
    ``_OBSERVATION_FLUSH_BATCH``).
    """
    unresolved = _probe_rows(database, rows, total, progress=progress, stats=stats,
                             presence=presence)
    if not unresolved:
        return
    repointed, ambiguous, still_missing = _reconcile_drifted_paths(
        database, unresolved, stats=stats, repair=manual,
    )
    if repointed:
        # A repointed row is an ordinary present file now: it must get the same
        # size/quality/tag refresh as every other one, or "Refresh & Scan"
        # would fix the path and leave the metadata stale until the next run.
        _probe_rows(database, repointed, len(repointed), progress=None, stats=stats,
                    presence=presence)
    _observe_missing(database, still_missing, stats=stats, manual=manual,
                     ambiguous=ambiguous, presence=presence)


def _probe_rows(database, rows, total, *, progress, stats,
                presence=None) -> List[Dict[str, Any]]:
    """Phase 1: measure every file that resolves; return the ones that do not."""
    from core.imports.file_ops import probe_audio_quality
    from core.library2.paths import resolve_lib2_path
    from core.library2.status import quality_tier
    from core.library2.tag_cache import read_tag_snapshot

    pending: list = []
    unresolved: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        if progress and i % 25 == 0:
            progress("scan", i, total)
        if len(pending) >= _OBSERVATION_FLUSH_BATCH:
            _flush_observations(database, pending, stats, presence)
        path = resolve_lib2_path(row["path"])
        if not path:
            unresolved.append(row)
            continue

        stats["scanned"] += 1
        file_tags = read_tag_snapshot(path)
        try:
            quality = probe_audio_quality(path)
        except Exception as e:  # noqa: BLE001
            logger.debug("probe failed (%s): %s", path, e)
            quality = None
        size = None
        tier = None
        if quality is not None:
            try:
                size = os.path.getsize(path)
            except OSError:
                pass
            tier = quality_tier(quality.format, quality.bitrate, quality.bit_depth)
        pending.append(("present", row.get("track_id"), {
            "file_id": row["id"],
            "file_tags": file_tags,
            "quality": quality,
            "size": size,
            "tier": tier,
        }))
    _flush_observations(database, pending, stats, presence)
    return unresolved


def _reconcile_drifted_paths(database, rows, *, stats, repair: bool):
    """Phase 2: ask whether an unresolvable path is a rename, not a deletion.

    "The file is not where the index says" and "the file is gone" are
    different statements, and the catalogue cannot tell them apart on its own:
    a naming-template change, a manual rename, or a reorganize that reached
    only one index all look exactly like a deletion. So the stale-index-path
    matcher gets asked first, scoped to precisely these rows — one directory
    listing per affected folder, shared across the rows in it.

    Only a single unambiguous candidate is ever applied, and only for a scan a
    person triggered; the index is repointed, no file is moved, renamed or
    deleted. Several plausible files stay ``ambiguous`` and are kept out of
    ``missing_confirmed`` for the Stale Index Paths tool to resolve, because
    handing one track's file to another is the one failure this must never
    create.

    Returns ``(repointed_rows, ambiguous_file_ids, still_missing_rows)``.
    """
    from core.library2.path_drift import apply_path_drift_fix, scan_path_drift

    file_ids = [int(row["id"]) for row in rows]
    try:
        report = scan_path_drift(database, file_ids=file_ids, limit=len(file_ids))
    except Exception as exc:  # noqa: BLE001 — a drift probe must never abort a scan
        logger.error("path drift scan failed during rescan: %s", exc, exc_info=True)
        return [], set(), rows

    proposals = {
        int(entry["file_id"]): entry for entry in report.get("proposals", [])
    }
    ambiguous = {
        int(entry["file_id"]) for entry in report.get("unresolved", [])
        if entry.get("status") == "ambiguous"
    }
    stats["path_drift"] += len(proposals) + len(ambiguous)

    if not repair:
        # Report-only mode keeps the pre-existing contract: an unambiguous
        # proposal is still evidence the song is there, so the row must not be
        # confirmed missing behind the user's back.
        return [], ambiguous | set(proposals), rows

    repointed: List[Dict[str, Any]] = []
    for row in rows:
        entry = proposals.get(int(row["id"]))
        if not entry:
            continue
        result = apply_path_drift_fix(
            database, int(row["id"]), entry["candidate_path"],
        )
        if result.get("success"):
            stats["path_repointed"] += 1
            repointed.append({**row, "path": result["path"]})
        else:
            # Someone changed the file between proposal and apply. Not an
            # error: the row simply stays unresolved and is protected from
            # confirmation this pass.
            logger.debug("path drift apply skipped (file %s): %s",
                         row["id"], result.get("error"))
            ambiguous.add(int(row["id"]))

    done = {int(row["id"]) for row in repointed}
    return repointed, ambiguous, [r for r in rows if int(r["id"]) not in done]


def _observe_missing(database, rows, *, stats, manual: bool, ambiguous,
                     presence=None) -> None:
    """Phase 3: what survived the drift check is genuinely absent."""
    from core.library2.paths import missing_path_root_is_healthy

    pending: list = []
    for row in rows:
        stats["missing"] += 1
        # pathdrift25-01: "unresolvable" and "gone" are not the same thing, and
        # neither is "gone" and "the storage is away". Only a reachable root
        # makes absence credible; on an unmounted library nothing advances.
        pending.append(("missing", row.get("track_id"), {
            "file_id": row["id"],
            "root_healthy": missing_path_root_is_healthy(row["path"]),
            "allow_confirm": int(row["id"]) not in ambiguous,
            "force_confirm": bool(manual),
        }))
        if len(pending) >= _OBSERVATION_FLUSH_BATCH:
            _flush_observations(database, pending, stats, presence)
    _flush_observations(database, pending, stats, presence)


__all__ = ["MISSING_CONFIRMATION_SCANS", "rescan_files"]
