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
from core.lidarr_download_client import LidarrDownloadClient

logger = get_logger("download_orchestrator")


class DownloadOrchestrator:
    """
    Orchestrates downloads between Soulseek, YouTube, Tidal, Qobuz, and HiFi based on user preferences.

    Acts as a drop-in replacement for SoulseekClient by exposing the same async interface.
    Routes requests to the appropriate client(s) based on configured mode.
    """

    def __init__(self):
        """Initialize orchestrator with all clients.
        Each client is initialized independently — one failing client doesn't prevent others from working."""
        self._init_failures = []

        self.soulseek = self._safe_init('Soulseek', SoulseekClient)
        self.youtube = self._safe_init('YouTube', YouTubeClient)
        self.tidal = self._safe_init('Tidal', TidalDownloadClient)
        self.qobuz = self._safe_init('Qobuz', QobuzClient)
        self.hifi = self._safe_init('HiFi', HiFiClient)
        self.deezer_dl = self._safe_init('Deezer', DeezerDownloadClient)
        self.lidarr = self._safe_init('Lidarr', LidarrDownloadClient)

        if self._init_failures:
            logger.warning(f"Download clients failed to initialize: {', '.join(self._init_failures)}")

        # Load mode from config
        self.mode = config_manager.get('download_source.mode', 'soulseek')
        self.hybrid_primary = config_manager.get('download_source.hybrid_primary', 'soulseek')
        self.hybrid_secondary = config_manager.get('download_source.hybrid_secondary', 'youtube')
        self.hybrid_order = config_manager.get('download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'])

        logger.info(f"Download Orchestrator initialized - Mode: {self.mode}")
        if self.mode == 'hybrid':
            if self.hybrid_order:
                logger.info(f"   Source priority: {' → '.join(self.hybrid_order)}")
            else:
                logger.info(f"   Primary: {self.hybrid_primary}, Fallback: {self.hybrid_secondary}")

    def _safe_init(self, name, cls):
        """Initialize a download client, returning None on failure instead of crashing."""
        try:
            return cls()
        except Exception as e:
            logger.error(f"{name} download client failed to initialize: {e}")
            self._init_failures.append(name)
            return None

    def reload_settings(self):
        """Reload settings from config (call after settings change)"""
        self.mode = config_manager.get('download_source.mode', 'soulseek')
        self.hybrid_primary = config_manager.get('download_source.hybrid_primary', 'soulseek')
        self.hybrid_secondary = config_manager.get('download_source.hybrid_secondary', 'youtube')
        self.hybrid_order = config_manager.get('download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'])

        # Reload underlying client configs (SLSKD URL, API key, etc.)
        if self.soulseek:
            self.soulseek._setup_client()
            logger.info(f"Soulseek client config reloaded")

        # Reconnect Deezer if ARL changed
        deezer_arl = config_manager.get('deezer_download.arl', '')
        if deezer_arl and self.deezer_dl:
            self.deezer_dl.reconnect(deezer_arl)
            self.deezer_dl._quality = config_manager.get('deezer_download.quality', 'flac')

        logger.info(f"Download Orchestrator settings reloaded - Mode: {self.mode}")

    def _client(self, name):
        """Get a client by name, returning None if not initialized."""
        return {'soulseek': self.soulseek, 'youtube': self.youtube, 'tidal': self.tidal,
                'qobuz': self.qobuz, 'hifi': self.hifi, 'deezer_dl': self.deezer_dl,
                'lidarr': self.lidarr}.get(name)

    def is_configured(self) -> bool:
        """
        Check if orchestrator is configured and ready to use.

        Returns True if at least one download source is configured.
        """
        client = self._client(self.mode)
        if client:
            return client.is_configured()
        elif self.mode == 'hybrid':
            sources = self.hybrid_order if self.hybrid_order else [self.hybrid_primary, self.hybrid_secondary]
            return any(c.is_configured() for s in sources if (c := self._client(s)))
        return False

    def get_source_status(self) -> dict:
        """Return configured status for each download source."""
        return {name: (c.is_configured() if c else False)
                for name, c in [('soulseek', self.soulseek), ('youtube', self.youtube),
                                ('tidal', self.tidal), ('qobuz', self.qobuz),
                                ('hifi', self.hifi), ('deezer_dl', self.deezer_dl),
                                ('lidarr', self.lidarr)]}

    async def check_connection(self) -> bool:
        """
        Test if download sources are accessible.

        Returns True if the configured source(s) are reachable.
        """
        client = self._client(self.mode)
        if client and self.mode != 'hybrid':
            return await client.check_connection()
        elif self.mode == 'hybrid':
            sources_to_check = self.hybrid_order if self.hybrid_order else ['soulseek', 'youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr']
            results = {}
            for source in sources_to_check:
                client = self._client(source)
                if client:
                    try:
                        results[source] = await client.check_connection()
                    except Exception:
                        results[source] = False

            status_parts = [f"{s}: {'' if ok else ''}" for s, ok in results.items()]
            logger.info(f"   {' | '.join(status_parts)}")

            return any(results.values())

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
        source_names = {'soulseek': 'Soulseek', 'youtube': 'YouTube', 'tidal': 'Tidal',
                        'qobuz': 'Qobuz', 'hifi': 'HiFi', 'deezer_dl': 'Deezer', 'lidarr': 'Lidarr'}

        if self.mode != 'hybrid':
            client = self._client(self.mode)
            if not client:
                logger.error(f"{source_names.get(self.mode, self.mode)} client not available (failed to initialize)")
                return [], []
            logger.info(f"Searching {source_names.get(self.mode, self.mode)}: {query}")
            return await client.search(query, timeout, progress_callback)

        elif self.mode == 'hybrid':
            clients = {name: self._client(name) for name in source_names}

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

            logger.info(f"Hybrid search ({' → '.join(source_order)}): {query}")

            # Try each source in priority order (skip unconfigured/unavailable ones)
            for i, source_name in enumerate(source_order):
                client = clients.get(source_name)
                if not client:
                    logger.info(f"Skipping {source_name} (not available)")
                    continue
                if hasattr(client, 'is_configured') and not client.is_configured():
                    logger.info(f"Skipping {source_name} (not configured)")
                    continue

                try:
                    if i == 0:
                        logger.info(f"Trying {source_name} (priority {i+1}): {query}")
                    else:
                        logger.info(f"Trying {source_name} (priority {i+1}): {query}")

                    tracks, albums = await client.search(query, timeout, progress_callback)
                    if tracks:
                        logger.info(f"{source_name} found {len(tracks)} tracks")
                        return (tracks, albums)
                except Exception as e:
                    logger.warning(f"{source_name} search failed: {e}")

            # Nothing found from any source
            logger.warning(f"Hybrid search: all sources ({', '.join(source_order)}) found nothing for: {query}")
            return ([], [])

        # Fallback: empty results
        return ([], [])

    async def search_and_download_best(self, query: str, expected_track=None) -> Optional[str]:
        """
        Search and automatically download the best result.
        Supports Hybrid mode (uses configured source priority).

        Args:
            query: Search query string
            expected_track: Optional SpotifyTrack for match validation (title/artist/duration)

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

        # 2. Filter and validate results
        _streaming_sources = ('youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr')
        is_streaming = tracks[0].username in _streaming_sources if tracks else False

        if is_streaming and expected_track:
            # Score streaming results against expected track metadata
            from core.matching_engine import MusicMatchingEngine
            me = MusicMatchingEngine()

            expected_title = expected_track.name if hasattr(expected_track, 'name') else ''
            expected_artists = expected_track.artists if hasattr(expected_track, 'artists') else []
            expected_duration = expected_track.duration_ms if hasattr(expected_track, 'duration_ms') else 0

            expected_title_lower = (expected_title or '').lower()
            _version_kw = ['remix', 'live', 'acoustic', 'instrumental', 'radio edit',
                           'extended', 'slowed', 'sped up', 'reverb', 'karaoke']
            expected_is_version = any(kw in expected_title_lower for kw in _version_kw)

            scored = []
            for r in tracks:
                confidence, _ = me.score_track_match(
                    source_title=expected_title,
                    source_artists=expected_artists,
                    source_duration_ms=expected_duration,
                    candidate_title=r.title or '',
                    candidate_artists=[r.artist] if r.artist else [],
                    candidate_duration_ms=r.duration or 0,
                )
                # Version penalty
                r_title_lower = (r.title or '').lower()
                if not expected_is_version:
                    for kw in _version_kw:
                        if kw in r_title_lower and kw not in expected_title_lower:
                            confidence *= 0.4
                            break

                if confidence >= 0.55:
                    r._match_confidence = confidence
                    scored.append(r)

            if scored:
                scored.sort(key=lambda x: x._match_confidence, reverse=True)
                filtered_results = scored
                logger.info(f"Streaming validation: {len(scored)}/{len(tracks)} passed "
                            f"(best: {scored[0]._match_confidence:.2f})")
            else:
                logger.warning(f"No streaming results passed validation for: {query}")
                return None
        elif is_streaming:
            filtered_results = tracks
        else:
            filtered_results = self.soulseek.filter_results_by_quality_preference(tracks) if self.soulseek else tracks

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
        source_map = {'youtube': self.youtube, 'tidal': self.tidal, 'qobuz': self.qobuz,
                      'hifi': self.hifi, 'deezer_dl': self.deezer_dl, 'lidarr': self.lidarr}
        source_names = {'youtube': 'YouTube', 'tidal': 'Tidal', 'qobuz': 'Qobuz',
                        'hifi': 'HiFi', 'deezer_dl': 'Deezer', 'lidarr': 'Lidarr'}

        if username in source_map:
            client = source_map[username]
            if not client:
                raise RuntimeError(f"{source_names[username]} download client not available (failed to initialize)")
            logger.info(f"Downloading from {source_names[username]}: {filename}")
            return await client.download(username, filename, file_size)
        else:
            if not self.soulseek:
                raise RuntimeError("Soulseek client not available (failed to initialize)")
            logger.info(f"Downloading from Soulseek: {filename}")
            return await self.soulseek.download(username, filename, file_size)

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """
        Get all active downloads from all sources.

        Returns:
            List of DownloadStatus objects
        """
        # Get downloads from all available sources
        all_downloads = []
        for client in [self.soulseek, self.youtube, self.tidal, self.qobuz, self.hifi, self.deezer_dl]:
            if client:
                try:
                    all_downloads.extend(await client.get_all_downloads())
                except Exception:
                    pass
        return all_downloads

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """
        Get status of a specific download.

        Args:
            download_id: Download ID to query

        Returns:
            DownloadStatus object or None if not found
        """
        # Try each source until we find the download
        for client in [self.soulseek, self.youtube, self.tidal, self.qobuz, self.hifi, self.deezer_dl]:
            if not client:
                continue
            try:
                status = await client.get_download_status(download_id)
                if status:
                    return status
            except Exception:
                pass

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
        # If username is provided, route directly to that source
        source_map = {'youtube': self.youtube, 'tidal': self.tidal, 'qobuz': self.qobuz,
                      'hifi': self.hifi, 'deezer_dl': self.deezer_dl, 'lidarr': self.lidarr}
        if username in source_map:
            client = source_map[username]
            return await client.cancel_download(download_id, username, remove) if client else False
        elif username:
            return await self.soulseek.cancel_download(download_id, username, remove) if self.soulseek else False

        # Otherwise, try all available sources
        for client in [self.soulseek, self.youtube, self.tidal, self.qobuz, self.hifi, self.deezer_dl]:
            if not client:
                continue
            try:
                if await client.cancel_download(download_id, username, remove):
                    return True
            except Exception:
                pass
        return False

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
        if not self.soulseek:
            return False
        return await self.soulseek.signal_download_completion(download_id, username, remove)

    async def clear_all_completed_downloads(self) -> bool:
        """
        Clear all completed downloads from both sources.

        Returns:
            True if successful
        """
        results = []
        for client in [self.soulseek, self.youtube, self.tidal, self.qobuz, self.hifi, self.deezer_dl]:
            if client:
                try:
                    results.append(await client.clear_all_completed_downloads())
                except Exception:
                    pass

        return all(results) if results else True

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
        if not self.soulseek:
            raise RuntimeError("Soulseek client not available (failed to initialize)")
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
        if not self.soulseek:
            raise RuntimeError("Soulseek client not available (failed to initialize)")
        return await self.soulseek._make_direct_request(method, endpoint, **kwargs)

    async def clear_all_searches(self) -> bool:
        """
        Clear all searches (Soulseek-specific).

        Returns:
            True if successful
        """
        return await self.soulseek.clear_all_searches() if self.soulseek else True

    async def maintain_search_history_with_buffer(self, keep_searches: int = 50, trigger_threshold: int = 200) -> bool:
        """
        Maintain search history (Soulseek-specific).

        Args:
            keep_searches: Number of searches to keep
            trigger_threshold: Threshold to trigger cleanup

        Returns:
            True if successful
        """
        return await self.soulseek.maintain_search_history_with_buffer(keep_searches, trigger_threshold) if self.soulseek else True

    async def cancel_all_downloads(self) -> bool:
        """Cancel and remove all downloads from all sources."""
        ok = True
        for client in [self.soulseek, self.tidal, self.qobuz, self.hifi, self.deezer_dl]:
            if client:
                try:
                    await client.cancel_all_downloads() if hasattr(client, 'cancel_all_downloads') else await client.clear_all_completed_downloads()
                except Exception:
                    ok = False
        return ok
