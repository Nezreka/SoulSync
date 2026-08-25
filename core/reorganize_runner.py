"""Builds the per-item runner closure that the reorganize queue worker
invokes. Lives outside ``web_server`` so the wiring is unit-testable
and the monolith stays small.

The runner ties three subsystems together:

* :func:`core.library_reorganize.reorganize_album_rename_only` — the
  executor. It MOVES each file to the path the current template
  dictates and does nothing else. There was a second, "full" executor
  that staged a copy and pushed it through the download post-process;
  it is gone, along with everything a reorganize had to opt out of to
  survive it.
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
    cleanup_empty_directories_fn: Callable[[str, str], None],
    is_shutting_down_fn: Callable[[], bool],
    get_transfer_path: Callable[[], str],
    post_process_fn: Optional[Callable[[str, dict, str], None]] = None,
    get_download_path: Optional[Callable[[], str]] = None,
    build_final_path_fn: Optional[Callable] = None,
) -> Callable[[object], dict]:
    """Return the closure the queue worker invokes per item.

    Args:
        get_database: Returns the live MusicDatabase singleton.
        resolve_file_path_fn: Resolves a DB-stored file path to the
            actual on-disk path (or ``None`` if missing).
        cleanup_empty_directories_fn: Called as
            ``cleanup_empty_directories_fn(transfer_dir, marker_path)``
            to prune empty source dirs after a track is moved.
        is_shutting_down_fn: Returns True when the server is shutting
            down so the orchestrator can abort early.
        get_transfer_path: Resolves the user's configured transfer path
            *at call time* (so config changes apply live).
        post_process_fn, get_download_path: accepted and ignored. A
            reorganize no longer stages a copy and no longer runs the
            download post-process, so it needs neither the pipeline nor
            the staging root. Kept in the signature so existing wiring
            keeps working.

    Returns:
        A callable ``runner(item)`` suitable for
        :meth:`core.reorganize_queue.ReorganizeQueue.set_runner`.
    """
    from core.library_reorganize import reorganize_album_rename_only
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
        """Repoint the catalogue at ``new_path``.

        This MUST raise when the catalogue was not updated. ``_finalize_track``
        detects a failed path update SOLELY by catching an exception out of this
        callback, and on success it goes on to ``os.remove`` the original.
        Swallowing the error here therefore deleted the user's only copy while
        the catalogue still pointed at the old path — reported as "moved", read
        by the library as MISSING, and re-downloaded later as if it had never
        been there.

        The realistic trigger is the SQLite write lock being held elsewhere (an
        import commit, ``recompute_wanted``): the connection raises ``database
        is locked``, the whole transaction rolls back, and the file is destroyed
        anyway. Reported against a fresh library — songs downloaded, Reorganize
        run straight afterwards, tracks came back missing.

        A 0-row UPDATE is NOT an SQLite error — it is silent success — so the
        rowcount is checked explicitly: an id the catalogue does not know has to
        fail just as loudly as a locked database.
        """
        db = get_database()
        with db._get_connection() as conn:
            # Read the old path BEFORE overwriting it — it is the only key
            # the findings rows can be matched on.
            row = conn.execute(
                "SELECT file_path FROM tracks WHERE id = ?", (str(track_id),)
            ).fetchone()
            old_path = row[0] if row else None
            cursor = conn.execute(
                "UPDATE tracks SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_path, str(track_id)),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"track {track_id} was not repointed — the UPDATE matched "
                    f"{cursor.rowcount} rows, so the catalogue still names the "
                    f"old path"
                )
            _repoint_findings(conn, old_path, new_path)
            conn.commit()

    def runner(item):
        # Read config per-run so the user changing their download path
        # in Settings takes effect on the next reorganize without a
        # server restart.
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

        # A reorganize moves files to the current scheme, and that is all it
        # does. There used to be a second, "full" executor that staged a COPY of
        # a file the user already owns and pushed it through the DOWNLOAD
        # post-process — an acceptance check for files of unknown origin. It is
        # gone: tagging is the Library Re-tag job's, identity is the AcoustID
        # Scanner's, and neither of them moves anyone's audio to answer.
        if build_final_path_fn is None:
            return {
                'status': 'setup_failed', 'source': None,
                'total': 0, 'moved': 0, 'skipped': 0, 'failed': 0,
                'errors': [{'error': 'Reorganize unavailable (no path builder)'}],
            }
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
            stop_check=is_shutting_down_fn,
        )

    return runner
