"""Post-processing worker for completed downloads.

The verification workflow that runs AFTER a slskd transfer reports as
'Succeeded' but BEFORE the task is marked completed in the UI. Locates
the file on disk (with retries + multiple search strategies), routes
it through metadata enhancement and the import pipeline, and finally
calls the batch lifecycle completion callback.

Lifted verbatim from web_server.py's `_run_post_processing_worker`.
The single function is intentionally kept as one ~400-line block to
preserve byte-for-byte parity with the original — refactoring into
smaller helpers gets its own follow-up PR.

Dependencies are passed in via `PostProcessDeps` since the function
needs ~9 callbacks/refs and direct injection beats hidden imports.
"""

from __future__ import annotations

import logging
import os
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from core.imports.album_naming import resolve_album_group as _resolve_album_group
from core.imports.context import (
    get_import_clean_album,
    get_import_clean_title,
    get_import_context_album,
    get_import_context_artist,
    get_import_original_search,
    normalize_import_context,
)
from core.imports.filename import extract_track_number_from_filename
from core.metadata import enrichment as metadata_enrichment
from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)

logger = logging.getLogger(__name__)


@dataclass
class PostProcessDeps:
    """Bundle of dependencies the post-processing worker needs.

    Constructed per-call by the route layer so client / config refs are
    always live (no caching of pre-init Spotify clients etc).
    """
    config_manager: Any
    soulseek_client: Any
    run_async: Callable
    docker_resolve_path: Callable[[str], str]
    extract_filename: Callable[[str], str]
    make_context_key: Callable[[str, str], str]
    find_completed_file: Callable
    enhance_file_metadata: Callable
    wipe_source_tags: Callable[[str], bool]
    post_process_with_verification: Callable
    mark_task_completed: Callable[[str, Optional[dict]], None]
    on_download_completed: Callable[[str, str, bool], None]


