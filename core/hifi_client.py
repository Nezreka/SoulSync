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
- HLS manifest-based downloads via /trackManifests/ endpoint
- Quality selection: HIRES_LOSSLESS, LOSSLESS, HIGH, LOW
- Multiple API instance failover
- FFmpeg demuxing for FLAC extraction from MP4 containers
"""

import os
import re
import uuid
import time
import shutil
import subprocess
import threading
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from urllib.parse import urljoin

import requests as http_requests

from utils.logging_config import get_logger
from config.settings import config_manager
from core.soulseek_client import TrackResult, AlbumResult, DownloadStatus

logger = get_logger("hifi_client")

# HLS quality presets mapping to /trackManifests/ format parameters
HLS_QUALITY_MAP = {
    'hires': {
        'formats': ['FLAC_HIRES'],
        'manifest_type': 'HLS',
        'extension': 'flac',
        'label': 'FLAC 24-bit/96kHz',
        'bitrate': 9216,
        'codec': 'flac',
    },
    'lossless': {
        'formats': ['FLAC'],
        'manifest_type': 'HLS',
        'extension': 'flac',
        'label': 'FLAC 16-bit/44.1kHz',
        'bitrate': 1411,
        'codec': 'flac',
    },
    'high': {
        'formats': ['AACLC'],
        'manifest_type': 'HLS',
        'extension': 'm4a',
        'label': 'AAC 320kbps',
        'bitrate': 320,
        'codec': 'aac',
    },
    'low': {
        'formats': ['HEAACV1'],
        'manifest_type': 'HLS',
        'extension': 'm4a',
        'label': 'AAC 96kbps',
        'bitrate': 96,
        'codec': 'aac',
    },
}

HLS_MAP_TAG_RE = re.compile(r'#EXT-X-MAP:.*URI="([^"]+)"')

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
        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')
        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        self._instances = []
        self._instance_lock = threading.Lock()
        self._load_instances_from_db()

        self._current_instance = self._instances[0] if self._instances else None

        self.session = http_requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json',
        })

        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._download_lock = threading.Lock()

        self.shutdown_check = None

        self._last_api_call = 0
        self._api_lock = threading.Lock()
        self._min_interval = 0.5

        logger.info(f"HiFi client initialized (instance: {self._current_instance}, "
                     f"download path: {self.download_path})")

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def _load_instances_from_db(self):
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
        with self._instance_lock:
            old_current = self._current_instance
            self._load_instances_from_db()
            self._current_instance = self._instances[0] if self._instances else None
            if self._current_instance != old_current:
                logger.info(f"HiFi instances reloaded, active: {self._current_instance}")
            else:
                logger.info("HiFi instances reloaded")

    def _get_instance(self) -> Optional[str]:
        with self._instance_lock:
            return self._current_instance

    def _rotate_instance(self, failed_url: str):
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
        with self._api_lock:
            now = time.time()
            elapsed = now - self._last_api_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_api_call = time.time()

    def _api_get(self, path: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
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

    def is_available(self) -> bool:
        try:
            data = self._api_get('/', timeout=5)
            return data is not None
        except Exception:
            return False

    def is_configured(self) -> bool:
        return self._current_instance is not None

    async def check_connection(self) -> bool:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.is_available)
        except Exception as e:
            logger.error(f"HiFi connection check failed: {e}")
            return False

    def get_version(self) -> Optional[str]:
        data = self._api_get('/')
        if data and isinstance(data, dict):
            return data.get('version') or data.get('data', {}).get('version')
        return None

    def search_tracks(self, title: str = None, artist: str = None,
                      album: str = None, limit: int = 20) -> List[Dict]:
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
        return self.search_tracks(title=query, limit=limit)

    def _parse_track(self, item: dict) -> Dict:
        artist_name = 'Unknown Artist'
        artist_id = None
        artists_raw = item.get('artists', item.get('artist'))
        if isinstance(artists_raw, list):
            names = []
            for a in artists_raw:
                if isinstance(a, dict):
                    names.append(a.get('name', ''))
                    if artist_id is None:
                        artist_id = a.get('id')
                elif isinstance(a, str):
                    names.append(a)
            artist_name = ', '.join(n for n in names if n) or 'Unknown Artist'
        elif isinstance(artists_raw, dict):
            artist_name = artists_raw.get('name', 'Unknown Artist')
            artist_id = artists_raw.get('id')
        elif isinstance(artists_raw, str):
            artist_name = artists_raw

        album_raw = item.get('album', {})
        album_name = ''
        album_id = None
        if isinstance(album_raw, dict):
            album_name = album_raw.get('title', album_raw.get('name', ''))
            album_id = album_raw.get('id')
        elif isinstance(album_raw, str):
            album_name = album_raw

        duration_s = item.get('duration', 0)
        duration_ms = duration_s * 1000 if duration_s and duration_s < 100000 else duration_s

        return {
            'id': item.get('id'),
            'artist_id': artist_id,
            'album_id': album_id,
            'title': item.get('title', item.get('name', 'Unknown')),
            'artist': artist_name,
            'album': album_name,
            'duration_ms': int(duration_ms) if duration_ms else 0,
            'track_number': item.get('trackNumber', item.get('track_number')),
            'isrc': item.get('isrc'),
            'bpm': item.get('bpm'),
            'copyright': item.get('copyright'),
            'explicit': item.get('explicit', False),
            'quality': item.get('audioQuality', item.get('quality', '')),
        }

    def get_track_info(self, track_id: int) -> Optional[Dict]:
        data = self._api_get('/info/', params={'id': track_id})
        if not data:
            return None

        inner = data.get('data', data) if isinstance(data, dict) else data
        if isinstance(inner, dict):
            return self._parse_track(inner)
        return None

    def get_album(self, album_id: int, limit: int = 100) -> Optional[Dict]:
        data = self._api_get('/album/', params={'id': album_id, 'limit': limit})
        if not data:
            return None

        inner = data.get('data', data) if isinstance(data, dict) else data
        if not isinstance(inner, dict):
            return None

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
        data = self._api_get('/artist/', params={'id': artist_id})
        if not data:
            return None

        inner = data.get('data', data) if isinstance(data, dict) else data
        return inner if isinstance(inner, dict) else None

    def _parse_hls_playlist(self, text: str, playlist_url: str):
        init_uri = None
        segment_uris = []
        variant_uri = None

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        for index, line in enumerate(lines):
            if line.startswith('#EXTM3U'):
                continue

            if line.startswith('#EXT-X-STREAM-INF'):
                for next_line in lines[index + 1:]:
                    if not next_line.startswith('#'):
                        variant_uri = urljoin(playlist_url, next_line)
                        break
                break

            if line.startswith('#EXT-X-MAP'):
                match = HLS_MAP_TAG_RE.search(line)
                if match:
                    init_uri = match.group(1)
                continue

            if line.startswith('#'):
                continue

            segment_uris.append(urljoin(playlist_url, line))

        if variant_uri:
            return None, [variant_uri]

        if not segment_uris:
            raise ValueError('No segment URIs found in the HLS playlist')

        if init_uri:
            init_uri = urljoin(playlist_url, init_uri)

        return init_uri, segment_uris

    def _get_hls_manifest(self, track_id: int, quality: str = 'lossless') -> Optional[Dict]:
        q_info = HLS_QUALITY_MAP.get(quality, HLS_QUALITY_MAP['lossless'])
        formats = q_info['formats']

        params = [
            ('id', str(track_id)),
            ('formats', ','.join(formats)),
            ('usage', 'DOWNLOAD'),
            ('manifestType', 'HLS'),
            ('adaptive', 'true'),
            ('uriScheme', 'HTTPS'),
        ]

        data = self._api_get('/trackManifests/', params=params, timeout=20)
        if not data:
            return None

        try:
            inner = data.get('data', data) if isinstance(data, dict) else data
            attrs = inner.get('data', {}).get('attributes', {})
            uri = attrs.get('uri')
        except (AttributeError, KeyError) as e:
            logger.warning(f"Failed to extract playlist URI from manifest response: {e}")
            return None

        if not uri:
            logger.warning(f"No playlist URI in manifest for track {track_id}")
            return None

        try:
            playlist_resp = self.session.get(uri, allow_redirects=True, timeout=30)
            playlist_resp.raise_for_status()
            playlist_text = playlist_resp.text
        except Exception as e:
            logger.warning(f"Failed to fetch HLS playlist for track {track_id}: {e}")
            return None

        try:
            init_uri, segment_uris = self._parse_hls_playlist(playlist_text, uri)
        except ValueError as e:
            logger.warning(f"Failed to parse HLS playlist for track {track_id}: {e}")
            return None

        if '#EXT-X-STREAM-INF' in playlist_text and segment_uris:
            playlist_uri = segment_uris[0]
            try:
                logger.debug(f"Detected master HLS playlist, following variant: {playlist_uri}")
                variant_resp = self.session.get(playlist_uri, allow_redirects=True, timeout=30)
                variant_resp.raise_for_status()
                variant_text = variant_resp.text
                init_uri, segment_uris = self._parse_hls_playlist(variant_text, playlist_uri)
            except Exception as e:
                logger.warning(f"Failed to fetch variant playlist for track {track_id}: {e}")
                return None

        if init_uri:
            logger.info(f"HiFi HLS manifest for track {track_id}: "
                        f"init segment + {len(segment_uris)} segments ({quality})")
        else:
            logger.info(f"HiFi HLS manifest for track {track_id}: "
                        f"{len(segment_uris)} segments ({quality})")

        return {
            'init_uri': init_uri,
            'segment_uris': segment_uris,
            'extension': q_info['extension'],
            'codec': q_info['codec'],
            'quality': quality,
        }

    def _demux_flac(self, input_path: Path, output_path: Path) -> None:
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            tools_dir = Path(__file__).parent.parent / 'tools'
            ffmpeg_candidate = tools_dir / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
            if ffmpeg_candidate.exists():
                ffmpeg = str(ffmpeg_candidate)
            else:
                raise RuntimeError('ffmpeg is required to demux FLAC from MP4. Install ffmpeg and retry.')

        try:
            result = subprocess.run(
                [
                    ffmpeg,
                    '-y',
                    '-hide_banner',
                    '-loglevel', 'error',
                    '-i', str(input_path),
                    '-map', '0:a:0',
                    '-c', 'copy',
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f'ffmpeg failed while demuxing {input_path} -> {output_path}: '
                f'{exc.returncode}\n{exc.stderr}'
            ) from exc

    async def search(self, query: str, timeout: int = None,
                     progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            tracks = await loop.run_in_executor(None, lambda: self.search_raw(query))

            quality_key = config_manager.get('hifi_download.quality', 'lossless')
            q_info = HLS_QUALITY_MAP.get(quality_key, HLS_QUALITY_MAP['lossless'])

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
            _source_metadata={
                'source': 'hifi',
                'track_id': track.get('id'),
                'artist_id': track.get('artist_id'),
                'album_id': track.get('album_id'),
                'isrc': track.get('isrc'),
                'bpm': track.get('bpm'),
                'copyright': track.get('copyright'),
            },
        )

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
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
        quality_key = config_manager.get('hifi_download.quality', 'lossless')
        chain = ['hires', 'lossless', 'high', 'low']
        start = chain.index(quality_key) if quality_key in chain else 1
        allow_fallback = config_manager.get('hifi_download.allow_fallback', True)
        chain = chain[start:] if allow_fallback else [quality_key]

        MIN_AUDIO_SIZE = 100 * 1024

        for q_key in chain:
            if self.shutdown_check and self.shutdown_check():
                logger.info("Shutdown detected, aborting HiFi download")
                return None

            manifest_info = self._get_hls_manifest(track_id, quality=q_key)
            if not manifest_info or not manifest_info.get('segment_uris'):
                logger.warning(f"No HLS manifest at quality {q_key}, trying next")
                continue

            extension = manifest_info['extension']
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', display_name)
            out_filename = f"{safe_name}.{extension}"
            out_path = self.download_path / out_filename

            is_flac = q_key in ('hires', 'lossless')
            intermediate_path = out_path.with_suffix('.m4a') if is_flac else out_path

            try:
                init_uri = manifest_info.get('init_uri')
                segment_uris = manifest_info['segment_uris']
                total_segments = len(segment_uris) + (1 if init_uri else 0)

                logger.info(f"Downloading from HiFi ({q_key}): {out_filename} "
                            f"({total_segments} segments)")

                downloaded = 0
                speed_start = time.time()
                segments_completed = 0

                with self._download_lock:
                    if download_id in self.active_downloads:
                        self.active_downloads[download_id]['size'] = 0

                with intermediate_path.open('wb') as output_file:
                    if init_uri:
                        if self.shutdown_check and self.shutdown_check():
                            logger.info("Shutdown detected, aborting HiFi download")
                            intermediate_path.unlink(missing_ok=True)
                            return None

                        logger.debug(f"Downloading init segment: {init_uri}")
                        init_data = self._download_segment_with_retry(init_uri)
                        output_file.write(init_data)
                        downloaded += len(init_data)
                        segments_completed += 1

                        self._update_download_progress(download_id, downloaded,
                                                       segments_completed, total_segments, speed_start)

                    for segment_url in segment_uris:
                        if self.shutdown_check and self.shutdown_check():
                            logger.info("Shutdown detected, aborting HiFi download")
                            intermediate_path.unlink(missing_ok=True)
                            return None

                        segment_data = self._download_segment_with_retry(segment_url)
                        output_file.write(segment_data)
                        downloaded += len(segment_data)
                        segments_completed += 1

                        self._update_download_progress(download_id, downloaded,
                                                       segments_completed, total_segments, speed_start)

            except Exception as e:
                logger.warning(f"Download failed at quality {q_key}: {e}")
                intermediate_path.unlink(missing_ok=True)
                continue

            if downloaded < MIN_AUDIO_SIZE:
                logger.warning(f"File too small at {q_key} ({downloaded} bytes), trying next")
                intermediate_path.unlink(missing_ok=True)
                continue

            try:
                if is_flac:
                    logger.info(f"Demuxing FLAC from MP4 container: {intermediate_path} -> {out_path}")
                    self._demux_flac(intermediate_path, out_path)
                    intermediate_path.unlink(missing_ok=True)
                    final_size = out_path.stat().st_size if out_path.exists() else 0
                else:
                    final_size = intermediate_path.stat().st_size if intermediate_path.exists() else 0

                if final_size < MIN_AUDIO_SIZE:
                    logger.warning(f"Final file too small after processing at {q_key} "
                                   f"({final_size} bytes), trying next")
                    out_path.unlink(missing_ok=True)
                    continue

                logger.info(f"HiFi download complete ({q_key}): {out_path} "
                            f"({final_size / (1024*1024):.1f} MB)")
                return str(out_path)

            except Exception as e:
                logger.warning(f"Post-processing failed at quality {q_key}: {e}")
                out_path.unlink(missing_ok=True)
                intermediate_path.unlink(missing_ok=True)
                continue

        logger.error(f"All quality tiers exhausted for '{display_name}'")
        return None

    def _download_segment_with_retry(self, url: str) -> bytes:
        """Download a single HLS segment with 3 retries and 2s fixed backoff."""
        last_error = None
        for attempt in range(4):
            try:
                resp = self.session.get(url, allow_redirects=True, timeout=30)
                resp.raise_for_status()
                return resp.content
            except http_requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if 400 <= status < 500:
                    raise
                last_error = e
            except (http_requests.exceptions.Timeout,
                    http_requests.exceptions.ConnectionError) as e:
                last_error = e

            if attempt < 3:
                if self.shutdown_check and self.shutdown_check():
                    raise RuntimeError("Shutdown requested")
                logger.warning(f"Segment download failed (attempt {attempt + 1}/4), "
                              f"retrying in 2s: {url}")
                time.sleep(2)

        raise last_error

    def _update_download_progress(self, download_id: str, downloaded: int,
                                  segments_completed: int, total_segments: int,
                                  speed_start: float):
        with self._download_lock:
            if download_id not in self.active_downloads:
                return
            info = self.active_downloads[download_id]
            info['transferred'] = downloaded

            now = time.time()
            elapsed_total = now - speed_start
            speed = int(downloaded / elapsed_total) if elapsed_total > 0 else 0
            info['speed'] = speed

            if total_segments > 0:
                progress = (segments_completed / total_segments) * 100
                info['progress'] = round(min(progress, 99.9), 1)

            time_remaining = None
            if speed > 0:
                remaining_bytes = downloaded * (total_segments / max(segments_completed, 1)) - downloaded
                if remaining_bytes > 0:
                    time_remaining = int(remaining_bytes / speed)
            info['time_remaining'] = time_remaining

    async def get_all_downloads(self) -> List[DownloadStatus]:
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
        with self._download_lock:
            if download_id not in self.active_downloads:
                return False
            self.active_downloads[download_id]['state'] = 'Cancelled'
            if remove:
                del self.active_downloads[download_id]
        return True

    async def clear_all_completed_downloads(self) -> bool:
        with self._download_lock:
            to_remove = [
                did for did, info in self.active_downloads.items()
                if info.get('state', '') in ('Completed, Succeeded', 'Cancelled', 'Errored', 'Aborted')
            ]
            for did in to_remove:
                del self.active_downloads[did]
        return True
