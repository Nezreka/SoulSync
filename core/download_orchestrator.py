"""
Download Orchestrator
Routes downloads between Soulseek, YouTube, Tidal, Qobuz, HiFi, and Deezer based on configuration.

Supports seven modes:
- Soulseek Only: Traditional behavior
- YouTube Only: YouTube-exclusive downloads
- Tidal Only: Tidal-exclusive downloads
- Qobuz Only: Qobuz-exclusive downloads
- HiFi Only: Free lossless downloads via public hifi-api instances
- Deezer Only: Deezer downloads via ARL authentication
- Hybrid: Try primary source first, fallback to others
"""

import asyncio
from typing import List, Optional, Tuple
from pathlib import Path

from utils.logging_config import get_logger
from config.settings import config_manager
from core.soulseek_client import SoulseekClient, TrackResult, AlbumResult, DownloadStatus
from core.youtube_client import YouTubeClient
from core.tidal_download_client import TidalDownloadClient
from core.qobuz_client import QobuzClient
from core.hifi_client import HiFiClient
from core.deezer_download_client import DeezerDownloadClient

logger = get_logger("download_orchestrator")


class DownloadOrchestrator:
    """
    Orchestrates downloads between Soulseek, YouTube, Tidal, Qobuz, and HiFi based on user preferences.

    Acts as a drop-in replacement for SoulseekClient by exposing the same async interface.
    Routes requests to the appropriate client(s) based on configured mode.
    """

    def __init__(self):
        """Initialize orchestrator with all clients"""
        self.soulseek = SoulseekClient()
        self.youtube = YouTubeClient()
        self.tidal = TidalDownloadClient()
        self.qobuz = QobuzClient()
        self.hifi = HiFiClient()
        self.deezer_dl = DeezerDownloadClient()

        # Load mode from config
        self.mode = config_manager.get('download_source.mode', 'soulseek')
        self.hybrid_primary = config_manager.get('download_source.hybrid_primary', 'soulseek')
        self.hybrid_secondary = config_manager.get('download_source.hybrid_secondary', 'youtube')
        self.hybrid_order = config_manager.get('download_source.hybrid_order', [])

        logger.info(f"🎛️  Download Orchestrator initialized - Mode: {self.mode}")
        if self.mode == 'hybrid':
            if self.hybrid_order:
                logger.info(f"   Source priority: {' → '.join(self.hybrid_order)}")
            else:
                logger.info(f"   Primary: {self.hybrid_primary}, Fallback: {self.hybrid_secondary}")

    def reload_settings(self):
        """Reload settings from config (call after settings change)"""
        self.mode = config_manager.get('download_source.mode', 'soulseek')
        self.hybrid_primary = config_manager.get('download_source.hybrid_primary', 'soulseek')
        self.hybrid_secondary = config_manager.get('download_source.hybrid_secondary', 'youtube')
        self.hybrid_order = config_manager.get('download_source.hybrid_order', [])

        # Reload underlying client configs (SLSKD URL, API key, etc.)
        self.soulseek._setup_client()
        logger.info(f"🔄 Soulseek client config reloaded")

        # Reconnect Deezer if ARL changed
        deezer_arl = config_manager.get('deezer_download.arl', '')
        if deezer_arl:
            self.deezer_dl.reconnect(deezer_arl)
            self.deezer_dl._quality = config_manager.get('deezer_download.quality', 'flac')

        logger.info(f"🔄 Download Orchestrator settings reloaded - Mode: {self.mode}")

    def is_configured(self) -> bool:
        """
        Check if orchestrator is configured and ready to use.

        Returns True if at least one download source is configured.
        """
        if self.mode == 'soulseek':
            return self.soulseek.is_configured()
        elif self.mode == 'youtube':
            return self.youtube.is_configured()
        elif self.mode == 'tidal':
            return self.tidal.is_configured()
        elif self.mode == 'qobuz':
            return self.qobuz.is_configured()
        elif self.mode == 'hifi':
            return self.hifi.is_configured()
        elif self.mode == 'deezer_dl':
            return self.deezer_dl.is_configured()
        elif self.mode == 'hybrid':
            clients = {'soulseek': self.soulseek, 'youtube': self.youtube, 'tidal': self.tidal, 'qobuz': self.qobuz, 'hifi': self.hifi, 'deezer_dl': self.deezer_dl}
            sources = self.hybrid_order if self.hybrid_order else [self.hybrid_primary, self.hybrid_secondary]
            return any(clients[s].is_configured() for s in sources if s in clients)

        return False

    def get_source_status(self) -> dict:
        """Return configured status for each download source."""
        clients = {
            'soulseek': self.soulseek,
            'youtube': self.youtube,
            'tidal': self.tidal,
            'qobuz': self.qobuz,
            'hifi': self.hifi,
            'deezer_dl': self.deezer_dl,
        }
        return {name: client.is_configured() for name, client in clients.items()}

    async def check_connection(self) -> bool:
        """
        Test if download sources are accessible.

        Returns True if the configured source(s) are reachable.
        """
        if self.mode == 'soulseek':
            return await self.soulseek.check_connection()
        elif self.mode == 'youtube':
            return await self.youtube.check_connection()
        elif self.mode == 'tidal':
            return await self.tidal.check_connection()
        elif self.mode == 'qobuz':
            return await self.qobuz.check_connection()
        elif self.mode == 'hifi':
            return await self.hifi.check_connection()
        elif self.mode == 'deezer_dl':
            return await self.deezer_dl.check_connection()
        elif self.mode == 'hybrid':
            soulseek_ok = await self.soulseek.check_connection()
            youtube_ok = await self.youtube.check_connection()
            tidal_ok = await self.tidal.check_connection()
            qobuz_ok = await self.qobuz.check_connection()
            hifi_ok = await self.hifi.check_connection()
            deezer_ok = await self.deezer_dl.check_connection()

            logger.info(f"   Soulseek: {'✅' if soulseek_ok else '❌'} | YouTube: {'✅' if youtube_ok else '❌'} | Tidal: {'✅' if tidal_ok else '❌'} | Qobuz: {'✅' if qobuz_ok else '❌'} | HiFi: {'✅' if hifi_ok else '❌'} | Deezer: {'✅' if deezer_ok else '❌'}")

            return soulseek_ok or youtube_ok or tidal_ok or qobuz_ok or hifi_ok or deezer_ok

        return False

    async def search(self, query: str, timeout: int = None, progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """
        Search for tracks using configured source(s).

        Args:
            query: Search query
            timeout: Search timeout (for Soulseek)
            progress_callback: Progress callback (for Soulseek)

        Returns:
            Tuple of (track_results, album_results)
        """
        if self.mode == 'soulseek':
            logger.info(f"🔍 Searching Soulseek: {query}")
            return await self.soulseek.search(query, timeout, progress_callback)

        elif self.mode == 'youtube':
            logger.info(f"🔍 Searching YouTube: {query}")
            return await self.youtube.search(query, timeout, progress_callback)

        elif self.mode == 'tidal':
            logger.info(f"🔍 Searching Tidal: {query}")
            return await self.tidal.search(query, timeout, progress_callback)

        elif self.mode == 'qobuz':
            logger.info(f"🔍 Searching Qobuz: {query}")
            return await self.qobuz.search(query, timeout, progress_callback)

        elif self.mode == 'hifi':
            logger.info(f"🔍 Searching HiFi: {query}")
            return await self.hifi.search(query, timeout, progress_callback)

        elif self.mode == 'deezer_dl':
            logger.info(f"🔍 Searching Deezer: {query}")
            return await self.deezer_dl.search(query, timeout, progress_callback)

        elif self.mode == 'hybrid':
            clients = {
                'soulseek': self.soulseek,
                'youtube': self.youtube,
                'tidal': self.tidal,
                'qobuz': self.qobuz,
                'hifi': self.hifi,
                'deezer_dl': self.deezer_dl,
            }

            # Build ordered source list: prefer hybrid_order, fall back to legacy primary/secondary
            if self.hybrid_order:
                source_order = [s for s in self.hybrid_order if s in clients]
            else:
                primary = self.hybrid_primary if self.hybrid_primary in clients else 'soulseek'
                secondary = self.hybrid_secondary if self.hybrid_secondary in clients else 'soulseek'
                if secondary == primary:
                    secondary = next((name for name in clients if name != primary), 'soulseek')
                source_order = [primary, secondary]

            if not source_order:
                source_order = ['soulseek']

            logger.info(f"🔍 Hybrid search ({' → '.join(source_order)}): {query}")

            # Try each source in priority order (skip unconfigured ones)
            for i, source_name in enumerate(source_order):
                client = clients[source_name]
                if hasattr(client, 'is_configured') and not client.is_configured():
                    logger.info(f"⏭️ Skipping {source_name} (not configured)")
                    continue

                try:
                    if i == 0:
                        logger.info(f"🔍 Trying {source_name} (priority {i+1}): {query}")
                    else:
                        logger.info(f"🔄 Trying {source_name} (priority {i+1}): {query}")

                    tracks, albums = await client.search(query, timeout, progress_callback)
                    if tracks:
                        logger.info(f"✅ {source_name} found {len(tracks)} tracks")
                        return (tracks, albums)
                except Exception as e:
                    logger.warning(f"⚠️ {source_name} search failed: {e}")

            # Nothing found from any source
            logger.warning(f"❌ Hybrid search: all sources ({', '.join(source_order)}) found nothing for: {query}")
            return ([], [])

        # Fallback: empty results
        return ([], [])

    async def search_and_download_best(self, query: str) -> Optional[str]:
        """
        Search and automatically download the best result.
        Supports Hybrid mode (uses configured source priority).
        
        Args:
            query: Search query string
            
        Returns:
            Download ID (str) or None if failed
        """
        # 1. Search using configured mode/hybrid logic
        results = await self.search(query)
        
        # Unpack tuple (tracks, albums) - defensive handling
        if isinstance(results, tuple):
            tracks = results[0]
        else:
            tracks = results # Should not happen based on search() return type, but safe
            
        if not tracks:
            logger.warning(f"No results found for: {query}")
            return None

        # 2. Filter using Soulseek's quality preferences (Soulseek only)
        # Streaming sources (YouTube/Tidal/Qobuz) handle quality internally
        is_streaming = tracks[0].username in ('youtube', 'tidal', 'qobuz', 'hifi') if tracks else False
        if is_streaming:
            filtered_results = tracks
        else:
            filtered_results = self.soulseek.filter_results_by_quality_preference(tracks)

        if not filtered_results:
            logger.warning(f"No suitable quality results found for: {query}")
            return None

        # 3. Download the best match
        best_result = filtered_results[0]
        
        quality_info = f"{best_result.quality.upper()}"
        if best_result.bitrate:
            quality_info += f" {best_result.bitrate}kbps"

        logger.info(f"Downloading best match: {best_result.filename} ({quality_info}) from {best_result.username}")
        
        # Use orchestrator's download method to route correctly
        return await self.download(best_result.username, best_result.filename, best_result.size)

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """
        Download a track using the appropriate client.

        Args:
            username: Username (or "youtube" for YouTube)
            filename: Filename or YouTube video ID
            file_size: File size estimate

        Returns:
            download_id: Unique download ID for tracking
        """
        # Detect which client to use based on username
        if username == 'youtube':
            logger.info(f"📥 Downloading from YouTube: {filename}")
            return await self.youtube.download(username, filename, file_size)
        elif username == 'tidal':
            logger.info(f"📥 Downloading from Tidal: {filename}")
            return await self.tidal.download(username, filename, file_size)
        elif username == 'qobuz':
            logger.info(f"📥 Downloading from Qobuz: {filename}")
            return await self.qobuz.download(username, filename, file_size)
        elif username == 'hifi':
            logger.info(f"📥 Downloading from HiFi: {filename}")
            return await self.hifi.download(username, filename, file_size)
        elif username == 'deezer_dl':
            logger.info(f"📥 Downloading from Deezer: {filename}")
            return await self.deezer_dl.download(username, filename, file_size)
        else:
            logger.info(f"📥 Downloading from Soulseek: {filename}")
            return await self.soulseek.download(username, filename, file_size)

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """
        Get all active downloads from all sources.

        Returns:
            List of DownloadStatus objects
        """
        # Get downloads from all sources
        soulseek_downloads = await self.soulseek.get_all_downloads()
        youtube_downloads = await self.youtube.get_all_downloads()
        tidal_downloads = await self.tidal.get_all_downloads()
        qobuz_downloads = await self.qobuz.get_all_downloads()
        hifi_downloads = await self.hifi.get_all_downloads()
        deezer_downloads = await self.deezer_dl.get_all_downloads()

        return soulseek_downloads + youtube_downloads + tidal_downloads + qobuz_downloads + hifi_downloads + deezer_downloads

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """
        Get status of a specific download.

        Args:
            download_id: Download ID to query

        Returns:
            DownloadStatus object or None if not found
        """
        # Try Soulseek first
        status = await self.soulseek.get_download_status(download_id)
        if status:
            return status

        # Try YouTube
        status = await self.youtube.get_download_status(download_id)
        if status:
            return status

        # Try Tidal
        status = await self.tidal.get_download_status(download_id)
        if status:
            return status

        # Try Qobuz
        status = await self.qobuz.get_download_status(download_id)
        if status:
            return status

        # Try HiFi
        status = await self.hifi.get_download_status(download_id)
        if status:
            return status

        # Try Deezer
        status = await self.deezer_dl.get_download_status(download_id)
        if status:
            return status

        return None

    async def cancel_download(self, download_id: str, username: str = None, remove: bool = False) -> bool:
        """
        Cancel an active download.

        Args:
            download_id: Download ID to cancel
            username: Username hint (optional)
            remove: Whether to remove from active downloads

        Returns:
            True if cancelled successfully
        """
        # If username is provided, route directly
        if username == 'youtube':
            return await self.youtube.cancel_download(download_id, username, remove)
        elif username == 'tidal':
            return await self.tidal.cancel_download(download_id, username, remove)
        elif username == 'qobuz':
            return await self.qobuz.cancel_download(download_id, username, remove)
        elif username == 'hifi':
            return await self.hifi.cancel_download(download_id, username, remove)
        elif username == 'deezer_dl':
            return await self.deezer_dl.cancel_download(download_id, username, remove)
        elif username:
            return await self.soulseek.cancel_download(download_id, username, remove)

        # Otherwise, try all sources
        soulseek_cancelled = await self.soulseek.cancel_download(download_id, username, remove)
        if soulseek_cancelled:
            return True

        youtube_cancelled = await self.youtube.cancel_download(download_id, username, remove)
        if youtube_cancelled:
            return True

        tidal_cancelled = await self.tidal.cancel_download(download_id, username, remove)
        if tidal_cancelled:
            return True

        qobuz_cancelled = await self.qobuz.cancel_download(download_id, username, remove)
        if qobuz_cancelled:
            return True

        hifi_cancelled = await self.hifi.cancel_download(download_id, username, remove)
        if hifi_cancelled:
            return True

        deezer_cancelled = await self.deezer_dl.cancel_download(download_id, username, remove)
        return deezer_cancelled

    async def signal_download_completion(self, download_id: str, username: str, remove: bool = True) -> bool:
        """
        Signal that a download has completed (Soulseek only).

        Args:
            download_id: Download ID
            username: Username
            remove: Whether to remove from active downloads

        Returns:
            True if successful
        """
        # This is Soulseek-specific, so only call on Soulseek client
        return await self.soulseek.signal_download_completion(download_id, username, remove)

    async def clear_all_completed_downloads(self) -> bool:
        """
        Clear all completed downloads from both sources.

        Returns:
            True if successful
        """
        soulseek_cleared = await self.soulseek.clear_all_completed_downloads()
        youtube_cleared = await self.youtube.clear_all_completed_downloads()
        tidal_cleared = await self.tidal.clear_all_completed_downloads()
        qobuz_cleared = await self.qobuz.clear_all_completed_downloads()
        hifi_cleared = await self.hifi.clear_all_completed_downloads()
        deezer_cleared = await self.deezer_dl.clear_all_completed_downloads()

        return soulseek_cleared and youtube_cleared and tidal_cleared and qobuz_cleared and hifi_cleared and deezer_cleared

    # ===== Soulseek-specific methods (for backwards compatibility) =====
    # These are internal methods that some parts of the codebase use directly

    async def _make_request(self, method: str, endpoint: str, **kwargs):
        """
        Proxy to SoulseekClient._make_request for backwards compatibility.
        This is a Soulseek-specific internal method.

        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional request parameters

        Returns:
            API response
        """
        return await self.soulseek._make_request(method, endpoint, **kwargs)

    async def _make_direct_request(self, method: str, endpoint: str, **kwargs):
        """
        Proxy to SoulseekClient._make_direct_request for backwards compatibility.
        This is a Soulseek-specific internal method.

        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional request parameters

        Returns:
            API response
        """
        return await self.soulseek._make_direct_request(method, endpoint, **kwargs)

    async def clear_all_searches(self) -> bool:
        """
        Clear all searches (Soulseek-specific).

        Returns:
            True if successful
        """
        return await self.soulseek.clear_all_searches()

    async def maintain_search_history_with_buffer(self, keep_searches: int = 50, trigger_threshold: int = 200) -> bool:
        """
        Maintain search history (Soulseek-specific).

        Args:
            keep_searches: Number of searches to keep
            trigger_threshold: Threshold to trigger cleanup

        Returns:
            True if successful
        """
        return await self.soulseek.maintain_search_history_with_buffer(keep_searches, trigger_threshold)

    async def cancel_all_downloads(self) -> bool:
        """Cancel and remove all downloads from all sources."""
        soulseek_ok = await self.soulseek.cancel_all_downloads()
        await self.tidal.clear_all_completed_downloads()
        await self.qobuz.clear_all_completed_downloads()
        await self.hifi.clear_all_completed_downloads()
        await self.deezer_dl.clear_all_completed_downloads()
        return soulseek_ok
