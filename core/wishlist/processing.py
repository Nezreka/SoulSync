"""Wishlist processing helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any, Callable, Dict

from core.wishlist.payloads import build_failed_track_wishlist_context
from core.wishlist.selection import filter_wishlist_tracks_by_category, sanitize_and_dedupe_wishlist_tracks
from core.wishlist.service import get_wishlist_service
from core.wishlist.state import get_wishlist_cycle, set_wishlist_cycle
from utils.logging_config import get_logger


module_logger = get_logger("wishlist.processing")
logger = module_logger


@dataclass
class WishlistAutoProcessingRuntime:
    """Dependencies needed to run automatic wishlist processing outside the controller."""

    processing_guard: Callable[[], AbstractContextManager[bool]]
    is_actually_processing: Callable[[], bool]
    app_context_factory: Callable[[], AbstractContextManager[Any]]
    get_profiles_database: Callable[[], Any]
    get_music_database: Callable[[], Any]
    download_batches: Dict[str, Dict[str, Any]]
    tasks_lock: Any
    update_automation_progress: Callable[..., Any]
    automation_engine: Any
    missing_download_executor: Any
    run_full_missing_tracks_process: Callable[[str, str, list[dict[str, Any]]], Any]
    get_batch_max_concurrent: Callable[[], int]
    get_active_server: Callable[[], str]
    current_time_fn: Callable[[], float]
    profile_id: int = 1
    logger: Any = module_logger


def remove_completed_tracks_from_wishlist(
    batch: Dict[str, Any],
    download_tasks: Dict[str, Dict[str, Any]],
    remove_from_wishlist: Callable[[Dict[str, Any]], Any],
    *,
    logger=logger,
) -> int:
    """Remove completed batch tasks from the wishlist."""
    removed_count = 0
    for task_id in batch.get('queue', []):
        if task_id in download_tasks:
            task = download_tasks[task_id]
            if task.get('status') == 'completed':
                try:
                    track_info = task.get('track_info', {})
                    context = {'track_info': track_info, 'original_search_result': track_info}
                    remove_from_wishlist(context)
                    removed_count += 1
                except Exception as exc:
                    logger.error(f"[Wishlist Processing] Error removing completed track from wishlist: {exc}")
    return removed_count


def add_cancelled_tracks_to_failed_tracks(
    batch: Dict[str, Any],
    download_tasks: Dict[str, Dict[str, Any]],
    permanently_failed_tracks: list[Dict[str, Any]],
    *,
    logger=logger,
    max_process: int = 100,
) -> int:
    """Promote cancelled-but-missing tasks into the failed-track list."""
    cancelled_tracks = batch.get('cancelled_tracks', set())
    if not cancelled_tracks:
        return 0

    processed_count = 0
    for task_id in batch.get('queue', [])[:max_process]:
        if task_id not in download_tasks:
            continue
        task = download_tasks[task_id]
        track_index = task.get('track_index', 0)
        if track_index not in cancelled_tracks:
            continue

        if task.get('status', 'unknown') == 'completed':
            continue

        original_track_info = task.get('track_info', {})
        cancelled_track_info = build_failed_track_wishlist_context(
            original_track_info,
            track_index=track_index,
            retry_count=0,
            failure_reason='Download cancelled',
            candidates=task.get('cached_candidates', []),
        )

        if any(t.get('table_index') == track_index for t in permanently_failed_tracks):
            continue

        permanently_failed_tracks.append(cancelled_track_info)
        processed_count += 1
        logger.error(
            f"[Wishlist Processing] Added cancelled missing track {cancelled_track_info['track_name']} to failed list for wishlist"
        )

    return processed_count


def recover_uncaptured_failed_tracks(
    batch: Dict[str, Any],
    download_tasks: Dict[str, Dict[str, Any]],
    permanently_failed_tracks: list[Dict[str, Any]],
    *,
    logger=logger,
) -> int:
    """Recover tasks force-marked failed/not_found so wishlist processing does not skip them."""
    recovered_count = 0
    for task_id in batch.get('queue', []):
        if task_id not in download_tasks:
            continue
        task = download_tasks[task_id]
        if task.get('status') not in ('failed', 'not_found'):
            continue

        track_index = task.get('track_index', 0)
        if any(t.get('table_index') == track_index for t in permanently_failed_tracks):
            continue

        original_track_info = task.get('track_info', {})
        recovered_track_info = build_failed_track_wishlist_context(
            original_track_info,
            track_index=track_index,
            retry_count=task.get('retry_count', 0),
            failure_reason=task.get('error_message', 'Download failed'),
            candidates=task.get('cached_candidates', []),
        )
        permanently_failed_tracks.append(recovered_track_info)
        recovered_count += 1
        logger.error(
            f"[Wishlist Processing] Recovered uncaptured failed track for wishlist: {recovered_track_info['track_name']}"
        )

    return recovered_count


def build_wishlist_source_context(batch: Dict[str, Any], current_time: datetime | None = None) -> Dict[str, Any]:
    """Build the source_context payload used when adding failed tracks back to the wishlist."""
    current_time = current_time or datetime.now()
    return {
        'playlist_name': batch.get('playlist_name', 'Unknown Playlist'),
        'playlist_id': batch.get('playlist_id', None),
        'added_from': 'webui_modal',
        'timestamp': current_time.isoformat(),
    }


def finalize_auto_wishlist_completion(
    batch_id: str,
    completion_summary: Dict[str, Any],
    *,
    download_batches: Dict[str, Dict[str, Any]],
    tasks_lock,
    reset_processing_state: Callable[[], None],
    add_activity_item: Callable[[Any, Any, Any, Any], Any],
    automation_engine,
    db_factory: Callable[[], Any],
    logger=logger,
) -> Dict[str, Any]:
    """Finalize auto wishlist processing after a batch finishes."""
    tracks_added = completion_summary.get('tracks_added', 0)
    total_failed = completion_summary.get('total_failed', 0)
    logger.error(
        f"[Auto-Wishlist] Background processing complete: {tracks_added} added to wishlist, {total_failed} failed"
    )

    if tracks_added > 0:
        add_activity_item("", "Wishlist Updated", f"{tracks_added} failed tracks added to wishlist", "Now")

    try:
        with tasks_lock:
            if batch_id in download_batches:
                current_cycle = download_batches[batch_id].get('current_cycle', 'albums')
            else:
                current_cycle = 'albums'

        next_cycle = 'singles' if current_cycle == 'albums' else 'albums'

        db = db_factory()
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                    INSERT OR REPLACE INTO metadata (key, value, updated_at)
                    VALUES ('wishlist_cycle', ?, CURRENT_TIMESTAMP)
                """,
                (next_cycle,),
            )
            conn.commit()

        logger.info(f"[Auto-Wishlist] Cycle toggled after completion: {current_cycle} → {next_cycle}")
    except Exception as cycle_error:
        logger.error(f"[Auto-Wishlist] Error toggling cycle: {cycle_error}")

    reset_processing_state()

    try:
        if automation_engine:
            automation_engine.emit('wishlist_processing_completed', {
                'tracks_processed': str(total_failed),
                'tracks_found': str(tracks_added),
                'tracks_failed': str(total_failed - tracks_added),
            })
    except Exception as e:
        logger.debug("emit wishlist_processing_completed failed: %s", e)

    return completion_summary


