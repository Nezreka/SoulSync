import asyncio
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
from utils.logging_config import get_logger
from core.spotify_client import SpotifyClient, Playlist as SpotifyPlaylist, Track as SpotifyTrack
from core.plex_client import PlexClient, PlexTrackInfo
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
    def __init__(self, spotify_client: SpotifyClient, plex_client: PlexClient, soulseek_client: SoulseekClient):
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.soulseek_client = soulseek_client
        self.progress_callback = None
        self.is_syncing = False
        self._cancelled = False
        self.matching_engine = MusicMatchingEngine()
    
    def set_progress_callback(self, callback):
        self.progress_callback = callback
    
    def cancel_sync(self):
        """Cancel the current sync operation"""
        self._cancelled = True
        self.is_syncing = False
    
    def _update_progress(self, step: str, track: str, progress: float, total_steps: int, current_step: int, 
                        total_tracks: int = 0, matched_tracks: int = 0, failed_tracks: int = 0):
        if self.progress_callback:
            self.progress_callback(SyncProgress(
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
        if self.is_syncing:
            logger.warning("Sync already in progress")
            return SyncResult(
                playlist_name=playlist.name,
                total_tracks=0,
                matched_tracks=0,
                synced_tracks=0,
                downloaded_tracks=0,
                failed_tracks=0,
                sync_time=datetime.now(),
                errors=["Sync already in progress"]
            )
        
        self.is_syncing = True
        self._cancelled = False
        errors = []
        
        try:
            logger.info(f"Starting sync for playlist: {playlist.name}")
            
            if self._cancelled:
                return self._create_error_result(playlist.name, ["Sync cancelled"])
            
            # Skip fetching playlist since we already have it
            self._update_progress("Preparing playlist sync", "", 10, 5, 1)
            
            if not playlist.tracks:
                errors.append(f"Playlist '{playlist.name}' has no tracks")
                return self._create_error_result(playlist.name, errors)
            
            if self._cancelled:
                return self._create_error_result(playlist.name, ["Sync cancelled"])
            
            total_tracks = len(playlist.tracks)
            
            self._update_progress("Matching tracks against Plex library", "", 20, 5, 2, total_tracks=total_tracks)
            
            # Use the same robust matching approach as "Download Missing Tracks"
            match_results = []
            for i, track in enumerate(playlist.tracks):
                if self._cancelled:
                    return self._create_error_result(playlist.name, ["Sync cancelled"])
                
                # Update progress for each track
                progress_percent = 20 + (40 * (i + 1) / total_tracks)  # 20-60% for matching
                current_track_name = f"{track.artists[0]} - {track.name}" if track.artists else track.name
                self._update_progress("Matching tracks", current_track_name, progress_percent, 5, 2, 
                                    total_tracks=total_tracks,
                                    matched_tracks=len([r for r in match_results if r.is_match]),
                                    failed_tracks=len([r for r in match_results if not r.is_match]))
                
                # Use the robust search approach
                plex_match, confidence = await self._find_track_in_plex(track)
                
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
            self._update_progress("Matching completed", "", 60, 5, 3, 
                                total_tracks=total_tracks, 
                                matched_tracks=len(matched_tracks), 
                                failed_tracks=len(unmatched_tracks))
            
            downloaded_tracks = 0
            if download_missing and unmatched_tracks:
                if self._cancelled:
                    return self._create_error_result(playlist.name, ["Sync cancelled"])
                self._update_progress("Downloading missing tracks", "", 70, 5, 4, 
                                    total_tracks=total_tracks,
                                    matched_tracks=len(matched_tracks),
                                    failed_tracks=len(unmatched_tracks))
                downloaded_tracks = await self._download_missing_tracks(unmatched_tracks)
            
            if self._cancelled:
                return self._create_error_result(playlist.name, ["Sync cancelled"])
            
            self._update_progress("Creating/updating Plex playlist", "", 80, 5, 4,
                                total_tracks=total_tracks,
                                matched_tracks=len(matched_tracks),
                                failed_tracks=len(unmatched_tracks))
            
            # Get the actual Plex track objects (not PlexTrackInfo)
            plex_tracks = [r.plex_track for r in matched_tracks if r.plex_track]
            logger.info(f"Creating playlist with {len(plex_tracks)} matched tracks")
            
            sync_success = self.plex_client.update_playlist(playlist.name, plex_tracks)
            
            synced_tracks = len(plex_tracks) if sync_success else 0
            failed_tracks = len(playlist.tracks) - synced_tracks - downloaded_tracks
            
            self._update_progress("Sync completed", "", 100, 5, 5,
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
            self.is_syncing = False
            self._cancelled = False
    
    async def _find_track_in_plex(self, spotify_track: SpotifyTrack) -> Tuple[Optional[PlexTrackInfo], float]:
        """Find a track in Plex using the same robust search approach as Download Missing Tracks"""
        try:
            if not self.plex_client or not self.plex_client.is_connected():
                logger.warning("Plex client not connected")
                return None, 0.0
            
            # Use same robust search logic as PlaylistTrackAnalysisWorker
            original_title = spotify_track.name
            
            # Create title variations
            unique_title_variations = []
            original_clean = self.matching_engine.get_core_string(original_title)
            unique_title_variations.append(original_clean)
            
            # Add cleaned version
            cleaned_version = self.matching_engine.clean_title(original_title)
            if cleaned_version != original_clean:
                unique_title_variations.append(cleaned_version)
            
            all_potential_matches = []
            found_match_ids = set()
            
            # Search by artist + title combinations
            for artist in spotify_track.artists[:2]:  # Limit to first 2 artists
                if self._cancelled:
                    return None, 0.0
                
                artist_name = self.matching_engine.clean_artist(artist)
                
                for query_title in unique_title_variations:
                    if self._cancelled:
                        return None, 0.0
                    
                    potential_plex_matches = self.plex_client.search_tracks(
                        title=query_title,
                        artist=artist_name,
                        limit=15
                    )
                    
                    for track in potential_plex_matches:
                        if track.id not in found_match_ids:
                            all_potential_matches.append(track)
                            found_match_ids.add(track.id)
                
                # Early exit check for confident match
                if all_potential_matches:
                    match_result = self.matching_engine.find_best_match(spotify_track, all_potential_matches)
                    if match_result.is_match:
                        logger.debug(f"Early confident match found for '{original_title}'")
                        return match_result.plex_track, match_result.confidence
            
            # Fallback: Title-only search
            if not all_potential_matches:
                logger.debug(f"No artist-based matches found. Using title-only fallback for '{original_title}'")
                for query_title in unique_title_variations:
                    title_only_matches = self.plex_client.search_tracks(title=query_title, artist="", limit=10)
                    for track in title_only_matches:
                        if track.id not in found_match_ids:
                            all_potential_matches.append(track)
                            found_match_ids.add(track.id)
            
            if not all_potential_matches:
                logger.debug(f"No Plex candidates found for '{original_title}'")
                return None, 0.0
            
            # Final scoring
            final_match_result = self.matching_engine.find_best_match(spotify_track, all_potential_matches)
            
            if final_match_result.is_match:
                logger.debug(f"Match found for '{original_title}': '{final_match_result.plex_track.title}' (confidence: {final_match_result.confidence:.2f})")
            else:
                logger.debug(f"No confident match for '{original_title}' (best score: {final_match_result.confidence:.2f})")
            
            return final_match_result.plex_track, final_match_result.confidence
            
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