def run_post_processing_worker(task_id: str, batch_id: str, deps: PostProcessDeps) -> None:
    """NEW VERIFICATION WORKFLOW: Post-processing worker that only sets 'completed'
    status after successful file verification and processing. This matches sync.py's
    reliability.
    """
    try:
        logger.info(f"[Post-Processing] Starting verification for task {task_id}")

        # Retrieve task details from global state
        with tasks_lock:
            if task_id not in download_tasks:
                logger.warning(f"[Post-Processing] Task {task_id} not found in download_tasks")
                return
            task = download_tasks[task_id].copy()

        # Check if task was cancelled or already completed during post-processing
        if task['status'] == 'cancelled':
            logger.warning(f"[Post-Processing] Task {task_id} was cancelled, skipping verification")
            return
        if task['status'] == 'completed' or task.get('stream_processed'):
            logger.info(f"[Post-Processing] Task {task_id} already completed by stream processor, skipping verification")
            return

        # Extract file information for verification
        track_info = task.get('track_info', {})
        task_filename = task.get('filename') or track_info.get('filename')
        task_username = task.get('username') or track_info.get('username')

        if not task_filename or not task_username:
            logger.warning(f"[Post-Processing] Missing filename or username for task {task_id}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = 'Post-processing failed: missing file or source information from Soulseek transfer'
            deps.on_download_completed(batch_id, task_id, False)
            return

        download_dir = deps.docker_resolve_path(deps.config_manager.get('soulseek.download_path', './downloads'))
        transfer_dir = deps.docker_resolve_path(deps.config_manager.get('soulseek.transfer_path', './Transfer'))

        # Try to get context for generating the correct final filename
        task_basename = deps.extract_filename(task_filename)
        context_key = deps.make_context_key(task_username, task_filename)
        expected_final_filename = None

        logger.info(f"[Post-Processing] Looking up context with key: {context_key}")

        with matched_context_lock:
            context = matched_downloads_context.get(context_key)
            # Debug: Show all available context keys
            available_keys = list(matched_downloads_context.keys())
            logger.info(f"[Post-Processing] Available context keys: {available_keys[:10]}...")  # Show first 10 keys

        if context:
            logger.info(f"[Post-Processing] Found context for key: {context_key}")
            try:
                original_search = context.get("original_search_result", {})
                logger.info(f"[Post-Processing] original_search keys: {list(original_search.keys())}")

                clean_title = get_import_clean_title(context, default=original_search.get('title', ''))
                track_number = original_search.get('track_number')

                logger.info(f"[Post-Processing] clean_title: '{clean_title}', track_number: {track_number}")

                if clean_title and track_number:
                    # Generate expected final filename that stream processor would create
                    # Pattern: f"{track_number:02d} - {clean_title}.flac"
                    sanitized_title = clean_title.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
                    expected_final_filename = f"{track_number:02d} - {sanitized_title}.flac"
                    logger.info(f"[Post-Processing] Generated expected final filename: {expected_final_filename}")
                else:
                    logger.warning(f"[Post-Processing] Missing required data - clean_title: {bool(clean_title)}, track_number: {bool(track_number)}")
            except Exception as e:
                logger.error(f"[Post-Processing] Error generating expected filename: {e}")
                traceback.print_exc()
        else:
            logger.warning(f"[Post-Processing] No context found for key: {context_key}")
            # Try fuzzy matching with similar keys containing the filename
            # SAFETY: Constrain to same Soulseek username to prevent cross-album
            # metadata contamination during mass downloads (e.g., two albums both
            # having "01 - Intro.flac" would match the wrong context without this)
            with matched_context_lock:
                similar_keys = [k for k in matched_downloads_context.keys()
                                if k.startswith(f"{task_username}::") and task_basename in k]
            if similar_keys:
                # Use the first similar key found
                fuzzy_key = similar_keys[0]
                context = matched_downloads_context.get(fuzzy_key)
                logger.info(f"[Post-Processing] Found context using fuzzy key matching: {fuzzy_key}")

                # Generate expected final filename using the found context
                try:
                    original_search = context.get("original_search_result", {})
                    logger.info(f"[Post-Processing] fuzzy context original_search keys: {list(original_search.keys())}")

                    clean_title = get_import_clean_title(context, default=original_search.get('title', ''))
                    track_number = original_search.get('track_number')

                    logger.info(f"[Post-Processing] fuzzy context clean_title: '{clean_title}', track_number: {track_number}")

                    if clean_title and track_number:
                        # Generate expected final filename that stream processor would create
                        # Pattern: f"{track_number:02d} - {clean_title}.flac"
                        sanitized_title = clean_title.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
                        expected_final_filename = f"{track_number:02d} - {sanitized_title}.flac"
                        logger.info(f"[Post-Processing] Generated expected final filename from fuzzy match: {expected_final_filename}")
                    else:
                        logger.warning(f"[Post-Processing] Missing required data from fuzzy match - clean_title: {bool(clean_title)}, track_number: {bool(track_number)}")
                except Exception as e:
                    logger.error(f"[Post-Processing] Error generating expected filename from fuzzy match: {e}")
                    traceback.print_exc()
            else:
                logger.warning(f"[Post-Processing] No similar keys found containing '{task_basename}'")
                # Show a sample of what keys actually exist for debugging
                sample_keys = list(matched_downloads_context.keys())[:5]
                logger.info(f"[Post-Processing] Sample of existing keys: {sample_keys}")

        # RESILIENT FILE-FINDING LOOP: Try up to 3 times with delays
        found_file = None
        file_location = None

        # CRITICAL FIX: For YouTube downloads, the filename in task is 'id||title' (metadata),
        # but the actual file on disk is 'Title.mp3'. We must ask the client for the real path.
        if (task.get('username') == 'youtube' or '||' in str(task_filename)) and not found_file:
            logger.info(f"[Post-Processing] Detected YouTube download task: {task_id}")
            try:
                # Query the download orchestrator for the status which contains the real file path
                # CRITICAL FIX: Use the actual download_id designated by the client, not the internal task_id
                actual_download_id = task.get('download_id') or task_id
                status = deps.run_async(deps.soulseek_client.get_download_status(actual_download_id))
                if status and status.file_path:
                    real_path = status.file_path
                    if os.path.exists(real_path):
                        # Determine if it's in download or transfer directory
                        real_path_obj = Path(real_path)
                        download_dir_obj = Path(download_dir)
                        transfer_dir_obj = Path(transfer_dir)

                        # Use absolute path comparison
                        try:
                            if download_dir_obj.resolve() in real_path_obj.resolve().parents:
                                file_location = 'download'
                            elif transfer_dir_obj.resolve() in real_path_obj.resolve().parents:
                                file_location = 'transfer'
                            else:
                                file_location = 'absolute'
                        except:  # noqa: E722  -- byte-faithful to original (catches even KeyboardInterrupt)
                            # Fallback if resolve fails (e.g. permission or path issues)
                            file_location = 'absolute'

                        if file_location:
                            # We found the file! Use the absolute path if it confuses the joining logic,
                            # but usually we want just the filename if location is 'download'/'transfer'
                            # CRITICAL FIX: Always use the absolute real_path.
                            # Stripping to basename causes FileNotFoundError because post-processing
                            # runs with CWD as project root, not download dir.
                            found_file = real_path

                            logger.info(f"[Post-Processing] Resolved actual YouTube filename: {found_file} (Location: {file_location})")
                    else:
                        logger.warning(f"[Post-Processing] YouTube status reported path but file missing: {real_path}")
                else:
                    logger.warning(f"[Post-Processing] YouTube status returned no file_path for task {task_id}")
            except Exception as e:
                logger.error(f"[Post-Processing] Failed to retrieve YouTube task status: {e}")

        _file_search_max_retries = 5
        for retry_count in range(_file_search_max_retries):
            # If we already resolved the file (e.g. via YouTube status), skip searching
            if found_file:
                logger.info(f"[Post-Processing] Skipping search loop, file already resolved: {found_file}")
                break

            # Check if stream processor already completed this task while we were waiting
            with tasks_lock:
                if task_id in download_tasks:
                    if download_tasks[task_id].get('stream_processed') or download_tasks[task_id]['status'] == 'completed':
                        logger.info(f"[Post-Processing] Task {task_id} was completed by stream processor during file search - done")
                        return

            logger.warning(f"[Post-Processing] Attempt {retry_count + 1}/{_file_search_max_retries} to find file")
            logger.info(f"[Post-Processing] Original filename: {task_basename}")
            if expected_final_filename:
                logger.info(f"[Post-Processing] Expected final filename: {expected_final_filename}")
            else:
                logger.warning("[Post-Processing] No expected final filename available")

            # Strategy 1: Try with original filename in both downloads and transfer
            logger.info("[Post-Processing] Strategy 1: Searching with original filename...")
            found_file, file_location = deps.find_completed_file(download_dir, task_filename, transfer_dir)

            if found_file:
                logger.info(f"[Post-Processing] Strategy 1 SUCCESS: Found file with original filename in {file_location}: {found_file}")
            else:
                logger.error("[Post-Processing] Strategy 1 FAILED: Original filename not found in either location")

            # Strategy 2: If not found and we have an expected final filename, try that in transfer folder
            if not found_file and expected_final_filename:
                logger.info("[Post-Processing] Strategy 2: Searching transfer folder with expected final filename...")
                found_result = deps.find_completed_file(transfer_dir, expected_final_filename)
                if found_result and found_result[0]:
                    found_file, file_location = found_result[0], 'transfer'
                    logger.info(f"[Post-Processing] Strategy 2 SUCCESS: Found file with expected final filename: {found_file}")
                else:
                    logger.error("[Post-Processing] Strategy 2 FAILED: Expected final filename not found in transfer folder")
            elif not expected_final_filename:
                logger.warning("[Post-Processing] Strategy 2 SKIPPED: No expected final filename available")

            if found_file:
                logger.warning(f"[Post-Processing] FILE FOUND after {retry_count + 1} attempts in {file_location}: {found_file}")
                break
            else:
                logger.error(f"[Post-Processing] All search strategies failed on attempt {retry_count + 1}/{_file_search_max_retries}")
                if retry_count < _file_search_max_retries - 1:  # Don't sleep on final attempt
                    logger.info("[Post-Processing] Waiting 5 seconds before next attempt...")
                    time.sleep(5)

        if not found_file:
            # CRITICAL: Before marking as failed, check if stream processor already handled this
            # The /api/downloads/status polling endpoint processes files independently and may have
            # already moved/renamed/tagged the file successfully while we were searching
            with tasks_lock:
                if task_id in download_tasks:
                    if download_tasks[task_id].get('stream_processed') or download_tasks[task_id]['status'] == 'completed':
                        logger.error(f"[Post-Processing] Task {task_id} was completed by stream processor - not marking as failed")
                        return
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = f'File not found on disk after {_file_search_max_retries} search attempts. Expected: {os.path.basename(task_filename)}'
            deps.on_download_completed(batch_id, task_id, False)
            return

        # Handle file found in transfer folder - already completed by stream processor
        if file_location == 'transfer':
            logger.info(f"[Post-Processing] File found in transfer folder - already completed by stream processor: {found_file}")

            # Check if metadata enhancement was completed
            metadata_enhanced = False
            with tasks_lock:
                if task_id in download_tasks:
                    metadata_enhanced = download_tasks[task_id].get('metadata_enhanced', False)

            if not metadata_enhanced:
                logger.warning("[Post-Processing] File in transfer folder missing metadata enhancement - completing now")
                # Attempt to complete metadata enhancement using context
                if context and expected_final_filename:
                    try:
                        context = normalize_import_context(context)
                        # Extract required data from context
                        original_search = get_import_original_search(context)
                        artist_context = get_import_context_artist(context)
                        album_context = get_import_context_album(context)

                        if artist_context and album_context:
                            # CRITICAL FIX: Create album_info dict with proper structure for metadata enhancement
                            # This must match the format used in main stream processor to ensure consistency

                            # Extract track number from context (should be available from fuzzy match)
                            track_number = original_search.get('track_number', 1)

                            # If no track number in context, extract from filename
                            if track_number == 1 and found_file:
                                track_number = extract_track_number_from_filename(found_file)
                                logger.warning(
                                    "[Verification] missing track_number; extracted from filename=%r -> %s",
                                    os.path.basename(found_file),
                                    track_number,
                                )

                            # Ensure track_number is valid
                            if not isinstance(track_number, int) or track_number < 1:
                                logger.error(f"[Verification] Invalid track number ({track_number}), defaulting to 1")
                                track_number = 1

                            # Get clean track name
                            clean_track_name = get_import_clean_title(context, default=original_search.get('title', 'Unknown Track'))
                            album_name = get_import_clean_album(context, default=album_context.get('name', 'Unknown Album'))
                            album_image_url = album_context.get('image_url')
                            if not album_image_url and album_context.get('images'):
                                album_images = album_context.get('images', [])
                                if album_images and isinstance(album_images[0], dict):
                                    album_image_url = album_images[0].get('url')

                            album_info = {
                                'is_album': True,  # CRITICAL: Mark as album track
                                'album_name': album_name,
                                'track_number': track_number,  # CORRECTED TRACK NUMBER
                                'disc_number': original_search.get('disc_number', 1),
                                'clean_track_name': clean_track_name,
                                'album_image_url': album_image_url,
                                'confidence': 0.9,
                                'source': 'verification_worker_corrected',
                            }

                            # Apply album grouping for consistency with stream processor path.
                            # Only for singles/auto-detected — explicit album downloads already
                            # have the correct Spotify name and re-grouping would mangle it.
                            if not context.get("is_album_download", False):
                                try:
                                    raw_album_ctx = original_search.get('album')
                                    if isinstance(raw_album_ctx, str):
                                        original_album_ctx = raw_album_ctx
                                    elif isinstance(raw_album_ctx, dict) and 'name' in raw_album_ctx:
                                        original_album_ctx = raw_album_ctx['name']
                                    else:
                                        original_album_ctx = None
                                    consistent_album_name = _resolve_album_group(artist_context, album_info, original_album_ctx)
                                    album_info['album_name'] = consistent_album_name
                                except Exception as group_err:
                                    logger.error(f"[Verification] Album grouping failed, using raw name: {group_err}")
                            else:
                                logger.info(f"[Verification] Explicit album download - preserving album name: '{album_info['album_name']}'")

                            logger.info(f"[Verification] Created proper album_info - track_number: {track_number}, album: {album_info['album_name']}")

                            logger.info(f"[Post-Processing] Attempting metadata enhancement for: {found_file}")
                            logger.warning(f"[Metadata Input] Verification worker - artist: '{artist_context.get('name', 'MISSING')}' (id: {artist_context.get('id', 'MISSING')})")
                            logger.warning(f"[Metadata Input] Verification worker - album: '{album_info.get('album_name', 'MISSING')}', track#: {album_info.get('track_number', 'MISSING')}, source: {album_info.get('source', 'unknown')}")
                            enhancement_success = deps.enhance_file_metadata(found_file, context, artist_context, album_info)

                            if enhancement_success:
                                with tasks_lock:
                                    if task_id in download_tasks:
                                        download_tasks[task_id]['metadata_enhanced'] = True
                                logger.info(f"[Post-Processing] Successfully completed metadata enhancement for: {os.path.basename(found_file)}")
                            else:
                                logger.info(f"[Post-Processing] Metadata enhancement returned False for: {os.path.basename(found_file)}")
                        else:
                            logger.warning("[Post-Processing] Missing artist or album in context")
                            logger.info(f"[Post-Processing] artist_context: {artist_context is not None}, album_context: {album_context is not None}")
                            # Wipe source tags even without full enhancement — prevents
                            # Soulseek uploader's MusicBrainz IDs from causing album splits
                            if found_file and os.path.exists(found_file):
                                deps.wipe_source_tags(found_file)
                    except Exception as enhancement_error:
                        logger.error(f"[Post-Processing] Error during metadata enhancement: {enhancement_error}\n{traceback.format_exc()}")
                        if found_file and os.path.exists(found_file):
                            deps.wipe_source_tags(found_file)
                else:
                    logger.warning("[Post-Processing] Cannot complete metadata enhancement - missing context or expected filename")
                    if found_file and os.path.exists(found_file):
                        deps.wipe_source_tags(found_file)
            else:
                logger.info("[Post-Processing] File already has metadata enhancement completed")

            with tasks_lock:
                if task_id in download_tasks:
                    track_info = download_tasks[task_id].get('track_info')
                    deps.mark_task_completed(task_id, track_info)

            # Clean up context now that both stream processor and verification worker are done
            with matched_context_lock:
                if context_key in matched_downloads_context:
                    del matched_downloads_context[context_key]
                    logger.info(f"[Verification] Cleaned up context after successful verification: {context_key}")

            deps.on_download_completed(batch_id, task_id, True)
            return

        # File found in downloads folder - attempt post-processing
        try:
            # Rebuild the context key using the same function that stored it
            context_key = deps.make_context_key(task_username, task_filename)

            # Check if this download has matched context for post-processing
            with matched_context_lock:
                context = matched_downloads_context.get(context_key)

            if context:
                logger.info(f"[Post-Processing] Found matched context, running full post-processing for: {context_key}")
                # Run the existing post-processing logic with verification
                deps.post_process_with_verification(context_key, context, found_file, task_id, batch_id)
            else:
                # No matched context - just mark as completed since file exists
                logger.warning(f"[Post-Processing] No matched context, marking as completed: {os.path.basename(found_file)}")
                with tasks_lock:
                    if task_id in download_tasks:
                        track_info = download_tasks[task_id].get('track_info')
                        deps.mark_task_completed(task_id, track_info)

                # Clean up context if it exists (might be leftover from stream processor)
                with matched_context_lock:
                    if context_key in matched_downloads_context:
                        del matched_downloads_context[context_key]
                        logger.info(f"[Verification] Cleaned up leftover context: {context_key}")

                # Call completion callback since there's no other post-processing to handle it
                deps.on_download_completed(batch_id, task_id, True)

        except Exception as processing_error:
            logger.error(f"[Post-Processing] Processing failed for task {task_id}: {processing_error}")
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error_message'] = f"Post-processing failed: {str(processing_error)}"
            deps.on_download_completed(batch_id, task_id, False)

    except Exception as e:
        logger.error(f"[Post-Processing] Critical error in post-processing worker for task {task_id}: {e}")
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['status'] = 'failed'
                download_tasks[task_id]['error_message'] = f"Critical post-processing error: {str(e)}"
        deps.on_download_completed(batch_id, task_id, False)


# Re-export the metadata helper so callers can wrap it in a callback without
# importing from core.metadata directly.
__all__ = [
    'PostProcessDeps',
    'run_post_processing_worker',
    'metadata_enrichment',
]
