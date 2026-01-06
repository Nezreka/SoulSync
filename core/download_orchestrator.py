"""
Download Orchestrator
Routes downloads between Soulseek and YouTube based on configuration.

Supports three modes:
- Soulseek Only: Traditional behavior
- YouTube Only: YouTube-exclusive downloads
- Hybrid: Try primary source first, fallback to secondary if it fails
"""

import asyncio
from typing import List, Optional, Tuple
from pathlib import Path

from utils.logging_config import get_logger
from config.settings import config_manager
from core.soulseek_client import SoulseekClient, TrackResult, AlbumResult, DownloadStatus
from core.youtube_client import YouTubeClient

logger = get_logger("download_orchestrator")


class DownloadOrchestrator:
    """
    Orchestrates downloads between Soulseek and YouTube based on user preferences.

    Acts as a drop-in replacement for SoulseekClient by exposing the same async interface.
    Routes requests to the appropriate client(s) based on configured mode.
    """

    def __init__(self):
        """Initialize orchestrator with both clients"""
        self.soulseek = SoulseekClient()
        self.youtube = YouTubeClient()

        # Load mode from config
        self.mode = config_manager.get('download_source.mode', 'soulseek')
        self.hybrid_primary = config_manager.get('download_source.hybrid_primary', 'soulseek')
        self.youtube_min_confidence = config_manager.get('download_source.youtube_min_confidence', 0.65)

        logger.info(f"🎛️  Download Orchestrator initialized - Mode: {self.mode}")
        if self.mode == 'hybrid':
            logger.info(f"   Primary source: {self.hybrid_primary}")

    def reload_settings(self):
        """Reload settings from config (call after settings change)"""
        self.mode = config_manager.get('download_source.mode', 'soulseek')
        self.hybrid_primary = config_manager.get('download_source.hybrid_primary', 'soulseek')
        self.youtube_min_confidence = config_manager.get('download_source.youtube_min_confidence', 0.65)

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
        elif self.mode == 'hybrid':
            # In hybrid mode, at least one source must be configured
            return self.soulseek.is_configured() or self.youtube.is_configured()

        return False

    async def check_connection(self) -> bool:
        """
        Test if download sources are accessible.

        Returns True if the configured source(s) are reachable.
        """
        if self.mode == 'soulseek':
            return await self.soulseek.check_connection()
        elif self.mode == 'youtube':
            return await self.youtube.check_connection()
        elif self.mode == 'hybrid':
            # In hybrid mode, check both sources
            soulseek_ok = await self.soulseek.check_connection()
            youtube_ok = await self.youtube.check_connection()

            logger.info(f"   Soulseek: {'✅' if soulseek_ok else '❌'} | YouTube: {'✅' if youtube_ok else '❌'}")

            # At least one must be available
            return soulseek_ok or youtube_ok

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

        elif self.mode == 'hybrid':
            # Try primary source first
            if self.hybrid_primary == 'soulseek':
                logger.info(f"🔍 Hybrid search - trying Soulseek first: {query}")
                tracks, albums = await self.soulseek.search(query, timeout, progress_callback)

                # If Soulseek found good results, return them
                if tracks:
                    logger.info(f"✅ Soulseek found {len(tracks)} tracks")
                    return (tracks, albums)

                # Otherwise, try YouTube as fallback
                logger.info(f"🔄 Soulseek found nothing, trying YouTube fallback")
                return await self.youtube.search(query, timeout, progress_callback)

            else:  # YouTube first
                logger.info(f"🔍 Hybrid search - trying YouTube first: {query}")
                tracks, albums = await self.youtube.search(query, timeout, progress_callback)

                # If YouTube found good results, return them
                if tracks:
                    logger.info(f"✅ YouTube found {len(tracks)} tracks")
                    return (tracks, albums)

                # Otherwise, try Soulseek as fallback
                logger.info(f"🔄 YouTube found nothing, trying Soulseek fallback")
                return await self.soulseek.search(query, timeout, progress_callback)

        # Fallback: empty results
        return ([], [])

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
        else:
            logger.info(f"📥 Downloading from Soulseek: {filename}")
            return await self.soulseek.download(username, filename, file_size)

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """
        Get all active downloads from all sources.

        Returns:
            List of DownloadStatus objects
        """
        # Get downloads from both sources
        soulseek_downloads = await self.soulseek.get_all_downloads()
        youtube_downloads = await self.youtube.get_all_downloads()

        # Combine and return
        return soulseek_downloads + youtube_downloads

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
        elif username:
            return await self.soulseek.cancel_download(download_id, username, remove)

        # Otherwise, try both sources
        soulseek_cancelled = await self.soulseek.cancel_download(download_id, username, remove)
        if soulseek_cancelled:
            return True

        youtube_cancelled = await self.youtube.cancel_download(download_id, username, remove)
        return youtube_cancelled

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
        # YouTube downloads must also be cleared from memory
        youtube_cleared = await self.youtube.clear_all_completed_downloads()

        return soulseek_cleared and youtube_cleared

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
