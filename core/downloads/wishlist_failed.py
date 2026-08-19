"""Failed-tracks wishlist processing — lifted from web_server.py.

Body is byte-identical to the original. Wishlist helpers are
direct imports from core.wishlist.*; runtime state comes from
core.runtime_state; automation_engine, download_orchestrator, and the
sweep helper are injected via init() because they are constructed
in web_server.py.
"""
from utils.logging_config import get_logger
import time

from core.discovery.wing_it import is_stub_id, should_wishlist_stub
from core.runtime_state import (
    download_batches,
    download_tasks,
    tasks_lock,
)
from core.wishlist.processing import (
    add_cancelled_tracks_to_failed_tracks as _add_cancelled_tracks_to_failed_tracks,
    build_wishlist_source_context as _build_wishlist_source_context,
    record_failed_attempt as _record_failed_attempt,
    recover_uncaptured_failed_tracks as _recover_uncaptured_failed_tracks,
    remove_completed_tracks_from_wishlist as _remove_completed_tracks_from_wishlist,
    resolve_wishlist_source_type_for_batch as _resolve_wishlist_source_type_for_batch,
)
from core.wishlist.resolution import (
    check_and_remove_from_wishlist as _check_and_remove_from_wishlist,
)
from utils.async_helpers import run_async

logger = get_logger("downloads.wishlist_failed")

# Injected at runtime via init().
automation_engine = None
download_orchestrator = None
_sweep_empty_download_directories = None


def init(engine, download_orchestrator_obj, sweep_fn):
    """Bind shared singletons + the sweep helper from web_server."""
    global automation_engine, download_orchestrator, _sweep_empty_download_directories
    automation_engine = engine
    download_orchestrator = download_orchestrator_obj
    _sweep_empty_download_directories = sweep_fn


