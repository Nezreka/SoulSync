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

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core.runtime_state import (
    download_batches,
    download_tasks,
    tasks_lock,
)

logger = logging.getLogger(__name__)


@dataclass
class StatusDeps:
    """Cross-cutting deps the status helpers need."""
    config_manager: Any
    docker_resolve_path: Callable[[str], str]
    find_completed_file: Callable
    make_context_key: Callable[[str, str], str]
    submit_post_processing: Callable[[str, str], None]  # (task_id, batch_id) -> None
    get_cached_transfer_data: Callable[[], dict]


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

    if response_data["phase"] == 'analysis':
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
                'has_candidates': bool(task.get('cached_candidates')),  # Whether search found results (for clickable review)
            }
            _ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
            task_filename = task.get('filename') or _ti.get('filename')
            task_username = task.get('username') or _ti.get('username')
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
    """For /api/download_status/batch. Returns the full response dict (always 200)."""
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

        for batch_id, batch in target_batches.items():
            try:
                response["batches"][batch_id] = build_batch_status_data(
                    batch_id, batch, live_transfers_lookup, deps,
                )
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


def build_unified_downloads_response(limit: int, deps: StatusDeps) -> dict:
    """Flat list of every task across batches, sorted active-first then by recency.

    Powers /api/downloads/all for the centralized Downloads page.
    """
    items = []
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
            })

    # Sort: active first (by priority), then by timestamp desc within each group
    items.sort(key=lambda x: (x['priority'], -x['timestamp']))

    # Build batch summaries for the batch context panel
    batch_summaries = []
    with tasks_lock:
        for bid, batch in download_batches.items():
            queue = batch.get('queue', [])
            statuses = [download_tasks[tid]['status'] for tid in queue if tid in download_tasks]
            batch_summaries.append({
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
            })

    return {
        'success': True,
        'downloads': items[:limit],
        'total': len(items),
        'batches': batch_summaries,
        'timestamp': time.time(),
    }
