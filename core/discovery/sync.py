"""Background worker for the playlist sync task.

`run_sync_task(playlist_id, playlist_name, tracks_json, automation_id, profile_id,
playlist_image_url, deps)` is the function `sync_executor.submit(...)` invokes
to drive the entire playlist-sync workflow:

1. Convert frontend JSON tracks → SpotifyTrack/SpotifyPlaylist objects.
2. Normalize artist/album shapes for downstream wishlist parity.
3. Wire a progress_callback that updates `sync_states` + automation card.
4. Patch sync_service for database-only fallback when no media server is connected.
5. `run_async(sync_service.sync_playlist(...))` and capture the result.
6. Update sync_states to 'finished', push playlist poster image to Plex/Jellyfin/Emby,
   record sync history (with re-sync vs new-sync branching), emit
   `playlist_synced` event for automation engine, and persist sync status with a
   tracks_hash for smart-skip on the next scheduled sync.
7. On exception → mark error in sync_states + automation; finally clear progress
   callback + drop `_original_tracks_map` from sync_service.

Lifted verbatim from web_server.py. Wide dependency surface (sync_service,
sync_states, plex/jellyfin clients, automation engine, multiple helper funcs)
all injected via `SyncDeps`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from core.spotify_client import Playlist as SpotifyPlaylist, Track as SpotifyTrack

logger = logging.getLogger(__name__)


@dataclass
class SyncDeps:
    """Bundle of cross-cutting deps the sync worker needs."""
    config_manager: Any
    sync_service: Any
    media_server_engine: Any
    automation_engine: Any
    run_async: Callable[..., Any]
    record_sync_history_start: Callable
    update_automation_progress: Callable
    update_and_save_sync_status: Callable
    sync_states: dict
    sync_lock: Any  # threading.Lock


def run_sync_task(playlist_id, playlist_name, tracks_json, automation_id=None, profile_id=1, playlist_image_url='', deps: SyncDeps = None, sync_mode: str = 'replace'):
    """The actual sync function that runs in the background thread."""
    sync_states = deps.sync_states
    sync_lock = deps.sync_lock
    sync_service = deps.sync_service

    task_start_time = time.time()
    logger.info(f"[TIMING] _run_sync_task STARTED for playlist '{playlist_name}' at {time.strftime('%H:%M:%S')}")
    logger.info(f"Received {len(tracks_json)} tracks from frontend")

    # Record sync history start (skip for re-syncs triggered from history)
    _is_resync = playlist_id.startswith('resync_')
    _resync_entry_id = None
    sync_batch_id = f"sync_{playlist_id}_{int(time.time())}"
    if _is_resync:
        # Extract the original entry ID from resync_{entryId}_{timestamp}
        try:
            _resync_entry_id = int(playlist_id.split('_')[1])
        except (IndexError, ValueError):
            pass
    else:
        deps.record_sync_history_start(
            batch_id=sync_batch_id,
            playlist_id=playlist_id,
            playlist_name=playlist_name,
            tracks=tracks_json,
            is_album_download=False,
            album_context=None,
            artist_context=None,
            playlist_folder_mode=False,
            source_page='sync'
        )

    try:
        # Recreate a Playlist object from the JSON data sent by the frontend
        # This avoids needing to re-fetch it from Spotify
        logger.info("Converting JSON tracks to SpotifyTrack objects...")

        # Store original track data with full album objects (for wishlist with cover art)
        # Normalize formats for wishlist: album must be dict {'name': ...}, artists must be [{'name': ...}]
        # Important: copy data — don't mutate tracks_json since SpotifyTrack expects List[str] artists
        original_tracks_map = {}
        for t in tracks_json:
            track_id = t.get('id', '')
            if track_id:
                normalized = dict(t)
                # Normalize album to dict format, preserving images and metadata
                raw_album = normalized.get('album', '')
                if isinstance(raw_album, str):
                    normalized['album'] = {
                        'name': raw_album or normalized.get('name', 'Unknown Album'),
                        'images': [], 'album_type': 'single', 'total_tracks': 1, 'release_date': ''
                    }
                elif not isinstance(raw_album, dict):
                    normalized['album'] = {
                        'name': str(raw_album) if raw_album else normalized.get('name', 'Unknown Album'),
                        'images': [], 'album_type': 'single', 'total_tracks': 1, 'release_date': ''
                    }
                else:
                    # Dict — ensure required keys exist
                    raw_album.setdefault('name', 'Unknown Album')
                    raw_album.setdefault('images', [])
                # Normalize artists to list of dicts
                raw_artists = normalized.get('artists', [])
                if raw_artists and isinstance(raw_artists[0], str):
                    normalized['artists'] = [{'name': a} for a in raw_artists]
                original_tracks_map[track_id] = normalized

        tracks = []
        for i, t in enumerate(tracks_json):
            # Handle album field - extract name if it's a dictionary
            raw_album = t.get('album', '')
            if isinstance(raw_album, dict) and 'name' in raw_album:
                album_name = raw_album['name']
            elif isinstance(raw_album, str):
                album_name = raw_album
            else:
                album_name = str(raw_album)
            
            # Extract image URL from album data if available
            _track_image = ''
            if isinstance(raw_album, dict):
                _imgs = raw_album.get('images', [])
                if _imgs and isinstance(_imgs, list) and len(_imgs) > 0:
                    _track_image = _imgs[0].get('url', '') if isinstance(_imgs[0], dict) else ''
            if not _track_image:
                _track_image = t.get('image_url', '')

            # Create SpotifyTrack objects with proper default values for missing fields
            track = SpotifyTrack(
                id=t.get('id', ''),
                name=t.get('name', ''),
                artists=t.get('artists', []),
                album=album_name,
                duration_ms=t.get('duration_ms', 0),
                popularity=t.get('popularity', 0),
                preview_url=t.get('preview_url'),
                external_urls=t.get('external_urls'),
                image_url=_track_image or None
            )
            tracks.append(track)
            if i < 3:  # Log first 3 tracks for debugging
                logger.info(f"  Track {i+1}: '{track.name}' by {track.artists}")
        
        logger.info(f"Created {len(tracks)} SpotifyTrack objects")
        
        playlist = SpotifyPlaylist(
            id=playlist_id, 
            name=playlist_name, 
            description=None,  # Not needed for sync
            owner="web_user",  # Placeholder  
            public=False,      # Default
            collaborative=False,  # Default
            tracks=tracks, 
            total_tracks=len(tracks)
        )
        logger.info(f"Created SpotifyPlaylist object: '{playlist.name}' with {playlist.total_tracks} tracks")

        first_callback_time = [None]  # Use list to allow modification in nested function
        
        def progress_callback(progress):
            """Callback to update the shared state."""
            if first_callback_time[0] is None:
                first_callback_time[0] = time.time()
                first_callback_duration = (first_callback_time[0] - task_start_time) * 1000
                logger.info(f"⏱️ [TIMING] FIRST progress callback at {time.strftime('%H:%M:%S')} (took {first_callback_duration:.1f}ms from start)")

            logger.info(f"PROGRESS CALLBACK: {progress.current_step} - {progress.current_track}")
            logger.error(f"   Progress: {progress.progress}% ({progress.matched_tracks}/{progress.total_tracks} matched, {progress.failed_tracks} failed)")

            with sync_lock:
                sync_states[playlist_id] = {
                    "status": "syncing",
                    "progress": progress.__dict__ # Convert dataclass to dict
                }
                logger.info(f"   Updated sync_states for {playlist_id}")

            # Update automation progress card
            if automation_id:
                step = getattr(progress, 'current_step', '')
                track = getattr(progress, 'current_track', '')
                pct = getattr(progress, 'progress', 0)
                matched = getattr(progress, 'matched_tracks', 0)
                failed = getattr(progress, 'failed_tracks', 0)
                total = getattr(progress, 'total_tracks', 0)
                log_type = 'success' if 'matched' in step.lower() or 'found' in step.lower() else 'info'
                if 'not found' in step.lower() or 'failed' in step.lower():
                    log_type = 'error'
                deps.update_automation_progress(automation_id, progress=pct,
                    phase=f'Syncing: {step}',
                    processed=matched + failed, total=total,
                    current_item=track,
                    log_line=f'{track} — {step}' if track else step, log_type=log_type)
                
    except Exception as setup_error:
        logger.error(f"SETUP ERROR in _run_sync_task: {setup_error}")
        import traceback
        traceback.print_exc()
        with sync_lock:
            sync_states[playlist_id] = {
                "status": "error",
                "error": f"Setup error: {str(setup_error)}"
            }
        if automation_id:
            deps.update_automation_progress(automation_id, status='error', progress=100,
                phase='Error', log_line=f'Setup error: {str(setup_error)}', log_type='error')
        return

    try:
        logger.info("Setting up sync service...")
        logger.info(f"   sync_service available: {sync_service is not None}")
        
        if sync_service is None:
            raise Exception("sync_service is None - not initialized properly")
            
        # Check sync service components
        logger.info(f"   spotify_client: {sync_service.spotify_client is not None}")
        _ms_engine = getattr(sync_service, '_engine', None)
        logger.info(f"   plex_client: {(_ms_engine.client('plex') if _ms_engine else None) is not None}")
        logger.info(f"   jellyfin_client: {(_ms_engine.client('jellyfin') if _ms_engine else None) is not None}")
        
        # Check media server connection before starting
        from config.settings import config_manager
        active_server = config_manager.get_active_media_server()
        logger.info(f"   Active media server: {active_server}")
        
        media_client, server_type = sync_service._get_active_media_client()
        logger.info(f"   Media client available: {media_client is not None}")
        
        if media_client:
            is_connected = media_client.is_connected()
            logger.info(f"   Media client connected: {is_connected}")
        
        # Check database access
        try:
            from database.music_database import MusicDatabase
            db = MusicDatabase()
            logger.debug(f"   Database initialized: {db is not None}")
        except Exception as db_error:
            logger.error(f"   Database initialization failed: {db_error}")
        
        logger.info("Attaching progress callback...")
        # Attach the progress callback
        sync_service.set_progress_callback(progress_callback, playlist.name)
        logger.info(f"Progress callback attached for playlist: {playlist.name}")

        # CRITICAL FIX: Add database-only fallback for web context
        # If media client is not connected, patch the sync service to use database-only matching
        if media_client is None or not media_client.is_connected():
            logger.info("Media client not connected - patching sync service for database-only matching")
            
            # Store original method
            original_find_track = sync_service._find_track_in_media_server
            
            # Create database-only replacement method
            async def database_only_find_track(spotify_track):
                logger.info(f"Database-only search for: '{spotify_track.name}' by {spotify_track.artists}")
                try:
                    from database.music_database import MusicDatabase
                    from config.settings import config_manager

                    db = MusicDatabase()
                    active_server = config_manager.get_active_media_server()
                    original_title = spotify_track.name
                    spotify_id = getattr(spotify_track, 'id', '') or ''

                    # --- Sync match cache fast-path ---
                    if spotify_id:
                        try:
                            cached = db.read_sync_match_cache(spotify_id, active_server)
                            if cached:
                                db_track_check = db.get_track_by_id(cached['server_track_id'])
                                if db_track_check:
                                    class DatabaseTrackCached:
                                        def __init__(self, db_t):
                                            self.ratingKey = db_t.id
                                            self.title = db_t.title
                                            self.id = db_t.id
                                    logger.debug(f"Sync cache hit: '{original_title}' → server track {cached['server_track_id']}")
                                    return DatabaseTrackCached(db_track_check), cached['confidence']
                                logger.warning(f"Sync cache stale for '{original_title}' — track gone")
                        except Exception as e:
                            logger.debug("sync match cache fast-path failed: %s", e)
                    # --- End cache fast-path ---

                    # Try each artist (same logic as original)
                    for artist in spotify_track.artists:
                        # Extract artist name from both string and dict formats
                        if isinstance(artist, str):
                            artist_name = artist
                        elif isinstance(artist, dict) and 'name' in artist:
                            artist_name = artist['name']
                        else:
                            artist_name = str(artist)

                        db_track, confidence = db.check_track_exists(
                            original_title, artist_name,
                            confidence_threshold=0.80,
                            server_source=active_server
                        )

                        if db_track and confidence >= 0.80:
                            logger.info(f"Database match: '{db_track.title}' (confidence: {confidence:.2f})")

                            # Save to sync match cache
                            if spotify_id:
                                try:
                                    from core.matching_engine import MusicMatchingEngine
                                    me = MusicMatchingEngine()
                                    db.save_sync_match_cache(
                                        spotify_id, me.clean_title(original_title), me.clean_artist(artist_name),
                                        active_server, db_track.id, db_track.title, confidence
                                    )
                                except Exception as e:
                                    logger.debug("save sync match cache failed: %s", e)

                            # Create mock track object for playlist creation
                            class DatabaseTrackMock:
                                def __init__(self, db_track):
                                    self.ratingKey = db_track.id
                                    self.title = db_track.title
                                    self.id = db_track.id

                            return DatabaseTrackMock(db_track), confidence

                    logger.warning(f"No database match found for: '{original_title}'")
                    return None, 0.0

                except Exception as e:
                    logger.error(f"Database search error: {e}")
                    return None, 0.0
            
            # Patch the method
            sync_service._find_track_in_media_server = database_only_find_track
            logger.info("Patched sync service to use database-only matching")

        sync_start_time = time.time()
        setup_duration = (sync_start_time - task_start_time) * 1000
        logger.info(f"⏱️ [TIMING] Setup completed at {time.strftime('%H:%M:%S')} (took {setup_duration:.1f}ms)")
        logger.info("Starting actual sync process with run_async()...")

        # Attach original tracks map to sync_service for wishlist with album images
        sync_service._original_tracks_map = original_tracks_map

        # Wing It mode — skip wishlist for unmatched tracks
        with sync_lock:
            is_wing_it = sync_states.get(playlist_id, {}).get('wing_it', False)
        sync_service._skip_wishlist = is_wing_it

        # Run the sync (this is a blocking call within this thread)
        result = deps.run_async(sync_service.sync_playlist(playlist, download_missing=False, profile_id=profile_id, sync_mode=sync_mode))

        # Clear progress callback immediately to prevent race condition where a
        # late-firing progress callback overwrites the "finished" state below
        if sync_service:
            sync_service.clear_progress_callback(playlist.name)

        sync_duration = (time.time() - sync_start_time) * 1000
        total_duration = (time.time() - task_start_time) * 1000
        logger.info(f"⏱️ [TIMING] Sync completed at {time.strftime('%H:%M:%S')} (sync: {sync_duration:.1f}ms, total: {total_duration:.1f}ms)")
        logger.info(f"Sync process completed! Result type: {type(result)}")
        logger.info(f"   Result details: matched={getattr(result, 'matched_tracks', 'N/A')}, total={getattr(result, 'total_tracks', 'N/A')}")

        # Update final state on completion
        # Convert result to JSON-serializable dict (datetime/errors can't be emitted via SocketIO)
        # Exclude match_details (large) but include a summary of unmatched tracks
        result_dict = {
            k: (v.isoformat() if hasattr(v, 'isoformat') else v)
            for k, v in result.__dict__.items()
            if k != 'match_details'
        }
        # Include unmatched track names so the frontend can show which tracks failed
        match_details = getattr(result, 'match_details', None)
        if match_details:
            unmatched_summary = [
                {'name': d.get('name', ''), 'artist': d.get('artist', ''), 'image_url': d.get('image_url', '')}
                for d in match_details if d.get('status') == 'not_found'
            ]
            if unmatched_summary:
                result_dict['unmatched_tracks'] = unmatched_summary
        with sync_lock:
            sync_states[playlist_id] = {
                "status": "finished",
                "progress": result_dict,
                "result": result_dict
            }
        logger.info(f"Sync finished for {playlist_id} - state updated")

        # Set playlist poster image if available (Plex, Jellyfin, Emby)
        _synced = getattr(result, 'synced_tracks', 0)
        logger.info(f"[PLAYLIST IMAGE] image_url={playlist_image_url!r}, synced_tracks={_synced}")
        if playlist_image_url and _synced > 0:
            try:
                active_server = deps.config_manager.get_active_media_server()
                logger.info(f"[PLAYLIST IMAGE] active_server={active_server}")
                _engine = deps.media_server_engine
                if active_server == 'plex' and _engine and _engine.client('plex'):
                    ok = _engine.client('plex').set_playlist_image(playlist_name, playlist_image_url)
                    logger.info(f"[PLAYLIST IMAGE] Plex upload result: {ok}")
                elif active_server in ('jellyfin', 'emby') and _engine and _engine.client('jellyfin'):
                    ok = _engine.client('jellyfin').set_playlist_image(playlist_name, playlist_image_url)
                    logger.info(f"[PLAYLIST IMAGE] Jellyfin upload result: {ok}")
                # Navidrome doesn't support custom playlist images
            except Exception as img_err:
                logger.error(f"[PLAYLIST IMAGE] Exception: {img_err}")

        # Record sync history completion with per-track data
        try:
            matched = getattr(result, 'matched_tracks', 0)
            failed = getattr(result, 'failed_tracks', 0)
            synced = getattr(result, 'synced_tracks', 0)
            db = MusicDatabase()
            target_batch_id = sync_batch_id
            if _is_resync and _resync_entry_id:
                db.refresh_sync_history_entry(_resync_entry_id, matched, synced, failed)
                # For re-sync, get the batch_id from the original entry
                try:
                    entry = db.get_sync_history_entry(_resync_entry_id)
                    if entry:
                        target_batch_id = entry.get('batch_id', sync_batch_id)
                except Exception as e:
                    logger.debug("resync history lookup failed: %s", e)
            else:
                db.update_sync_history_completion(sync_batch_id, matched, synced, failed)

            # Save per-track match details from sync service
            match_details = getattr(result, 'match_details', None)
            if match_details:
                try:
                    track_results_json = json.dumps(match_details, default=str)
                    saved = db.update_sync_history_track_results(target_batch_id, track_results_json)
                    logger.info(f"[Sync History] Saved {len(match_details)} track results for batch {target_batch_id} (saved={saved})")
                except Exception as json_err:
                    logger.error(f"[Sync History] Failed to serialize track results: {json_err}")
            else:
                logger.warning(f"[Sync History] No match_details on SyncResult for batch {target_batch_id}")
        except Exception as e:
            logger.warning(f"Failed to record sync history completion: {e}")

        if automation_id:
            matched = getattr(result, 'matched_tracks', 0)
            total = getattr(result, 'total_tracks', 0)
            failed = getattr(result, 'failed_tracks', 0)
            deps.update_automation_progress(automation_id, status='finished', progress=100,
                phase='Sync complete',
                log_line=f'Done: {matched}/{total} matched, {failed} failed', log_type='success')

        # Emit playlist_synced event for automation engine
        try:
            if deps.automation_engine:
                deps.automation_engine.emit('playlist_synced', {
                    'playlist_name': playlist_name,
                    'total_tracks': str(getattr(result, 'total_tracks', 0)),
                    'matched_tracks': str(getattr(result, 'matched_tracks', 0)),
                    'synced_tracks': str(getattr(result, 'synced_tracks', 0)),
                    'failed_tracks': str(getattr(result, 'failed_tracks', 0)),
                })
        except Exception as e:
            logger.debug("playlist_synced emit failed: %s", e)

        # Save sync status with match counts and track hash for smart-skip on next scheduled sync
        import hashlib as _hl
        _track_ids_str = ','.join(sorted(t.get('id', '') for t in tracks_json))
        _tracks_hash = _hl.md5(_track_ids_str.encode()).hexdigest()
        snapshot_id = getattr(playlist, 'snapshot_id', None)
        deps.update_and_save_sync_status(playlist_id, playlist_name, playlist.owner, snapshot_id,
            matched_tracks=getattr(result, 'matched_tracks', 0),
            total_tracks=getattr(result, 'total_tracks', 0),
            discovered_tracks=len(tracks_json),
            tracks_hash=_tracks_hash)

    except Exception as e:
        logger.error(f"SYNC FAILED for {playlist_id}: {e}")
        import traceback
        traceback.print_exc()
        with sync_lock:
            sync_states[playlist_id] = {
                "status": "error",
                "error": str(e)
            }
        if automation_id:
            deps.update_automation_progress(automation_id, status='error', progress=100,
                phase='Error', log_line=f'Sync failed: {str(e)}', log_type='error')
    finally:
        logger.info(f"Cleaning up progress callback for {playlist.name}")
        # Clean up the callback
        if sync_service:
            sync_service.clear_progress_callback(playlist.name)
            # Clean up original tracks map
            if hasattr(sync_service, '_original_tracks_map'):
                del sync_service._original_tracks_map
        logger.info(f"Cleanup completed for {playlist_id}")
