"""
HiFi API Client — Alternative lossless download source via public hifi-api instances.

Provides Tidal-sourced FLAC downloads (16-bit and 24-bit) through the open hifi-api
project. No authentication required from the client — the API instances handle
Tidal credentials internally.

Interface follows the same patterns as TidalDownloadClient for drop-in compatibility
with the existing download infrastructure (TrackResult, DownloadStatus, etc).

Supports:
- Track search by title, artist, album
- Album lookup by ID
- Artist lookup by ID
- Direct FLAC download URLs from Tidal CDN
- Quality selection: HI_RES_LOSSLESS, LOSSLESS, HIGH, LOW
- Multiple API instance failover
"""

import os
import re
import json
import base64
import uuid
import time
import threading
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path

import requests as http_requests

from utils.logging_config import get_logger
from config.settings import config_manager
from core.soulseek_client import TrackResult, AlbumResult, DownloadStatus

logger = get_logger("hifi_client")

# Quality tiers matching Tidal's internal quality labels
HIFI_QUALITY_MAP = {
    'hires': {
        'api_value': 'HI_RES_LOSSLESS',
        'label': 'FLAC 24-bit/96kHz',
        'extension': 'flac',
        'bitrate': 9216,
        'codec': 'flac',
    },
    'lossless': {
        'api_value': 'LOSSLESS',
        'label': 'FLAC 16-bit/44.1kHz',
        'extension': 'flac',
        'bitrate': 1411,
        'codec': 'flac',
    },
    'high': {
        'api_value': 'HIGH',
        'label': 'AAC 320kbps',
        'extension': 'm4a',
        'bitrate': 320,
        'codec': 'aac',
    },
    'low': {
        'api_value': 'LOW',
        'label': 'AAC 96kbps',
        'extension': 'm4a',
        'bitrate': 96,
        'codec': 'aac',
    },
}

# Default public hifi-api instances (ordered by preference)
DEFAULT_INSTANCES = [
    'https://triton.squid.wtf',
    'https://hifi-one.spotisaver.net',
    'https://hifi-two.spotisaver.net',
    'https://hund.qqdl.site',
    'https://katze.qqdl.site',
    'https://arran.monochrome.tf',
]


