"""Builds the per-item runner closure that the reorganize queue worker
invokes. Lives outside ``web_server`` so the wiring is unit-testable
and the monolith stays small.

The runner ties three subsystems together:

* :func:`core.library_reorganize.reorganize_album` — the orchestrator
  that copies files to staging, matches them against the metadata
  source, and routes each through the post-process pipeline.
* :func:`core.reorganize_queue.get_queue` — the queue this runner is
  registered with; we forward live progress updates back into the
  active queue item so the status panel can show per-track state.
* The dependency callbacks injected by ``web_server`` (DB accessor,
  resolve-file-path, post-process function, empty-dir cleanup,
  shutdown signal). These are passed in rather than imported so the
  module stays testable in isolation.

Config (download path / transfer path) is read **per run**, not at
module load. That way a user changing their download path in settings
takes effect on the next reorganize without needing a server restart.
"""

import json
import os
from typing import Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("reorganize_runner")


def build_runner(
    *,
    get_database: Callable[[], object],
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]],
    post_process_fn: Callable[[str, dict, str], None],
    cleanup_empty_directories_fn: Callable[[str, str], None],
    is_shutting_down_fn: Callable[[], bool],
    get_download_path: Callable[[], str],
    get_transfer_path: Callable[[], str],
    build_final_path_fn: Optional[Callable] = None,
    get_config_manager: Optional[Callable[[], object]] = None,
) -> Callable[[object], dict]:
    """Return the closure the queue worker invokes per item.

    Args:
        get_database: Returns the live MusicDatabase singleton.
        resolve_file_path_fn: Resolves a DB-stored file path to the
            actual on-disk path (or ``None`` if missing).
        post_process_fn: ``_post_process_matched_download``. Must set
            ``context['_final_processed_path']`` on success.
        cleanup_empty_directories_fn: Called as
            ``cleanup_empty_directories_fn(transfer_dir, marker_path)``
            to prune empty source dirs after a track is moved.
        is_shutting_down_fn: Returns True when the server is shutting
            down so the orchestrator can abort early.
        get_download_path: Resolves the user's configured download
            path *at call time* (so config changes apply live).
        get_transfer_path: Same, for the transfer path.
        get_config_manager: Optional live config accessor. When supplied, a
            successful path update is reconciled through the strictly gated
            Library-v2 maintenance boundary and appears in entity history.

    Returns:
        A callable ``runner(item)`` suitable for
        :meth:`core.reorganize_queue.ReorganizeQueue.set_runner`.
    """
    from core.library_reorganize import reorganize_album, reorganize_album_rename_only
    from core.reorganize_queue import get_queue

    def _repoint_findings(conn, old_path, new_path):
        """Move any pending maintenance findings onto the file's new path.

        A finding stores its OWN snapshot of the path, so a reorganize used to
        leave every finding on a moved file naming somewhere that no longer
        exists. Those fixes could never succeed, and because a failed fix keeps
        the finding pending they were retried on every subsequent run until the
        user cleared them by hand (#1143).

        Both the column and ``details_json`` are updated: several fix handlers
        read ``details['file_path']`` in PREFERENCE to the column, so updating
        only the column would leave the fix still using the stale path while
        the UI showed the new one.

        Best-effort by design. A reorganize must never fail because of the
        maintenance tables — the track path update above is the important
        write, and a finding left behind is merely stale, which the
        vanished-file retirement already handles.
        """
        if not old_path or old_path == new_path:
            return
        try:
            rows = conn.execute(
                "SELECT id, details_json FROM repair_findings "
                "WHERE file_path = ? AND status = 'pending'",
                (old_path,),
            ).fetchall()
            for row in rows:
                finding_id, details_json = row[0], row[1]
                details_out = details_json
                if details_json:
                    try:
                        parsed = json.loads(details_json)
                        if isinstance(parsed, dict) and parsed.get('file_path') == old_path:
                            parsed['file_path'] = new_path
                            details_out = json.dumps(parsed)
                    except (ValueError, TypeError):
                        pass    # unparseable details: still fix the column
                conn.execute(
                    "UPDATE repair_findings SET file_path = ?, details_json = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_path, details_out, finding_id),
                )
            if rows:
                logger.info("[Reorganize] Re-pointed %d finding(s) onto %s",
                            len(rows), os.path.basename(new_path))
        except Exception as e:    # noqa: BLE001 — never break a reorganize
            logger.debug("[Reorganize] Could not re-point findings: %s", e)

    def _update_track_path(track_id, new_path):
        """Repoint the native catalogue at ``new_path``.

        iss29-E01: this MUST raise when the catalogue was not updated.
        ``_finalize_track`` detects a failed path update solely by catching an
        exception out of this callback, and on success it goes on to
        ``os.remove`` the original. Swallowing the error here therefore deleted
        the user's only copy while both catalogues still pointed at the old
        path — reported as "moved". The realistic trigger is the SQLite write
        lock being held elsewhere (import commit, ``recompute_wanted``): the
        connection raises ``database is locked``, the whole transaction rolls
        back, and the file is destroyed anyway. Downstream that reads as a lib2
        miss, a ``dead_file_cleaner`` finding and a re-download of a track the
        user already owned.

        The callback raises unless exactly one native file row is resolved, so
        the caller never deletes the source after an ambiguous/stale update.
        """
        lib2_links = {"track_ids": [], "file_ids": []}
        config_manager = get_config_manager() if get_config_manager is not None else None
        db = get_database()
        with db._get_connection() as conn:
            previous_path = None
            has_v2_files = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='lib2_track_files'"
            ).fetchone())
            if has_v2_files:
                rows = conn.execute(
                    """SELECT f.id AS file_id, f.track_id, f.path, f.is_primary
                         FROM lib2_track_files f
                        WHERE f.track_id=?
                          AND COALESCE(f.file_state,'active')<>'deleted'
                        ORDER BY f.is_primary DESC, f.id""",
                    (int(track_id),),
                ).fetchall()
                primary_rows = [row for row in rows if bool(row["is_primary"])] if rows else []
                if primary_rows:
                    previous_path = primary_rows[0]["path"]
                lib2_links = {
                    "track_ids": sorted({int(row["track_id"]) for row in rows}),
                    "file_ids": sorted({int(row["file_id"]) for row in primary_rows}),
                }
            if len(lib2_links["file_ids"]) != 1:
                raise RuntimeError(f"track {track_id} has no unambiguous file row")
            old_stem = os.path.splitext(os.path.basename(previous_path or ""))[0]
            new_stem = os.path.splitext(os.path.basename(new_path))[0]
            new_dir = os.path.dirname(new_path)
            repointed_ids = []
            for row in rows:
                old_row_path = str(row["path"] or "")
                row_stem, row_ext = os.path.splitext(os.path.basename(old_row_path))
                # The filesystem mover carries same-stem audio versions (for
                # example FLAC + OPUS) alongside the primary. Repoint exactly
                # those rows in the same transaction; unrelated historical
                # versions with another stem were not moved and stay put.
                if int(row["file_id"]) == lib2_links["file_ids"][0]:
                    row_new_path = new_path
                elif row_stem == old_stem and row_ext:
                    row_new_path = os.path.join(new_dir, new_stem + row_ext)
                else:
                    continue
                conn.execute(
                    """UPDATE lib2_track_files
                          SET path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (row_new_path, int(row["file_id"])),
                )
                _repoint_findings(conn, old_row_path, row_new_path)
                repointed_ids.append(int(row["file_id"]))
            lib2_links["file_ids"] = sorted(repointed_ids)
            # Findings carry their own path snapshot. Move those snapshots in
            # the same transaction as the native file row so future fixes do
            # not keep retrying a path that the reorganize just removed.
            conn.commit()

        # A lib2-imported track keeps a legacy_track_id back-reference. Route
        # the update through the common boundary so path, file snapshot,
        # artwork state and History stay coherent under the native-catalogue
        # cutover contract.
        try:
            if config_manager is not None:
                from core.library2.maintenance_sync import sync_repair_change

                sync_repair_change(
                    db,
                    config_manager,
                    job_id="library_reorganize",
                    finding_type="path_mismatch",
                    action="moved_file",
                    entity_type="track",
                    entity_id=(
                        f"lib2:{lib2_links['track_ids'][0]}"
                        if len(lib2_links["track_ids"]) == 1 else str(track_id)
                    ),
                    file_path=new_path,
                    details={
                        "to_abs": new_path,
                        "library_v2": {
                            "track_ids": lib2_links["track_ids"],
                            "file_ids": lib2_links["file_ids"],
                        },
                    },
                    result={"success": True, "action": "moved_file"},
                )
        except Exception as lib2_err:
            logger.debug(
                "[Reorganize] Library-v2 path sync skipped for %s: %s",
                track_id, lib2_err,
            )

    def runner(item):
        # Read config per-run so the user changing their download path
        # in Settings takes effect on the next reorganize without a
        # server restart.
        download_dir = get_download_path()
        transfer_dir = get_transfer_path()

        def _cleanup_empty(src_dir):
            try:
                cleanup_empty_directories_fn(transfer_dir, os.path.join(src_dir, '_'))
            except Exception as e:
                logger.debug("cleanup empty dirs failed: %s", e)

        def _on_progress(updates):
            try:
                get_queue().update_active_progress(queue_id=item.queue_id, **updates)
            except Exception as e:
                # Progress fan-out failures must never break a run.
                logger.debug("reorganize progress fan-out: %s", e)

        # A reorganize moves files. It used to have a second mode that copied
        # each one into staging and sent it back through the download
        # post-processing pipeline -- an ADMISSION check, run against a file
        # the user already owns. That mode fingerprinted a library file and
        # quarantined it over its own audio (Kanji artist vs the Romaji the
        # catalogue held), and it collected four opt-outs, one per bug report,
        # each disabling a gate that had no business running here.
        #
        # Re-tagging did not live in there alone: it is a job of its own, with
        # a preview, findings and a rule about hand-set fields. So nothing is
        # left for the staging path to contribute, and every item takes the
        # mover -- `rename_only` included, which is why the flag is no longer
        # read.
        if build_final_path_fn is None:
            return {
                'status': 'setup_failed', 'source': None,
                'total': 0, 'moved': 0, 'skipped': 0, 'failed': 0,
                'errors': [{'error': 'Reorganize unavailable (no path builder)'}],
            }
        from core.library2.reorganize_bridge import catalogue_preview_fn

        return reorganize_album_rename_only(
            album_id=item.album_id,
            db=get_database(),
            transfer_dir=transfer_dir,
            resolve_file_path_fn=resolve_file_path_fn,
            build_final_path_fn=build_final_path_fn,
            update_track_path_fn=_update_track_path,
            cleanup_empty_dir_fn=_cleanup_empty,
            on_progress=_on_progress,
            primary_source=item.source,
            strict_source=bool(item.source),
            metadata_source=getattr(item, 'metadata_source', 'api') or 'api',
            stop_check=is_shutting_down_fn,
            # The catalogue is the source for a destination path -- no provider
            # call, no source-id requirement, and the filename matches what the
            # Library page shows because both read the same override layer.
            preview_fn=catalogue_preview_fn,
        )

    return runner
