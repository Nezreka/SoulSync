"""Candidate fallback download logic.

`attempt_download_with_candidates(task_id, candidates, track, batch_id, deps)`
is the function the search/match pipeline calls once it has a sorted list of
Soulseek candidates for a track. It walks the candidates by descending
confidence and starts the first one that:

1. Hasn't been tried for this task already (`used_sources` dedup).
2. Isn't blacklisted (user-flagged bad match).
3. Doesn't trigger a cancellation race (checked at three points).

When a candidate accepts:

- Stores rich post-processing context in `matched_downloads_context` keyed by
  `make_context_key(username, filename)` — clean Spotify metadata, album
  context (real or synthesized), `is_album_download` flag, batch/task IDs.
- For tracks with clean Spotify data, resolves track_number / disc_number
  from (1) track_info → (2) track object → (3) Spotify API call, with album
  metadata backfilled from the API response when local context is incomplete.
- Updates the task with the assigned `download_id`, falls through with a
  "searching" reset on failure so the next attempt finds a clean state.

On cancellation mid-download, attempts to cancel the active Soulseek transfer
and notifies the lifecycle via `on_download_completed(success=False)` so the
worker slot frees up.

Lifted verbatim from web_server.py. Wide dependency surface
(download_orchestrator, spotify_client, lifecycle callback, context-key helper,
status updater, DB) all injected via `CandidatesDeps`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)

logger = logging.getLogger(__name__)


@dataclass
class CandidatesDeps:
    """Bundle of cross-cutting deps the candidate-fallback logic needs."""
    download_orchestrator: Any
    spotify_client: Any
    run_async: Callable[..., Any]
    get_database: Callable[[], Any]
    update_task_status: Callable
    make_context_key: Callable[[str, str], str]
    on_download_completed: Callable


def attempt_download_with_candidates(task_id, candidates, track, batch_id=None, deps: CandidatesDeps = None):
    """
    Attempts to download with fallback candidate logic (matches GUI's retry_parallel_download_with_fallback).
    Returns True if successful, False if all candidates fail.
    """
    # Sort candidates by confidence (best first)
    candidates.sort(key=lambda r: r.confidence, reverse=True)
    
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return False
        used_sources = task.get('used_sources', set())
    
    # Try each candidate until one succeeds (like GUI's fallback logic)
    for candidate_index, candidate in enumerate(candidates):
        # Check cancellation before each attempt
        with tasks_lock:
            if task_id not in download_tasks:
                logger.info(f"[Modal Worker] Task {task_id} was deleted during candidate {candidate_index + 1}")
                return False
            if download_tasks[task_id]['status'] == 'cancelled':
                logger.warning(f"[Modal Worker] Task {task_id} cancelled during candidate {candidate_index + 1}")
                # Don't call _on_download_completed for cancelled tasks as it can stop monitoring
                return False
            download_tasks[task_id]['current_candidate_index'] = candidate_index
            
        # Create source key to avoid duplicate attempts (like GUI)
        source_key = f"{candidate.username}_{candidate.filename}"
        if source_key in used_sources:
            logger.info(f"[Modal Worker] Skipping already tried source: {source_key}")
            continue

        # Blacklist check — skip sources the user has flagged as bad matches
        try:
            _bl_db = deps.get_database()
            if _bl_db.is_blacklisted(candidate.username, candidate.filename):
                logger.info(f"[Modal Worker] Skipping blacklisted source: {source_key}")
                continue
        except Exception as e:
            logger.debug("blacklist check failed: %s", e)
        
        # CRITICAL: Add source to used_sources IMMEDIATELY to prevent race conditions
        # This must happen BEFORE starting download to prevent multiple retries from picking same source
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['used_sources'].add(source_key)
                logger.info(f"[Modal Worker] Marked source as used before download attempt: {source_key}")
            
        logger.info(f"[Modal Worker] Trying candidate {candidate_index + 1}/{len(candidates)}: {candidate.filename} (Confidence: {candidate.confidence:.2f})")
        
        try:
            # Update task status to downloading
            deps.update_task_status(task_id, 'downloading')

            # Prepare download - check if we have explicit album context from artist page
            track_info = {}
            with tasks_lock:
                if task_id in download_tasks:
                    raw_track_info = download_tasks[task_id].get('track_info')
                    track_info = raw_track_info if isinstance(raw_track_info, dict) else {}

            # Use explicit album/artist context if available (from artist album downloads)
            has_explicit_context = track_info and track_info.get('_is_explicit_album_download', False)

            if has_explicit_context:
                # Use the real Spotify album/artist data from the UI
                explicit_album = track_info.get('_explicit_album_context', {})
                explicit_artist = track_info.get('_explicit_artist_context', {})
                # Normalize artist context if it's a plain string (e.g. from wishlist spotify_data)
                if isinstance(explicit_artist, str):
                    explicit_artist = {'name': explicit_artist}

                spotify_artist_context = {
                    'id': explicit_artist.get('id', 'explicit_artist'),
                    'name': explicit_artist.get('name', track.artists[0] if track.artists else 'Unknown'),
                    'genres': explicit_artist.get('genres', [])
                }
                # Handle both image_url formats (direct string or images array)
                album_image_url = None
                if explicit_album.get('image_url'):
                    # Backend API returns image_url as direct string
                    album_image_url = explicit_album.get('image_url')
                elif explicit_album.get('images'):
                    # Fallback: images array format from Spotify API
                    album_image_url = explicit_album.get('images', [{}])[0].get('url')

                spotify_album_context = {
                    'id': explicit_album.get('id', 'explicit_album'),
                    'name': explicit_album.get('name', track.album),
                    'release_date': explicit_album.get('release_date', ''),
                    'image_url': album_image_url,
                    'total_tracks': explicit_album.get('total_tracks', 0),
                    'total_discs': explicit_album.get('total_discs', 1),
                    'album_type': explicit_album.get('album_type', 'album'),
                    'artists': explicit_album.get('artists', [{'name': spotify_artist_context.get('name', '')}])
                }
                logger.info(f"[Explicit Context] Using real album data: '{spotify_album_context['name']}' ({spotify_album_context['album_type']}, {spotify_album_context['total_discs']} disc(s))")
            else:
                # Fallback to generic context for playlists/wishlists
                # Extract album metadata from track_info if available (discovery enriches tracks with full album objects)
                fallback_album = track_info.get('album', {}) if track_info else {}
                if isinstance(fallback_album, str):
                    fallback_album = {'name': fallback_album}
                elif not isinstance(fallback_album, dict):
                    fallback_album = {}
                fallback_image_url = None
                fallback_images = fallback_album.get('images', [])
                if fallback_album.get('image_url'):
                    fallback_image_url = fallback_album['image_url']
                elif fallback_images and isinstance(fallback_images, list) and len(fallback_images) > 0:
                    fallback_image_url = fallback_images[0].get('url') if isinstance(fallback_images[0], dict) else None
                spotify_artist_context = {'id': 'from_sync_modal', 'name': track.artists[0] if track.artists else 'Unknown', 'genres': []}
                # Preserve album-level artists for consistent folder naming
                _fallback_album_artists = fallback_album.get('artists', [])
                if not _fallback_album_artists:
                    _fallback_album_artists = [{'name': track.artists[0]}] if track.artists else []
                spotify_album_context = {
                    'id': fallback_album.get('id', 'from_sync_modal'),
                    'name': fallback_album.get('name', '') or track.album,
                    'release_date': fallback_album.get('release_date', ''),
                    'image_url': fallback_image_url,
                    'album_type': fallback_album.get('album_type', 'album'),
                    'total_tracks': fallback_album.get('total_tracks', 0),
                    'total_discs': fallback_album.get('total_discs', 1),
                    'artists': _fallback_album_artists
                }

            download_payload = candidate.__dict__

            username = download_payload.get('username')
            filename = download_payload.get('filename')
            size = download_payload.get('size', 0)

            if not username or not filename:
                logger.error("[Modal Worker] Invalid candidate data: missing username or filename")
                continue

            # PROTECTION: Check if there's already an active download for this task
            current_download_id = None
            with tasks_lock:
                if task_id in download_tasks:
                    current_download_id = download_tasks[task_id].get('download_id')
            
            if current_download_id:
                logger.info(f"[Modal Worker] Task {task_id} already has active download {current_download_id} - skipping new download attempt")
                logger.info("[Modal Worker] This prevents race condition where multiple retries start overlapping downloads")
                continue

            # Initiate download
            logger.info(f"[Modal Worker] Starting download: {username} / {os.path.basename(filename)}")
            download_id = deps.run_async(deps.download_orchestrator.download(username, filename, size))

            if download_id:
                # Store context for post-processing with complete Spotify metadata (GUI PARITY)
                context_key = deps.make_context_key(username, filename)
                with matched_context_lock:
                    # Create WebUI equivalent of GUI's SpotifyBasedSearchResult data structure
                    enhanced_payload = download_payload.copy()
                    
                    # Extract clean Spotify metadata from track object (same as GUI)
                    has_clean_spotify_data = track and hasattr(track, 'name') and hasattr(track, 'album')
                    if has_clean_spotify_data:
                        # Use clean Spotify metadata (matches GUI's SpotifyBasedSearchResult)
                        enhanced_payload['spotify_clean_title'] = track.name
                        enhanced_payload['spotify_clean_album'] = track.album
                        enhanced_payload['spotify_clean_artist'] = track.artists[0] if track.artists else enhanced_payload.get('artist', '')
                        # Preserve all artists for metadata tagging
                        enhanced_payload['artists'] = [{'name': artist} for artist in track.artists] if track.artists else []
                        logger.info(f"[Context] Using clean Spotify metadata - Album: '{track.album}', Title: '{track.name}'")
                        
                        # Get track_number and disc_number — prefer track data we already have,
                        # fall back to detailed API call only if needed
                        got_track_number = False

                        # 1. Try track_info (from frontend, has album track data)
                        tn = track_info.get('track_number', 0) if isinstance(track_info, dict) else 0
                        dn = track_info.get('disc_number', 1) if isinstance(track_info, dict) else 1
                        if tn and tn > 0:
                            enhanced_payload['track_number'] = tn
                            enhanced_payload['disc_number'] = dn
                            got_track_number = True
                            logger.info(f"[Context] Added track_number from track_info: {tn}, disc_number: {dn}")

                        # 2. Try the track object itself (from album tracks response)
                        if not got_track_number and hasattr(track, 'track_number') and track.track_number:
                            enhanced_payload['track_number'] = track.track_number
                            enhanced_payload['disc_number'] = getattr(track, 'disc_number', 1) or 1
                            got_track_number = True
                            logger.info(f"[Context] Added track_number from track object: {track.track_number}, disc_number: {enhanced_payload['disc_number']}")

                        # 3. Last resort — fetch from metadata source API
                        if not got_track_number and hasattr(track, 'id') and track.id:
                            try:
                                detailed_track = deps.spotify_client.get_track_details(track.id)
                                if detailed_track and detailed_track.get('track_number'):
                                    enhanced_payload['track_number'] = detailed_track['track_number']
                                    enhanced_payload['disc_number'] = detailed_track.get('disc_number', 1)
                                    got_track_number = True
                                    logger.info(f"[Context] Added track_number from API: {detailed_track['track_number']}, disc_number: {enhanced_payload['disc_number']}")

                                    # Backfill album metadata from detailed track when context
                                    # has incomplete data (missing release_date, total_tracks, etc.)
                                    if isinstance(detailed_track.get('album'), dict):
                                        dt_album = detailed_track['album']
                                        if not spotify_album_context.get('release_date') and dt_album.get('release_date'):
                                            spotify_album_context['release_date'] = dt_album['release_date']
                                            logger.info(f"[Context] Backfilled release_date from API: {dt_album['release_date']}")
                                        if not spotify_album_context.get('album_type') and dt_album.get('album_type'):
                                            spotify_album_context['album_type'] = dt_album['album_type']
                                        if not spotify_album_context.get('total_tracks') and dt_album.get('total_tracks'):
                                            spotify_album_context['total_tracks'] = dt_album['total_tracks']
                                        if not spotify_album_context.get('id') and dt_album.get('id'):
                                            spotify_album_context['id'] = dt_album['id']
                                        if not spotify_album_context.get('image_url') and dt_album.get('images'):
                                            spotify_album_context['image_url'] = dt_album['images'][0].get('url', '')
                            except Exception as e:
                                logger.error(f"[Context] API track details failed: {e}")

                        if not got_track_number:
                            enhanced_payload.setdefault('track_number', 0)
                            enhanced_payload.setdefault('disc_number', 1)
                            logger.warning("[Context] No track_number found from any source")
                        
                        # Determine if this should be treated as album download
                        # First check if we have explicit album context from artist page
                        if has_explicit_context:
                            is_album_context = True
                            logger.info("[Context] Using explicit album context flag from artist page")
                        else:
                            # Fall back to guessing based on clean data
                            is_album_context = (
                                track.album and
                                track.album.strip() and
                                track.album != "Unknown Album" and
                                track.album.lower() != track.name.lower()  # Album different from track
                            )
                    else:
                        # Fallback to original data
                        enhanced_payload['spotify_clean_title'] = enhanced_payload.get('title', '')
                        enhanced_payload['spotify_clean_album'] = enhanced_payload.get('album', '')
                        enhanced_payload['spotify_clean_artist'] = enhanced_payload.get('artist', '')
                        # Preserve existing artists array if available, otherwise create from single artist
                        if 'artists' not in enhanced_payload and enhanced_payload.get('artist'):
                            enhanced_payload['artists'] = [{'name': enhanced_payload['artist']}]
                        enhanced_payload['track_number'] = track_info.get('track_number', 1)  # Fallback when no clean Spotify data
                        is_album_context = False
                        logger.warning(f"[Context] Using fallback data - no clean Spotify metadata available, track_number={enhanced_payload['track_number']}")
                    
                    matched_downloads_context[context_key] = {
                        "spotify_artist": spotify_artist_context,
                        "spotify_album": spotify_album_context,
                        "original_search_result": enhanced_payload,
                        "is_album_download": is_album_context,  # Critical fix: Use actual album context
                        "has_clean_spotify_data": has_clean_spotify_data,  # Flag for post-processing
                        "task_id": task_id,  # Add task_id for completion callbacks
                        "batch_id": batch_id,  # Add batch_id for completion callbacks
                        "track_info": track_info,  # Add track_info for playlist folder mode
                        "_download_username": username,  # Source username for AcoustID skip logic
                    }

                    logger.info(f"[Context] Set is_album_download: {is_album_context} (has clean data: {has_clean_spotify_data})")
                    logger.debug(f"[Debug] Context creation - track_info: {track_info is not None}, playlist_folder_mode: {track_info.get('_playlist_folder_mode', False) if track_info else False}")
                
                # Update task with successful download info
                with tasks_lock:
                    if task_id in download_tasks:
                        # PHASE 3: Final cancellation check after download started (GUI PARITY)
                        if download_tasks[task_id]['status'] == 'cancelled':
                            logger.warning(f"[Modal Worker] Task {task_id} cancelled after download {download_id} started - attempting to cancel download")
                            # Try to cancel the download immediately
                            try:
                                deps.run_async(deps.download_orchestrator.cancel_download(download_id, username, remove=True))
                                logger.warning(f"Successfully cancelled active download {download_id}")
                            except Exception as cancel_error:
                                logger.error(f"Failed to cancel active download {download_id}: {cancel_error}")
                            
                            # Free worker slot
                            if batch_id:
                                deps.on_download_completed(batch_id, task_id, success=False)
                            return False
                        
                        # Store download information - use real download ID from download_orchestrator
                        # CRITICAL FIX: Trust the download ID returned by download_orchestrator.download()
                        download_tasks[task_id]['download_id'] = download_id
                        
                        download_tasks[task_id]['username'] = username
                        download_tasks[task_id]['filename'] = filename
                        
                logger.info(f"[Modal Worker] Download started successfully for '{filename}'. Download ID: {download_id}")
                return True  # Success!
            else:
                logger.error(f"[Modal Worker] Failed to start download for '{filename}'")
                # Reset status back to searching for next attempt
                with tasks_lock:
                    if task_id in download_tasks:
                        download_tasks[task_id]['status'] = 'searching'
                continue
                
        except Exception as e:
            import traceback
            logger.error(f"[Modal Worker] Error attempting download for '{candidate.filename}': {e}")
            traceback.print_exc()
            # Reset status back to searching for next attempt
            with tasks_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['status'] = 'searching'
            continue

    # All candidates failed
    logger.error(f"[Modal Worker] All {len(candidates)} candidates failed for '{track.name}'")
    return False
