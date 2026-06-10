"""Batch + unified download status helpers.

`build_batch_status_data` is the per-batch payload formatter shared by:
- /api/playlists/<batch_id>/download_status (single batch)
- /api/download_status/batch (multiple batches in one call)

It's NOT pure read-only — it has a safety valve that mutates task state
when slskd reports terminal-but-stuck downloads, and it submits the
post-processing worker when slskd reports 'Succeeded' or when a stuck
file is recovered. Those side effects are preserved exactly.

`build_unified_downloads_response` powers /api/downloads/all — flattens
all tasks across batches into one sorted list with per-row metadata for
the centralized Downloads page.

Lifted verbatim from web_server.py. Dependencies that touch the live
runtime (config, file finder, post-processing submitter, transfer cache)
are passed via `StatusDeps` so the module is web_server-import-free.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from core.runtime_state import (
    download_batches,
    download_tasks,
    tasks_lock,
)
from utils.logging_config import get_logger

# Project logger factory so these lines reach app.log (soulsync.* namespace).
logger = get_logger("downloads.status")


def _schedule_completion_callback(deps, batch_id: str, task_id: str, success: bool) -> None:
    """Fire ``deps.on_download_completed`` on a one-shot daemon thread so
    the caller can hold ``tasks_lock`` without deadlocking.

    ``on_download_completed`` re-acquires ``tasks_lock`` (it removes the
    completed task from the batch's active set, decrements active_count,
    and may submit the next queued worker). Calling it synchronously
    from within ``build_batch_status_data`` — which is invoked under the
    same Lock — would self-deadlock since ``threading.Lock`` is not
    reentrant. A daemon thread defers the call until after the lock is
    released.
    """
    if deps.on_download_completed is None:
        return

    def _run():
        try:
            deps.on_download_completed(batch_id, task_id, success)
        except Exception as exc:
            logger.error(
                "[Status] deferred on_download_completed raised for task %s: %s",
                task_id, exc,
            )

    threading.Thread(
        target=_run,
        name=f"on-completed-{task_id[:8]}",
        daemon=True,
    ).start()


@dataclass
class StatusDeps:
    """Cross-cutting deps the status helpers need."""
    config_manager: Any
    docker_resolve_path: Callable[[str], str]
    find_completed_file: Callable
    make_context_key: Callable[[str, str], str]
    submit_post_processing: Callable[[str, str], None]  # (task_id, batch_id) -> None
    get_cached_transfer_data: Callable[[], dict]
    # Engine-state fallback for non-Soulseek (streaming) downloads.
    # Without these, YouTube/Tidal/Qobuz/HiFi/Deezer/SoundCloud/Lidarr
    # tasks never appear in live_transfers_lookup so their status never
    # advances out of 'downloading 0%'.
    download_orchestrator: Any = None
    run_async: Optional[Callable] = None
    on_download_completed: Optional[Callable[[str, str, bool], None]] = None
    get_persistent_download_history: Optional[Callable[[int], list[dict]]] = None


# Streaming sources the engine fallback applies to. Soulseek goes through
# slskd's live_transfers path and must NOT hit the engine fallback.
_STREAMING_SOURCE_NAMES = frozenset((
    'youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr', 'soundcloud', 'amazon',
    'torrent', 'usenet',
))
_RELEASE_SOURCE_NAMES = frozenset(('torrent', 'usenet'))

# Keep these in sync with the engine plugins' state strings.
_ENGINE_FAILURE_STATES = ('Errored', 'Failed', 'Rejected', 'TimedOut', 'Aborted')
_ENGINE_CANCELLED_STATES = ('Cancelled', 'Canceled')
_ENGINE_SUCCESS_STATES = ('Succeeded', 'Completed, Succeeded')


def _engine_state_str(record: Any) -> str:
    if record is None:
        return ''
    state = getattr(record, 'state', None)
    if state is None and isinstance(record, dict):
        state = record.get('state')
    return str(state) if state is not None else ''


def _engine_progress_pct(record: Any) -> float:
    if record is None:
        return 0
    progress = getattr(record, 'progress', None)
    if progress is None and isinstance(record, dict):
        progress = record.get('progress')
    try:
        progress = float(progress)
    except (TypeError, ValueError):
        return 0
    if progress <= 1.0:
        progress *= 100
    return progress


def _apply_engine_state_fallback(
    task_id: str,
    task: dict,
    task_status: dict,
    batch_id: str,
    deps: StatusDeps,
) -> None:
    """Populate ``task_status`` from the download engine's per-source
    record when the task isn't in ``live_transfers_lookup`` — i.e. it's
    a non-Soulseek streaming source. Mirrors the Soulseek branch's
    Cancelled → Failed → Succeeded → InProgress priority order so
    compound states like ``"Completed, Errored"`` hit the failure branch
    first.

    Mutates ``task`` in place (status / error_message) the same way the
    Soulseek branch does, so the next status poll sees the new state.
    Submits post-processing on terminal success. Manual-pick failures
    are completed here; automatic failures keep the existing retry path
    in charge.
    """
    if deps.download_orchestrator is None or deps.run_async is None:
        return
    download_id = task.get('download_id')
    if not download_id:
        return
    ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
    username = task.get('username') or ti.get('username')
    if username not in _STREAMING_SOURCE_NAMES:
        return
    manual_pick = bool(task.get('_user_manual_pick'))
    if not manual_pick and username not in _RELEASE_SOURCE_NAMES:
        return
    release_source = username in _RELEASE_SOURCE_NAMES
    terminal_status = task.get('status') in ('completed', 'failed', 'cancelled', 'not_found', 'post_processing')
    if terminal_status and not (
        release_source
        and task.get('status') in ('failed', 'not_found')
        and download_id
    ):
        return

    try:
        record = deps.run_async(
            deps.download_orchestrator.get_download_status(download_id)
        )
    except Exception as exc:
        logger.debug(
            "[Engine Fallback] get_download_status(%s) raised: %s",
            download_id, exc,
        )
        return

    if record is None:
        return

    state_str = _engine_state_str(record)
    if not state_str:
        return

    if any(s in state_str for s in _ENGINE_CANCELLED_STATES):
        if task['status'] != 'cancelled':
            task['status'] = 'cancelled'
            err = getattr(record, 'error_message', None) or getattr(record, 'error', None) or ''
            if err:
                task['error_message'] = str(err)
            _schedule_completion_callback(deps, batch_id, task_id, False)
        task_status['status'] = 'cancelled'
        task_status['progress'] = _engine_progress_pct(record)
        return

    if any(s in state_str for s in _ENGINE_FAILURE_STATES):
        if not manual_pick:
            task_status['status'] = task.get('status', 'downloading')
            task_status['progress'] = _engine_progress_pct(record)
            logger.info(
                "[Engine Fallback] Task %s engine reports '%s' for auto %s attempt; leaving retry handling to monitor",
                task_id, state_str, username,
            )
            return
        if task['status'] != 'failed':
            task['status'] = 'failed'
            err = getattr(record, 'error_message', None) or getattr(record, 'error', None) or ''
            task['error_message'] = (
                str(err) if err
                else f'{username} download failed (engine state: {state_str})'
            )
            logger.info(
                "[Engine Fallback] Task %s engine reports '%s' — marking failed",
                task_id, state_str,
            )
            _schedule_completion_callback(deps, batch_id, task_id, False)
        task_status['status'] = 'failed'
        task_status['error_message'] = task.get('error_message')
        task_status['progress'] = _engine_progress_pct(record)
        return

    if any(s in state_str for s in _ENGINE_SUCCESS_STATES):
        if task['status'] != 'post_processing':
            task['status'] = 'post_processing'
            logger.info(
                "[Engine Fallback] Task %s engine reports '%s' — starting post-processing verification",
                task_id, state_str,
            )
            try:
                deps.submit_post_processing(task_id, batch_id)
            except Exception as exc:
                logger.error(
                    "[Engine Fallback] submit_post_processing raised for task %s: %s",
                    task_id, exc,
                )
        task_status['status'] = 'post_processing'
        task_status['progress'] = 95
        return

    if 'InProgress' in state_str:
        task_status['status'] = 'downloading'
        if task['status'] in ('searching', 'queued'):
            task['status'] = 'downloading'
    elif 'Queued' in state_str:
        task_status['status'] = 'queued'
    task_status['progress'] = _engine_progress_pct(record)


def build_batch_status_data(batch_id: str, batch: dict, live_transfers_lookup: dict, deps: StatusDeps) -> dict:
    """Build status payload for a single batch.

    Includes a safety-valve that mutates stuck task state and submits the
    post-processing worker when slskd reports 'Succeeded' or when a
    stuck-but-recovered file is found on disk.
    """
    response_data = {
        "phase": batch.get('phase', 'unknown'),
        "error": batch.get('error'),
        "auto_initiated": batch.get('auto_initiated', False),
        "playlist_id": batch.get('playlist_id'),  # Include playlist_id for rehydration
        "playlist_name": batch.get('playlist_name'),  # Include playlist_name for reference
    }
    album_bundle = _build_album_bundle_status(batch)
    if album_bundle:
        response_data['album_bundle'] = album_bundle

    if response_data["phase"] in ('analysis', 'queued'):
        # Surface analysis_progress for both phases — ``queued`` rows
        # haven't started analysis yet (processed=0) but the UI still
        # needs ``total`` so it can render "Queued — N tracks". The
        # master worker flips phase from ``queued`` to ``analysis`` on
        # entry (see ``core/downloads/master.py:328``); the wishlist
        # submission sites pre-populate ``analysis_total`` so the
        # queued state isn't shape-mismatched against the analysis
        # state for downstream consumers.
        response_data['analysis_progress'] = {
            'total': batch.get('analysis_total', 0),
            'processed': batch.get('analysis_processed', 0),
        }
        response_data['analysis_results'] = batch.get('analysis_results', [])

    elif response_data["phase"] in ['downloading', 'complete', 'error']:
        response_data['analysis_results'] = batch.get('analysis_results', [])
        batch_tasks = []
        for task_id in batch.get('queue', []):
            task = download_tasks.get(task_id)
            if not task:
                continue

            # SAFETY VALVE: Check for downloads stuck too long
            current_time = time.time()
            task_start_time = task.get('status_change_time', current_time)
            task_age = current_time - task_start_time

            # If task has been running too long, check if file completed
            _dl_timeout = deps.config_manager.get('soulseek.download_timeout', 600) or 600
            if task_age > _dl_timeout and task['status'] in ['downloading', 'queued', 'searching']:
                stuck_state = task['status']
                task_filename = task.get('filename') or (task.get('track_info') or {}).get('filename')

                # Before failing, check if the file actually downloaded successfully
                recovered = False
                if task_filename and stuck_state == 'downloading':
                    try:
                        download_dir = deps.docker_resolve_path(deps.config_manager.get('soulseek.download_path', './downloads'))
                        transfer_dir = deps.docker_resolve_path(deps.config_manager.get('soulseek.transfer_path', './Transfer'))
                        found_file, file_location = deps.find_completed_file(download_dir, task_filename, transfer_dir)
                        if found_file:
                            logger.info(f"[Safety Valve] Task {task_id} stuck but file found in {file_location} — routing to post-processing")
                            task['status'] = 'post_processing'
                            task['status_change_time'] = current_time
                            deps.submit_post_processing(task_id, batch_id)
                            recovered = True
                    except Exception as e:
                        logger.error(f"[Safety Valve] Error checking for completed file: {e}")

                if not recovered:
                    if stuck_state == 'searching':
                        logger.info(f"⏰ [Safety Valve] Task {task_id} stuck in searching for {task_age:.1f}s - marking not_found")
                        task['status'] = 'not_found'
                        task['error_message'] = f'Search stuck for {int(task_age // 60)} minutes with no results — timed out'
                    else:
                        logger.error(f"⏰ [Safety Valve] Task {task_id} stuck for {task_age:.1f}s - forcing failure")
                        task['status'] = 'failed'
                        task['error_message'] = f'Task stuck in {stuck_state} state for {int(task_age // 60)} minutes — forcibly stopped'

            task_status = {
                'task_id': task_id,
                'track_index': task['track_index'],
                'status': task['status'],
                'track_info': task['track_info'],
                'progress': 0,
                # V2 SYSTEM: Add persistent state information
                'cancel_requested': task.get('cancel_requested', False),
                'cancel_timestamp': task.get('cancel_timestamp'),
                'ui_state': task.get('ui_state', 'normal'),  # normal|cancelling|cancelled
                'playlist_id': task.get('playlist_id'),      # For V2 system identification
                'error_message': task.get('error_message'),  # Surface failure reasons to UI
                'quarantine_entry_id': task.get('quarantine_entry_id'),
                'has_candidates': bool(task.get('cached_candidates')),  # Whether search found results (for clickable review)
                # 'verified' / 'unverified' / 'force_imported' — set by the
                # import pipeline once post-processing finishes.
                'verification_status': task.get('verification_status'),
                # "2/5" while the quarantine-retry engine walks candidates.
                'retry_info': task.get('retry_info'),
                'retry_trigger': task.get('retry_trigger'),
            }
            _ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
            task_filename = task.get('filename') or _ti.get('filename')
            task_username = task.get('username') or _ti.get('username')
            if (
                task_username in _RELEASE_SOURCE_NAMES
                and task['status'] not in ['completed', 'cancelled', 'post_processing']
                and task.get('download_id')
            ):
                _apply_engine_state_fallback(
                    task_id, task, task_status, batch_id, deps,
                )
                batch_tasks.append(task_status)
                continue

            if task_filename and task_username:
                lookup_key = deps.make_context_key(task_username, task_filename)

                if lookup_key in live_transfers_lookup:
                    live_info = live_transfers_lookup[lookup_key]
                    state_str = live_info.get('state', 'Unknown')

                    # Don't override tasks that are already in terminal states or post-processing
                    if task['status'] not in ['completed', 'failed', 'cancelled', 'not_found', 'post_processing']:
                        # SYNC.PY PARITY: Prioritized state checking (Errored/Cancelled before Completed)
                        # This prevents "Completed, Errored" states from being marked as completed
                        if 'Cancelled' in state_str or 'Canceled' in state_str:
                            task_status['status'] = 'cancelled'
                            task['status'] = 'cancelled'
                        elif 'Failed' in state_str or 'Errored' in state_str or 'Rejected' in state_str or 'TimedOut' in state_str:
                            # User-initiated manual pick — surface the failure
                            # immediately. The monitor's auto-retry path is gated
                            # on `_user_manual_pick` and won't fire, so deferring
                            # to it would leave the task stuck at 'downloading 0%'
                            # forever. Mark failed here and free the worker slot.
                            if task.get('_user_manual_pick'):
                                err_msg = live_info.get('errorMessage') or live_info.get('error') or ''
                                task['status'] = 'failed'
                                task['error_message'] = (
                                    str(err_msg) if err_msg
                                    else f'Manual pick failed (state: {state_str})'
                                )
                                task_status['status'] = 'failed'
                                task_status['error_message'] = task['error_message']
                                logger.info(
                                    f"[Manual Pick] Task {task_id} engine reports '{state_str}' — marking failed"
                                )
                                # NOTE: caller (build_batched_status) holds
                                # tasks_lock. on_download_completed re-acquires
                                # the same Lock — synchronous call would
                                # deadlock. Spawn a thread so it runs after we
                                # release the lock.
                                _schedule_completion_callback(deps, batch_id, task_id, False)
                            else:
                                # UNIFIED ERROR HANDLING: Let monitor handle errors for consistency
                                # Monitor will detect errored state and trigger retry within 5 seconds
                                logger.error(f"Task {task_id} API shows error state: {state_str} - letting monitor handle retry")

                                # Keep task in current status (downloading/queued) so monitor can detect error
                                # Don't mark as failed here - let the unified retry system handle it
                                if task['status'] in ['searching', 'downloading', 'queued']:
                                    task_status['status'] = task['status']  # Keep current status for monitor
                                else:
                                    task_status['status'] = 'downloading'  # Default to downloading for error detection
                                    task['status'] = 'downloading'
                        elif 'Completed' in state_str or 'Succeeded' in state_str:
                            # Verify bytes actually transferred before trusting state string
                            expected_size = live_info.get('size', 0)
                            transferred = live_info.get('bytesTransferred', 0)
                            if expected_size > 0 and transferred < expected_size:
                                # State says complete but bytes don't match — keep current status
                                task_status['status'] = task['status']
                                logger.info(f"Task {task_id} state says complete but bytes incomplete ({transferred}/{expected_size})")
                            # NEW VERIFICATION WORKFLOW: Use intermediate post_processing status
                            # Only set this status once to prevent multiple worker submissions
                            elif task['status'] != 'post_processing':
                                task_status['status'] = 'post_processing'
                                task['status'] = 'post_processing'
                                logger.info(f"Task {task_id} API reports 'Succeeded' - starting post-processing verification")

                                # Submit post-processing worker to verify file and complete the task
                                deps.submit_post_processing(task_id, batch_id)
                            else:
                                # FIXED: Always require verification workflow - no bypass for stream processed tasks
                                # Stream processing only handles metadata, not file verification
                                task_status['status'] = 'post_processing'
                                logger.info(f"Task {task_id} waiting for verification worker to complete")
                        elif 'InProgress' in state_str:
                            task_status['status'] = 'downloading'
                        else:
                            task_status['status'] = 'queued'
                        task_status['progress'] = live_info.get('percentComplete', 0)
                    # For completed/post-processing tasks, keep appropriate progress
                    elif task['status'] == 'completed':
                        task_status['progress'] = 100
                    elif task['status'] == 'post_processing':
                        task_status['progress'] = 95  # Nearly complete, just verifying
                else:
                    # If task is completed but not in live transfers, keep appropriate status
                    if task['status'] == 'completed':
                        task_status['progress'] = 100
                    elif task['status'] == 'post_processing':
                        task_status['progress'] = 95  # Nearly complete, just verifying
                    else:
                        # Non-Soulseek (streaming) sources don't appear in
                        # slskd's live_transfers_lookup — poll the engine
                        # directly so YouTube/Tidal/Qobuz/HiFi/Deezer/
                        # SoundCloud/Lidarr tasks actually advance out of
                        # 'downloading 0%' instead of staying there forever.
                        _apply_engine_state_fallback(
                            task_id, task, task_status, batch_id, deps,
                        )
            batch_tasks.append(task_status)
        batch_tasks.sort(key=lambda x: x['track_index'])
        response_data['tasks'] = batch_tasks

        # CRITICAL: Add batch worker management metadata (was missing!)
        # This is essential for client-side worker validation and prevents false desync warnings
        response_data['active_count'] = batch.get('active_count', 0)
        response_data['max_concurrent'] = batch.get('max_concurrent', 3)

        # Add wishlist summary if batch is complete (matching sync.py behavior)
        if response_data["phase"] == 'complete' and 'wishlist_summary' in batch:
            response_data['wishlist_summary'] = batch['wishlist_summary']

    return response_data


# ---------------------------------------------------------------------------
# Route-shaped builders
# ---------------------------------------------------------------------------

def build_single_batch_status(batch_id: str, deps: StatusDeps) -> tuple[Optional[dict], int]:
    """For /api/playlists/<batch_id>/download_status. Returns (response, status)."""
    live_transfers_lookup = deps.get_cached_transfer_data()

    with tasks_lock:
        if batch_id not in download_batches:
            return {"error": "Batch not found"}, 404

        batch = download_batches[batch_id]
        return build_batch_status_data(batch_id, batch, live_transfers_lookup, deps), 200


def build_batched_status(requested_batch_ids: list, deps: StatusDeps) -> dict:
    """For /api/download_status/batch. Returns the full response dict (always 200).

    When a requested batch carries a ``wishlist_run_id`` (Phase 1c.2.1
    per-album split), the response merges in every sibling sub-batch
    of the same run via ``merge_wishlist_run_status``. The merged view
    lands keyed under the originally-requested ``batch_id`` so the
    frontend modal (which polls one batch id) sees every sibling's
    tasks + progress without needing to know about the split."""
    from core.downloads.wishlist_aggregator import merge_wishlist_run_status

    live_transfers_lookup = deps.get_cached_transfer_data()
    response: dict[str, Any] = {"batches": {}}

    with tasks_lock:
        if requested_batch_ids:
            target_batches = {
                bid: batch for bid, batch in download_batches.items()
                if bid in requested_batch_ids
            }
        else:
            target_batches = download_batches.copy()

        # Pre-index sibling batch ids by wishlist_run_id so the per-
        # batch loop below can find them in O(1). Snapshot under the
        # held lock; subsequent dict mutations don't matter for this
        # build.
        run_id_to_batch_ids: dict[str, list[str]] = {}
        for bid, batch_row in download_batches.items():
            run_id = (batch_row or {}).get('wishlist_run_id') if isinstance(batch_row, dict) else None
            if run_id:
                run_id_to_batch_ids.setdefault(str(run_id), []).append(bid)

        for batch_id, batch in target_batches.items():
            try:
                primary_status = build_batch_status_data(
                    batch_id, batch, live_transfers_lookup, deps,
                )

                # Wishlist-run merge — kicks in only when this batch
                # has a run_id AND at least one sibling exists. Falls
                # through to legacy single-batch shape otherwise.
                run_id = batch.get('wishlist_run_id') if isinstance(batch, dict) else None
                sibling_ids = run_id_to_batch_ids.get(str(run_id), []) if run_id else []
                if run_id and len(sibling_ids) > 1:
                    sibling_statuses = []
                    for sib_id in sibling_ids:
                        if sib_id == batch_id:
                            continue
                        sib_batch = download_batches.get(sib_id)
                        if not isinstance(sib_batch, dict):
                            continue
                        try:
                            sibling_statuses.append(
                                build_batch_status_data(
                                    sib_id, sib_batch, live_transfers_lookup, deps,
                                )
                            )
                        except Exception as sib_err:
                            logger.warning(
                                f"[Wishlist Run] Sibling status build failed for {sib_id}: {sib_err}"
                            )
                    merged = merge_wishlist_run_status(primary_status, sibling_statuses)
                    response["batches"][batch_id] = merged
                else:
                    response["batches"][batch_id] = primary_status
            except Exception as batch_error:
                logger.error(f"Error processing batch {batch_id}: {batch_error}")
                response["batches"][batch_id] = {"error": str(batch_error)}

    response["metadata"] = {
        "total_batches": len(response["batches"]),
        "requested_batch_ids": requested_batch_ids,
        "timestamp": time.time(),
    }

    debug_info = {}
    for batch_id, batch_status in response["batches"].items():
        if "error" not in batch_status:
            active_count = batch_status.get("active_count", 0)
            max_concurrent = batch_status.get("max_concurrent", 3)
            task_count = len(batch_status.get("tasks", []))
            active_tasks = len([t for t in batch_status.get("tasks", []) if t.get("status") in ['searching', 'downloading', 'queued']])

            debug_info[batch_id] = {
                "reported_active": active_count,
                "actual_active_tasks": active_tasks,
                "max_concurrent": max_concurrent,
                "total_tasks": task_count,
                "worker_discrepancy": active_count != active_tasks,
            }

    response["debug_info"] = debug_info

    logger.info(f"[Batched Status] Returning status for {len(response['batches'])} batches")

    discrepancies = [bid for bid, info in debug_info.items() if info.get("worker_discrepancy")]
    if discrepancies:
        logger.info(f"[Batched Status] Worker count discrepancies in batches: {discrepancies}")

    return response


_STATUS_PRIORITY = {
    'downloading': 0, 'searching': 1, 'post_processing': 2,
    'queued': 3, 'pending': 3,
    'completed': 4, 'skipped': 5, 'already_owned': 5,
    'not_found': 6, 'failed': 7, 'cancelled': 8,
}

_PERSISTENT_HISTORY_TAIL_LIMIT = 50


def _normalize_identity_part(value: Any) -> str:
    return str(value or '').strip().casefold()


def _download_identity(title: Any, artist: Any, album: Any) -> tuple[str, str, str]:
    return (
        _normalize_identity_part(title),
        _normalize_identity_part(artist),
        _normalize_identity_part(album),
    )


def _history_timestamp(value: Any) -> float:
    if not value:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        # SQLite CURRENT_TIMESTAMP uses "YYYY-MM-DD HH:MM:SS".
        return datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp()
    except ValueError:
        try:
            return datetime.strptime(text[:19], '%Y-%m-%d %H:%M:%S').timestamp()
        except ValueError:
            return 0.0


def _build_history_download_item(entry: dict) -> dict:
    history_id = entry.get('id') or entry.get('history_id') or ''
    title = entry.get('title') or entry.get('source_track_title') or ''
    artist = entry.get('artist_name') or entry.get('source_artist') or ''
    album = entry.get('album_name') or ''
    created_at = entry.get('created_at') or entry.get('completed_at') or ''
    source = entry.get('download_source') or ''
    return {
        'task_id': f'history-{history_id}' if history_id else f'history-{title}-{created_at}',
        'title': title,
        'artist': artist,
        'album': album,
        'artwork': entry.get('thumb_url') or '',
        'status': 'completed',
        'progress': 100,
        'error': None,
        'batch_id': '',
        'batch_name': source,
        'batch_source': source,
        'playlist_id': '',
        'track_index': 0,
        'batch_total': 1,
        'timestamp': _history_timestamp(created_at),
        'created_at': created_at,
        'priority': _STATUS_PRIORITY['completed'],
        'quality': entry.get('quality') or '',
        'file_path': entry.get('file_path') or '',
        'verification_status': entry.get('verification_status'),
        'is_persistent_history': True,
    }


def build_unified_downloads_response(limit: int, deps: StatusDeps) -> dict:
    """Flat list of every task across batches, sorted active-first then by recency.

    Powers /api/downloads/all for the centralized Downloads page.
    """
    items = []
    live_identities = set()
    with tasks_lock:
        for task_id, task in download_tasks.items():
            track_info = task.get('track_info') or {}
            batch_id = task.get('batch_id', '')
            batch = download_batches.get(batch_id, {})

            # Extract track metadata — handle all format variations
            title = ''
            artist = ''
            album = ''
            artwork = ''
            if isinstance(track_info, dict):
                title = track_info.get('title') or track_info.get('name') or track_info.get('track_name') or ''

                # Artist can be: string, list of strings, list of dicts with 'name'
                raw_artist = track_info.get('artist') or track_info.get('artist_name') or track_info.get('artists') or ''
                if isinstance(raw_artist, list):
                    parts = []
                    for a in raw_artist:
                        if isinstance(a, dict):
                            parts.append(a.get('name', ''))
                        else:
                            parts.append(str(a))
                    artist = ', '.join(p for p in parts if p)
                elif isinstance(raw_artist, dict):
                    artist = raw_artist.get('name', '')
                else:
                    artist = str(raw_artist) if raw_artist else ''

                # Album can be: string or dict with 'name'
                raw_album = track_info.get('album') or track_info.get('album_name') or ''
                if isinstance(raw_album, dict):
                    album = raw_album.get('name', '')
                else:
                    album = str(raw_album) if raw_album else ''

                artwork = track_info.get('artwork_url') or track_info.get('image_url') or track_info.get('album_art') or ''
                # Try album images
                if not artwork:
                    raw_alb = track_info.get('album')
                    if isinstance(raw_alb, dict):
                        images = raw_alb.get('images') or []
                        if images and isinstance(images, list) and len(images) > 0:
                            artwork = images[0].get('url', '') if isinstance(images[0], dict) else str(images[0])

            status = task.get('status', 'queued')
            live_identities.add(_download_identity(title, artist, album))
            # Determine download progress percentage
            progress = 0
            if status == 'completed':
                progress = 100
            elif status == 'post_processing':
                progress = 95
            elif status in ('downloading', 'searching'):
                # Check live transfer data for real progress
                task_filename = task.get('filename') or track_info.get('filename')
                task_username = task.get('username') or track_info.get('username')
                if task_filename and task_username:
                    lookup_key = deps.make_context_key(task_username, task_filename)
                    live_info = deps.get_cached_transfer_data().get(lookup_key)
                    if live_info:
                        progress = live_info.get('percentComplete', 0)

            items.append({
                'task_id': task_id,
                'title': title,
                'artist': artist,
                'album': album,
                'artwork': artwork,
                'status': status,
                'progress': progress,
                'error': task.get('error_message'),
                'verification_status': task.get('verification_status'),
                'retry_info': task.get('retry_info'),
                'retry_trigger': task.get('retry_trigger'),
                'batch_id': batch_id,
                'batch_name': batch.get('playlist_name') or batch.get('album_name') or '',
                'batch_source': batch.get('source_page') or batch.get('initiated_from') or '',
                # playlist_id is needed by per-row cancel (cancel_task_v2
                # takes playlist_id + track_index). Surfacing it here so
                # the frontend doesn't need a second lookup.
                'playlist_id': batch.get('playlist_id', ''),
                'track_index': task.get('track_index', 0),
                'batch_total': len(batch.get('queue', [])),
                'timestamp': task.get('status_change_time', 0),
                'priority': _STATUS_PRIORITY.get(status, 9),
                'is_persistent_history': False,
            })

    if deps.get_persistent_download_history is not None and len(items) < limit:
        history_limit = min(limit - len(items), _PERSISTENT_HISTORY_TAIL_LIMIT)
        try:
            history_entries = deps.get_persistent_download_history(history_limit) or []
        except Exception as exc:
            logger.debug("[Downloads] persistent history lookup failed: %s", exc)
            history_entries = []

        appended_history = 0
        for entry in history_entries:
            if len(items) >= limit or appended_history >= history_limit:
                break
            item = _build_history_download_item(entry)
            identity = _download_identity(item.get('title'), item.get('artist'), item.get('album'))
            if identity in live_identities:
                continue
            items.append(item)
            live_identities.add(identity)
            appended_history += 1

    # Sort: active first (by priority), then by timestamp desc within each group
    items.sort(key=lambda x: (x['priority'], -x['timestamp']))

    # Build batch summaries for the batch context panel
    batch_summaries = []
    with tasks_lock:
        for bid, batch in download_batches.items():
            queue = batch.get('queue', [])
            statuses = [download_tasks[tid]['status'] for tid in queue if tid in download_tasks]
            summary = {
                'batch_id': bid,
                'playlist_id': batch.get('playlist_id', ''),
                'batch_name': batch.get('playlist_name') or batch.get('album_name') or '',
                'source_page': batch.get('source_page') or batch.get('initiated_from') or '',
                'phase': batch.get('phase', 'unknown'),
                'total': len(queue),
                'completed': sum(1 for s in statuses if s in ('completed', 'skipped', 'already_owned')),
                'failed': sum(1 for s in statuses if s in ('failed', 'not_found', 'cancelled')),
                'active': sum(1 for s in statuses if s in ('downloading', 'searching', 'post_processing')),
                'queued': sum(1 for s in statuses if s in ('queued', 'pending')),
            }
            album_bundle = _build_album_bundle_status(batch)
            if album_bundle:
                summary['album_bundle'] = album_bundle
            batch_summaries.append(summary)

    return {
        'success': True,
        'downloads': items[:limit],
        'total': len(items),
        'batches': batch_summaries,
        'timestamp': time.time(),
    }


def _build_album_bundle_status(batch: dict) -> dict:
    """Return public batch-level status for torrent/Usenet album-bundle work."""
    state = batch.get('album_bundle_state')
    if not state:
        return {}

    status = {
        'state': state,
        'source': batch.get('album_bundle_source'),
        'release': batch.get('album_bundle_release'),
        'progress': batch.get('album_bundle_progress'),
        'progress_percent': _album_bundle_progress_percent(
            batch.get('album_bundle_progress')
        ),
        'speed': batch.get('album_bundle_speed'),
        'downloaded': batch.get('album_bundle_downloaded'),
        'size': batch.get('album_bundle_size'),
        'seeders': batch.get('album_bundle_seeders'),
        'grabs': batch.get('album_bundle_grabs'),
        'count': batch.get('album_bundle_count'),
    }
    return {key: value for key, value in status.items() if value is not None}


def _album_bundle_progress_percent(value: Any) -> int:
    try:
        progress = float(value)
    except (TypeError, ValueError):
        return 0

    if progress <= 1:
        progress *= 100
    return max(0, min(100, int(round(progress))))