def remove_tracks_already_in_library(
    wishlist_service,
    profiles_database,
    music_database,
    active_server: str,
    *,
    logger=logger,
    skip_track_fn: Callable[[dict[str, Any]], bool] | None = None,
    log_prefix: str = "[Auto-Wishlist]",
) -> int:
    """Remove wishlist entries that are already present in the library."""
    all_profiles = profiles_database.get_all_profiles()
    cleanup_tracks = []
    for profile in all_profiles:
        cleanup_tracks.extend(wishlist_service.get_wishlist_tracks_for_download(profile_id=profile["id"]))

    cleanup_removed = 0
    for track in cleanup_tracks:
        if skip_track_fn and skip_track_fn(track):
            continue

        track_name = track.get('name', '')
        artists = track.get('artists', [])
        spotify_track_id = track.get('spotify_track_id') or track.get('id')
        track_album = track.get('album', {}).get('name') if isinstance(track.get('album'), dict) else track.get('album')

        if not track_name or not artists or not spotify_track_id:
            continue

        found_in_db = False
        matched_artist_name = ''
        for artist in artists:
            if isinstance(artist, str):
                artist_name = artist
            elif isinstance(artist, dict) and 'name' in artist:
                artist_name = artist['name']
            else:
                artist_name = str(artist)

            try:
                db_track, confidence = music_database.check_track_exists(
                    track_name,
                    artist_name,
                    confidence_threshold=0.7,
                    server_source=active_server,
                    album=track_album,
                )

                if db_track and confidence >= 0.7:
                    found_in_db = True
                    matched_artist_name = artist_name
                    break
            except Exception:
                continue

        if found_in_db:
            try:
                removed = wishlist_service.mark_track_download_result(spotify_track_id, success=True)
                if removed:
                    cleanup_removed += 1
                    logger.info(f"{log_prefix} Removed already-owned track: '{track_name}' by {matched_artist_name or artist_name}")
            except Exception as remove_error:
                logger.error(f"{log_prefix} Error removing track from wishlist: {remove_error}")

    return cleanup_removed