class HiFiClient:
    """
    HiFi API client for searching and downloading lossless music.
    Uses public hifi-api instances (Tidal backend) — no auth required.
    """

    def __init__(self, download_path: str = None, base_url: str = None):
        # Download path (use Soulseek path for consistency with post-processing)
        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')
        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        # API instance management — loaded from database
        self._instances = []
        self._instance_lock = threading.Lock()
        self._load_instances_from_db()

        self._current_instance = self._instances[0] if self._instances else None

        # HTTP session with retry-friendly settings
        self.session = http_requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json',
        })

        # Download tracking (mirrors TidalDownloadClient pattern)
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._download_lock = threading.Lock()

        # Shutdown check callback
        self.shutdown_check = None

        # Rate limiting
        self._last_api_call = 0
        self._api_lock = threading.Lock()
        self._min_interval = 0.5  # 500ms between calls

        logger.info(f"HiFi client initialized (instance: {self._current_instance}, "
                     f"download path: {self.download_path})")

    def set_shutdown_check(self, check_callable):
        """Set a callback function to check for system shutdown."""
        self.shutdown_check = check_callable

    def _load_instances_from_db(self):
        """Load instances from the database, seeding defaults if empty."""
        try:
            from database.music_database import get_database
            db = get_database()
            db.seed_hifi_instances(DEFAULT_INSTANCES)
            rows = db.get_hifi_instances()
            urls = [r['url'] for r in rows if r['enabled']]
            if urls:
                self._instances = urls
            else:
                self._instances = list(DEFAULT_INSTANCES)
        except Exception as e:
            logger.warning(f"Failed to load HiFi instances from DB, using defaults: {e}")
            self._instances = list(DEFAULT_INSTANCES)

    def reload_instances(self):
        """Reload instances from the database (called after settings change)."""
        with self._instance_lock:
            old_current = self._current_instance
            self._load_instances_from_db()
            self._current_instance = self._instances[0] if self._instances else None
            if self._current_instance != old_current:
                logger.info(f"HiFi instances reloaded, active: {self._current_instance}")
            else:
                logger.info("HiFi instances reloaded")

    # ===================== Instance Management =====================

    def _get_instance(self) -> Optional[str]:
        """Get the current active API instance URL."""
        with self._instance_lock:
            return self._current_instance

    def _rotate_instance(self, failed_url: str):
        """Move a failed instance to the back of the list and switch to next."""
        with self._instance_lock:
            if failed_url in self._instances:
                self._instances.remove(failed_url)
                self._instances.append(failed_url)
            if self._instances:
                self._current_instance = self._instances[0]
                logger.info(f"Rotated to HiFi instance: {self._current_instance}")
            else:
                self._current_instance = None

    def _rate_limit(self):
        """Enforce minimum interval between API calls."""
        with self._api_lock:
            now = time.time()
            elapsed = now - self._last_api_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_api_call = time.time()

    def _api_get(self, path: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
        """
        Make a GET request to the hifi-api, with instance failover.
        Tries each instance up to once before giving up.
        """
        tried = set()

        while True:
            instance = self._get_instance()
            if not instance or instance in tried:
                logger.error("All HiFi API instances exhausted")
                return None

            tried.add(instance)
            url = f"{instance}{path}"
            self._rate_limit()

            try:
                response = self.session.get(url, params=params, timeout=timeout)
                response.raise_for_status()
                data = response.json()

                # Check for API-level errors
                if isinstance(data, dict) and data.get('error'):
                    logger.warning(f"HiFi API error from {instance}: {data['error']}")
                    return None

                return data

            except http_requests.exceptions.Timeout:
                logger.warning(f"HiFi API timeout: {instance}")
                self._rotate_instance(instance)
            except http_requests.exceptions.ConnectionError:
                logger.warning(f"HiFi API connection error: {instance}")
                self._rotate_instance(instance)
            except http_requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status >= 500:
                    logger.warning(f"HiFi API server error ({status}): {instance}")
                    self._rotate_instance(instance)
                else:
                    logger.error(f"HiFi API HTTP error ({status}): {e}")
                    return None
            except Exception as e:
                logger.error(f"HiFi API unexpected error: {e}")
                return None

    # ===================== Availability =====================

    def is_available(self) -> bool:
        """Check if the HiFi API is reachable."""
        try:
            data = self._api_get('/', timeout=5)
            return data is not None
        except Exception:
            return False

    def is_configured(self) -> bool:
        """Check if HiFi client is configured and ready (matches Soulseek interface)."""
        return self._current_instance is not None

    async def check_connection(self) -> bool:
        """Test if HiFi API is accessible (async, Soulseek-compatible)."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.is_available)
        except Exception as e:
            logger.error(f"HiFi connection check failed: {e}")
            return False

    def get_version(self) -> Optional[str]:
        """Get the API version of the current instance."""
        data = self._api_get('/')
        if data and isinstance(data, dict):
            return data.get('version') or data.get('data', {}).get('version')
        return None

    # ===================== Search =====================

    def search_tracks(self, title: str = None, artist: str = None,
                      album: str = None, limit: int = 20) -> List[Dict]:
        """
        Search for tracks on Tidal via hifi-api.

        Args:
            title: Track title to search for
            artist: Artist name to search for
            album: Album name to search for
            limit: Max results to return

        Returns:
            List of track dicts with id, title, artist, album, duration, etc.
        """
        params = {'limit': limit}
        if title:
            params['s'] = title
        if artist:
            params['a'] = artist
        if album:
            params['al'] = album

        if not any(k in params for k in ('s', 'a', 'al')):
            logger.warning("search_tracks called with no search terms")
            return []

        data = self._api_get('/search/', params=params)
        if not data:
            return []

        # Handle response format: {data: {items: [...]}} or {data: [...]}
        items = []
        if isinstance(data, dict):
            inner = data.get('data', data)
            if isinstance(inner, dict):
                items = inner.get('items', inner.get('tracks', []))
            elif isinstance(inner, list):
                items = inner

        results = []
        for item in items:
            try:
                results.append(self._parse_track(item))
            except Exception as e:
                logger.debug(f"Skipping unparseable track: {e}")

        logger.info(f"HiFi search: {len(results)} tracks found "
                     f"(title={title}, artist={artist}, album={album})")
        return results

    def search_raw(self, query: str, limit: int = 20) -> List[Dict]:
        """
        Generic search (free-text query). Maps to title search.
        Returns raw dicts (not TrackResult).
        """
        return self.search_tracks(title=query, limit=limit)

    def _parse_track(self, item: dict) -> Dict:
        """Parse a track item from hifi-api response into a normalized dict."""
        # Artist can be a dict with 'name' or a list of artists
        artist_name = 'Unknown Artist'
        artists_raw = item.get('artists', item.get('artist'))
        if isinstance(artists_raw, list):
            names = []
            for a in artists_raw:
                if isinstance(a, dict):
                    names.append(a.get('name', ''))
                elif isinstance(a, str):
                    names.append(a)
            artist_name = ', '.join(n for n in names if n) or 'Unknown Artist'
        elif isinstance(artists_raw, dict):
            artist_name = artists_raw.get('name', 'Unknown Artist')
        elif isinstance(artists_raw, str):
            artist_name = artists_raw

        # Album
        album_raw = item.get('album', {})
        album_name = ''
        if isinstance(album_raw, dict):
            album_name = album_raw.get('title', album_raw.get('name', ''))
        elif isinstance(album_raw, str):
            album_name = album_raw

        # Duration
        duration_s = item.get('duration', 0)
        duration_ms = duration_s * 1000 if duration_s and duration_s < 100000 else duration_s

        return {
            'id': item.get('id'),
            'title': item.get('title', item.get('name', 'Unknown')),
            'artist': artist_name,
            'album': album_name,
            'duration_ms': int(duration_ms) if duration_ms else 0,
            'track_number': item.get('trackNumber', item.get('track_number')),
            'isrc': item.get('isrc'),
            'explicit': item.get('explicit', False),
            'quality': item.get('audioQuality', item.get('quality', '')),
        }

    # ===================== Track Info & Stream URL =====================

    def get_track_info(self, track_id: int) -> Optional[Dict]:
        """Get detailed metadata for a specific track."""
        data = self._api_get('/info/', params={'id': track_id})
        if not data:
            return None

        inner = data.get('data', data) if isinstance(data, dict) else data
        if isinstance(inner, dict):
            return self._parse_track(inner)
        return None

    def get_stream_url(self, track_id: int, quality: str = 'lossless') -> Optional[Dict]:
        """
        Get the direct download URL for a track.

        Args:
            track_id: Tidal track ID
            quality: One of 'hires', 'lossless', 'high', 'low'

        Returns:
            Dict with 'url', 'mime_type', 'codec', 'quality' or None on failure.
        """
        q_info = HIFI_QUALITY_MAP.get(quality, HIFI_QUALITY_MAP['lossless'])
        api_quality = q_info['api_value']

        data = self._api_get('/track/', params={'id': track_id, 'quality': api_quality})
        if not data:
            return None

        # Extract manifest from response
        inner = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(inner, dict):
            return None

        manifest_b64 = inner.get('manifest')
        if not manifest_b64:
            logger.warning(f"No manifest in track response for {track_id}")
            return None

        try:
            manifest = json.loads(base64.b64decode(manifest_b64))
        except Exception as e:
            logger.error(f"Failed to decode manifest for track {track_id}: {e}")
            return None

        urls = manifest.get('urls', [])
        if not urls:
            logger.warning(f"No URLs in manifest for track {track_id}")
            return None

        return {
            'url': urls[0],
            'mime_type': manifest.get('mimeType', ''),
            'codec': manifest.get('codecs', ''),
            'encryption': manifest.get('encryptionType', 'NONE'),
            'quality': quality,
        }

    # ===================== Album & Artist =====================

    def get_album(self, album_id: int, limit: int = 100) -> Optional[Dict]:
        """Get album metadata and track list."""
        data = self._api_get('/album/', params={'id': album_id, 'limit': limit})
        if not data:
            return None

        inner = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(inner, dict):
            return None

        # Parse tracks within album
        tracks_raw = inner.get('items', inner.get('tracks', []))
        tracks = []
        for item in tracks_raw:
            try:
                tracks.append(self._parse_track(item))
            except Exception as e:
                logger.debug(f"Skipping album track: {e}")

        return {
            'id': inner.get('id', album_id),
            'title': inner.get('title', inner.get('name', 'Unknown Album')),
            'artist': inner.get('artist', {}).get('name', '') if isinstance(inner.get('artist'), dict) else str(inner.get('artist', '')),
            'tracks': tracks,
            'track_count': inner.get('numberOfTracks', len(tracks)),
            'duration_s': inner.get('duration', 0),
            'release_date': inner.get('releaseDate', ''),
            'cover_id': inner.get('cover', ''),
        }

    def get_artist(self, artist_id: int) -> Optional[Dict]:
        """Get artist info and top tracks."""
        data = self._api_get('/artist/', params={'id': artist_id})
        if not data:
            return None

        inner = data.get('data', data) if isinstance(data, dict) else data
        return inner if isinstance(inner, dict) else None

    # ===================== Soulseek-Compatible Search =====================

    async def search(self, query: str, timeout: int = None,
                     progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """
        Search with Soulseek-compatible return format (TrackResult, AlbumResult).
        Matches the interface expected by DownloadOrchestrator.
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            tracks = await loop.run_in_executor(None, lambda: self.search_raw(query))

            quality_key = config_manager.get('hifi_download.quality', 'lossless')
            q_info = HIFI_QUALITY_MAP.get(quality_key, HIFI_QUALITY_MAP['lossless'])

            results = []
            for t in tracks:
                try:
                    tr = self._to_track_result(t, q_info)
                    results.append(tr)
                except Exception as e:
                    logger.debug(f"Skipping track result conversion: {e}")

            return (results, [])

        except Exception as e:
            logger.error(f"HiFi compatible search failed: {e}")
            return ([], [])

    def _to_track_result(self, track: Dict, quality_info: Dict) -> TrackResult:
        """Convert a hifi track dict to a TrackResult."""
        display_name = f"{track['artist']} - {track['title']}"
        filename = f"{track['id']}||{display_name}"

        return TrackResult(
            username='hifi',
            filename=filename,
            size=0,
            bitrate=quality_info.get('bitrate'),
            duration=track.get('duration_ms'),
            quality=quality_info.get('codec', 'flac'),
            free_upload_slots=999,
            upload_speed=999999,
            queue_length=0,
            artist=track.get('artist'),
            title=track.get('title'),
            album=track.get('album'),
            track_number=track.get('track_number'),
        )

    # ===================== Download =====================

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """
        Download a track (async, Soulseek-compatible interface).
        Filename format: "track_id||display_name"
        """
        try:
            if '||' not in filename:
                logger.error(f"Invalid filename format: {filename}")
                return None

            track_id_str, display_name = filename.split('||', 1)
            try:
                track_id = int(track_id_str)
            except ValueError:
                logger.error(f"Invalid track ID: {track_id_str}")
                return None

            download_id = str(uuid.uuid4())

            with self._download_lock:
                self.active_downloads[download_id] = {
                    'id': download_id,
                    'filename': filename,
                    'username': 'hifi',
                    'state': 'Initializing',
                    'progress': 0.0,
                    'size': 0,
                    'transferred': 0,
                    'speed': 0,
                    'time_remaining': None,
                    'track_id': track_id,
                    'display_name': display_name,
                    'file_path': None,
                }

            thread = threading.Thread(
                target=self._download_worker,
                args=(download_id, track_id, display_name),
                daemon=True,
            )
            thread.start()

            return download_id

        except Exception as e:
            logger.error(f"Failed to start HiFi download: {e}")
            return None

    def _download_worker(self, download_id: str, track_id: int, display_name: str):
        """Background download thread."""
        try:
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['state'] = 'InProgress, Downloading'

            file_path = self._download_sync(download_id, track_id, display_name)

            with self._download_lock:
                if download_id in self.active_downloads:
                    if file_path:
                        self.active_downloads[download_id]['state'] = 'Completed, Succeeded'
                        self.active_downloads[download_id]['progress'] = 100.0
                        self.active_downloads[download_id]['file_path'] = file_path
                    else:
                        self.active_downloads[download_id]['state'] = 'Errored'

        except Exception as e:
            logger.error(f"HiFi download worker failed for {download_id}: {e}")
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['state'] = 'Errored'

    def _download_sync(self, download_id: str, track_id: int, display_name: str) -> Optional[str]:
        """
        Synchronous download with quality fallback chain.
        Returns file path on success, None on failure.
        """
        quality_key = config_manager.get('hifi_download.quality', 'lossless')
        chain = ['hires', 'lossless', 'high', 'low']
        start = chain.index(quality_key) if quality_key in chain else 1
        allow_fallback = config_manager.get('hifi_download.allow_fallback', True)
        chain = chain[start:] if allow_fallback else [quality_key]

        MIN_AUDIO_SIZE = 100 * 1024  # 100KB

        for q_key in chain:
            if self.shutdown_check and self.shutdown_check():
                logger.info("Shutdown detected, aborting HiFi download")
                return None

            stream_info = self.get_stream_url(track_id, quality=q_key)
            if not stream_info or not stream_info.get('url'):
                logger.warning(f"No stream URL at quality {q_key}, trying next")
                continue

            download_url = stream_info['url']
            codec = stream_info.get('codec', '')

            # Determine extension
            if 'flac' in codec.lower():
                extension = 'flac'
            elif 'mp4a' in codec.lower() or 'aac' in codec.lower():
                extension = 'm4a'
            else:
                extension = HIFI_QUALITY_MAP.get(q_key, {}).get('extension', 'flac')

            # Build output path
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', display_name)
            out_filename = f"{safe_name}.{extension}"
            out_path = self.download_path / out_filename

            try:
                logger.info(f"Downloading from HiFi ({q_key}): {out_filename}")
                response = http_requests.get(download_url, stream=True, timeout=120)
                response.raise_for_status()

                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                chunk_size = 64 * 1024
                speed_start = time.time()
                last_speed_update = speed_start

                with self._download_lock:
                    if download_id in self.active_downloads:
                        self.active_downloads[download_id]['size'] = total_size

                with open(out_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        if self.shutdown_check and self.shutdown_check():
                            f.close()
                            out_path.unlink(missing_ok=True)
                            return None

                        f.write(chunk)
                        downloaded += len(chunk)

                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                        else:
                            progress = 0

                        # Calculate speed every 0.5s
                        now = time.time()
                        elapsed_total = now - speed_start
                        speed = int(downloaded / elapsed_total) if elapsed_total > 0 else 0
                        time_remaining = int((total_size - downloaded) / speed) if speed > 0 and total_size > 0 else None

                        with self._download_lock:
                            if download_id in self.active_downloads:
                                self.active_downloads[download_id]['transferred'] = downloaded
                                self.active_downloads[download_id]['progress'] = round(progress, 1)
                                self.active_downloads[download_id]['speed'] = speed
                                self.active_downloads[download_id]['time_remaining'] = time_remaining

            except Exception as e:
                logger.warning(f"Download failed at quality {q_key}: {e}")
                out_path.unlink(missing_ok=True)
                continue

            # Validate file size
            if downloaded < MIN_AUDIO_SIZE:
                logger.warning(f"File too small at {q_key} ({downloaded} bytes), trying next")
                out_path.unlink(missing_ok=True)
                continue

            logger.info(f"HiFi download complete ({q_key}): {out_path} "
                         f"({downloaded / (1024*1024):.1f} MB)")
            return str(out_path)

        logger.error(f"All quality tiers exhausted for '{display_name}'")
        return None

    # ===================== Status / Cancel / Clear =====================

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """Get all active downloads (Soulseek-compatible)."""
        statuses = []
        with self._download_lock:
            for _dl_id, info in self.active_downloads.items():
                statuses.append(DownloadStatus(
                    id=info['id'],
                    filename=info['filename'],
                    username=info['username'],
                    state=info['state'],
                    progress=info['progress'],
                    size=info['size'],
                    transferred=info['transferred'],
                    speed=info['speed'],
                    time_remaining=info.get('time_remaining'),
                    file_path=info.get('file_path'),
                ))
        return statuses

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """Get status of a specific download."""
        with self._download_lock:
            info = self.active_downloads.get(download_id)
            if not info:
                return None
            return DownloadStatus(
                id=info['id'],
                filename=info['filename'],
                username=info['username'],
                state=info['state'],
                progress=info['progress'],
                size=info['size'],
                transferred=info['transferred'],
                speed=info['speed'],
                time_remaining=info.get('time_remaining'),
                file_path=info.get('file_path'),
            )

    async def cancel_download(self, download_id: str, username: str = None,
                              remove: bool = False) -> bool:
        """Cancel an active download."""
        with self._download_lock:
            if download_id not in self.active_downloads:
                return False
            self.active_downloads[download_id]['state'] = 'Cancelled'
            if remove:
                del self.active_downloads[download_id]
        return True

    async def clear_all_completed_downloads(self) -> bool:
        """Clear all terminal downloads."""
        with self._download_lock:
            to_remove = [
                did for did, info in self.active_downloads.items()
                if info.get('state', '') in ('Completed, Succeeded', 'Cancelled', 'Errored', 'Aborted')
            ]
            for did in to_remove:
                del self.active_downloads[did]
        return True
