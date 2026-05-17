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

    Returns:
        A callable ``runner(item)`` suitable for
        :meth:`core.reorganize_queue.ReorganizeQueue.set_runner`.
    """
    from core.library_reorganize import reorganize_album
    from core.reorganize_queue import get_queue

    def _update_track_path(track_id, new_path):
        try:
            db = get_database()
            with db._get_connection() as conn:
                conn.execute(
                    "UPDATE tracks SET file_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_path, str(track_id)),
                )
                conn.commit()
        except Exception as db_err:
            logger.warning(f"[Reorganize] DB path update failed for {track_id}: {db_err}")

    def runner(item):
        # Read config per-run so the user changing their download path
        # in Settings takes effect on the next reorganize without a
        # server restart.
        download_dir = get_download_path()
        transfer_dir = get_transfer_path()
        staging_root = os.path.join(download_dir, 'ssync_staging')
        try:
            os.makedirs(staging_root, exist_ok=True)
        except OSError as mk_err:
            logger.error(f"[Reorganize] Cannot create staging dir {staging_root}: {mk_err}")
            return {
                'status': 'setup_failed',
                'source': None,
                'total': 0, 'moved': 0, 'skipped': 0, 'failed': 0,
                'errors': [{'error': f'Could not create staging dir: {mk_err}'}],
            }

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

        return reorganize_album(
            album_id=item.album_id,
            db=get_database(),
            staging_root=staging_root,
            resolve_file_path_fn=resolve_file_path_fn,
            post_process_fn=post_process_fn,
            update_track_path_fn=_update_track_path,
            cleanup_empty_dir_fn=_cleanup_empty,
            transfer_dir=transfer_dir,
            on_progress=_on_progress,
            primary_source=item.source,
            strict_source=bool(item.source),
            stop_check=is_shutting_down_fn,
            metadata_source=getattr(item, 'metadata_source', 'api') or 'api',
        )

    return runner