@dataclass
class WishlistManualDownloadRuntime:
    """Dependencies needed to start a manual wishlist download batch outside the controller."""

    get_music_database: Callable[[], Any]
    download_batches: Dict[str, Dict[str, Any]]
    tasks_lock: Any
    missing_download_executor: Any
    run_full_missing_tracks_process: Callable[[str, str, list[dict[str, Any]]], Any]
    get_batch_max_concurrent: Callable[[], int]
    add_activity_item: Callable[[Any, Any, Any, Any], Any]
    active_server: str
    profile_id: int
    logger: Any = module_logger


def start_manual_wishlist_download_batch(
    runtime: WishlistManualDownloadRuntime,
    *,
    track_ids=None,
    category: str | None = None,
    force_download_all: bool = False,
) -> tuple[Dict[str, Any], int]:
    """Submit a manual wishlist batch.

    The batch entry is created synchronously so the frontend can start polling
    status immediately. The slow library-cleanup pass and master-worker hand-off
    run in the background, freeing the request handler from a 30s+ block on
    per-track DB checks for large wishlists.
    """
    logger = runtime.logger

    try:
        batch_id = str(uuid.uuid4())
        playlist_id = "wishlist"
        playlist_name = "Wishlist"

        with runtime.tasks_lock:
            runtime.download_batches[batch_id] = {
                'phase': 'analysis',
                'playlist_id': playlist_id,
                'playlist_name': playlist_name,
                'queue': [],
                'active_count': 0,
                'max_concurrent': runtime.get_batch_max_concurrent(),
                'queue_index': 0,
                # analysis_total starts at 0; the bg job updates it after cleanup
                # finishes and the real track count is known.
                'analysis_total': 0,
                'analysis_processed': 0,
                'analysis_results': [],
                'permanently_failed_tracks': [],
                'cancelled_tracks': set(),
                'force_download_all': True,
                'profile_id': runtime.profile_id,
            }

        runtime.missing_download_executor.submit(
            _prepare_and_run_manual_wishlist_batch,
            runtime,
            batch_id,
            track_ids,
            category,
        )

        return {"success": True, "batch_id": batch_id}, 200

    except Exception as e:
        logger.error(f"Error starting wishlist download process: {e}")
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e)}, 500


