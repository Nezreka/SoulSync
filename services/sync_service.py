import asyncio
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from utils.logging_config import get_logger
from core.spotify_client import SpotifyClient, Playlist as SpotifyPlaylist, Track as SpotifyTrack
from core.plex_client import PlexClient, PlexTrackInfo
from core.jellyfin_client import JellyfinClient
from core.navidrome_client import NavidromeClient
from core.soulseek_client import SoulseekClient
from core.matching_engine import MusicMatchingEngine, MatchResult

logger = get_logger("sync_service")

@dataclass
class SyncResult:
    playlist_name: str
    total_tracks: int
    matched_tracks: int
    synced_tracks: int
    downloaded_tracks: int
    failed_tracks: int
    sync_time: datetime
    errors: List[str]
    wishlist_added_count: int = 0
    match_details: list = None  # Per-track match data for sync history

    @property
    def success_rate(self) -> float:
        if self.total_tracks == 0:
            return 0.0
        return (self.synced_tracks / self.total_tracks) * 100

@dataclass
class SyncProgress:
    current_step: str
    current_track: str
    progress: float
    total_steps: int
    current_step_number: int
    # Add detailed track stats for UI updates
    total_tracks: int = 0
    matched_tracks: int = 0
    failed_tracks: int = 0

class PlaylistSyncService:
    def __init__(self, spotify_client: SpotifyClient, soulseek_client: SoulseekClient, media_server_engine=None):
        """Initialize the sync service.

        ``media_server_engine`` is the central MediaServerEngine that owns
        the per-server clients (Plex / Jellyfin / Navidrome / SoulSync).
        Replaces the legacy per-server kwargs (plex_client / jellyfin_client
        / navidrome_client) — all media-server access now goes through
        ``self._engine.client(name)`` so swapping the active server doesn't
        need a service rebuild.
        """
        self.spotify_client = spotify_client
        self._engine = media_server_engine
        self.soulseek_client = soulseek_client
        self.progress_callbacks = {}  # Playlist-specific progress callbacks
        self.syncing_playlists = set()  # Track multiple syncing playlists
        self._cancelled = False
        self.matching_engine = MusicMatchingEngine()

    def _media_client(self, name: str):
        """Resolve a per-server client through the engine, or None when the
        engine isn't wired (defensive — every production path passes one)."""
        if self._engine is None:
            return None
        return self._engine.client(name)

    def _get_active_media_client(self, profile_id=None):
        """Get the active media client based on config settings.

        If profile_id is provided (or set on the instance via sync_playlist),
        and that profile has a custom library selection, sets the library on
        the client before returning.
        """
        profile_id = profile_id or getattr(self, '_active_profile_id', None)
        try:
            from config.settings import config_manager
            active_server = config_manager.get_active_media_server()

            if active_server == "jellyfin":
                client = self._media_client('jellyfin')
                if not client:
                    logger.error("Jellyfin client not provided to sync service")
                    return None, "jellyfin"
                if profile_id:
                    self._apply_profile_library(profile_id, 'jellyfin', client)
                return client, "jellyfin"
            elif active_server == "navidrome":
                client = self._media_client('navidrome')
                if not client:
                    logger.error("Navidrome client not provided to sync service")
                    return None, "navidrome"
                return client, "navidrome"
            else:  # Default to Plex
                client = self._media_client('plex')
                if profile_id and client:
                    self._apply_profile_library(profile_id, 'plex', client)
                return client, "plex"
        except Exception as e:
            logger.error(f"Error determining active media server: {e}")
            return self._media_client('plex'), "plex"  # Fallback to Plex
    
    def _apply_profile_library(self, profile_id, server_type, client):
        """Apply per-profile library selection to a media client if configured."""
        try:
            from database.music_database import MusicDatabase
            db = MusicDatabase()
            libs = db.get_profile_server_library(profile_id)
            if not libs:
                return

            if server_type == 'plex' and libs.get('plex_library_id'):
                lib_name = libs['plex_library_id']
                if hasattr(client, 'set_music_library_by_name'):
                    client.set_music_library_by_name(lib_name)
                    logger.info(f"Per-profile: set Plex library to '{lib_name}' for profile {profile_id}")
            elif server_type == 'jellyfin':
                if libs.get('jellyfin_user_id') and hasattr(client, 'user_id'):
                    client.user_id = libs['jellyfin_user_id']
                    logger.info(f"Per-profile: set Jellyfin user to '{libs['jellyfin_user_id']}' for profile {profile_id}")
                if libs.get('jellyfin_library_id') and hasattr(client, 'music_library_id'):
                    client.music_library_id = libs['jellyfin_library_id']
                    logger.info(f"Per-profile: set Jellyfin library to '{libs['jellyfin_library_id']}' for profile {profile_id}")
        except Exception as e:
            logger.debug(f"Error applying profile library for profile {profile_id}: {e}")

    @property
    def is_syncing(self):
        """Check if any playlist is currently syncing"""
        return len(self.syncing_playlists) > 0
    
    def set_progress_callback(self, callback, playlist_name=None):
        """Set progress callback for specific playlist or global if no playlist specified"""
        if playlist_name:
            self.progress_callbacks[playlist_name] = callback
        else:
            # Legacy support - set for all current syncing playlists
            for playlist in self.syncing_playlists:
                self.progress_callbacks[playlist] = callback
    
    def clear_progress_callback(self, playlist_name):
        """Clear progress callback for specific playlist"""
        if playlist_name in self.progress_callbacks:
            del self.progress_callbacks[playlist_name]
    
    def cancel_sync(self):
        """Cancel the current sync operation"""
        logger.info("PlaylistSyncService.cancel_sync() called - setting cancellation flag")
        self._cancelled = True
        self.is_syncing = False
    
    def _update_progress(self, playlist_name: str, step: str, track: str, progress: float, total_steps: int, current_step: int, 
                        total_tracks: int = 0, matched_tracks: int = 0, failed_tracks: int = 0):
        # Send progress update to the specific playlist's callback
        callback = self.progress_callbacks.get(playlist_name)
        if callback:
            callback(SyncProgress(
                current_step=step,
                current_track=track,
                progress=progress,
                total_steps=total_steps,
                current_step_number=current_step,
                total_tracks=total_tracks,
                matched_tracks=matched_tracks,
                failed_tracks=failed_tracks
            ))
    
    async def sync_playlist(self, playlist: SpotifyPlaylist, download_missing: bool = False, profile_id: int = None) -> SyncResult:
        self._active_profile_id = profile_id
        # Check if THIS specific playlist is already syncing
        if playlist.name in self.syncing_playlists:
            logger.warning(f"Sync already in progress for playlist: {playlist.name}")
            return SyncResult(
                playlist_name=playlist.name,
                total_tracks=0,
                matched_tracks=0,
                synced_tracks=0,
                downloaded_tracks=0,
                failed_tracks=0,
                sync_time=datetime.now(),
                errors=[f"Sync already in progress for playlist: {playlist.name}"]
            )
        
        # Add this playlist to syncing set
        self.syncing_playlists.add(playlist.name)
        self._cancelled = False
        errors = []
        
        try:
            logger.info(f"Starting sync for playlist: {playlist.name}")
            
            if self._cancelled:
                return self._create_error_result(playlist.name, ["Sync cancelled"])
            
            # Skip fetching playlist since we already have it
            self._update_progress(playlist.name, "Preparing playlist sync", "", 10, 5, 1)
            
            if not playlist.tracks:
                errors.append(f"Playlist '{playlist.name}' has no tracks")
                return self._create_error_result(playlist.name, errors)
            
            if self._cancelled:
                return self._create_error_result(playlist.name, ["Sync cancelled"])
            
            total_tracks = len(playlist.tracks)
            media_client, server_type = self._get_active_media_client()

            media_client, server_type = self._get_active_media_client()
            self._update_progress(playlist.name, f"Matching tracks against {server_type.title()} library", "", 20, 5, 2, total_tracks=total_tracks)
            
            # Use the same robust matching approach as "Download Missing Tracks"
            match_results = []
            for i, track in enumerate(playlist.tracks):
                if self._cancelled:
                    return self._create_error_result(playlist.name, ["Sync cancelled"])
                
                # Update progress for each track
                progress_percent = 20 + (40 * (i + 1) / total_tracks)  # 20-60% for matching
                # Extract artist name from both string and dict formats
                if track.artists:
                    first_artist = track.artists[0]
                    artist_name = first_artist if isinstance(first_artist, str) else (first_artist.get('name', 'Unknown') if isinstance(first_artist, dict) else str(first_artist))
                    current_track_name = f"{artist_name} - {track.name}"
                else:
                    current_track_name = track.name
                self._update_progress(playlist.name, "Matching tracks", current_track_name, progress_percent, 5, 2, 
                                    total_tracks=total_tracks,
                                    matched_tracks=len([r for r in match_results if r.is_match]),
                                    failed_tracks=len([r for r in match_results if not r.is_match]))
                
                # Use the robust search approach
                plex_match, confidence = await self._find_track_in_media_server(track)
                
                match_result = MatchResult(
                    spotify_track=track,
                    plex_track=plex_match,
                    confidence=confidence,
                    match_type="robust_search" if plex_match else "no_match"
                )
                match_results.append(match_result)
            
            matched_tracks = [r for r in match_results if r.is_match]
            unmatched_tracks = [r for r in match_results if not r.is_match]
            
            logger.info(f"Found {len(matched_tracks)} matches out of {len(playlist.tracks)} tracks")
            
            
            if self._cancelled:
                return self._create_error_result(playlist.name, ["Sync cancelled"])
            
            # Update progress with match results
            self._update_progress(playlist.name, "Matching completed", "", 60, 5, 3, 
                                total_tracks=total_tracks, 
                                matched_tracks=len(matched_tracks), 
                                failed_tracks=len(unmatched_tracks))
            
            downloaded_tracks = 0
            if download_missing and unmatched_tracks:
                if self._cancelled:
                    return self._create_error_result(playlist.name, ["Sync cancelled"])
                self._update_progress(playlist.name, "Downloading missing tracks", "", 70, 5, 4, 
                                    total_tracks=total_tracks,
                                    matched_tracks=len(matched_tracks),
                                    failed_tracks=len(unmatched_tracks))
                downloaded_tracks = await self._download_missing_tracks(unmatched_tracks)
            
            if self._cancelled:
                return self._create_error_result(playlist.name, ["Sync cancelled"])
            
            media_client, server_type = self._get_active_media_client()
            self._update_progress(playlist.name, f"Creating/updating {server_type.title()} playlist", "", 80, 5, 4,
                                total_tracks=total_tracks,
                                matched_tracks=len(matched_tracks),
                                failed_tracks=len(unmatched_tracks))
            
            # Get the actual media server track objects
            media_tracks = [r.plex_track for r in matched_tracks if r.plex_track] # plex_track is a generic name here
            logger.info(f"Creating playlist with {len(media_tracks)} matched tracks")

            # Validate that all tracks have proper ratingKey attributes for playlist creation
            valid_tracks = []
            for i, track in enumerate(media_tracks):
                if track and hasattr(track, 'ratingKey'):
                    valid_tracks.append(track)
                    logger.debug(f"Track {i+1} valid for playlist: '{track.title}' (ratingKey: {track.ratingKey})")
                else:
                    logger.warning(f"Track {i+1} invalid for playlist: {track} (type: {type(track)}, has ratingKey: {hasattr(track, 'ratingKey') if track else 'N/A'})")
            
            logger.info(f"Playlist validation: {len(valid_tracks)}/{len(media_tracks)} tracks are valid {server_type.title()} objects with ratingKeys")

            # Deduplicate by ratingKey — media servers silently drop duplicates,
            # so count should reflect what actually ends up in the playlist
            seen_keys = set()
            deduped_tracks = []
            for t in valid_tracks:
                if t.ratingKey not in seen_keys:
                    seen_keys.add(t.ratingKey)
                    deduped_tracks.append(t)
            if len(deduped_tracks) < len(valid_tracks):
                logger.info(f"Deduplicated {len(valid_tracks) - len(deduped_tracks)} duplicate ratingKeys ({len(valid_tracks)} → {len(deduped_tracks)} tracks)")

            # Use the deduplicated tracks for the sync
            plex_tracks = deduped_tracks
            
            # Use active media server for playlist sync
            media_client, server_type = self._get_active_media_client()
            if not media_client:
                logger.error("No active media client available for playlist sync")
                sync_success = False
            else:
                logger.info(f"Syncing playlist '{playlist.name}' to {server_type.upper()} server")
                # THE FIX: Ensure we are passing the correct, native track objects to the client
                sync_success = media_client.update_playlist(playlist.name, valid_tracks)
            
            synced_tracks = len(plex_tracks) if sync_success else 0
            failed_tracks = len(playlist.tracks) - synced_tracks - downloaded_tracks
            
            self._update_progress(playlist.name, "Sync completed", "", 100, 5, 5,
                                total_tracks=total_tracks,
                                matched_tracks=len(matched_tracks),
                                failed_tracks=failed_tracks)

            # Auto-add unmatched tracks to wishlist (skip in Wing It mode)
            wishlist_added_count = 0
            if unmatched_tracks and getattr(self, '_skip_wishlist', False):
                logger.info(f"[Wing It] Skipping wishlist for {len(unmatched_tracks)} unmatched tracks")
                unmatched_tracks = []  # Clear so the loop below doesn't run
            if unmatched_tracks:
                try:
                    from core.wishlist_service import get_wishlist_service
                    wishlist_service = get_wishlist_service()

                    logger.info(f"Auto-adding {len(unmatched_tracks)} unmatched tracks to wishlist")

                    for match_result in unmatched_tracks:
                        spotify_track = match_result.spotify_track

                        # Skip wing-it fallback tracks — they have no real metadata
                        # and should never be added to wishlist
                        if str(spotify_track.id).startswith('wing_it_'):
                            logger.info(f"Skipping wishlist for wing-it track: {spotify_track.name}")
                            continue

                        # Check if we have original track data with full album objects
                        original_track_data = None
                        if hasattr(self, '_original_tracks_map') and self._original_tracks_map:
                            original_track_data = self._original_tracks_map.get(spotify_track.id)

                        # Use original data if available (preserves album images), otherwise convert
                        if original_track_data:
                            spotify_track_data = original_track_data
                        else:
                            spotify_track_data = {
                                'id': spotify_track.id,
                                'name': spotify_track.name,
                                'artists': [{'name': a} if isinstance(a, str) else a for a in spotify_track.artists],
                                'album': {'name': spotify_track.album},
                                'duration_ms': spotify_track.duration_ms,
                                'popularity': getattr(spotify_track, 'popularity', 0),
                                'preview_url': getattr(spotify_track, 'preview_url', None),
                                'external_urls': getattr(spotify_track, 'external_urls', {})
                            }

                        # Add to wishlist with source context
                        success = wishlist_service.add_spotify_track_to_wishlist(
                            spotify_track_data=spotify_track_data,
                            failure_reason='Missing from media server after sync',
                            source_type='playlist',
                            source_context={
                                'playlist_name': playlist.name,
                                'playlist_id': playlist.id,
                                'sync_type': 'automatic_sync',
                                'timestamp': datetime.now().isoformat()
                            }
                        )

                        if success:
                            wishlist_added_count += 1

                    logger.info(f"Successfully added {wishlist_added_count}/{len(unmatched_tracks)} tracks to wishlist")

                except Exception as e:
                    logger.warning(f"Failed to auto-add tracks to wishlist: {e}")
                    # Don't fail the sync if wishlist add fails

            # Build per-track match details for sync history
            _match_details = []
            for i, mr in enumerate(match_results):
                t = mr.spotify_track
                artists = t.artists if hasattr(t, 'artists') and t.artists else []
                first_artist = artists[0] if artists else ''
                if isinstance(first_artist, dict):
                    first_artist = first_artist.get('name', '')
                album = getattr(t, 'album', '')
                if isinstance(album, dict):
                    album = album.get('name', '')

                # Extract image URL from track's album data
                image_url = getattr(t, 'image_url', None) or ''
                if not image_url:
                    t_album = getattr(t, 'album', None)
                    if isinstance(t_album, dict):
                        imgs = t_album.get('images', [])
                        if imgs and isinstance(imgs, list) and len(imgs) > 0:
                            image_url = imgs[0].get('url', '') if isinstance(imgs[0], dict) else ''

                detail = {
                    'index': i,
                    'name': t.name if hasattr(t, 'name') else '',
                    'artist': str(first_artist),
                    'album': str(album or ''),
                    'duration_ms': getattr(t, 'duration_ms', 0),
                    'source_track_id': getattr(t, 'id', ''),
                    'image_url': image_url,
                    'status': 'found' if mr.is_match else 'not_found',
                    'confidence': round(mr.confidence, 3),
                    'matched_track': None,
                    'download_status': 'wishlist' if not mr.is_match else None,
                }
                if mr.plex_track:
                    detail['matched_track'] = {
                        'title': getattr(mr.plex_track, 'title', ''),
                        'artist_name': getattr(mr.plex_track, 'artist', ''),
                        'album_title': getattr(mr.plex_track, 'album', ''),
                    }
                _match_details.append(detail)

            result = SyncResult(
                playlist_name=playlist.name,
                total_tracks=len(playlist.tracks),
                matched_tracks=len(matched_tracks),
                synced_tracks=synced_tracks,
                downloaded_tracks=downloaded_tracks,
                failed_tracks=failed_tracks,
                sync_time=datetime.now(),
                errors=errors,
                wishlist_added_count=wishlist_added_count,
                match_details=_match_details
            )

            logger.info(f"Sync completed: {result.success_rate:.1f}% success rate")
            return result
            
        except Exception as e:
            logger.error(f"Error during sync: {e}")
            errors.append(str(e))
            return self._create_error_result(playlist.name, errors)
        
        finally:
            # Remove this playlist from syncing set and clear its callback
            self.syncing_playlists.discard(playlist.name)
            self.clear_progress_callback(playlist.name)
            self._cancelled = False
    
    async def _find_track_in_media_server(self, spotify_track: SpotifyTrack) -> Tuple[Optional[PlexTrackInfo], float]:
        """Find a track using the same improved database matching as Download Missing Tracks modal"""
        try:
            # Check active media server connection
            media_client, server_type = self._get_active_media_client()
            if not media_client or not media_client.is_connected():
                logger.warning(f"{server_type.upper()} client not connected")
                return None, 0.0
            
            # Use the SAME improved database matching as PlaylistTrackAnalysisWorker
            from database.music_database import MusicDatabase
            from config.settings import config_manager

            original_title = spotify_track.name
            spotify_id = getattr(spotify_track, 'id', '') or ''
            active_server = config_manager.get_active_media_server()

            # --- Sync match cache fast-path ---
            if spotify_id:
                try:
                    cache_db = MusicDatabase()
                    cached = cache_db.read_sync_match_cache(spotify_id, active_server)
                    if cached:
                        server_track_id = cached['server_track_id']
                        db_track_check = cache_db.get_track_by_id(server_track_id)
                        if db_track_check:
                            if server_type == "jellyfin":
                                class JellyfinTrackFromCache:
                                    def __init__(self, db_t):
                                        self.ratingKey = db_t.id
                                        self.title = db_t.title
                                        self.id = db_t.id
                                actual_track = JellyfinTrackFromCache(db_track_check)
                            elif server_type == "navidrome":
                                class NavidromeTrackFromCache:
                                    def __init__(self, db_t):
                                        self.ratingKey = db_t.id
                                        self.title = db_t.title
                                        self.id = db_t.id
                                actual_track = NavidromeTrackFromCache(db_track_check)
                            else:
                                try:
                                    actual_track = media_client.server.fetchItem(int(server_track_id))
                                    if not (actual_track and hasattr(actual_track, 'ratingKey')):
                                        actual_track = None
                                except Exception:
                                    actual_track = None

                            if actual_track:
                                logger.debug(f"Sync cache hit: '{original_title}' → server track {server_track_id}")
                                return actual_track, cached['confidence']

                        logger.debug(f"Sync cache stale for '{original_title}' — track {server_track_id} gone")
                except Exception as cache_err:
                    logger.debug(f"Sync cache lookup error: {cache_err}")
            # --- End cache fast-path ---

            # Try each artist (same as modal logic)
            for artist in spotify_track.artists:
                if self._cancelled:
                    return None, 0.0

                # Extract artist name from both string and dict formats
                if isinstance(artist, str):
                    artist_name = artist
                elif isinstance(artist, dict) and 'name' in artist:
                    artist_name = artist['name']
                else:
                    artist_name = str(artist)
                
                # Use the improved database check_track_exists method with server awareness
                try:
                    db = MusicDatabase()
                    db_track, confidence = db.check_track_exists(original_title, artist_name, confidence_threshold=0.7, server_source=active_server)

                    if db_track and confidence >= 0.7:
                        logger.debug(f"Database match found for '{original_title}' by '{artist_name}': '{db_track.title}' with confidence {confidence:.2f}")

                        # Save to sync match cache for next time
                        if spotify_id:
                            try:
                                from core.matching_engine import MusicMatchingEngine
                                me = MusicMatchingEngine()
                                db.save_sync_match_cache(
                                    spotify_id, me.clean_title(original_title), me.clean_artist(artist_name),
                                    active_server, db_track.id, db_track.title, confidence
                                )
                            except Exception:
                                pass
                        
                        # Fetch the actual track object from active media server using the database track ID
                        try:
                            if server_type == "jellyfin":
                                # For Jellyfin, create a track object from database info (Jellyfin doesn't have fetchItem)
                                class JellyfinTrackFromDB:
                                    def __init__(self, db_track):
                                        self.ratingKey = db_track.id
                                        self.title = db_track.title
                                        self.id = db_track.id
                                
                                actual_track = JellyfinTrackFromDB(db_track)
                                logger.debug(f"Created Jellyfin track object for '{db_track.title}' (ID: {actual_track.ratingKey})")
                                return actual_track, confidence
                            elif server_type == "navidrome":
                                # For Navidrome, create a track object from database info (similar to Jellyfin)
                                class NavidromeTrackFromDB:
                                    def __init__(self, db_track):
                                        self.ratingKey = db_track.id
                                        self.title = db_track.title
                                        self.id = db_track.id

                                actual_track = NavidromeTrackFromDB(db_track)
                                logger.debug(f"Created Navidrome track object for '{db_track.title}' (ID: {actual_track.ratingKey})")
                                return actual_track, confidence
                            else:
                                # For Plex, use the original fetchItem approach
                                # Validate that the track ID is numeric (Plex requirement)
                                try:
                                    track_id = int(db_track.id)
                                    actual_plex_track = media_client.server.fetchItem(track_id)
                                    if actual_plex_track and hasattr(actual_plex_track, 'ratingKey'):
                                        logger.debug(f"Successfully fetched actual Plex track for '{db_track.title}' (ratingKey: {actual_plex_track.ratingKey})")
                                        return actual_plex_track, confidence
                                    else:
                                        logger.warning(f"Fetched Plex track for '{db_track.title}' lacks ratingKey attribute")
                                except ValueError:
                                    logger.warning(f"Invalid Plex track ID format for '{db_track.title}' (ID: {db_track.id}) - skipping this track")
                                    continue
                                
                        except Exception as fetch_error:
                            logger.error(f"Failed to fetch actual {server_type} track for '{db_track.title}' (ID: {db_track.id}): {fetch_error}")
                            # Continue to try other artists rather than fail completely
                            continue
                        
                except Exception as db_error:
                    logger.error(f"Error checking track existence for '{original_title}' by '{artist_name}': {db_error}")
                    continue
            
            logger.debug(f"No database match found for '{original_title}' by any of the artists {spotify_track.artists}")
            return None, 0.0
            
        except Exception as e:
            logger.error(f"Error searching for track '{spotify_track.name}': {e}")
            return None, 0.0
    
    async def sync_multiple_playlists(self, playlist_names: List[str], download_missing: bool = False) -> List[SyncResult]:
        results = []
        
        for i, playlist_name in enumerate(playlist_names):
            logger.info(f"Syncing playlist {i+1}/{len(playlist_names)}: {playlist_name}")
            result = await self.sync_playlist(playlist_name, download_missing)
            results.append(result)
            
            if i < len(playlist_names) - 1:
                await asyncio.sleep(1)
        
        return results
    
    def _get_spotify_playlist(self, playlist_name: str) -> Optional[SpotifyPlaylist]:
        try:
            playlists = self.spotify_client.get_user_playlists()
            for playlist in playlists:
                if playlist.name.lower() == playlist_name.lower():
                    return playlist
            return None
        except Exception as e:
            logger.error(f"Error fetching Spotify playlist: {e}")
            return None
    
    async def _get_media_tracks(self) -> List:
        """Get tracks from the active media server"""
        try:
            media_client, server_type = self._get_active_media_client()
            if not media_client:
                logger.error("No active media client available")
                return []

            if hasattr(media_client, 'search_tracks'):
                return media_client.search_tracks("", limit=10000)
            else:
                logger.warning(f"{server_type.title()} client doesn't support track search")
                return []
        except Exception as e:
            logger.error(f"Error fetching {server_type} tracks: {e}")
            return []
    
    async def _download_missing_tracks(self, unmatched_tracks: List[MatchResult]) -> int:
        downloaded_count = 0
        
        for match_result in unmatched_tracks:
            try:
                query = self.matching_engine.generate_download_query(match_result.spotify_track)
                logger.info(f"Attempting to download: {query}")
                
                download_id = await self.soulseek_client.search_and_download_best(query, expected_track=match_result.spotify_track)
                
                if download_id:
                    downloaded_count += 1
                    logger.info(f"Download started for: {match_result.spotify_track.name}")
                else:
                    logger.warning(f"No download sources found for: {match_result.spotify_track.name}")
                
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Error downloading track: {e}")
        
        return downloaded_count
    
    def _create_error_result(self, playlist_name: str, errors: List[str]) -> SyncResult:
        return SyncResult(
            playlist_name=playlist_name,
            total_tracks=0,
            matched_tracks=0,
            synced_tracks=0,
            downloaded_tracks=0,
            failed_tracks=0,
            sync_time=datetime.now(),
            errors=errors,
            wishlist_added_count=0
        )
    
    def get_sync_preview(self, playlist_name: str) -> Dict[str, Any]:
        try:
            spotify_playlist = self._get_spotify_playlist(playlist_name)
            if not spotify_playlist:
                return {"error": f"Playlist '{playlist_name}' not found"}

            media_client, server_type = self._get_active_media_client()
            if not media_client or not hasattr(media_client, 'search_tracks'):
                return {"error": f"Active media server ({server_type}) doesn't support track search"}

            media_tracks = media_client.search_tracks("", limit=1000)

            match_results = self.matching_engine.match_playlist_tracks(
                spotify_playlist.tracks,
                media_tracks
            )

            stats = self.matching_engine.get_match_statistics(match_results)

            preview = {
                "playlist_name": playlist_name,
                "total_tracks": len(spotify_playlist.tracks),
                f"available_in_{server_type}": stats["matched_tracks"],
                "needs_download": stats["total_tracks"] - stats["matched_tracks"],
                "match_percentage": stats["match_percentage"],
                "confidence_breakdown": stats["confidence_distribution"],
                "tracks_preview": []
            }

            for result in match_results[:10]:
                track_info = {
                    "spotify_track": f"{result.spotify_track.name} - {result.spotify_track.artists[0]}",
                    f"{server_type}_match": getattr(result, 'plex_track', None).title if getattr(result, 'plex_track', None) else None,
                    "confidence": result.confidence,
                    "status": "available" if result.is_match else "needs_download"
                }
                preview["tracks_preview"].append(track_info)

            return preview

        except Exception as e:
            logger.error(f"Error generating sync preview: {e}")
            return {"error": str(e)}
    
    def get_library_comparison(self) -> Dict[str, Any]:
        try:
            spotify_playlists = self.spotify_client.get_user_playlists()
            spotify_track_count = sum(len(p.tracks) for p in spotify_playlists)

            media_client, server_type = self._get_active_media_client()
            if not media_client:
                return {"error": "No active media client available"}

            media_playlists = media_client.get_all_playlists() if hasattr(media_client, 'get_all_playlists') else []
            media_stats = media_client.get_library_stats() if hasattr(media_client, 'get_library_stats') else {}

            comparison = {
                "spotify": {
                    "playlists": len(spotify_playlists),
                    "total_tracks": spotify_track_count
                },
                server_type: {
                    "playlists": len(media_playlists),
                    "artists": media_stats.get("artists", 0),
                    "albums": media_stats.get("albums", 0),
                    "tracks": media_stats.get("tracks", 0)
                },
                "sync_potential": {
                    "estimated_matches": min(spotify_track_count, media_stats.get("tracks", 0)),
                    "potential_downloads": max(0, spotify_track_count - media_stats.get("tracks", 0))
                }
            }

            return comparison

        except Exception as e:
            logger.error(f"Error generating library comparison: {e}")
            return {"error": str(e)}