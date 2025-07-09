import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from utils.logging_config import get_logger
from core.spotify_client import SpotifyClient, Playlist as SpotifyPlaylist
from core.plex_client import PlexClient, PlexTrackInfo
from core.soulseek_client import SoulseekClient
from core.matching_engine import matching_engine, MatchResult

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

class PlaylistSyncService:
    def __init__(self, spotify_client: SpotifyClient, plex_client: PlexClient, soulseek_client: SoulseekClient):
        self.spotify_client = spotify_client
        self.plex_client = plex_client
        self.soulseek_client = soulseek_client
        self.progress_callback = None
        self.is_syncing = False
    
    def set_progress_callback(self, callback):
        self.progress_callback = callback
    
    def _update_progress(self, step: str, track: str, progress: float, total_steps: int, current_step: int):
        if self.progress_callback:
            self.progress_callback(SyncProgress(
                current_step=step,
                current_track=track,
                progress=progress,
                total_steps=total_steps,
                current_step_number=current_step
            ))
    
    async def sync_playlist(self, playlist_name: str, download_missing: bool = False) -> SyncResult:
        if self.is_syncing:
            logger.warning("Sync already in progress")
            return SyncResult(
                playlist_name=playlist_name,
                total_tracks=0,
                matched_tracks=0,
                synced_tracks=0,
                downloaded_tracks=0,
                failed_tracks=0,
                sync_time=datetime.now(),
                errors=["Sync already in progress"]
            )
        
        self.is_syncing = True
        errors = []
        
        try:
            logger.info(f"Starting sync for playlist: {playlist_name}")
            
            self._update_progress("Fetching Spotify playlist", "", 0, 6, 1)
            spotify_playlist = self._get_spotify_playlist(playlist_name)
            if not spotify_playlist:
                errors.append(f"Spotify playlist '{playlist_name}' not found")
                return self._create_error_result(playlist_name, errors)
            
            self._update_progress("Fetching Plex library", "", 16, 6, 2)
            plex_tracks = await self._get_plex_tracks()
            
            self._update_progress("Matching tracks", "", 33, 6, 3)
            match_results = matching_engine.match_playlist_tracks(
                spotify_playlist.tracks, 
                plex_tracks
            )
            
            matched_tracks = [r for r in match_results if r.is_match]
            unmatched_tracks = [r for r in match_results if not r.is_match]
            
            logger.info(f"Found {len(matched_tracks)} matches out of {len(spotify_playlist.tracks)} tracks")
            
            downloaded_tracks = 0
            if download_missing and unmatched_tracks:
                self._update_progress("Downloading missing tracks", "", 50, 6, 4)
                downloaded_tracks = await self._download_missing_tracks(unmatched_tracks)
            
            self._update_progress("Creating/updating Plex playlist", "", 66, 6, 5)
            plex_track_infos = [r.plex_track for r in matched_tracks if r.plex_track]
            
            sync_success = self.plex_client.update_playlist(playlist_name, plex_track_infos)
            
            synced_tracks = len(plex_track_infos) if sync_success else 0
            failed_tracks = len(spotify_playlist.tracks) - synced_tracks - downloaded_tracks
            
            self._update_progress("Sync completed", "", 100, 6, 6)
            
            result = SyncResult(
                playlist_name=playlist_name,
                total_tracks=len(spotify_playlist.tracks),
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
            return self._create_error_result(playlist_name, errors)
        
        finally:
            self.is_syncing = False
    
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
                query = matching_engine.generate_download_query(match_result.spotify_track)
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
            
            match_results = matching_engine.match_playlist_tracks(
                spotify_playlist.tracks, 
                plex_tracks
            )
            
            stats = matching_engine.get_match_statistics(match_results)
            
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