"""WebUIDownloadMonitor — lifted from web_server.py.

The class body is byte-identical to the original. Module-level globals
(injected via ``init()`` from web_server) include the worker / completion
helpers and orchestrator handles. ``IS_SHUTTING_DOWN`` is a module-level
flag mirrored from web_server's own flag in ``_shutdown_runtime_components``.
"""
import logging
import threading
import time

from config.settings import config_manager
from core.runtime_state import (
    download_batches,
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)
from utils.async_helpers import run_async

logger = logging.getLogger(__name__)

# Mirrored from web_server.IS_SHUTTING_DOWN via _shutdown_runtime_components.
IS_SHUTTING_DOWN = False

# Injected at runtime via init() — these are defined later in web_server.py
# than the class is instantiated, so we late-bind them.
_make_context_key = None
_on_download_completed = None
_download_track_worker = None
_run_post_processing_worker = None
_start_next_batch_of_downloads = None
_orphaned_download_keys = None
missing_download_executor = None
soulseek_client = None


def init(
    make_context_key,
    on_download_completed,
    download_track_worker,
    run_post_processing_worker,
    start_next_batch_of_downloads,
    orphaned_download_keys,
    missing_download_executor_obj,
    soulseek_client_obj,
):
    """Bind web_server-side helpers/globals so the class body can resolve them."""
    global _make_context_key, _on_download_completed, _download_track_worker
    global _run_post_processing_worker, _start_next_batch_of_downloads
    global _orphaned_download_keys, missing_download_executor, soulseek_client
    _make_context_key = make_context_key
    _on_download_completed = on_download_completed
    _download_track_worker = download_track_worker
    _run_post_processing_worker = run_post_processing_worker
    _start_next_batch_of_downloads = start_next_batch_of_downloads
    _orphaned_download_keys = orphaned_download_keys
    missing_download_executor = missing_download_executor_obj
    soulseek_client = soulseek_client_obj