def _prepare_and_run_manual_wishlist_batch(
    runtime: WishlistManualDownloadRuntime,
    batch_id: str,
    track_ids,
    category: str | None,
) -> None:
    """Background worker for the manual wishlist batch — does the slow cleanup
    + sanitize + filter + master-worker hand-off off the request thread."""
    logger = runtime.logger

    try:
        wishlist_service = get_wishlist_service()
        db = runtime.get_music_database()
        manual_profile_id = runtime.profile_id

        logger.warning("[Manual-Wishlist] Cleaning duplicate tracks before download...")
        duplicates_removed = db.remove_wishlist_duplicates(profile_id=manual_profile_id)
        if duplicates_removed > 0:
            logger.warning(f"[Manual-Wishlist] Removed {duplicates_removed} duplicate tracks")

        # NOTE: We deliberately do NOT call remove_tracks_already_in_library here.
        # Wishlist tracks are already known-missing (force_download_all=True is set on
        # the batch). The library check duplicates the work the master worker would
        # skip, and on large wishlists costs ~1s per track in serial DB lookups.
        # The standalone /api/wishlist/cleanup endpoint still runs that pass when
        # users explicitly ask for maintenance.

        raw_wishlist_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=manual_profile_id)
        if not raw_wishlist_tracks:
            logger.warning("[Manual-Wishlist] No tracks in wishlist after cleanup — marking batch complete")
            with runtime.tasks_lock:
                if batch_id in runtime.download_batches:
                    runtime.download_batches[batch_id]['phase'] = 'complete'
                    runtime.download_batches[batch_id]['error'] = 'No tracks in wishlist'
            return

        wishlist_tracks, duplicates_found = sanitize_and_dedupe_wishlist_tracks(raw_wishlist_tracks)
        if duplicates_found > 0:
            logger.warning(f"[Manual-Wishlist] Found and removed {duplicates_found} duplicate tracks during sanitization")
        logger.info(f"[Manual-Wishlist] Sanitized {len(wishlist_tracks)} tracks from wishlist service")

        if track_ids:
            track_lookup = {}
            for track in wishlist_tracks:
                spotify_track_id = track.get('spotify_track_id') or track.get('id')
                if spotify_track_id and spotify_track_id not in track_lookup:
                    track_lookup[spotify_track_id] = track

            filtered_tracks = []
            seen_track_ids = set()
            for frontend_index, tid in enumerate(track_ids):
                if tid in track_lookup and tid not in seen_track_ids:
                    track = track_lookup[tid]
                    track['_original_index'] = frontend_index
                    filtered_tracks.append(track)
                    seen_track_ids.add(tid)

            wishlist_tracks = filtered_tracks
            logger.info(f"[Manual-Wishlist] Filtered to {len(wishlist_tracks)} specific tracks by ID (preserving frontend display order)")
        elif category:
            wishlist_tracks, _ = filter_wishlist_tracks_by_category(wishlist_tracks, category)
            logger.info(f"[Manual-Wishlist] Filtered to {len(wishlist_tracks)} tracks for category: {category}")

        for i, track in enumerate(wishlist_tracks):
            track['_original_index'] = i

        # Update batch with the real track count now that filtering is done
        with runtime.tasks_lock:
            if batch_id in runtime.download_batches:
                runtime.download_batches[batch_id]['analysis_total'] = len(wishlist_tracks)

        runtime.add_activity_item("", "Wishlist Download Started", f"{len(wishlist_tracks)} tracks", "Now")

        logger.info(f"Starting wishlist batch {batch_id} with {len(wishlist_tracks)} tracks")
        runtime.run_full_missing_tracks_process(batch_id, "wishlist", wishlist_tracks)

    except Exception as exc:
        logger.error(f"Error preparing manual wishlist batch {batch_id}: {exc}")
        import traceback

        traceback.print_exc()
        with runtime.tasks_lock:
            if batch_id in runtime.download_batches:
                runtime.download_batches[batch_id]['phase'] = 'error'
                runtime.download_batches[batch_id]['error'] = str(exc)