def _process_failed_tracks_to_wishlist_exact(batch_id):
    """
    Process failed and cancelled tracks to wishlist - EXACT replication of sync.py's on_all_downloads_complete() logic.
    This matches sync.py's behavior precisely.
    """
    try:
        from core.wishlist_service import get_wishlist_service
        
        logger.info(f"[Wishlist Processing] Starting wishlist processing for batch {batch_id}")

        with tasks_lock:
            if batch_id not in download_batches:
                logger.warning(f"[Wishlist Processing] Batch {batch_id} not found")
                return {'tracks_added': 0, 'errors': 0}

        batch = download_batches[batch_id]

        # Wing It mode used to skip wishlist entirely here. Now the per-track
        # is_stub_id()/should_wishlist_stub() gate below decides: a searchable
        # stub is wishlisted when wishlist.wing_it_guesses is on, everything
        # else (non-stub failures, non-searchable stubs) behaves as before.
        permanently_failed_tracks = batch.get('permanently_failed_tracks', [])
        cancelled_tracks = batch.get('cancelled_tracks', set())
        
        # STEP 0: Remove completed tracks from wishlist (THIS WAS MISSING!)
        logger.info("[Wishlist Processing] Checking completed tracks for wishlist removal")
        _remove_completed_tracks_from_wishlist(
            batch,
            download_tasks,
            _check_and_remove_from_wishlist,
        )
        
        # STEP 1: Add cancelled tracks that were missing to permanently_failed_tracks (replicating sync.py)
        # This matches sync.py's logic for adding cancelled missing tracks to the failed list
        if cancelled_tracks:
            logger.warning(f"[Wishlist Processing] Processing {len(cancelled_tracks)} cancelled tracks")
            processed_count = _add_cancelled_tracks_to_failed_tracks(
                batch,
                download_tasks,
                permanently_failed_tracks,
            )
            logger.warning(f"[Wishlist Processing] Processed {processed_count} cancelled tracks")

        # STEP 1.5: Recover any failed/not_found tasks not captured in permanently_failed_tracks.
        # Stuck detection (in _on_download_completed, _check_batch_completion_v2, and the Safety Valve)
        # can force-mark tasks as not_found/failed without adding them to permanently_failed_tracks,
        # causing them to silently skip wishlist processing.
        recovered_count = _recover_uncaptured_failed_tracks(
            batch,
            download_tasks,
            permanently_failed_tracks,
        )
        if recovered_count:
            logger.warning(f"[Wishlist Processing] Recovered {recovered_count} uncaptured failed tracks for wishlist")

        # STEP 2: Add permanently failed tracks to wishlist (exact sync.py logic).
        # A Library-v2 track-level Automatic Search is a transient one-shot
        # pipeline input, not monitoring state. Its batch explicitly disables
        # this replay so a failed unmonitored search cannot materialize itself
        # in the Wishlist after the fact.
        failed_count = len(permanently_failed_tracks)
        wishlist_added_count = 0
        error_count = 0
        requeue_failed = batch.get('requeue_failed_to_wishlist', True) is not False
        
        logger.error(f"[Wishlist Processing] Processing {failed_count} failed tracks for wishlist")
        
        if permanently_failed_tracks and requeue_failed:
            try:
                wishlist_service = get_wishlist_service()

                # Create source_context identical to sync.py
                source_context = _build_wishlist_source_context(batch)
                batch_source_type = _resolve_wishlist_source_type_for_batch(batch)

                # Process each failed track (matching sync.py's loop) with safety limit
                max_failed_tracks = min(len(permanently_failed_tracks), 50)  # Safety limit
                wing_it_skipped = 0
                for i, failed_track_info in enumerate(permanently_failed_tracks[:max_failed_tracks]):
                    try:
                        track_name = failed_track_info.get('track_name', f'Track {i+1}')

                        # Wing-it stubs had no catalogue match, so re-adding them just
                        # retries the same raw data — unless wishlist.wing_it_guesses is
                        # on, which is exactly the choice to search the guess anyway.
                        # Check the track ID prefix since the wishlist payload helper overwrites source.
                        track_data = failed_track_info.get('track_data') or failed_track_info.get('spotify_track', {})
                        sp_id = track_data.get('id', '') if isinstance(track_data, dict) else ''
                        _artists = track_data.get('artists') if isinstance(track_data, dict) else None
                        _artist = (_artists or [None])[0] if isinstance(_artists, list) else _artists
                        if isinstance(_artist, dict):
                            _artist = _artist.get('name')
                        # Use the source's own title for the predicate, not track_name's
                        # "Track {i+1}" logging fallback — that placeholder isn't a
                        # placeholder should_wishlist_stub recognizes, so it would read
                        # as a real title and let a nameless stub through.
                        _source_title = track_data.get('name', '') if isinstance(track_data, dict) else ''
                        if is_stub_id(sp_id) and not should_wishlist_stub(_artist, _source_title):
                            wing_it_skipped += 1
                            logger.info(f"[Wishlist Processing] Skipping wing-it track: {track_name}")
                            continue

                        logger.error(f"[Wishlist Processing] Adding track {i+1}/{max_failed_tracks}: {track_name}")

                        success = wishlist_service.add_failed_track_from_modal(
                            track_info=failed_track_info,
                            source_type=batch_source_type,
                            source_context=source_context,
                            profile_id=batch.get('profile_id', 1)
                        )
                        # Count the attempt EITHER way — a fresh add becomes
                        # attempt 1; a duplicate-skip (track already wishlisted,
                        # i.e. it failed again this cycle) accumulates. This is
                        # what feeds the failing badge + retry backoff.
                        _record_failed_attempt(
                            wishlist_service, track_data,
                            failed_track_info.get('failure_reason', ''),
                            batch.get('profile_id', 1))

                        if success:
                            wishlist_added_count += 1
                            logger.info(f"[Wishlist Processing] Added {track_name} to wishlist")
                            try:
                                if automation_engine:
                                    automation_engine.emit('wishlist_item_added', {
                                        'artist': failed_track_info.get('artist_name', ''),
                                        'title': track_name,
                                        'reason': failed_track_info.get('failure_reason', ''),
                                    })
                            except Exception as e:
                                logger.debug("emit wishlist_item_added failed: %s", e)
                        else:
                            logger.error(f"[Wishlist Processing] Failed to add {track_name} to wishlist")
                            
                    except Exception as e:
                        error_count += 1
                        logger.error(f"[Wishlist Processing] Exception adding track to wishlist: {e}")
                
                if wing_it_skipped:
                    logger.warning(f"[Wishlist Processing] Skipped {wing_it_skipped} wing-it fallback tracks")
                logger.error(f"[Wishlist Processing] Added {wishlist_added_count}/{failed_count} failed tracks to wishlist (errors: {error_count})")
                        
            except Exception as e:
                error_count = len(permanently_failed_tracks)
                logger.error(f"[Wishlist Processing] Critical error adding failed tracks to wishlist: {e}")
                import traceback
                traceback.print_exc()
        elif not permanently_failed_tracks:
            logger.error("ℹ️ [Wishlist Processing] No failed tracks to add to wishlist")
        else:
            logger.info(
                "[Wishlist Processing] Keeping %d transient search failure(s) out of Wishlist",
                failed_count,
            )
        
        # Store completion summary in batch for API response (matching sync.py pattern)
        completion_summary = {
            'tracks_added': wishlist_added_count,
            'errors': error_count,
            'total_failed': failed_count
        }
        
        with tasks_lock:
            if batch_id in download_batches:
                download_batches[batch_id]['wishlist_summary'] = completion_summary
                download_batches[batch_id]['wishlist_processing_complete'] = True
                # Phase already set to 'complete' in _on_download_completed

        logger.info(f"[Wishlist Processing] Completed wishlist processing for batch {batch_id}")

        # Auto-cleanup: Clear completed downloads from slskd
        try:
            logger.info(f"[Auto-Cleanup] Clearing completed downloads from slskd after batch {batch_id}")
            run_async(download_orchestrator.clear_all_completed_downloads())
            logger.info("[Auto-Cleanup] Completed downloads cleared from slskd")
        except Exception as cleanup_error:
            logger.warning(f"[Auto-Cleanup] Failed to clear completed downloads: {cleanup_error}")

        # Sweep empty directories left behind by this batch's downloads
        try:
            _sweep_empty_download_directories()
        except Exception as sweep_error:
            logger.warning(f"[Auto-Cleanup] Failed to sweep empty directories: {sweep_error}")

        return completion_summary
    
    except Exception as e:
        logger.error(f"[Wishlist Processing] CRITICAL ERROR in wishlist processing: {e}")
        import traceback
        traceback.print_exc()
        
        # Mark batch as complete even with errors to prevent infinite loops
        try:
            with tasks_lock:
                if batch_id in download_batches:
                    download_batches[batch_id]['phase'] = 'complete'
                    download_batches[batch_id]['completion_time'] = time.time()  # Track for auto-cleanup
                    download_batches[batch_id]['wishlist_summary'] = {
                        'tracks_added': 0,
                        'errors': 1,
                        'total_failed': 0,
                        'error_message': str(e)
                    }
                    download_batches[batch_id]['wishlist_processing_complete'] = True
        except Exception as lock_error:
            logger.error(f"[Wishlist Processing] Failed to update batch after error: {lock_error}")
        
        return {'tracks_added': 0, 'errors': 1, 'total_failed': 0}
