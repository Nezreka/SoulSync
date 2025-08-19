import asyncio
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from utils.logging_config import get_logger
from core.spotify_client import SpotifyClient, Playlist as SpotifyPlaylist, Track as SpotifyTrack
from core.plex_client import PlexClient, PlexTrackInfo
from core.jellyfin_client import JellyfinClient
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
    def __init__(self, spotify_client: SpotifyClient, plex_client: PlexClient, soulseek_client: SoulseekClient, jellyfin_client: JellyfinClient = None):
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.jellyfin_client = jellyfin_client
        self.soulseek_client = soulseek_client
        self.progress_callbacks = {}  # Playlist-specific progress callbacks
        self.syncing_playlists = set()  # Track multiple syncing playlists
        self._cancelled = False
        self.matching_engine = MusicMatchingEngine()
    
    def _get_active_media_client(self):
        """Get the active media client based on config settings"""
        try:
            from config.settings import config_manager
            active_server = config_manager.get_active_media_server()
            
            if active_server == "jellyfin":
                if not self.jellyfin_client:
                    logger.error("Jellyfin client not provided to sync service")
                    return None, "jellyfin"
                return self.jellyfin_client, "jellyfin"
            else:  # Default to Plex
                return self.plex_client, "plex"
        except Exception as e:
            logger.error(f"Error determining active media server: {e}")
            return self.plex_client, "plex"  # Fallback to Plex
    
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
    
    async def sync_playlist(self, playlist: SpotifyPlaylist, download_missing: bool = False) -> SyncResult:
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

            self._update_progress(playlist.name, "Matching tracks against Plex library", "", 20, 5, 2, total_tracks=total_tracks)
            
            # Use the same robust matching approach as "Download Missing Tracks"
            match_results = []
            for i, track in enumerate(playlist.tracks):
                if self._cancelled:
                    return self._create_error_result(playlist.name, ["Sync cancelled"])
                
                # Update progress for each track
                progress_percent = 20 + (40 * (i + 1) / total_tracks)  # 20-60% for matching
                current_track_name = f"{track.artists[0]} - {track.name}" if track.artists else track.name
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
            
            self._update_progress(playlist.name, "Creating/updating Plex playlist", "", 80, 5, 4,
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
                    logger.debug(f"✔️ Track {i+1} valid for playlist: '{track.title}' (ratingKey: {track.ratingKey})")
                else:
                    logger.warning(f"❌ Track {i+1} invalid for playlist: {track} (type: {type(track)}, has ratingKey: {hasattr(track, 'ratingKey') if track else 'N/A'})")
            
            logger.info(f"Playlist validation: {len(valid_tracks)}/{len(media_tracks)} tracks are valid {server_type.title()} objects with ratingKeys")
            
            # Use the validated tracks for the sync
            plex_tracks = valid_tracks # Keep variable name for compatibility with the rest of the function
            
            # Use active media server for playlist sync
            media_client, server_type = self._get_active_media_client()
            if not media_client:
                logger.error(f"No active media client available for playlist sync")
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
            
            result = SyncResult(
                playlist_name=playlist.name,
                total_tracks=len(playlist.tracks),
                matched_tracks=len(matched_tracks),
                synced_tracks=synced_tracks,
                downloaded_tracks=downloaded_tracks,
                failed_tracks=failed_tracks,
                sync_time=datetime.now(),
                errors=errors
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
            
            original_title = spotify_track.name
            
            # Try each artist (same as modal logic)
            for artist in spotify_track.artists:
                if self._cancelled:
                    return None, 0.0
                
                artist_name = artist if isinstance(artist, str) else artist
                
                # Use the improved database check_track_exists method with server awareness
                try:
                    from config.settings import config_manager
                    active_server = config_manager.get_active_media_server()
                    db = MusicDatabase()
                    db_track, confidence = db.check_track_exists(original_title, artist_name, confidence_threshold=0.7, server_source=active_server)
                    
                    if db_track and confidence >= 0.7:
                        logger.debug(f"✔️ Database match found for '{original_title}' by '{artist_name}': '{db_track.title}' with confidence {confidence:.2f}")
                        
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
                                logger.debug(f"✔️ Created Jellyfin track object for '{db_track.title}' (ID: {actual_track.ratingKey})")
                                return actual_track, confidence
                            else:
                                # For Plex, use the original fetchItem approach
                                # Validate that the track ID is numeric (Plex requirement)
                                try:
                                    track_id = int(db_track.id)
                                    actual_plex_track = media_client.server.fetchItem(track_id)
                                    if actual_plex_track and hasattr(actual_plex_track, 'ratingKey'):
                                        logger.debug(f"✔️ Successfully fetched actual Plex track for '{db_track.title}' (ratingKey: {actual_plex_track.ratingKey})")
                                        return actual_plex_track, confidence
                                    else:
                                        logger.warning(f"❌ Fetched Plex track for '{db_track.title}' lacks ratingKey attribute")
                                except ValueError:
                                    logger.warning(f"❌ Invalid Plex track ID format for '{db_track.title}' (ID: {db_track.id}) - skipping this track")
                                    continue
                                
                        except Exception as fetch_error:
                            logger.error(f"❌ Failed to fetch actual {server_type} track for '{db_track.title}' (ID: {db_track.id}): {fetch_error}")
                            # Continue to try other artists rather than fail completely
                            continue
                        
                except Exception as db_error:
                    logger.error(f"Error checking track existence for '{original_title}' by '{artist_name}': {db_error}")
                    continue
            
            logger.debug(f"❌ No database match found for '{original_title}' by any of the artists {spotify_track.artists}")
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
    
    async def _get_plex_tracks(self) -> List[PlexTrackInfo]:
        try:
            return self.plex_client.search_tracks("", limit=10000)
        except Exception as e:
            logger.error(f"Error fetching Plex tracks: {e}")
            return []
    
    async def _download_missing_tracks(self, unmatched_tracks: List[MatchResult]) -> int:
        downloaded_count = 0
        
        for match_result in unmatched_tracks:
            try:
                query = self.matching_engine.generate_download_query(match_result.spotify_track)
                logger.info(f"Attempting to download: {query}")
                
                download_id = await self.soulseek_client.search_and_download_best(query)
                
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
            errors=errors
        )
    
    def get_sync_preview(self, playlist_name: str) -> Dict[str, Any]:
        try:
            spotify_playlist = self._get_spotify_playlist(playlist_name)
            if not spotify_playlist:
                return {"error": f"Playlist '{playlist_name}' not found"}
            
            plex_tracks = self.plex_client.search_tracks("", limit=1000)
            
            match_results = self.matching_engine.match_playlist_tracks(
                spotify_playlist.tracks, 
                plex_tracks
            )
            
            stats = self.matching_engine.get_match_statistics(match_results)
            
            preview = {
                "playlist_name": playlist_name,
                "total_tracks": len(spotify_playlist.tracks),
                "available_in_plex": stats["matched_tracks"],
                "needs_download": stats["total_tracks"] - stats["matched_tracks"],
                "match_percentage": stats["match_percentage"],
                "confidence_breakdown": stats["confidence_distribution"],
                "tracks_preview": []
            }
            
            for result in match_results[:10]:
                track_info = {
                    "spotify_track": f"{result.spotify_track.name} - {result.spotify_track.artists[0]}",
                    "plex_match": result.plex_track.title if result.plex_track else None,
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
            plex_playlists = self.plex_client.get_all_playlists()
            plex_stats = self.plex_client.get_library_stats()
            
            spotify_track_count = sum(len(p.tracks) for p in spotify_playlists)
            
            comparison = {
                "spotify": {
                    "playlists": len(spotify_playlists),
                    "total_tracks": spotify_track_count
                },
                "plex": {
                    "playlists": len(plex_playlists),
                    "artists": plex_stats.get("artists", 0),
                    "albums": plex_stats.get("albums", 0),
                    "tracks": plex_stats.get("tracks", 0)
                },
                "sync_potential": {
                    "estimated_matches": min(spotify_track_count, plex_stats.get("tracks", 0)),
                    "potential_downloads": max(0, spotify_track_count - plex_stats.get("tracks", 0))
                }
            }
            
            return comparison
            
        except Exception as e:
            logger.error(f"Error generating library comparison: {e}")
            return {"error": str(e)}