class WebUIDownloadMonitor:
    """
    Background monitor for download progress and retry logic, matching GUI's SyncStatusProcessingWorker.
    Implements identical timeout detection and automatic retry functionality.
    """
    def __init__(self):
        self.monitoring = False
        self.monitor_thread = None
        self.monitored_batches = set()
        self._lock = threading.Lock()
        
    def start_monitoring(self, batch_id):
        """Start monitoring a download batch"""
        with self._lock:
            self.monitored_batches.add(batch_id)
            if not self.monitoring:
                self.monitoring = True
                self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
                self.monitor_thread.start()
                logger.info(f"Started download monitor for batch {batch_id}")
    
    def stop_monitoring(self, batch_id):
        """Stop monitoring a specific batch"""
        with self._lock:
            self.monitored_batches.discard(batch_id)
            if not self.monitored_batches:
                self.monitoring = False
                logger.debug("Stopped download monitor (no active batches)")

    def shutdown(self):
        """Stop the monitor loop and clear active batch tracking."""
        with self._lock:
            self.monitoring = False
            self.monitored_batches.clear()
            self.monitor_thread = None
        logger.info("Download monitor shutdown requested")
    
    def _monitor_loop(self):
        """Main monitoring loop - checks downloads every 1 second for responsive web UX"""
        while self.monitoring and self.monitored_batches:
            try:
                if globals().get('IS_SHUTTING_DOWN', False):
                    self.monitoring = False
                    break
                self._check_all_downloads()
                time.sleep(1)  # 1-second polling for fast web UI updates
            except Exception as e:
                # If we get shutdown errors, stop monitoring gracefully
                if "interpreter shutdown" in str(e) or "cannot schedule new futures" in str(e):
                    logger.info("Monitor detected shutdown, stopping gracefully")
                    self.monitoring = False
                    break
                logger.error(f"Download monitor error: {e}")
                
        logger.info("Download monitor loop ended")
    
    def _check_all_downloads(self):
        """Check all active downloads for timeouts and failures"""
        current_time = time.time()

        # Get live transfer data from slskd
        live_transfers_lookup = self._get_live_transfers()

        # Track tasks with exhausted retries to handle after releasing lock
        exhausted_tasks = []  # List of (batch_id, task_id) tuples
        # Track completed downloads to handle after releasing lock (prevents deadlock)
        completed_tasks = []  # List of (batch_id, task_id) tuples
        # Track deferred operations (network calls, nested locks) to run after releasing tasks_lock
        deferred_ops = []

        with tasks_lock:
            # Check all monitored batches for timeouts and errors
            for batch_id in list(self.monitored_batches):
                if batch_id not in download_batches:
                    self.monitored_batches.discard(batch_id)
                    continue

                for task_id in download_batches[batch_id].get('queue', []):
                    task = download_tasks.get(task_id)
                    if not task or task['status'] not in ['downloading', 'queued']:
                        continue

                    # Check for timeouts and errors - retries handled directly in _should_retry_task
                    # If _should_retry_task returns True, it means retries were exhausted
                    retry_exhausted = self._should_retry_task(task_id, task, live_transfers_lookup, current_time, deferred_ops)
                    # Collect exhausted tasks to handle outside lock (prevents deadlock)
                    if retry_exhausted:
                        exhausted_tasks.append((batch_id, task_id))

                    # ENHANCED: Check for successful completions (especially YouTube)
                    task_filename = task.get('filename') or task.get('track_info', {}).get('filename')
                    task_username = task.get('username') or task.get('track_info', {}).get('username')

                    if task_filename and task_username:
                        lookup_key = _make_context_key(task_username, task_filename)
                        live_info = live_transfers_lookup.get(lookup_key)

                        if live_info:
                            state = live_info.get('state', '')
                            # Trigger post-processing if download is completed successfully
                            # slskd uses compound states like 'Completed, Succeeded' - use substring matching
                            # Must exclude error states first (matching _build_batch_status_data's prioritized checking)
                            has_error = ('Errored' in state or 'Failed' in state or 'Rejected' in state or 'TimedOut' in state)
                            has_completion = ('Completed' in state or 'Succeeded' in state)
                            # Verify bytes actually transferred before trusting state string.
                            # slskd can report "Completed" before the full file is flushed to disk,
                            # or on connection drops that leave a partial file.
                            if has_completion and not has_error:
                                expected_size = live_info.get('size', 0)
                                transferred = live_info.get('bytesTransferred', 0)
                                if expected_size > 0 and transferred < expected_size:
                                    if not task.get('_incomplete_warned'):
                                        logger.debug(f"Monitor: {task_id} state={state} but bytes incomplete ({transferred}/{expected_size}) — waiting")
                                        task['_incomplete_warned'] = True
                                    continue
                            if has_completion and not has_error and task['status'] == 'downloading':
                                task.pop('_incomplete_warned', None)
                                # CRITICAL FIX: Transition to 'post_processing' HERE so downloads
                                # don't depend on browser polling to trigger post-processing.
                                # Previously, post-processing was only submitted by _build_batch_status_data
                                # (called from browser-polled endpoints), meaning closing the browser
                                # left tasks stuck in 'downloading' forever.
                                task['status'] = 'post_processing'
                                task['status_change_time'] = current_time
                                logger.info(f"Monitor detected completed download for {task_id} ({state}) - submitting post-processing")
                                # Collect for handling outside the lock to prevent deadlock.
                                # _on_download_completed acquires tasks_lock which is non-reentrant.
                                completed_tasks.append((batch_id, task_id))

        # ---- All work below runs WITHOUT tasks_lock held ----
        if globals().get('IS_SHUTTING_DOWN', False) or not self.monitoring:
            return

        # Execute deferred operations from _should_retry_task (network calls, nested locks)
        for op in deferred_ops:
            try:
                if op[0] == 'cancel_download':
                    _, download_id, username = op
                    logger.debug(f"[Deferred] Cancelling download: {download_id} from {username}")
                    run_async(soulseek_client.cancel_download(download_id, username, remove=True))
                    logger.debug(f"[Deferred] Successfully cancelled download {download_id}")
                elif op[0] == 'cleanup_orphan':
                    _, context_key = op
                    with matched_context_lock:
                        matched_downloads_context.pop(context_key, None)
                    logger.debug(f"[Deferred] Cleaned up orphaned download context: {context_key}")
                elif op[0] == 'restart_worker':
                    _, task_id, batch_id = op
                    logger.debug(f"[Deferred] Restarting worker for task {task_id}")
                    missing_download_executor.submit(_download_track_worker, task_id, batch_id)
                    logger.debug(f"[Deferred] Successfully restarted worker for task {task_id}")
            except Exception as e:
                logger.error(f"[Deferred] Error executing deferred operation {op[0]}: {e}")

        # Handle completed downloads outside the lock to prevent deadlock
        # (_on_download_completed acquires tasks_lock internally)
        for batch_id, task_id in completed_tasks:
            try:
                # Submit post-processing worker (file move, tagging, AcoustID verification)
                # This makes batch downloads fully independent of browser polling.
                logger.info(f"[Monitor] Submitting post-processing worker for task {task_id}")
                missing_download_executor.submit(_run_post_processing_worker, task_id, batch_id)
                # Chain to next download in the batch queue
                _on_download_completed(batch_id, task_id, success=True)
            except Exception as e:
                logger.error(f"[Monitor] Error handling completed task {task_id}: {e}")
        # Handle exhausted retry tasks outside the lock to prevent deadlock
        for batch_id, task_id in exhausted_tasks:
            try:
                logger.info(f"[Monitor] Calling completion callback for exhausted task {task_id}")
                _on_download_completed(batch_id, task_id, success=False)
            except Exception as e:
                logger.error(f"[Monitor] Error handling exhausted task {task_id}: {e}")

        # ENHANCED: Add worker count validation to detect ghost workers
        self._validate_worker_counts()
    
    def _get_live_transfers(self):
        """Get current transfer status from slskd API and YouTube client"""
        try:
            # Check if we should stop due to shutdown
            if not self.monitoring:
                return {}

            live_transfers = {}

            # Only hit slskd API if soulseek is actually configured and active
            dl_mode = config_manager.get('download_source.mode', 'hybrid')
            hybrid_order = config_manager.get('download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'])
            soulseek_active = (dl_mode == 'soulseek' or
                              (dl_mode == 'hybrid' and 'soulseek' in hybrid_order))

            # Get Soulseek downloads from API
            transfers_data = None
            _slsk = soulseek_client.client('soulseek') if soulseek_client and hasattr(soulseek_client, 'client') else None
            if soulseek_active and _slsk and _slsk.base_url:
                transfers_data = run_async(soulseek_client._make_request('GET', 'transfers/downloads'))
            if transfers_data:
                for user_data in transfers_data:
                    username = user_data.get('username', 'Unknown')
                    if 'directories' in user_data:
                        for directory in user_data['directories']:
                            if 'files' in directory:
                                for file_info in directory['files']:
                                    key = _make_context_key(username, file_info.get('filename', ''))
                                    live_transfers[key] = file_info

            # Also get non-Soulseek downloads via the engine — single
            # cross-source aggregation, no per-source iteration.
            try:
                all_downloads = []
                if soulseek_client and hasattr(soulseek_client, 'engine'):
                    try:
                        all_downloads = run_async(soulseek_client.engine.get_all_downloads())
                    except Exception:
                        pass
                for download in all_downloads:
                        key = _make_context_key(download.username, download.filename)
                        # Convert DownloadStatus to transfer dict format for monitor compatibility
                        live_transfers[key] = {
                            'id': download.id,
                            'filename': download.filename,
                            'username': download.username,
                            'state': download.state,
                            'percentComplete': download.progress,
                            'size': download.size,
                            'bytesTransferred': download.transferred,
                            'averageSpeed': download.speed,
                        }
            except Exception as yt_error:
                logger.error(f"Monitor: Could not fetch streaming source downloads: {yt_error}")

            return live_transfers
        except Exception as e:
            # If we get shutdown-related errors, stop monitoring immediately
            if ("interpreter shutdown" in str(e) or 
                "cannot schedule new futures" in str(e) or
                "Event loop is closed" in str(e)):
                logger.info("Monitor detected shutdown, stopping immediately")
                self.monitoring = False
                return {}
            else:
                logger.error(f"Monitor: Could not fetch live transfers: {e}")
            return {}
    
    def _should_retry_task(self, task_id, task, live_transfers_lookup, current_time, deferred_ops):
        """
        Determine if a task should be retried due to timeout (matches GUI logic).

        IMPORTANT: This runs while tasks_lock is held. All network calls (slskd API)
        and nested lock acquisitions (matched_context_lock) are collected into deferred_ops
        to be executed AFTER releasing tasks_lock. This prevents deadlocks and long lock holds.

        Returns True if retries are exhausted and _on_download_completed should be called outside the lock.
        """
        ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
        task_filename = task.get('filename') or ti.get('filename')
        task_username = task.get('username') or ti.get('username')

        if not task_filename or not task_username:
            return False

        lookup_key = _make_context_key(task_username, task_filename)
        live_info = live_transfers_lookup.get(lookup_key)

        if not live_info:
            # Task not in live transfers but status is downloading/queued - likely stuck
            if current_time - task.get('status_change_time', current_time) > 90:
                retry_count = task.get('stuck_retry_count', 0)
                last_retry = task.get('last_retry_time', 0)

                if retry_count < 3 and (current_time - last_retry) > 30:
                    logger.warning(f"Task not in live transfers for >90s - retry {retry_count + 1}/3")
                    task['stuck_retry_count'] = retry_count + 1
                    task['last_retry_time'] = current_time

                    download_id = task.get('download_id')

                    # Defer slskd cancel to outside the lock
                    if task_username and download_id:
                        deferred_ops.append(('cancel_download', download_id, task_username))

                    # Mark current source as used (full filename to match worker format)
                    if task_username and task_filename:
                        used_sources = task.get('used_sources', set())
                        source_key = f"{task_username}_{task_filename}"
                        used_sources.add(source_key)
                        task['used_sources'] = used_sources
                        logger.warning(f"Marked missing-transfer source as used: {source_key}")

                    # Defer orphan cleanup
                    if task_username and task_filename:
                        _orphaned_download_keys.add(lookup_key)
                        deferred_ops.append(('cleanup_orphan', lookup_key))

                    # Clear download info and reset for retry
                    task.pop('download_id', None)
                    task.pop('username', None)
                    task.pop('filename', None)
                    task['status'] = 'searching'
                    task.pop('queued_start_time', None)
                    task.pop('downloading_start_time', None)
                    task['status_change_time'] = current_time
                    logger.warning(f"Task {task.get('track_info', {}).get('name', 'Unknown')} reset for missing-transfer retry")

                    batch_id = task.get('batch_id')
                    if task_id and batch_id:
                        deferred_ops.append(('restart_worker', task_id, batch_id))
                    return False
                elif retry_count < 3:
                    return False
                else:
                    track_label = task.get('track_info', {}).get('name', 'Unknown')
                    tried_sources = task.get('used_sources', set())
                    sources_str = f' (tried {len(tried_sources)} source{"s" if len(tried_sources) != 1 else ""})' if tried_sources else ''
                    logger.error("Task failed after 3 retry attempts (not in live transfers)")
                    task['status'] = 'failed'
                    task['error_message'] = f'Download disappeared from transfer list 3 times for "{track_label}"{sources_str} — source may be unavailable'

                    batch_id = task.get('batch_id')
                    if batch_id:
                        return True
                    return False
            return False

        state_str = live_info.get('state', '')
        progress = live_info.get('percentComplete', 0)

        # IMMEDIATE ERROR RETRY: Check for errored/rejected/timed-out downloads first (no timeout needed)
        if 'Errored' in state_str or 'Failed' in state_str or 'Rejected' in state_str or 'TimedOut' in state_str:
            retry_count = task.get('error_retry_count', 0)
            last_retry = task.get('last_error_retry_time', 0)

            # Don't retry too frequently (wait at least 5 seconds between error retries)
            if retry_count < 3 and (current_time - last_retry) > 5:  # Max 3 error retry attempts
                logger.error(f"Task errored (state: {state_str}) - immediate retry {retry_count + 1}/3")
                task['error_retry_count'] = retry_count + 1
                task['last_error_retry_time'] = current_time

                _ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
                username = task.get('username') or _ti.get('username')
                filename = task.get('filename') or _ti.get('filename')
                download_id = task.get('download_id')

                # Defer slskd cancel to outside the lock
                if username and download_id:
                    deferred_ops.append(('cancel_download', download_id, username))

                # Mark current source as used to prevent retry loops
                # CRITICAL: Use full filename (not basename) to match worker's source_key format
                if username and filename:
                    used_sources = task.get('used_sources', set())
                    source_key = f"{username}_{filename}"
                    used_sources.add(source_key)
                    task['used_sources'] = used_sources
                    logger.error(f"Marked errored source as used: {source_key}")

                # Defer orphan cleanup to outside the lock (needs matched_context_lock)
                if username and filename:
                    old_context_key = _make_context_key(username, filename)
                    _orphaned_download_keys.add(old_context_key)
                    deferred_ops.append(('cleanup_orphan', old_context_key))

                # Clear download info since we cancelled it
                task.pop('download_id', None)
                task.pop('username', None)
                task.pop('filename', None)

                # Reset task state for immediate retry
                task['status'] = 'searching'
                task.pop('queued_start_time', None)
                task.pop('downloading_start_time', None)
                task['status_change_time'] = current_time
                logger.error(f"Task {task.get('track_info', {}).get('name', 'Unknown')} reset for error retry")

                # Defer worker restart to outside the lock
                batch_id = task.get('batch_id')
                if task_id and batch_id:
                    deferred_ops.append(('restart_worker', task_id, batch_id))
                return False
            elif retry_count < 3:
                # Wait a bit before next error retry
                return False
            else:
                # Too many error retries, mark as failed
                track_label = task.get('track_info', {}).get('name', 'Unknown')
                tried_sources = task.get('used_sources', set())
                sources_str = f' (tried {len(tried_sources)} source{"s" if len(tried_sources) != 1 else ""})' if tried_sources else ''
                logger.error("Task failed after 3 error retry attempts")
                task['status'] = 'failed'
                # Tidal-specific error: check if this was a quality issue.
                # task['username'] is popped on error-retry (line ~2866) so we can't rely on it;
                # used_sources keys are formatted as "{username}_{filename}", so startswith is exact.
                is_tidal = any(s.startswith('tidal_') for s in tried_sources)
                if is_tidal:
                    tidal_quality = config_manager.get('tidal_download.quality', 'lossless')
                    allow_fb = config_manager.get('tidal_download.allow_fallback', True)
                    if tidal_quality == 'hires' and not allow_fb:
                        task['error_message'] = (
                            f'Tidal download failed for "{track_label}" — HiRes quality is unavailable for this track '
                            f'on your account or in your region. Enable "Quality Fallback" in Tidal settings to fall back to Lossless.'
                        )
                    else:
                        task['error_message'] = (
                            f'Tidal download failed for "{track_label}"{sources_str} — '
                            f'check Tidal authentication and quality settings.'
                        )
                else:
                    task['error_message'] = f'Soulseek transfer errored 3 times for "{track_label}"{sources_str} — all sources failed or became unavailable'

                # CRITICAL: Notify batch manager so track is added to permanently_failed_tracks
                batch_id = task.get('batch_id')
                if batch_id:
                    logger.error(f"[Retry Exhausted] Notifying batch manager of permanent failure for task {task_id}")
                    return True  # Signal that we need to call completion outside the lock
                return False

        # Check for queued timeout (90 seconds like GUI)
        elif 'Queued' in state_str or task['status'] == 'queued':
            if 'queued_start_time' not in task:
                task['queued_start_time'] = current_time
                return False
            else:
                queue_time = current_time - task['queued_start_time']

                # Use context-aware timeouts like GUI:
                # - 15 seconds for artist album downloads (streaming context)
                # - 90 seconds for background playlist downloads
                is_streaming_context = task.get('track_info', {}).get('is_album_download', False)
                timeout_threshold = 15.0 if is_streaming_context else 90.0

                if queue_time > timeout_threshold:
                    # Track retry attempts to prevent rapid loops
                    retry_count = task.get('stuck_retry_count', 0)
                    last_retry = task.get('last_retry_time', 0)

                    # Don't retry too frequently (wait at least 30 seconds between retries)
                    if retry_count < 3 and (current_time - last_retry) > 30:  # Max 3 retry attempts
                        logger.warning(f"Task stuck in queue for {queue_time:.1f}s - immediate retry {retry_count + 1}/3")
                        task['stuck_retry_count'] = retry_count + 1
                        task['last_retry_time'] = current_time

                        _ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
                        username = task.get('username') or _ti.get('username')
                        filename = task.get('filename') or _ti.get('filename')
                        download_id = task.get('download_id')

                        # Defer slskd cancel to outside the lock
                        if username and download_id:
                            deferred_ops.append(('cancel_download', download_id, username))

                        # UNIFIED RETRY LOGIC: Handle timeout retry exactly like error retry
                        # Mark current source as used to prevent retry loops
                        # CRITICAL: Use full filename (not basename) to match worker's source_key format
                        if username and filename:
                            used_sources = task.get('used_sources', set())
                            source_key = f"{username}_{filename}"
                            used_sources.add(source_key)
                            task['used_sources'] = used_sources
                            logger.error(f"Marked timeout source as used: {source_key}")

                        # Defer orphan cleanup to outside the lock (needs matched_context_lock)
                        if username and filename:
                            old_context_key = _make_context_key(username, filename)
                            _orphaned_download_keys.add(old_context_key)
                            deferred_ops.append(('cleanup_orphan', old_context_key))

                        # Clear download info since we cancelled it
                        task.pop('download_id', None)
                        task.pop('username', None)
                        task.pop('filename', None)

                        # Reset task state for immediate retry (like error retry)
                        task['status'] = 'searching'
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)
                        task['status_change_time'] = current_time
                        logger.error(f"Task {task.get('track_info', {}).get('name', 'Unknown')} reset for timeout retry")

                        # Defer worker restart to outside the lock
                        batch_id = task.get('batch_id')
                        if task_id and batch_id:
                            deferred_ops.append(('restart_worker', task_id, batch_id))
                        return False
                    elif retry_count < 3:
                        # Wait longer before next retry
                        return False
                    else:
                        # Too many retries, mark as failed
                        track_label = task.get('track_info', {}).get('name', 'Unknown')
                        tried_sources = task.get('used_sources', set())
                        sources_str = f' (tried {len(tried_sources)} source{"s" if len(tried_sources) != 1 else ""})' if tried_sources else ''
                        logger.error("Task failed after 3 retry attempts (queue timeout)")
                        task['status'] = 'failed'
                        task['error_message'] = f'Download stayed queued too long 3 times for "{track_label}"{sources_str} — peers may be offline or have full queues'
                        # Clear timers to prevent further retry loops
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)

                        # CRITICAL: Notify batch manager so track is added to permanently_failed_tracks
                        batch_id = task.get('batch_id')
                        if batch_id:
                            logger.error(f"[Retry Exhausted] Notifying batch manager of permanent failure for task {task_id}")
                            return True  # Signal that we need to call completion outside the lock
                        return False
                
        # Check for downloading at 0% timeout (90 seconds like GUI) 
        elif 'InProgress' in state_str and progress < 1:
            if 'downloading_start_time' not in task:
                task['downloading_start_time'] = current_time
                return False
            else:
                download_time = current_time - task['downloading_start_time']
                
                # Use context-aware timeouts like GUI:
                # - 15 seconds for artist album downloads (streaming context)
                # - 90 seconds for background playlist downloads
                is_streaming_context = task.get('track_info', {}).get('is_album_download', False)
                timeout_threshold = 15.0 if is_streaming_context else 90.0

                if download_time > timeout_threshold:
                    retry_count = task.get('stuck_retry_count', 0)
                    last_retry = task.get('last_retry_time', 0)
                    
                    # Don't retry too frequently (wait at least 30 seconds between retries)
                    if retry_count < 3 and (current_time - last_retry) > 30:  # Max 3 retry attempts
                        logger.warning(f"Task stuck at 0% for {download_time:.1f}s - immediate retry {retry_count + 1}/3")
                        task['stuck_retry_count'] = retry_count + 1
                        task['last_retry_time'] = current_time

                        _ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
                        username = task.get('username') or _ti.get('username')
                        filename = task.get('filename') or _ti.get('filename')
                        download_id = task.get('download_id')

                        # Defer slskd cancel to outside the lock
                        if username and download_id:
                            deferred_ops.append(('cancel_download', download_id, username))

                        # UNIFIED RETRY LOGIC: Handle 0% timeout retry exactly like error retry
                        # Mark current source as used to prevent retry loops
                        # CRITICAL: Use full filename (not basename) to match worker's source_key format
                        if username and filename:
                            used_sources = task.get('used_sources', set())
                            source_key = f"{username}_{filename}"
                            used_sources.add(source_key)
                            task['used_sources'] = used_sources
                            logger.info(f"Marked 0% progress source as used: {source_key}")

                        # Defer orphan cleanup to outside the lock (needs matched_context_lock)
                        if username and filename:
                            old_context_key = _make_context_key(username, filename)
                            _orphaned_download_keys.add(old_context_key)
                            deferred_ops.append(('cleanup_orphan', old_context_key))

                        # Clear download info since we cancelled it
                        task.pop('download_id', None)
                        task.pop('username', None)
                        task.pop('filename', None)

                        # Reset task state for immediate retry (like error retry)
                        task['status'] = 'searching'
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)
                        task['status_change_time'] = current_time
                        logger.warning(f"Task {task.get('track_info', {}).get('name', 'Unknown')} reset for 0% retry")

                        # Defer worker restart to outside the lock
                        batch_id = task.get('batch_id')
                        if task_id and batch_id:
                            deferred_ops.append(('restart_worker', task_id, batch_id))
                        return False
                    elif retry_count < 3:
                        # Wait longer before next retry
                        return False
                    else:
                        track_label = task.get('track_info', {}).get('name', 'Unknown')
                        tried_sources = task.get('used_sources', set())
                        sources_str = f' (tried {len(tried_sources)} source{"s" if len(tried_sources) != 1 else ""})' if tried_sources else ''
                        logger.error("Task failed after 3 retry attempts (0% progress timeout)")
                        task['status'] = 'failed'
                        task['error_message'] = f'Download stuck at 0% three times for "{track_label}"{sources_str} — peers may have connection issues'
                        # Clear timers to prevent further retry loops
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)
                        
                        # CRITICAL: Notify batch manager so track is added to permanently_failed_tracks
                        batch_id = task.get('batch_id')
                        if batch_id:
                            logger.error(f"[Retry Exhausted] Notifying batch manager of permanent failure for task {task_id}")
                            return True  # Signal that we need to call completion outside the lock
                        return False
        else:
            # Only reset timers if actual byte progress is being made
            bytes_transferred = live_info.get('bytesTransferred', 0)
            if progress >= 1 or bytes_transferred > 0:
                # Real progress happening, reset timers and retry counts
                task.pop('queued_start_time', None)
                task.pop('downloading_start_time', None)
                task.pop('stuck_retry_count', None)
            else:
                # Unknown state with no progress (e.g., "Requested", "Initializing")
                # Treat like 0% stuck — start/keep the downloading timer running
                if 'downloading_start_time' not in task:
                    task['downloading_start_time'] = current_time
                download_time = current_time - task['downloading_start_time']

                # Use context-aware timeouts
                is_streaming_context = task.get('track_info', {}).get('is_album_download', False)
                timeout_threshold = 15.0 if is_streaming_context else 90.0

                if download_time > timeout_threshold:
                    retry_count = task.get('stuck_retry_count', 0)
                    last_retry = task.get('last_retry_time', 0)

                    if retry_count < 3 and (current_time - last_retry) > 30:
                        logger.warning(f"Task stuck in unknown state '{state_str}' with 0 progress for {download_time:.1f}s - retry {retry_count + 1}/3")
                        task['stuck_retry_count'] = retry_count + 1
                        task['last_retry_time'] = current_time

                        _ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
                        username = task.get('username') or _ti.get('username')
                        filename = task.get('filename') or _ti.get('filename')
                        download_id = task.get('download_id')

                        if username and download_id:
                            deferred_ops.append(('cancel_download', download_id, username))

                        if username and filename:
                            used_sources = task.get('used_sources', set())
                            source_key = f"{username}_{filename}"
                            used_sources.add(source_key)
                            task['used_sources'] = used_sources
                            logger.info(f"Marked unknown-state source as used: {source_key}")

                        if username and filename:
                            old_context_key = _make_context_key(username, filename)
                            _orphaned_download_keys.add(old_context_key)
                            deferred_ops.append(('cleanup_orphan', old_context_key))

                        task.pop('download_id', None)
                        task.pop('username', None)
                        task.pop('filename', None)
                        task['status'] = 'searching'
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)
                        task['status_change_time'] = current_time

                        batch_id = task.get('batch_id')
                        if task_id and batch_id:
                            deferred_ops.append(('restart_worker', task_id, batch_id))
                        return False
                    elif retry_count >= 3:
                        track_label = task.get('track_info', {}).get('name', 'Unknown')
                        tried_sources = task.get('used_sources', set())
                        sources_str = f' (tried {len(tried_sources)} source{"s" if len(tried_sources) != 1 else ""})' if tried_sources else ''
                        logger.error(f"Task failed after 3 retry attempts (unknown state '{state_str}')")
                        task['status'] = 'failed'
                        task['error_message'] = f'Download stuck in "{state_str}" state 3 times for "{track_label}"{sources_str}'
                        task.pop('queued_start_time', None)
                        task.pop('downloading_start_time', None)

                        batch_id = task.get('batch_id')
                        if batch_id:
                            return True
                        return False

        return False
    
    
    def _validate_worker_counts(self):
        """
        Validate worker counts to detect and fix ghost workers or orphaned tasks.
        This prevents the modal from showing wrong worker counts permanently.
        """
        try:
            batches_needing_workers = []

            with tasks_lock:
                for batch_id in list(self.monitored_batches):
                    if batch_id not in download_batches:
                        continue

                    batch = download_batches[batch_id]
                    reported_active = batch['active_count']
                    max_concurrent = batch['max_concurrent']
                    queue = batch.get('queue', [])
                    queue_index = batch.get('queue_index', 0)

                    # Count actually active tasks based on status
                    actually_active = 0
                    orphaned_tasks = []
                    # Tasks already processed by _on_download_completed should NOT be counted
                    # as active, even if their status hasn't been updated yet (race condition
                    # between stream processor calling _on_download_completed and
                    # _run_post_processing_worker setting status to 'completed')
                    completed_task_ids = batch.get('_completed_task_ids', set())

                    for task_id in queue:
                        if task_id in download_tasks:
                            task_status = download_tasks[task_id]['status']
                            if task_status in ['searching', 'downloading', 'queued', 'post_processing']:
                                if task_id not in completed_task_ids:
                                    actually_active += 1
                            elif task_status in ['failed', 'completed', 'cancelled', 'not_found'] and task_id in queue[queue_index:]:
                                # These are orphaned tasks - they're done but still in active queue
                                orphaned_tasks.append(task_id)

                    # Check for discrepancies
                    if reported_active != actually_active or orphaned_tasks:
                        logger.warning(f"[Worker Validation] Batch {batch_id}: reported={reported_active}, actual={actually_active}, orphaned={len(orphaned_tasks)}")

                        if orphaned_tasks:
                            logger.warning(f"[Worker Validation] Found {len(orphaned_tasks)} orphaned tasks to cleanup")

                        # Fix the active count if it's wrong
                        if reported_active != actually_active:
                            old_count = batch['active_count']
                            batch['active_count'] = actually_active
                            logger.info(f"[Worker Validation] Fixed active count: {old_count} → {actually_active}")

                            # Defer starting workers to outside the lock
                            if actually_active < max_concurrent and queue_index < len(queue):
                                batches_needing_workers.append(batch_id)

            # Start replacement workers outside the lock
            for batch_id in batches_needing_workers:
                try:
                    logger.info(f"[Worker Validation] Starting replacement workers for {batch_id}")
                    _start_next_batch_of_downloads(batch_id)
                except Exception as e:
                    logger.error(f"[Worker Validation] Error starting workers for {batch_id}: {e}")

        except Exception as validation_error:
            logger.error(f"Error in worker count validation: {validation_error}")