def cleanup_wishlist_against_library(
    wishlist_service,
    music_database,
    profile_id: int,
    active_server: str,
    *,
    logger=logger,
) -> tuple[Dict[str, Any], int]:
    """Remove wishlist tracks that already exist in the library for one profile."""
    try:
        logger.info("[Wishlist Cleanup] Starting wishlist cleanup process...")

        wishlist_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=profile_id)
        if not wishlist_tracks:
            return {"success": True, "message": "No tracks in wishlist to clean up", "removed_count": 0}, 200

        logger.info(f"[Wishlist Cleanup] Found {len(wishlist_tracks)} tracks in wishlist")

        removed_count = remove_tracks_already_in_library(
            wishlist_service,
            SimpleNamespace(get_all_profiles=lambda: [{"id": profile_id}]),
            music_database,
            active_server,
            logger=logger,
            log_prefix="[Wishlist Cleanup]",
        )

        logger.info(f"[Wishlist Cleanup] Completed cleanup: {removed_count} tracks removed from wishlist")
        return {
            "success": True,
            "message": f"Wishlist cleanup completed: {removed_count} tracks removed",
            "removed_count": removed_count,
            "processed_count": len(wishlist_tracks),
        }, 200

    except Exception as e:
        logger.error(f"Error in wishlist cleanup: {e}")
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e)}, 500


