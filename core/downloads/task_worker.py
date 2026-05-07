"""Per-task download worker.

Runs as a background thread (one per task) that:
1. Tries source-reuse (use the batch's last good slskd peer if available)
2. Tries staging-match (file already in staging folder, no download needed)
3. Generates smart search queries via the matching engine + legacy fallbacks
4. Iterates queries sequentially against the soulseek client
5. For each query: validates results, attempts download with fallback candidates
6. If hybrid mode: falls back to remaining sources (youtube/tidal/qobuz/hifi/deezer_dl)
7. On total failure: marks task not_found + records search diagnostics
8. On any uncaught exception: marks failed + emergency worker-slot recovery

Lifted verbatim from web_server.py's `_download_track_worker`. The helpers
this calls into (try_source_reuse, store_batch_source, try_staging_match,
get_valid_candidates, attempt_download_with_candidates, on_download_completed,
recover_worker_slot) are passed via `TaskWorkerDeps` since each is itself
a large web_server.py helper that will get its own lift in subsequent PRs.
"""

from __future__ import annotations

import logging
import re
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core.runtime_state import download_tasks, tasks_lock
from core.spotify_client import Track as SpotifyTrack

logger = logging.getLogger(__name__)


@dataclass
class TaskWorkerDeps:
    """Bundle of cross-cutting deps the per-task download worker needs."""
    download_orchestrator: Any
    matching_engine: Any
    run_async: Callable
    try_source_reuse: Callable                    # (task_id, batch_id, track) -> bool
    store_batch_source: Callable                  # (batch_id, username, filename) -> None
    try_staging_match: Callable                   # (task_id, batch_id, track) -> bool
    get_valid_candidates: Callable                # (results, spotify_track, query) -> list
    attempt_download_with_candidates: Callable    # (task_id, candidates, track, batch_id) -> bool
    on_download_completed: Callable               # (batch_id, task_id, success) -> None
    recover_worker_slot: Callable                 # (batch_id, task_id) -> None


