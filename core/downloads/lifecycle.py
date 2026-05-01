"""Batch lifecycle: start workers, on-completion accounting, completion check.

Three deeply-coupled functions:

- `start_next_batch_of_downloads(batch_id, deps)` — launches workers up to
  the batch's max_concurrent. Skips cancelled tasks, sets searching status,
  submits to the executor, decrement-safe on submit failures (no ghost
  workers).

- `on_download_completed(batch_id, task_id, success, deps)` — called when
  a single track download finishes (good or bad). Tracks failed/cancelled
  tracks for wishlist replay, decrements active count, then runs the full
  batch-completion check — which is its own beast: stuck-task detection
  (searching > 10min → not_found, post_processing > 5min → completed),
  M3U regeneration, repair worker hand-off, album consistency pass,
  wishlist failed-tracks processing.

- `check_batch_completion_v2(batch_id, deps)` — same completion check
  but called from the V2 atomic cancel path (which bypasses
  on_download_completed). Duplicate logic preserved verbatim.

Lifted verbatim from web_server.py. Dependencies injected via
`LifecycleDeps` since the surface is wide (15+ callbacks/refs).
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core.downloads.history import record_sync_history_completion
from core.runtime_state import (
    add_activity_item,
    download_batches,
    download_tasks,
    tasks_lock,
)

logger = logging.getLogger(__name__)


@dataclass
class LifecycleDeps:
    """Bundle of cross-cutting deps the batch lifecycle needs."""
    config_manager: Any
    automation_engine: Any
    download_monitor: Any
    repair_worker: Any
    mb_worker: Any
    is_shutting_down: Callable[[], bool]
    get_batch_lock: Callable[[str], Any]                    # (batch_id) -> threading.Lock
    submit_download_track_worker: Callable                  # (task_id, batch_id) -> None (submits to executor)
    submit_failed_to_wishlist: Callable[[str], None]                  # async — submits to executor
    submit_failed_to_wishlist_with_auto_completion: Callable[[str], None]  # async — submits to executor
    process_failed_to_wishlist: Callable[[str], None]                 # sync — direct call (used by v2 path)
    process_failed_to_wishlist_with_auto_completion: Callable[[str], None]  # sync — direct call (used by v2 path)
    get_track_artist_name: Callable
    check_and_remove_from_wishlist: Callable
    regenerate_batch_m3u: Callable
    youtube_playlist_states: dict
    tidal_discovery_states: dict
    deezer_discovery_states: dict
    spotify_public_discovery_states: dict
    ensure_wishlist_track_format: Callable | None = None
    ensure_spotify_track_format: Callable | None = None

    def __post_init__(self) -> None:
        if self.ensure_wishlist_track_format is None:
            self.ensure_wishlist_track_format = self.ensure_spotify_track_format
        if self.ensure_spotify_track_format is None:
            self.ensure_spotify_track_format = self.ensure_wishlist_track_format

        if self.ensure_wishlist_track_format is None:
            raise ValueError("LifecycleDeps requires a wishlist track format helper")


# ---------------------------------------------------------------------------
# start_next_batch_of_downloads
# ---------------------------------------------------------------------------

def start_next_batch_of_downloads(batch_id: str, deps: LifecycleDeps) -> None:
    """Start the next batch of downloads up to the concurrent limit (like GUI)."""
    # ENHANCED: Use batch-specific lock to prevent race conditions when multiple threads
    # try to start workers for the same batch concurrently
    batch_lock = deps.get_batch_lock(batch_id)

    with batch_lock:
        # Prevent starting new tasks if shutting down
        if deps.is_shutting_down():
            logger.info(f"[Batch Manager] Server shutting down - skipping new tasks for batch {batch_id}")
            return

        with tasks_lock:
            if batch_id not in download_batches:
                return

            batch = download_batches[batch_id]
            max_concurrent = batch['max_concurrent']
            queue = batch['queue']
            queue_index = batch['queue_index']
            active_count = batch['active_count']

            logger.info(f"[Batch Lock] Starting workers for {batch_id}: active={active_count}, max={max_concurrent}, queue_pos={queue_index}/{len(queue)}")

            # Start downloads up to the concurrent limit
            while active_count < max_concurrent and queue_index < len(queue):
                task_id = queue[queue_index]

                # CRITICAL V2 FIX: Skip cancelled tasks instead of trying to restart them
                if task_id in download_tasks:
                    current_status = download_tasks[task_id]['status']
                    if current_status == 'cancelled':
                        logger.warning(f"[Batch Lock] Skipping cancelled task {task_id} (queue position {queue_index + 1})")
                        download_batches[batch_id]['queue_index'] += 1
                        queue_index += 1
                        continue  # Skip to next task without consuming worker slot

                    # IMPORTANT: Set status to 'searching' BEFORE starting worker (like GUI)
                    # Must be done INSIDE the lock to prevent race conditions with status polling
                    download_tasks[task_id]['status'] = 'searching'
                    download_tasks[task_id]['status_change_time'] = time.time()
                    logger.info(f"[Batch Manager] Set task {task_id} status to 'searching'")
                else:
                    logger.warning(f"[Batch Lock] Task {task_id} not found in download_tasks - skipping")
                    download_batches[batch_id]['queue_index'] += 1
                    queue_index += 1
                    continue

                # CRITICAL FIX: Submit to executor BEFORE incrementing counters to prevent ghost workers
                try:
                    # Submit to executor first - this can fail
                    deps.submit_download_track_worker(task_id, batch_id)

                    # Only increment counters AFTER successful submit
                    download_batches[batch_id]['active_count'] += 1
                    download_batches[batch_id]['queue_index'] += 1

                    logger.info(f"[Batch Lock] Started download {queue_index + 1}/{len(queue)} - Active: {active_count + 1}/{max_concurrent}")

                    # Update local counters for next iteration
                    active_count += 1
                    queue_index += 1

                except Exception as submit_error:
                    logger.error(f"[Batch Lock] CRITICAL: Failed to submit task {task_id} to executor: {submit_error}")
                    logger.info("[Batch Lock] Worker slot NOT consumed - preventing ghost worker")

                    # Reset task status since worker never started
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        logger.error(f"[Batch Lock] Set task {task_id} status to 'failed' due to submit failure")

                    # Don't increment counters - no worker was actually started
                    # This prevents the "ghost worker" issue where active_count is incremented but no actual worker runs
                    break  # Stop trying to start more workers if executor is failing

            logger.info(f"[Batch Lock] Finished starting workers for {batch_id}: final_active={download_batches[batch_id]['active_count']}, max={max_concurrent}")


# ---------------------------------------------------------------------------
# on_download_completed
# ---------------------------------------------------------------------------

def on_download_completed(batch_id: str, task_id: str, success: bool, deps: LifecycleDeps) -> None:
    """Called when a download completes to start the next one in queue."""
    with tasks_lock:
        if batch_id not in download_batches:
            logger.warning(f"[Batch Manager] Batch {batch_id} not found for completed task {task_id}")
            return

        # Guard against double-calling: track which tasks have already been completed
        # This prevents active_count from being decremented multiple times for the same task
        # (e.g. monitor detects completion AND post-processing calls this again)
        # NOTE: On duplicate calls, we skip decrement/tracking but STILL check batch completion.
        # This is critical because the first call may see the task in 'post_processing' (not finished),
        # and the second call (from post-processing worker) arrives after the task is truly 'completed'.
        # Without the fallthrough, batch_complete would never be emitted.
        completed_tasks = download_batches[batch_id].setdefault('_completed_task_ids', set())
        _is_duplicate_completion = task_id in completed_tasks
        if _is_duplicate_completion:
            logger.info(f"[Batch Manager] Task {task_id} already completed — skipping decrement, still checking batch completion")
            # Set terminal status so the monitor loop stops re-processing this task
            if task_id in download_tasks and download_tasks[task_id].get('status') in ('downloading', 'queued'):
                download_tasks[task_id]['status'] = 'completed'
            # Fall through to batch completion check below (don't return)
        else:
            completed_tasks.add(task_id)

        if not _is_duplicate_completion:
            # Track failed/cancelled tasks in batch state (replicating sync.py)
            if not success and task_id in download_tasks:
                task = download_tasks[task_id]
                task_status = task.get('status', 'unknown')

                # Build track_info structure matching sync.py's permanently_failed_tracks format
                original_track_info = task.get('track_info', {})

                # Ensure wishlist track has proper structure for wishlist service
                wishlist_track_data = deps.ensure_wishlist_track_format(original_track_info)

                track_info = {
                    'download_index': task.get('track_index', 0),
                    'table_index': task.get('track_index', 0),
                    'track_name': original_track_info.get('name', 'Unknown Track'),
                    'artist_name': deps.get_track_artist_name(original_track_info),
                    'retry_count': task.get('retry_count', 0),
                    'track_data': wishlist_track_data,
                    'spotify_track': wishlist_track_data,  # Backward-compatible alias for older callers
                    'failure_reason': 'Download cancelled' if task_status == 'cancelled' else ('No matching track found' if task_status == 'not_found' else 'Download failed'),
                    'candidates': task.get('cached_candidates', []),  # Include search results if available
                }

                if task_status == 'cancelled':
                    download_batches[batch_id]['cancelled_tracks'].add(task.get('track_index', 0))
                    logger.warning(f"[Batch Manager] Added cancelled track to batch tracking: {track_info['track_name']}")
                    add_activity_item("", "Download Cancelled", f"'{track_info['track_name']}'", "Now")
                elif task_status in ('failed', 'not_found'):
                    download_batches[batch_id]['permanently_failed_tracks'].append(track_info)
                    if task_status == 'not_found':
                        logger.info(f"[Batch Manager] Added not-found track to batch tracking: {track_info['track_name']}")
                        add_activity_item("", "Not Found", f"'{track_info['track_name']}'", "Now")
                    else:
                        logger.error(f"[Batch Manager] Added failed track to batch tracking: {track_info['track_name']}")
                        add_activity_item("", "Download Failed", f"'{track_info['track_name']}'", "Now")

                    try:
                        if deps.automation_engine:
                            deps.automation_engine.emit('download_failed', {
                                'artist': track_info.get('artist_name', ''),
                                'title': track_info.get('track_name', ''),
                                'reason': track_info.get('failure_reason', 'Unknown'),
                            })
                    except Exception:
                        pass

            # WISHLIST REMOVAL: Handle successful downloads for wishlist removal
            if success and task_id in download_tasks:
                try:
                    task = download_tasks[task_id]
                    track_info = task.get('track_info', {})
                    logger.info(f"[Batch Manager] Successful download - checking wishlist removal for task {task_id}")

                    # Add activity for successful download
                    track_name = track_info.get('name', 'Unknown Track')

                    # Safely extract artist name (handle both list and string formats)
                    artists = track_info.get('artists', [])
                    if isinstance(artists, list) and len(artists) > 0:
                        first_artist = artists[0]
                        artist_name = first_artist.get('name', 'Unknown Artist') if isinstance(first_artist, dict) else str(first_artist)
                    elif isinstance(artists, str):
                        artist_name = artists
                    else:
                        artist_name = 'Unknown Artist'

                    add_activity_item("", "Download Complete", f"'{track_name}' by {artist_name}", "Now")

                    # Try to remove from wishlist using track info
                    if track_info:
                        # Create a context-like structure for the wishlist removal function
                        context = {
                            'track_info': track_info,
                            'original_search_result': track_info,  # fallback
                        }
                        deps.check_and_remove_from_wishlist(context)
                except Exception as wishlist_error:
                    logger.error(f"[Batch Manager] Error checking wishlist removal for successful download: {wishlist_error}")

            # Decrement active count
            old_active = download_batches[batch_id]['active_count']
            download_batches[batch_id]['active_count'] -= 1
            new_active = download_batches[batch_id]['active_count']

            logger.error(f"[Batch Manager] Task {task_id} completed ({'success' if success else 'failed/cancelled'}). Active workers: {old_active} → {new_active}/{download_batches[batch_id]['max_concurrent']}")

        # ENHANCED: Always check batch completion after any task completes (including duplicate calls)
        # This ensures completion is detected even when mixing normal downloads with cancelled tasks
        logger.info(f"[Batch Manager] Checking batch completion after task {task_id} completed")

        # FIXED: Check if batch is truly complete (all tasks finished, not just workers freed)
        batch = download_batches[batch_id]
        all_tasks_started = batch['queue_index'] >= len(batch['queue'])
        no_active_workers = batch['active_count'] == 0

        # Count actually finished tasks (completed, failed, or cancelled)
        # CRITICAL: Don't include 'post_processing' as finished - it's still in progress (unless stuck)!
        # CRITICAL: Don't include 'searching' as finished - task is being retried (unless stuck)!
        finished_count = 0
        retrying_count = 0
        queue = batch.get('queue', [])
        current_time = time.time()
        for queue_task_id in queue:
            if queue_task_id in download_tasks:
                task = download_tasks[queue_task_id]
                task_status = task['status']

                # STUCK DETECTION: Force fail tasks that have been in transitional states too long
                if task_status == 'searching':
                    task_age = current_time - task.get('status_change_time', current_time)
                    if task_age > 600:  # 10 minutes
                        logger.info(f"⏰ [Stuck Detection] Task {queue_task_id} stuck in searching for {task_age:.0f}s - forcing not_found")
                        task['status'] = 'not_found'
                        task['error_message'] = f'Search stuck for {int(task_age // 60)} minutes with no results — timed out'
                        finished_count += 1
                    else:
                        retrying_count += 1
                elif task_status == 'post_processing':
                    task_age = current_time - task.get('status_change_time', current_time)
                    if task_age > 300:  # 5 minutes (post-processing should be fast)
                        logger.info(f"⏰ [Stuck Detection] Task {queue_task_id} stuck in post_processing for {task_age:.0f}s - forcing completion")
                        task['status'] = 'completed'  # Assume it worked if file verification is taking too long
                        finished_count += 1
                    else:
                        retrying_count += 1
                elif task_status in ['completed', 'failed', 'cancelled', 'not_found']:
                    finished_count += 1
            else:
                # Task ID in queue but not in download_tasks - treat as completed to prevent blocking
                logger.warning(f"[Orphaned Task] Task {queue_task_id} in queue but not in download_tasks - counting as finished")
                finished_count += 1

        all_tasks_truly_finished = finished_count >= len(queue)
        has_retrying_tasks = retrying_count > 0

        if all_tasks_started and no_active_workers and all_tasks_truly_finished and not has_retrying_tasks:
            logger.error(f"[Batch Manager] Batch {batch_id} truly complete - all {finished_count}/{len(queue)} tasks finished - processing failed tracks to wishlist")
        elif all_tasks_started and no_active_workers and has_retrying_tasks:
            logger.warning(f"[Batch Manager] Batch {batch_id}: all workers free but {retrying_count} tasks retrying - continuing monitoring")
        elif all_tasks_started and no_active_workers:
            # This used to incorrectly mark batch as complete!
            logger.info(f"[Batch Manager] Batch {batch_id}: all workers free but only {finished_count}/{len(queue)} tasks finished - continuing monitoring")

        if all_tasks_started and no_active_workers and all_tasks_truly_finished and not has_retrying_tasks:

            # Check if this is an auto-initiated batch
            is_auto_batch = batch.get('auto_initiated', False)

            # FIXED: Ensure batch is not already marked as complete to prevent duplicate processing
            if batch.get('phase') != 'complete':
                # Mark batch as complete and set completion timestamp for auto-cleanup
                batch['phase'] = 'complete'
                batch['completion_time'] = time.time()  # Track when batch completed

                # Record sync history completion
                from database.music_database import MusicDatabase
                record_sync_history_completion(MusicDatabase(), batch_id, batch)

                # Add activity for batch completion
                playlist_name = batch.get('playlist_name', 'Unknown Playlist')
                failed_count = len(batch.get('permanently_failed_tracks', []))
                successful_downloads = finished_count - failed_count
                add_activity_item("", "Download Batch Complete", f"'{playlist_name}' - {successful_downloads} tracks downloaded", "Now")

                # Emit batch_complete event for automation engine (only if something downloaded)
                if successful_downloads > 0:
                    try:
                        if deps.automation_engine:
                            deps.automation_engine.emit('batch_complete', {
                                'playlist_name': playlist_name,
                                'total_tracks': str(len(queue)),
                                'completed_tracks': str(successful_downloads),
                                'failed_tracks': str(failed_count),
                            })
                    except Exception:
                        pass

                # Update YouTube playlist phase to 'download_complete' if this is a YouTube playlist
                playlist_id = batch.get('playlist_id')
                if playlist_id and playlist_id.startswith('youtube_'):
                    url_hash = playlist_id.replace('youtube_', '')
                    if url_hash in deps.youtube_playlist_states:
                        deps.youtube_playlist_states[url_hash]['phase'] = 'download_complete'
                        logger.info(f"Updated YouTube playlist {url_hash} to download_complete phase")

                # Update Tidal playlist phase to 'download_complete' if this is a Tidal playlist
                if playlist_id and playlist_id.startswith('tidal_'):
                    tidal_playlist_id = playlist_id.replace('tidal_', '')
                    if tidal_playlist_id in deps.tidal_discovery_states:
                        deps.tidal_discovery_states[tidal_playlist_id]['phase'] = 'download_complete'
                        logger.info(f"Updated Tidal playlist {tidal_playlist_id} to download_complete phase")

                # Update Deezer playlist phase to 'download_complete' if this is a Deezer playlist
                if playlist_id and playlist_id.startswith('deezer_'):
                    deezer_playlist_id = playlist_id.replace('deezer_', '')
                    if deezer_playlist_id in deps.deezer_discovery_states:
                        deps.deezer_discovery_states[deezer_playlist_id]['phase'] = 'download_complete'
                        logger.info(f"Updated Deezer playlist {deezer_playlist_id} to download_complete phase")

                # Update Spotify Public playlist phase to 'download_complete' if this is a Spotify Public playlist
                if playlist_id and playlist_id.startswith('spotify_public_'):
                    spotify_public_url_hash = playlist_id.replace('spotify_public_', '')
                    if spotify_public_url_hash in deps.spotify_public_discovery_states:
                        deps.spotify_public_discovery_states[spotify_public_url_hash]['phase'] = 'download_complete'
                        logger.info(f"Updated Spotify Public playlist {spotify_public_url_hash} to download_complete phase")

                logger.info(f"[Batch Manager] Batch {batch_id} complete - stopping monitor")
                deps.download_monitor.stop_monitoring(batch_id)

                # M3U REGENERATION: Regenerate M3U with real library paths now that
                # all post-processing (tagging, moving, DB writes) is complete.
                # The frontend M3U save may fire too early — this ensures paths resolve.
                if deps.config_manager.get('m3u_export.enabled', False):
                    try:
                        m3u_tracks = []
                        for tid in queue:
                            if tid in download_tasks and download_tasks[tid].get('status') == 'completed':
                                ti = download_tasks[tid].get('track_info', {})
                                artists = ti.get('artists', [])
                                artist_str = artists[0] if isinstance(artists, list) and artists else ''
                                if isinstance(artist_str, dict):
                                    artist_str = artist_str.get('name', '')
                                m3u_tracks.append({
                                    'name': ti.get('name', ''),
                                    'artist': artist_str,
                                    'duration_ms': ti.get('duration_ms', 0),
                                })
                        if m3u_tracks:
                            deps.regenerate_batch_m3u(batch, m3u_tracks)
                    except Exception as m3u_err:
                        logger.error(f"[M3U] Error regenerating M3U on batch complete: {m3u_err}")

                # REPAIR: Scan all album folders from this batch for track number issues
                if deps.repair_worker:
                    deps.repair_worker.process_batch(batch_id)

                # ALBUM CONSISTENCY: Picard-style post-batch pass — pick ONE MusicBrainz
                # release and overwrite album-level tags on all files to guarantee consistency.
                # This is the safety net: even if per-track MB lookups drifted (different cache
                # keys, API hiccups), this pass forces every file to share the same release MBID,
                # album artist ID, release group ID, etc. — preventing Navidrome album splits.
                _cons_files = batch.get('_consistency_files', [])
                if batch.get('is_album_download') and _cons_files and len(_cons_files) >= 2:
                    _cons_album = batch.get('album_context', {})
                    _cons_artist = batch.get('artist_context', {})
                    _cons_album_name = _cons_album.get('name', '') if isinstance(_cons_album, dict) else ''
                    _cons_artist_name = _cons_artist.get('name', '') if isinstance(_cons_artist, dict) else ''
                    if _cons_album_name and _cons_artist_name:
                        try:
                            _cons_mb_svc = deps.mb_worker.mb_service if deps.mb_worker else None
                            if _cons_mb_svc and deps.config_manager.get('musicbrainz.embed_tags', True):
                                from core.album_consistency import run_album_consistency
                                from core.metadata.common import get_file_lock
                                _cons_result = run_album_consistency(
                                    file_infos=_cons_files,
                                    album_name=_cons_album_name,
                                    artist_name=_cons_artist_name,
                                    mb_service=_cons_mb_svc,
                                    total_discs=_cons_album.get('total_discs', 1),
                                    file_lock_fn=get_file_lock,
                                )
                                if _cons_result.get('success'):
                                    logger.info(f"[Album Consistency] {_cons_result['tags_written']}/{_cons_result['total_files']} files "
                                          f"harmonized to release {_cons_result.get('release_mbid', '')[:8]}...")
                                elif _cons_result.get('error'):
                                    logger.error(f"[Album Consistency] Skipped: {_cons_result['error']}")
                        except Exception as cons_err:
                            logger.error(f"[Album Consistency] Failed (non-fatal): {cons_err}")

                # Mark that wishlist processing is starting (prevents premature cleanup)
                batch['wishlist_processing_started'] = True

                # Process wishlist outside of the lock to prevent threading issues
                if is_auto_batch:
                    # For auto-initiated batches, handle completion and schedule next cycle
                    deps.submit_failed_to_wishlist_with_auto_completion(batch_id)
                else:
                    # For manual batches, use standard wishlist processing
                    deps.submit_failed_to_wishlist(batch_id)
            else:
                logger.warning(f"[Batch Manager] Batch {batch_id} already marked complete - skipping duplicate processing")

            return  # Don't start next batch if we're done

    # Start next downloads in queue
    logger.info(f"[Batch Manager] Starting next batch for {batch_id}")
    start_next_batch_of_downloads(batch_id, deps)


# ---------------------------------------------------------------------------
# check_batch_completion_v2
# ---------------------------------------------------------------------------

def check_batch_completion_v2(batch_id: str, deps: LifecycleDeps) -> Optional[bool]:
    """V2 SYSTEM: Check if batch is complete after worker slot changes.

    This is needed because V2 atomic cancel bypasses on_download_completed,
    so we need to manually check for batch completion.
    """
    try:
        with tasks_lock:
            if batch_id not in download_batches:
                logger.warning(f"[Completion Check V2] Batch {batch_id} not found")
                return

            batch = download_batches[batch_id]
            all_tasks_started = batch['queue_index'] >= len(batch['queue'])
            no_active_workers = batch['active_count'] == 0

            # Count actually finished tasks (completed, failed, or cancelled)
            finished_count = 0
            retrying_count = 0
            queue = batch.get('queue', [])
            current_time = time.time()

            for task_id in queue:
                if task_id in download_tasks:
                    task = download_tasks[task_id]
                    task_status = task['status']

                    # STUCK DETECTION: Force fail tasks that have been in transitional states too long
                    if task_status == 'searching':
                        task_age = current_time - task.get('status_change_time', current_time)
                        if task_age > 600:  # 10 minutes
                            logger.info(f"⏰ [Stuck Detection V2] Task {task_id} stuck in searching for {task_age:.0f}s - forcing not_found")
                            task['status'] = 'not_found'
                            task['error_message'] = f'Search stuck for {int(task_age // 60)} minutes with no results — timed out'
                            finished_count += 1
                        else:
                            retrying_count += 1
                    elif task_status == 'post_processing':
                        task_age = current_time - task.get('status_change_time', current_time)
                        if task_age > 300:  # 5 minutes (post-processing should be fast)
                            logger.info(f"⏰ [Stuck Detection V2] Task {task_id} stuck in post_processing for {task_age:.0f}s - forcing completion")
                            task['status'] = 'completed'  # Assume it worked if file verification is taking too long
                            finished_count += 1
                        else:
                            retrying_count += 1
                    elif task_status in ['completed', 'failed', 'cancelled', 'not_found']:
                        finished_count += 1
                else:
                    # Task ID in queue but not in download_tasks - treat as completed to prevent blocking
                    logger.warning(f"[Orphaned Task V2] Task {task_id} in queue but not in download_tasks - counting as finished")
                    finished_count += 1

            all_tasks_truly_finished = finished_count >= len(queue)
            has_retrying_tasks = retrying_count > 0

            logger.warning(f"[Completion Check V2] Batch {batch_id}: tasks_started={all_tasks_started}, workers={no_active_workers}, finished={finished_count}/{len(queue)}, retrying={retrying_count}")

            is_auto_batch = False
            if all_tasks_started and no_active_workers and all_tasks_truly_finished and not has_retrying_tasks:
                # FIXED: Ensure batch is not already marked as complete to prevent duplicate processing
                if batch.get('phase') != 'complete':
                    logger.info(f"[Completion Check V2] Batch {batch_id} is complete - marking as finished")

                    # Check if this is an auto-initiated batch
                    is_auto_batch = batch.get('auto_initiated', False)

                    # Mark batch as complete and set completion timestamp for auto-cleanup
                    batch['phase'] = 'complete'
                    batch['completion_time'] = time.time()  # Track when batch completed

                    # Add activity for batch completion
                    playlist_name = batch.get('playlist_name', 'Unknown Playlist')
                    failed_count = len(batch.get('permanently_failed_tracks', []))
                    successful_downloads = finished_count - failed_count
                    add_activity_item("", "Download Batch Complete", f"'{playlist_name}' - {successful_downloads} tracks downloaded", "Now")

                    # Emit batch_complete event for automation engine (only if something downloaded)
                    if successful_downloads > 0:
                        try:
                            if deps.automation_engine:
                                deps.automation_engine.emit('batch_complete', {
                                    'playlist_name': playlist_name,
                                    'total_tracks': str(len(queue)),
                                    'completed_tracks': str(successful_downloads),
                                    'failed_tracks': str(failed_count),
                                })
                        except Exception:
                            pass
                else:
                    logger.warning(f"[Completion Check V2] Batch {batch_id} already marked complete - skipping duplicate processing")
                    return True  # Already complete

                # Update YouTube playlist phase to 'download_complete' if this is a YouTube playlist
                playlist_id = batch.get('playlist_id')
                if playlist_id and playlist_id.startswith('youtube_'):
                    url_hash = playlist_id.replace('youtube_', '')
                    if url_hash in deps.youtube_playlist_states:
                        deps.youtube_playlist_states[url_hash]['phase'] = 'download_complete'
                        logger.info(f"[Completion Check V2] Updated YouTube playlist {url_hash} to download_complete phase")

                # Update Tidal playlist phase to 'download_complete' if this is a Tidal playlist
                if playlist_id and playlist_id.startswith('tidal_'):
                    tidal_playlist_id = playlist_id.replace('tidal_', '')
                    if tidal_playlist_id in deps.tidal_discovery_states:
                        deps.tidal_discovery_states[tidal_playlist_id]['phase'] = 'download_complete'
                        logger.info(f"[Completion Check V2] Updated Tidal playlist {tidal_playlist_id} to download_complete phase")

                # Update Deezer playlist phase to 'download_complete' if this is a Deezer playlist
                if playlist_id and playlist_id.startswith('deezer_'):
                    deezer_playlist_id = playlist_id.replace('deezer_', '')
                    if deezer_playlist_id in deps.deezer_discovery_states:
                        deps.deezer_discovery_states[deezer_playlist_id]['phase'] = 'download_complete'
                        logger.info(f"[Completion Check V2] Updated Deezer playlist {deezer_playlist_id} to download_complete phase")

                # Update Spotify Public playlist phase to 'download_complete' if this is a Spotify Public playlist
                if playlist_id and playlist_id.startswith('spotify_public_'):
                    spotify_public_url_hash = playlist_id.replace('spotify_public_', '')
                    if spotify_public_url_hash in deps.spotify_public_discovery_states:
                        deps.spotify_public_discovery_states[spotify_public_url_hash]['phase'] = 'download_complete'
                        logger.info(f"[Completion Check V2] Updated Spotify Public playlist {spotify_public_url_hash} to download_complete phase")

                logger.info(f"[Completion Check V2] Batch {batch_id} complete - stopping monitor")
                deps.download_monitor.stop_monitoring(batch_id)

                # REPAIR: Scan all album folders from this batch for track number issues
                if deps.repair_worker:
                    deps.repair_worker.process_batch(batch_id)

                # ALBUM CONSISTENCY: Same Picard-style pass as the primary completion path
                _cons_files = batch.get('_consistency_files', [])
                if batch.get('is_album_download') and _cons_files and len(_cons_files) >= 2:
                    _cons_album = batch.get('album_context', {})
                    _cons_artist = batch.get('artist_context', {})
                    _cons_album_name = _cons_album.get('name', '') if isinstance(_cons_album, dict) else ''
                    _cons_artist_name = _cons_artist.get('name', '') if isinstance(_cons_artist, dict) else ''
                    if _cons_album_name and _cons_artist_name:
                        try:
                            _cons_mb_svc = deps.mb_worker.mb_service if deps.mb_worker else None
                            if _cons_mb_svc and deps.config_manager.get('musicbrainz.embed_tags', True):
                                from core.album_consistency import run_album_consistency
                                from core.metadata.common import get_file_lock
                                _cons_result = run_album_consistency(
                                    file_infos=_cons_files,
                                    album_name=_cons_album_name,
                                    artist_name=_cons_artist_name,
                                    mb_service=_cons_mb_svc,
                                    total_discs=_cons_album.get('total_discs', 1),
                                    file_lock_fn=get_file_lock,
                                )
                                if _cons_result.get('success'):
                                    logger.info(f"[Album Consistency V2] {_cons_result['tags_written']}/{_cons_result['total_files']} files "
                                          f"harmonized to release {_cons_result.get('release_mbid', '')[:8]}...")
                                elif _cons_result.get('error'):
                                    logger.error(f"[Album Consistency V2] Skipped: {_cons_result['error']}")
                        except Exception as cons_err:
                            logger.error(f"[Album Consistency V2] Failed (non-fatal): {cons_err}")

        # Process wishlist outside of the lock to prevent threading issues
        if all_tasks_started and no_active_workers and all_tasks_truly_finished and not has_retrying_tasks:
            # Call wishlist processing outside the lock — DIRECT (synchronous) call
            # to match original v2 behavior. The non-v2 path (on_download_completed)
            # uses the async submit_* deps; v2 calls directly because v2 itself runs
            # from a context where blocking is acceptable.
            if is_auto_batch:
                logger.info("[Completion Check V2] Processing auto-initiated batch completion")
                deps.process_failed_to_wishlist_with_auto_completion(batch_id)
            else:
                logger.info("[Completion Check V2] Processing regular batch completion")
                deps.process_failed_to_wishlist(batch_id)

            return True  # Batch was completed
        else:
            logger.warning(f"[Completion Check V2] Batch {batch_id} not yet complete: finished={finished_count}/{len(queue)}, retrying={retrying_count}, workers={batch['active_count']}")
            return False  # Batch still in progress

    except Exception as e:
        logger.error(f"[Completion Check V2] Error checking batch completion: {e}")
        traceback.print_exc()
        return False