def process_wishlist_automatically(runtime: WishlistAutoProcessingRuntime, automation_id=None):
    """Run automatic wishlist processing outside the controller."""
    logger = runtime.logger
    logger.info("[Auto-Wishlist] Timer triggered - starting automatic wishlist processing...")

    try:
        # CRITICAL FIX: Use smart stuck detection BEFORE acquiring lock
        # This prevents deadlock and handles stuck flags (2-hour timeout)
        if runtime.is_actually_processing():
            logger.info("[Auto-Wishlist] Already processing (verified with stuck detection), skipping.")
            return

        with runtime.processing_guard() as acquired:
            if not acquired:
                logger.info("[Auto-Wishlist] Already processing (race condition check), skipping.")
                return

            with runtime.app_context_factory():
                wishlist_service = get_wishlist_service()

                # Check if wishlist has tracks across all profiles
                database = runtime.get_profiles_database()
                all_profiles = database.get_all_profiles()
                count = sum(wishlist_service.get_wishlist_count(profile_id=p['id']) for p in all_profiles)
                logger.info(f"[Auto-Wishlist] Wishlist count check: {count} tracks found across {len(all_profiles)} profiles")
                runtime.update_automation_progress(automation_id, progress=10, phase='Checking wishlist',
                                                   log_line=f'{count} tracks across {len(all_profiles)} profiles', log_type='info')
                if count == 0:
                    logger.warning("ℹ️ [Auto-Wishlist] No tracks in wishlist for auto-processing.")
                    return

                logger.info(f"[Auto-Wishlist] Found {count} tracks in wishlist, starting automatic processing...")

                # Check if wishlist processing is already active (auto or manual)
                playlist_id = "wishlist"
                with runtime.tasks_lock:
                    for _batch_id, batch_data in runtime.download_batches.items():
                        batch_playlist_id = batch_data.get('playlist_id')
                        # Check for both auto ('wishlist') and manual ('wishlist_manual') batches
                        if (batch_playlist_id in ['wishlist', 'wishlist_manual'] and
                            batch_data.get('phase') not in ['complete', 'error', 'cancelled']):
                            logger.info(f"Wishlist processing already active in another batch ({batch_playlist_id}), skipping automatic start")
                            return

                # CRITICAL: Clean duplicates BEFORE fetching tracks to prevent count mismatches
                # This prevents the "11 tracks shown but 12 counted" bug
                music_database = runtime.get_music_database()

                logger.warning("[Auto-Wishlist] Cleaning duplicate tracks before processing...")
                for profile in all_profiles:
                    duplicates_removed = music_database.remove_wishlist_duplicates(profile_id=profile['id'])
                    if duplicates_removed > 0:
                        logger.warning(f"[Auto-Wishlist] Removed {duplicates_removed} duplicate tracks from profile {profile['id']}")

                # NOTE: We deliberately do NOT call remove_tracks_already_in_library here.
                # The batch sets force_download_all=True (see comment a few lines below),
                # so wishlist tracks are treated as known-missing and the master worker
                # skips per-track library lookups. Doing the same expensive scan here
                # before submitting the batch defeats that optimization and adds
                # ~1s per track in serial DB queries. The standalone
                # /api/wishlist/cleanup endpoint still exposes that pass for users
                # who want explicit maintenance.
                runtime.update_automation_progress(automation_id, progress=25, phase='Preparing wishlist',
                                                   log_line='Skipped library scan — wishlist tracks treated as known-missing',
                                                   log_type='info')

                # Get wishlist tracks for processing - combine all profiles
                raw_wishlist_tracks = []
                for profile in all_profiles:
                    raw_wishlist_tracks.extend(wishlist_service.get_wishlist_tracks_for_download(profile_id=profile['id']))
                if not raw_wishlist_tracks:
                    logger.warning("No tracks returned from wishlist service.")
                    return

                # SANITIZE: Ensure consistent data format from wishlist service
                wishlist_tracks, duplicates_found = sanitize_and_dedupe_wishlist_tracks(raw_wishlist_tracks)
                if duplicates_found > 0:
                    logger.warning(f"[Auto-Wishlist] Found and removed {duplicates_found} duplicate tracks during sanitization")
                logger.info(f"[Auto-Wishlist] Sanitized {len(wishlist_tracks)} tracks from wishlist service")

                # CYCLE FILTERING: Get current cycle and filter tracks by category
                current_cycle = get_wishlist_cycle(lambda: music_database)

                # Filter tracks by current cycle category
                filtered_tracks, _ = filter_wishlist_tracks_by_category(wishlist_tracks, current_cycle)

                logger.info(f"[Auto-Wishlist] Current cycle: {current_cycle}")
                logger.info(f"[Auto-Wishlist] Filtered {len(filtered_tracks)}/{len(wishlist_tracks)} tracks for '{current_cycle}' category")
                runtime.update_automation_progress(automation_id, progress=40, phase=f'Processing {current_cycle}',
                                                   log_line=f'Cycle: {current_cycle} — {len(filtered_tracks)} tracks to process', log_type='info')

                # If no tracks in this category, skip to next cycle immediately
                if len(filtered_tracks) == 0:
                    logger.warning(f"ℹ️ [Auto-Wishlist] No {current_cycle} tracks in wishlist, toggling cycle and scheduling next run")

                    # Toggle cycle
                    next_cycle = 'singles' if current_cycle == 'albums' else 'albums'
                    set_wishlist_cycle(lambda: music_database, next_cycle)
                    logger.info(f"[Auto-Wishlist] Cycle toggled: {current_cycle} → {next_cycle}")
                    return

                # Use filtered tracks for processing — stamp original index
                wishlist_tracks = filtered_tracks
                for i, track in enumerate(wishlist_tracks):
                    track['_original_index'] = i

                # Create batch for automatic processing
                batch_id = str(uuid.uuid4())
                playlist_name = f"Wishlist (Auto - {current_cycle.capitalize()})"

                # Create task queue - convert wishlist tracks to expected format
                with runtime.tasks_lock:
                    runtime.download_batches[batch_id] = {
                        'phase': 'analysis',
                        'playlist_id': playlist_id,
                        'playlist_name': playlist_name,
                        'queue': [],
                        'active_count': 0,
                        'max_concurrent': runtime.get_batch_max_concurrent(),  # Wishlist always does single-track downloads, not folder grabs
                        'queue_index': 0,
                        'analysis_total': len(wishlist_tracks),
                        'analysis_processed': 0,
                        'analysis_results': [],
                        # Track state management (replicating sync.py)
                        'permanently_failed_tracks': [],
                        'cancelled_tracks': set(),
                        # Wishlist tracks are already known-missing — skip the expensive library check
                        'force_download_all': True,
                        # Mark as auto-initiated
                        'auto_initiated': True,
                        'auto_processing_timestamp': runtime.current_time_fn(),
                        # Store current cycle for toggling after completion
                        'current_cycle': current_cycle,
                        # Profile context for failed track wishlist re-adds (auto = profile 1 default)
                        'profile_id': runtime.profile_id,
                    }

                logger.info(f"Starting automatic wishlist batch {batch_id} with {len(wishlist_tracks)} tracks")
                runtime.update_automation_progress(automation_id, progress=50, phase=f'Downloading {len(wishlist_tracks)} tracks',
                                                   log_line=f'Started batch: {len(wishlist_tracks)} {current_cycle}', log_type='success')

                # Submit the wishlist processing job using existing infrastructure
                runtime.missing_download_executor.submit(runtime.run_full_missing_tracks_process, batch_id, playlist_id, wishlist_tracks)

                # Don't mark auto_processing as False here - let completion handler do it

    except Exception as e:
        logger.error(f"Error in automatic wishlist processing: {e}")
        import traceback

        traceback.print_exc()
        runtime.update_automation_progress(automation_id, log_line=f'Error: {str(e)}', log_type='error')
        raise