def download_track_worker(task_id: str, batch_id: Optional[str], deps: TaskWorkerDeps) -> None:
    """Enhanced download worker that matches the GUI's exact retry logic.

    Implements sequential query retry, fallback candidates, and download
    failure retry.
    """
    try:
        # Retrieve task details from global state
        with tasks_lock:
            if task_id not in download_tasks:
                logger.warning(f"[Modal Worker] Task {task_id} not found in download_tasks")
                return
            task = download_tasks[task_id].copy()

        # Cancellation Checkpoint 1: Before doing anything
        with tasks_lock:
            if task_id not in download_tasks:
                logger.info(f"[Modal Worker] Task {task_id} was deleted before starting")
                return
            if download_tasks[task_id]['status'] == 'cancelled':
                logger.warning(f"[Modal Worker] Task {task_id} cancelled before starting")
                # V2 FIX: Don't call _on_download_completed for cancelled V2 tasks
                # V2 system handles worker slot freeing in atomic cancel function
                task_playlist_id = download_tasks[task_id].get('playlist_id')
                if task_playlist_id:
                    logger.warning(f"[Modal Worker] V2 task {task_id} cancelled - worker slot already freed by V2 system")
                    return  # V2 system already handled worker slot management
                elif batch_id:
                    # Legacy system - use old completion callback
                    logger.warning(f"[Modal Worker] Legacy task {task_id} cancelled - using legacy completion callback")
                    deps.on_download_completed(batch_id, task_id, False)
                return

        track_data = task['track_info']
        track_name = track_data.get('name', 'Unknown Track')

        logger.info(f"[Modal Worker] Task {task_id} starting search for track: '{track_name}'")

        # Recreate a SpotifyTrack object for the matching engine
        # Handle both string format and Spotify API format for artists
        raw_artists = track_data.get('artists', [])
        processed_artists = []
        for artist in raw_artists:
            if isinstance(artist, str):
                processed_artists.append(artist)
            elif isinstance(artist, dict) and 'name' in artist:
                processed_artists.append(artist['name'])
            else:
                processed_artists.append(str(artist))

        # Handle album field - extract name if it's a dictionary
        raw_album = track_data.get('album', '')
        if isinstance(raw_album, dict) and 'name' in raw_album:
            album_name = raw_album['name']
        elif isinstance(raw_album, str):
            album_name = raw_album
        else:
            album_name = str(raw_album)

        track = SpotifyTrack(
            id=track_data.get('id', ''),
            name=track_data.get('name', ''),
            artists=processed_artists,
            album=album_name,
            duration_ms=track_data.get('duration_ms', 0),
            popularity=track_data.get('popularity', 0),
        )
        logger.info(f"[Modal Worker] Starting download task for: {track.name} by {track.artists[0] if track.artists else 'Unknown'}")

        # === SOURCE REUSE: Check batch's last good source before searching ===
        if deps.try_source_reuse(task_id, batch_id, track):
            # Store source for next worker (cascading reuse)
            with tasks_lock:
                used_filename = download_tasks.get(task_id, {}).get('filename')
                used_username = download_tasks.get(task_id, {}).get('username')
            if used_filename and used_username:
                deps.store_batch_source(batch_id, used_username, used_filename)
            return

        # === STAGING CHECK: Check staging folder for existing file before searching ===
        if deps.try_staging_match(task_id, batch_id, track):
            return

        # Initialize task state tracking (like GUI's parallel_search_tracking)
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'searching'  # Now actively being processed
                download_tasks[task_id]['current_query_index'] = 0
                download_tasks[task_id]['current_candidate_index'] = 0
                download_tasks[task_id]['retry_count'] = 0
                download_tasks[task_id]['candidates'] = []
                # CRITICAL: Preserve used_sources from previous retry attempts (don't reset to empty set)
                # If this is a retry, the monitor will have already marked failed sources
                if 'used_sources' not in download_tasks[task_id]:
                    download_tasks[task_id]['used_sources'] = set()
                # Else: keep existing used_sources to avoid retrying same failed hosts

        # 1. Generate multiple search queries (like GUI's generate_smart_search_queries)
        artist_name = track.artists[0] if track.artists else None
        track_name = track.name

        # Start with matching engine queries
        search_queries = deps.matching_engine.generate_download_queries(track)

        # Add legacy fallback queries (like GUI does)
        legacy_queries = []

        if artist_name:
            # Add first word of artist approach (legacy compatibility)
            artist_words = artist_name.split()
            if artist_words:
                first_word = artist_words[0]
                if first_word.lower() == 'the' and len(artist_words) > 1:
                    first_word = artist_words[1]

                if len(first_word) > 1:
                    legacy_queries.append(f"{track_name} {first_word}".strip())

        # Add track-only query
        if track_name.strip():
            legacy_queries.append(track_name.strip())

        # Add traditional cleaned queries
        cleaned_name = re.sub(r'\s*\([^)]*\)', '', track_name).strip()
        cleaned_name = re.sub(r'\s*\[[^\]]*\]', '', cleaned_name).strip()

        if cleaned_name and cleaned_name.lower() != track_name.lower():
            legacy_queries.append(cleaned_name.strip())

        # Combine enhanced queries with legacy fallbacks
        all_queries = search_queries + legacy_queries

        # Remove duplicates while preserving order
        unique_queries = []
        seen = set()
        for query in all_queries:
            if query and query.lower() not in seen:
                unique_queries.append(query)
                seen.add(query.lower())

        search_queries = unique_queries
        logger.info(f"[Modal Worker] Generated {len(search_queries)} smart search queries for '{track.name}': {search_queries}")
        logger.info(f"[Modal Worker] About to start search loop for task {task_id} (track: '{track.name}')")

        # 2. Sequential Query Search (matches GUI's start_search_worker_parallel logic)
        search_diagnostics = []  # Track what happened per query for detailed error messages
        all_raw_results = []  # Collect raw results across queries for candidate review modal
        for query_index, query in enumerate(search_queries):
            # Cancellation check before each query
            with tasks_lock:
                if task_id not in download_tasks:
                    logger.debug(f"[Modal Worker] Task {task_id} was deleted during query {query_index + 1}")
                    return
                if download_tasks[task_id]['status'] == 'cancelled':
                    logger.debug(f"[Modal Worker] Task {task_id} cancelled during query {query_index + 1}")
                    # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                    return
                download_tasks[task_id]['current_query_index'] = query_index

            logger.debug(f"[Modal Worker] Query {query_index + 1}/{len(search_queries)}: '{query}'")
            logger.debug(f"About to call soulseek search for task {task_id}")

            try:
                # Perform search with timeout
                tracks_result, _ = deps.run_async(deps.download_orchestrator.search(query, timeout=30))
                logger.debug(f"Search completed for task {task_id}, got {len(tracks_result) if tracks_result else 0} results")

                # CRITICAL: Check cancellation immediately after search returns
                with tasks_lock:
                    if task_id not in download_tasks:
                        logger.info(f"[Modal Worker] Task {task_id} was deleted after search returned")
                        return
                    if download_tasks[task_id]['status'] == 'cancelled':
                        logger.warning(f"[Modal Worker] Task {task_id} cancelled after search returned - ignoring results")
                        # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                        # The cancellation endpoint already handles batch management properly
                        return

                if tracks_result:
                    result_count = len(tracks_result)
                    # Validate candidates using GUI's get_valid_candidates logic
                    candidates = deps.get_valid_candidates(tracks_result, track, query)
                    if candidates:
                        logger.debug(f"[Modal Worker] Found {len(candidates)} valid candidates for query '{query}'")

                        # CRITICAL: Check cancellation before processing candidates
                        with tasks_lock:
                            if task_id not in download_tasks:
                                logger.info(f"[Modal Worker] Task {task_id} was deleted before processing candidates")
                                return
                            if download_tasks[task_id]['status'] == 'cancelled':
                                logger.warning(f"[Modal Worker] Task {task_id} cancelled before processing candidates")
                                # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                                return
                            # Store candidates for retry fallback (like GUI)
                            download_tasks[task_id]['cached_candidates'] = candidates

                        # Try to download with these candidates
                        success = deps.attempt_download_with_candidates(task_id, candidates, track, batch_id)
                        if success:
                            # Download initiated successfully - let the download monitoring system handle completion
                            if batch_id:
                                logger.info(f"[Modal Worker] Download initiated successfully for task {task_id} - monitoring will handle completion")
                            # Store this source for batch reuse
                            with tasks_lock:
                                used_filename = download_tasks.get(task_id, {}).get('filename')
                                used_username = download_tasks.get(task_id, {}).get('username')
                            if used_filename and used_username:
                                deps.store_batch_source(batch_id, used_username, used_filename)
                            return  # Success, exit the worker
                        else:
                            search_diagnostics.append(f'"{query}": {result_count} results, {len(candidates)} passed filters but download failed to start')
                    else:
                        search_diagnostics.append(f'"{query}": {result_count} results but none passed quality/artist filters')
                        # Strip SoundCloud preview snippets before caching for the
                        # review modal — the user can't pick something useful from
                        # a 30s preview clip, and clicking one bypasses validation
                        # and downloads it anyway.
                        from core.downloads.validation import filter_soundcloud_previews
                        _filtered_raw = filter_soundcloud_previews(tracks_result[:20], track)
                        all_raw_results.extend(_filtered_raw)
                else:
                    search_diagnostics.append(f'"{query}": no results found')

            except Exception as e:
                logger.debug(f"[Modal Worker] Search failed for query '{query}': {e}")
                search_diagnostics.append(f'"{query}": search error — {e}')
                continue

        # === HYBRID FALLBACK: If primary source failed, try remaining sources directly ===
        # The orchestrator's hybrid search stops at the first source with results, even if
        # those results all fail quality filtering. Try remaining sources individually.
        if getattr(deps.download_orchestrator, 'mode', '') == 'hybrid':
            try:
                orch = deps.download_orchestrator
                hybrid_order = getattr(orch, 'hybrid_order', None) or []
                if not hybrid_order:
                    primary = getattr(orch, 'hybrid_primary', 'soulseek')
                    secondary = getattr(orch, 'hybrid_secondary', '')
                    hybrid_order = [primary, secondary] if secondary and secondary != primary else [primary]

                # Resolve via the orchestrator's generic accessor — the
                # legacy per-source attrs were dropped in the registry
                # refactor, so getattr(orch, 'soulseek', None) etc. all
                # silently returned None and the fallback never fired.
                source_clients = {
                    name: orch.client(name)
                    for name in ('soulseek', 'youtube', 'tidal', 'qobuz',
                                 'hifi', 'deezer_dl', 'lidarr', 'soundcloud')
                }

                # The orchestrator tried sources in order but stopped at the first with results.
                # We don't know which it stopped at, so try ALL sources except the first
                # (which was definitely tried). If the first was skipped (unconfigured),
                # the orchestrator would have tried the second — but trying it again is
                # harmless (streaming sources return fast).
                remaining_sources = [s for s in hybrid_order[1:] if s in source_clients and source_clients[s]]
                if remaining_sources:
                    logger.warning(f"[Hybrid Fallback] Primary source had no valid matches. Trying fallback sources: {remaining_sources}")

                for fallback_source in remaining_sources:
                    fb_client = source_clients[fallback_source]
                    if hasattr(fb_client, 'is_configured') and not fb_client.is_configured():
                        continue

                    # Use first 2 queries only for speed
                    for fb_query in search_queries[:2]:
                        try:
                            logger.warning(f"[Hybrid Fallback] Trying {fallback_source}: '{fb_query}'")
                            fb_results, _ = deps.run_async(fb_client.search(fb_query, timeout=20))
                            if not fb_results:
                                continue
                            fb_candidates = deps.get_valid_candidates(fb_results, track, fb_query)
                            if fb_candidates:
                                logger.warning(f"[Hybrid Fallback] {fallback_source} found {len(fb_candidates)} valid candidates!")
                                success = deps.attempt_download_with_candidates(task_id, fb_candidates, track, batch_id)
                                if success:
                                    return
                        except Exception as e:
                            logger.error(f"[Hybrid Fallback] {fallback_source} search failed: {e}")
                            continue

                    logger.warning(f"[Hybrid Fallback] {fallback_source} returned no valid candidates")

            except Exception as e:
                logger.error(f"[Hybrid Fallback] Error in fallback logic: {e}")

        # If we get here, all search queries and hybrid fallbacks failed
        logger.warning(f"[Modal Worker] No valid candidates found for '{track.name}' after trying all {len(search_queries)} queries.")
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'not_found'
                _diag_summary = ' | '.join(search_diagnostics) if search_diagnostics else 'no queries attempted'
                download_tasks[task_id]['error_message'] = f'No match found for "{track_name}" by {artist_name or "Unknown"} after {len(search_queries)} queries. Breakdown: {_diag_summary}'
                # Store raw results so the user can review what Soulseek returned
                if all_raw_results and not download_tasks[task_id].get('cached_candidates'):
                    download_tasks[task_id]['cached_candidates'] = all_raw_results

        # Notify batch manager that this task completed (failed) - THREAD SAFE
        if batch_id:
            try:
                deps.on_download_completed(batch_id, task_id, False)
            except Exception as completion_error:
                logger.error(f"Error in batch completion callback for {task_id}: {completion_error}")

    except Exception as e:
        track_name_safe = locals().get('track_name', 'unknown')  # Safe fallback for track_name
        logger.error(f"CRITICAL ERROR in download task for '{track_name_safe}' (task_id: {task_id}): {e}")
        traceback.print_exc()

        # Update task status safely with timeout
        try:
            lock_acquired = tasks_lock.acquire(timeout=2.0)
            if lock_acquired:
                try:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'failed'
                        download_tasks[task_id]['error_message'] = f'Unexpected error during download: {type(e).__name__}: {e}'
                        logger.error(f"[Exception Recovery] Set task {task_id} status to 'failed'")
                finally:
                    tasks_lock.release()
            else:
                logger.error(f"[Exception Recovery] Could not acquire lock to update task {task_id} status")
        except Exception as status_error:
            logger.error(f"Error updating task status in exception handler: {status_error}")

        # Notify batch manager that this task completed (failed) - THREAD SAFE with RECOVERY
        if batch_id:
            try:
                deps.on_download_completed(batch_id, task_id, False)
                logger.error(f"[Exception Recovery] Successfully freed worker slot for task {task_id}")
            except Exception as completion_error:
                logger.error(f"[Exception Recovery] Error in batch completion callback for {task_id}: {completion_error}")
                # CRITICAL: If batch completion fails, we need to manually recover the worker slot
                try:
                    logger.error(f"[Exception Recovery] Attempting manual worker slot recovery for batch {batch_id}")
                    deps.recover_worker_slot(batch_id, task_id)
                except Exception as recovery_error:
                    logger.error(f"[Exception Recovery] FATAL: Could not recover worker slot: {recovery_error}")