def automatic_wishlist_cleanup_after_db_update(
    *,
    wishlist_service=None,
    profiles_database=None,
    music_database=None,
    active_server: str | None = None,
    logger=logger,
) -> int:
    """Remove wishlist entries that already exist in the library after a DB update."""
    try:
        from config.settings import config_manager
        from database.music_database import MusicDatabase, get_database

        wishlist_service = wishlist_service or get_wishlist_service()
        profiles_database = profiles_database or get_database()
        music_database = music_database or MusicDatabase()
        active_server = active_server or config_manager.get_active_media_server()

        logger.info("[Auto Cleanup] Starting automatic wishlist cleanup after database update...")

        all_profiles = profiles_database.get_all_profiles()
        wishlist_tracks = []
        for profile in all_profiles:
            wishlist_tracks.extend(wishlist_service.get_wishlist_tracks_for_download(profile_id=profile["id"]))

        if not wishlist_tracks:
            logger.warning("[Auto Cleanup] No tracks in wishlist to clean up")
            return 0

        logger.info(f"[Auto Cleanup] Found {len(wishlist_tracks)} tracks in wishlist")

        removed_count = 0
        for track in wishlist_tracks:
            track_name = track.get('name', '')
            artists = track.get('artists', [])
            spotify_track_id = track.get('spotify_track_id') or track.get('id')
            track_album = track.get('album', {}).get('name') if isinstance(track.get('album'), dict) else track.get('album')

            if not track_name or not artists or not spotify_track_id:
                continue

            found_in_db = False
            for artist in artists:
                if isinstance(artist, str):
                    artist_name = artist
                elif isinstance(artist, dict) and 'name' in artist:
                    artist_name = artist['name']
                else:
                    artist_name = str(artist)

                try:
                    db_track, confidence = music_database.check_track_exists(
                        track_name,
                        artist_name,
                        confidence_threshold=0.7,
                        server_source=active_server,
                        album=track_album,
                    )

                    if db_track and confidence >= 0.7:
                        found_in_db = True
                        logger.info(
                            f"[Auto Cleanup] Track found in database: '{track_name}' by {artist_name} (confidence: {confidence:.2f})"
                        )
                        break
                except Exception as db_error:
                    logger.error(f"[Auto Cleanup] Error checking database for track '{track_name}': {db_error}")
                    continue

            if found_in_db:
                try:
                    removed = wishlist_service.mark_track_download_result(spotify_track_id, success=True)
                    if removed:
                        removed_count += 1
                        logger.info(f"[Auto Cleanup] Removed track from wishlist: '{track_name}' ({spotify_track_id})")
                except Exception as remove_error:
                    logger.error(f"[Auto Cleanup] Error removing track from wishlist: {remove_error}")

        logger.info(f"[Auto Cleanup] Completed automatic cleanup: {removed_count} tracks removed from wishlist")
        return removed_count

    except Exception as e:
        logger.error(f"[Auto Cleanup] Error in automatic wishlist cleanup: {e}")
        import traceback

        traceback.print_exc()
        return 0


__all__ = [
    "remove_completed_tracks_from_wishlist",
    "add_cancelled_tracks_to_failed_tracks",
    "recover_uncaptured_failed_tracks",
    "build_wishlist_source_context",
    "finalize_auto_wishlist_completion",
    "automatic_wishlist_cleanup_after_db_update",
    "WishlistAutoProcessingRuntime",
    "WishlistManualDownloadRuntime",
    "process_wishlist_automatically",
    "start_manual_wishlist_download_batch",
    "cleanup_wishlist_against_library",
    "remove_tracks_already_in_library",
]
