"""Deezer Download Client — download tracks from Deezer using ARL authentication.

Follows the same interface contract as Tidal, Qobuz, YouTube, and HiFi clients.
Supports FLAC (HiFi subscription), MP3 320 (Premium), and MP3 128 (Free) with
automatic quality fallback.

Authentication: User provides an ARL token (browser cookie from deezer.com).
"""

import asyncio
import hashlib
import json
import os
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.soulseek_client import AlbumResult, DownloadStatus, TrackResult
from utils.logging_config import get_logger

logger = get_logger("deezer_download")

# Deezer API endpoints
_GW_API = "https://www.deezer.com/ajax/gw-light.php"
_MEDIA_API = "https://media.deezer.com/v1/get_url"

# Blowfish decryption secret (public knowledge, used by all Deezer clients)
_BF_SECRET = b"g4el58wc0zvf9na1"

# Quality format codes for media API
_QUALITY_FORMATS = {
    'flac': {'cipher': 'BF_CBC_STRIPE', 'format': 'FLAC'},
    'mp3_320': {'cipher': 'BF_CBC_STRIPE', 'format': 'MP3_320'},
    'mp3_128': {'cipher': 'BF_CBC_STRIPE', 'format': 'MP3_128'},
}

# Quality preference order (highest first)
_QUALITY_ORDER = ['flac', 'mp3_320', 'mp3_128']

# Chunk size for Blowfish decryption (Deezer standard)
_CHUNK_SIZE = 2048

# Minimum valid file size (100KB — anything smaller is likely an error)
_MIN_FILE_SIZE = 100 * 1024


def _get_blowfish_key(track_id: str) -> bytes:
    """Derive the Blowfish decryption key for a track."""
    md5_hex = hashlib.md5(str(track_id).encode()).hexdigest()
    return bytes([
        ord(md5_hex[i]) ^ ord(md5_hex[i + 16]) ^ _BF_SECRET[i]
        for i in range(16)
    ])


def _decrypt_chunk(chunk: bytes, key: bytes) -> bytes:
    """Decrypt a single chunk using Blowfish CBC with null IV."""
    try:
        from Crypto.Cipher import Blowfish
        iv = b'\x00\x01\x02\x03\x04\x05\x06\x07'
        cipher = Blowfish.new(key, Blowfish.MODE_CBC, iv)
        return cipher.decrypt(chunk)
    except ImportError:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            iv = b'\x00\x01\x02\x03\x04\x05\x06\x07'
            cipher = Cipher(algorithms.Blowfish(key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            return decryptor.update(chunk) + decryptor.finalize()
        except ImportError:
            raise ImportError(
                "Deezer downloads require pycryptodome or cryptography package. "
                "Install with: pip install pycryptodome"
            )


class DeezerDownloadClient:
    """Deezer download client using ARL token authentication."""

    def __init__(self, download_path: str = None):
        from config.settings import config_manager
        self._config = config_manager

        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')
        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        # Download tracking (same pattern as Tidal/Qobuz/HiFi)
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._download_lock = threading.Lock()

        # Shutdown check callback (set by web_server)
        self.shutdown_check = None

        # Rate limiting
        self._last_request = 0
        self._min_interval = 0.5  # 500ms between API calls
        self._api_lock = threading.Lock()

        # Session state
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        self._api_token = None
        self._license_token = None
        self._user_data = None
        self._authenticated = False

        # Quality preference
        self._quality = config_manager.get('deezer_download.quality', 'flac')

        # Try to authenticate on init if ARL is configured
        arl = config_manager.get('deezer_download.arl', '')
        if arl:
            self._authenticate(arl)

        logger.info(f"Deezer download client initialized (download path: {self.download_path})")

    # ─── Authentication ──────────────────────────────────────────

    def _authenticate(self, arl: str) -> bool:
        """Authenticate with Deezer using ARL cookie token."""
        try:
            self._session.cookies.set('arl', arl)

            # Get user data and API token
            resp = self._gw_call('deezer.getUserData')
            if not resp:
                logger.error("Failed to get user data from Deezer")
                return False

            user = resp.get('USER', {})
            user_id = user.get('USER_ID', 0)
            if not user_id or user_id == 0:
                logger.error("Invalid ARL token — Deezer returned no user")
                return False

            self._api_token = resp.get('checkForm', '')
            self._license_token = user.get('OPTIONS', {}).get('license_token', '')
            self._user_data = user
            self._authenticated = True

            user_name = user.get('BLOG_NAME', 'Unknown')
            can_stream_lossless = user.get('OPTIONS', {}).get('web_lossless', False)
            can_stream_hq = user.get('OPTIONS', {}).get('web_hq', False)

            tier = 'Free'
            if can_stream_lossless:
                tier = 'HiFi'
            elif can_stream_hq:
                tier = 'Premium'

            logger.info(f"Deezer authenticated as '{user_name}' (tier: {tier})")
            return True

        except Exception as e:
            logger.error(f"Deezer authentication failed: {e}")
            self._authenticated = False
            return False

    def _gw_call(self, method: str, params: dict = None) -> Optional[dict]:
        """Call the Deezer gateway API."""
        with self._api_lock:
            elapsed = time.time() - self._last_request
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request = time.time()

        try:
            url_params = {'method': method, 'api_version': '1.0'}
            url_params['api_token'] = self._api_token if self._api_token else 'null'

            resp = self._session.post(
                _GW_API,
                params=url_params,
                json=params or {},
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get('error'):
                error = data['error']
                if isinstance(error, dict):
                    error_msg = error.get('VALID_TOKEN_REQUIRED') or error.get('GATEWAY_ERROR') or str(error)
                else:
                    error_msg = str(error)
                if error_msg:
                    logger.warning(f"Deezer API error ({method}): {error_msg}")
                    return None

            return data.get('results', {})

        except Exception as e:
            logger.error(f"Deezer API call failed ({method}): {e}")
            return None

    # ─── Status & Config ─────────────────────────────────────────

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def is_configured(self) -> bool:
        return self._authenticated

    def is_available(self) -> bool:
        return self._authenticated

    def is_authenticated(self) -> bool:
        return self._authenticated

    async def check_connection(self) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.is_available)

    def reconnect(self, arl: str = None) -> bool:
        """Re-authenticate with a new or existing ARL."""
        if arl is None:
            arl = self._config.get('deezer_download.arl', '')
        if not arl:
            return False
        self._authenticated = False
        return self._authenticate(arl)

    def get_quality_label(self) -> str:
        """Get human-readable label for current quality setting."""
        labels = {'flac': 'FLAC (Lossless)', 'mp3_320': 'MP3 320kbps', 'mp3_128': 'MP3 128kbps'}
        return labels.get(self._quality, 'MP3 320kbps')

    # ─── User Playlists (ARL-authenticated) ─────────────────────

    def get_user_playlists(self) -> list:
        """Fetch the authenticated user's playlists via Deezer public API with ARL cookies."""
        if not self._authenticated or not self._user_data:
            return []
        user_id = self._user_data.get('USER_ID')
        if not user_id:
            return []

        playlists = []
        index = 0
        while True:
            try:
                resp = self._session.get(
                    f'https://api.deezer.com/user/{user_id}/playlists',
                    params={'index': index, 'limit': 100},
                    timeout=15
                )
                resp.raise_for_status()
                data = resp.json()
                if 'error' in data:
                    logger.warning(f"Deezer playlists error: {data['error']}")
                    break
                items = data.get('data', [])
                if not items:
                    break
                for p in items:
                    playlists.append({
                        'id': str(p.get('id', '')),
                        'name': p.get('title', ''),
                        'track_count': p.get('nb_tracks', 0),
                        'image_url': p.get('picture_medium', ''),
                        'owner': p.get('creator', {}).get('name', ''),
                        'description': p.get('description', ''),
                    })
                if not data.get('next'):
                    break
                index += len(items)
            except Exception as e:
                logger.error(f"Error fetching user playlists at index {index}: {e}")
                break

        logger.info(f"Fetched {len(playlists)} user playlists from Deezer")
        return playlists

    def get_user_favorite_artists(self, limit: int = 200) -> list:
        """Fetch the authenticated user's favorite artists via public API with ARL cookies."""
        if not self._authenticated or not self._user_data:
            return []
        user_id = self._user_data.get('USER_ID')
        if not user_id:
            return []

        artists = []
        index = 0
        while len(artists) < limit:
            try:
                resp = self._session.get(
                    f'https://api.deezer.com/user/{user_id}/artists',
                    params={'index': index, 'limit': min(100, limit - len(artists))},
                    timeout=15
                )
                resp.raise_for_status()
                data = resp.json()
                if 'error' in data:
                    logger.warning(f"Deezer artists error: {data['error']}")
                    break
                items = data.get('data', [])
                if not items:
                    break
                for a in items:
                    artists.append({
                        'deezer_id': str(a.get('id', '')),
                        'name': a.get('name', ''),
                        'image_url': a.get('picture_xl') or a.get('picture_big') or a.get('picture_medium', ''),
                    })
                if not data.get('next'):
                    break
                index += len(items)
            except Exception as e:
                logger.error(f"Error fetching favorite artists at index {index}: {e}")
                break

        logger.info(f"Fetched {len(artists)} favorite artists from Deezer (ARL)")
        return artists

    def get_user_favorite_albums(self, limit: int = 200) -> list:
        """Fetch the authenticated user's favorite albums via public API with ARL cookies."""
        if not self._authenticated or not self._user_data:
            return []
        user_id = self._user_data.get('USER_ID')
        if not user_id:
            return []

        albums = []
        index = 0
        while len(albums) < limit:
            try:
                resp = self._session.get(
                    f'https://api.deezer.com/user/{user_id}/albums',
                    params={'index': index, 'limit': min(100, limit - len(albums))},
                    timeout=15
                )
                resp.raise_for_status()
                data = resp.json()
                if 'error' in data:
                    logger.warning(f"Deezer albums error: {data['error']}")
                    break
                items = data.get('data', [])
                if not items:
                    break
                for a in items:
                    artist_name = ''
                    if isinstance(a.get('artist'), dict):
                        artist_name = a['artist'].get('name', '')
                    albums.append({
                        'deezer_id': str(a.get('id', '')),
                        'album_name': a.get('title', ''),
                        'artist_name': artist_name,
                        'image_url': a.get('cover_xl') or a.get('cover_big') or a.get('cover_medium', ''),
                        'release_date': a.get('release_date', ''),
                        'total_tracks': a.get('nb_tracks', 0),
                    })
                if not data.get('next'):
                    break
                index += len(items)
            except Exception as e:
                logger.error(f"Error fetching favorite albums at index {index}: {e}")
                break

        logger.info(f"Fetched {len(albums)} favorite albums from Deezer (ARL)")
        return albums

    def get_playlist_tracks(self, playlist_id: str) -> Optional[dict]:
        """Fetch full playlist details with tracks via public API (ARL cookies grant private access)."""
        try:
            resp = self._session.get(
                f'https://api.deezer.com/playlist/{playlist_id}',
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            if 'error' in data:
                logger.error(f"Deezer playlist error: {data['error']}")
                return None

            total_tracks = data.get('nb_tracks', 0)
            raw_tracks = data.get('tracks', {}).get('data', [])

            # Paginate if needed
            while len(raw_tracks) < total_tracks:
                idx = len(raw_tracks)
                page_resp = self._session.get(
                    f'https://api.deezer.com/playlist/{playlist_id}/tracks',
                    params={'index': idx, 'limit': 400},
                    timeout=15
                )
                page_resp.raise_for_status()
                page_data = page_resp.json()
                if 'error' in page_data:
                    break
                page_tracks = page_data.get('data', [])
                if not page_tracks:
                    break
                raw_tracks.extend(page_tracks)

            # Batch-fetch release dates for unique albums (cache-first)
            album_ids = set()
            for t in raw_tracks:
                aid = t.get('album', {}).get('id')
                if aid:
                    album_ids.add(str(aid))
            album_release_dates = {}
            try:
                from core.metadata_cache import get_metadata_cache
                cache = get_metadata_cache()
            except Exception:
                cache = None
            for aid in album_ids:
                # Check metadata cache first
                if cache:
                    try:
                        cached = cache.get_entity('deezer', 'album', aid)
                        if cached and cached.get('release_date'):
                            album_release_dates[aid] = cached['release_date']
                            continue
                    except Exception:
                        pass
                # Cache miss — fetch from API
                try:
                    time.sleep(0.3)  # Respect rate limits
                    a_resp = self._session.get(f'https://api.deezer.com/album/{aid}', timeout=10)
                    if a_resp.ok:
                        a_data = a_resp.json()
                        album_release_dates[aid] = a_data.get('release_date', '')
                        # Store in metadata cache for future use
                        if cache:
                            try:
                                cache.store_entity('deezer', 'album', aid, a_data)
                            except Exception:
                                pass
                except Exception:
                    pass

            tracks = []
            for i, t in enumerate(raw_tracks, start=1):
                artist_name = t.get('artist', {}).get('name', 'Unknown Artist')
                album_data = t.get('album', {})
                album_cover = album_data.get('cover_medium') or album_data.get('cover_small') or ''
                album_id = str(album_data.get('id', ''))
                tracks.append({
                    'id': str(t.get('id', '')),
                    'name': t.get('title', ''),
                    'artists': [{'name': artist_name}],
                    'album': {
                        'name': album_data.get('title', ''),
                        'images': [{'url': album_cover}] if album_cover else [],
                        'release_date': album_release_dates.get(album_id, ''),
                        'album_type': 'album',
                        'total_tracks': total_tracks,
                        'id': album_id,
                    },
                    'duration_ms': t.get('duration', 0) * 1000,
                    'track_number': i,
                })

            return {
                'id': str(data.get('id', '')),
                'name': data.get('title', ''),
                'description': data.get('description', ''),
                'track_count': total_tracks,
                'image_url': data.get('picture_medium', ''),
                'owner': data.get('creator', {}).get('name', ''),
                'tracks': tracks,
            }
        except Exception as e:
            logger.error(f"Error fetching playlist {playlist_id}: {e}")
            return None

    # ─── Track Info ──────────────────────────────────────────────

    def _get_track_data(self, track_id: str) -> Optional[dict]:
        """Get full track data from Deezer private API."""
        return self._gw_call('song.getData', {'sng_id': str(track_id)})

    def _get_media_url(self, track_token: str, quality: str) -> Optional[str]:
        """Get the download URL for a track at the specified quality."""
        if not self._license_token:
            logger.error("No license token — cannot get media URL")
            return None

        fmt = _QUALITY_FORMATS.get(quality)
        if not fmt:
            logger.error(f"Unknown quality: {quality}")
            return None

        try:
            payload = {
                'license_token': self._license_token,
                'media': [{
                    'type': 'FULL',
                    'formats': [fmt]
                }],
                'track_tokens': [track_token]
            }

            resp = self._session.post(_MEDIA_API, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            media_list = data.get('data', [])
            if not media_list:
                return None

            media = media_list[0].get('media', [])
            if not media:
                return None

            sources = media[0].get('sources', [])
            if not sources:
                return None

            # Prefer the first URL
            return sources[0].get('url')

        except Exception as e:
            logger.error(f"Failed to get media URL: {e}")
            return None

    # ─── Search ──────────────────────────────────────────────────

    async def search(self, query: str, timeout: int = None,
                     progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Search Deezer for tracks matching the query."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_sync, query)

    def _search_sync(self, query: str) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Synchronous search implementation."""
        if not self._authenticated:
            logger.warning("Deezer not authenticated — cannot search")
            return [], []

        try:
            resp = self._session.get(
                'https://api.deezer.com/search',
                params={'q': query, 'limit': 30},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

            results = []
            for item in data.get('data', []):
                track_id = str(item.get('id', ''))
                if not track_id:
                    continue

                artist = item.get('artist', {}).get('name', 'Unknown')
                title = item.get('title', 'Unknown')
                album = item.get('album', {}).get('title', '')
                duration_ms = (item.get('duration', 0)) * 1000  # Deezer returns seconds
                # Estimate size based on quality
                duration_s = item.get('duration', 0)
                if self._quality == 'flac':
                    est_size = duration_s * 176400  # ~1411kbps
                    bitrate = 1411
                    quality = 'flac'
                elif self._quality == 'mp3_320':
                    est_size = duration_s * 40000  # ~320kbps
                    bitrate = 320
                    quality = 'mp3'
                else:
                    est_size = duration_s * 16000  # ~128kbps
                    bitrate = 128
                    quality = 'mp3'

                results.append(TrackResult(
                    username='deezer_dl',
                    filename=f"{track_id}||{artist} - {title}",
                    size=est_size,
                    bitrate=bitrate,
                    duration=duration_ms,
                    quality=quality,
                    free_upload_slots=999,
                    upload_speed=999999,
                    queue_length=0,
                    artist=artist,
                    title=title,
                    album=album,
                    track_number=item.get('track_position'),
                ))

            logger.info(f"Deezer search for '{query}' returned {len(results)} results")
            return results, []

        except Exception as e:
            logger.error(f"Deezer search failed: {e}")
            return [], []

    # ─── Download ────────────────────────────────────────────────

    async def download(self, username: str, filename: str,
                       file_size: int = 0) -> Optional[str]:
        """Start a download. Returns download_id immediately."""
        if not self._authenticated:
            logger.error("Deezer not authenticated — cannot download")
            return None

        # Parse filename: "track_id||display_name"
        parts = filename.split('||', 1)
        track_id = parts[0]
        display_name = parts[1] if len(parts) > 1 else f"Track {track_id}"

        download_id = str(uuid.uuid4())

        with self._download_lock:
            self.active_downloads[download_id] = {
                'id': download_id,
                'track_id': track_id,
                'display_name': display_name,
                'filename': filename,
                'username': 'deezer_dl',
                'state': 'Initializing',
                'progress': 0.0,
                'size': file_size,
                'transferred': 0,
                'speed': 0,
                'file_path': None,
                'error': None,
            }

        thread = threading.Thread(
            target=self._download_thread_worker,
            args=(download_id, track_id, display_name),
            daemon=True,
            name=f'deezer-dl-{track_id}'
        )
        thread.start()

        logger.info(f"Started Deezer download {download_id}: {display_name}")
        return download_id

    def _download_thread_worker(self, download_id: str, track_id: str, display_name: str):
        """Background worker for a single download."""
        try:
            result_path = self._download_sync(download_id, track_id, display_name)
            with self._download_lock:
                if download_id in self.active_downloads:
                    dl = self.active_downloads[download_id]
                    if dl['state'] == 'Cancelled':
                        return
                    if result_path:
                        dl['state'] = 'Completed, Succeeded'
                        dl['progress'] = 100.0
                        dl['file_path'] = result_path
                        logger.info(f"Deezer download {download_id} completed: {result_path}")
                    else:
                        dl['state'] = 'Errored'
                        logger.error(f"Deezer download {download_id} failed: {dl.get('error', 'unknown')}")
        except Exception as e:
            logger.error(f"Deezer download thread error: {e}")
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['state'] = 'Errored'
                    self.active_downloads[download_id]['error'] = str(e)

    def _download_sync(self, download_id: str, track_id: str, display_name: str) -> Optional[str]:
        """Synchronous download: get URL, download, decrypt, save."""
        # Check for shutdown
        if self.shutdown_check and self.shutdown_check():
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['state'] = 'Aborted'
            return None

        # Get track data from private API
        track_data = self._get_track_data(track_id)
        if not track_data:
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['error'] = 'Failed to get track data'
            return None

        track_token = track_data.get('TRACK_TOKEN', '')
        if not track_token:
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['error'] = 'No track token available'
            return None

        # Determine quality and get media URL with fallback
        media_url = None
        actual_quality = None
        allow_fallback = self._config.get('deezer_download.allow_fallback', True)

        if allow_fallback:
            quality_order = _QUALITY_ORDER.copy()
            # Start from user's preferred quality
            try:
                pref_idx = quality_order.index(self._quality)
                quality_order = quality_order[pref_idx:] + quality_order[:pref_idx]
            except ValueError:
                pass
        else:
            quality_order = [self._quality]

        for q in quality_order:
            url = self._get_media_url(track_token, q)
            if url:
                media_url = url
                actual_quality = q
                break

        if not media_url:
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['error'] = 'No media URL available (may require higher subscription tier)'
            return None

        if actual_quality != self._quality:
            logger.info(f"Quality fallback: {self._quality} → {actual_quality} for {display_name}")

        # Determine file extension
        ext = '.flac' if actual_quality == 'flac' else '.mp3'

        # Sanitize filename
        safe_name = self._sanitize_filename(display_name)
        out_path = str(self.download_path / f"{safe_name}{ext}")

        # Update state
        with self._download_lock:
            if download_id in self.active_downloads:
                dl = self.active_downloads[download_id]
                dl['state'] = 'InProgress, Downloading'

        # Download and decrypt
        try:
            bf_key = _get_blowfish_key(track_id)
            resp = self._session.get(media_url, stream=True, timeout=30)
            resp.raise_for_status()

            total_size = int(resp.headers.get('content-length', 0))
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['size'] = total_size

            downloaded = 0
            chunk_index = 0
            start_time = time.time()

            with open(out_path, 'wb') as f:
                for raw_chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                    if not raw_chunk:
                        continue

                    # Check for cancellation/shutdown
                    if self.shutdown_check and self.shutdown_check():
                        with self._download_lock:
                            if download_id in self.active_downloads:
                                self.active_downloads[download_id]['state'] = 'Aborted'
                        try:
                            os.remove(out_path)
                        except OSError:
                            pass
                        return None

                    with self._download_lock:
                        if download_id in self.active_downloads:
                            if self.active_downloads[download_id]['state'] == 'Cancelled':
                                try:
                                    os.remove(out_path)
                                except OSError:
                                    pass
                                return None

                    # Decrypt every 3rd chunk (Deezer's encryption pattern)
                    if chunk_index % 3 == 0 and len(raw_chunk) == _CHUNK_SIZE:
                        chunk_to_write = _decrypt_chunk(raw_chunk, bf_key)
                    else:
                        chunk_to_write = raw_chunk

                    f.write(chunk_to_write)
                    downloaded += len(raw_chunk)
                    chunk_index += 1

                    # Update progress
                    elapsed = time.time() - start_time
                    speed = int(downloaded / elapsed) if elapsed > 0 else 0
                    progress = (downloaded / total_size * 100) if total_size > 0 else 0

                    with self._download_lock:
                        if download_id in self.active_downloads:
                            dl = self.active_downloads[download_id]
                            dl['transferred'] = downloaded
                            dl['progress'] = min(progress, 99.9)
                            dl['speed'] = speed

            # Validate file size
            file_size = os.path.getsize(out_path)
            if file_size < _MIN_FILE_SIZE:
                logger.warning(f"Downloaded file too small ({file_size} bytes): {out_path}")
                try:
                    os.remove(out_path)
                except OSError:
                    pass
                with self._download_lock:
                    if download_id in self.active_downloads:
                        self.active_downloads[download_id]['error'] = f'File too small ({file_size} bytes)'
                return None

            logger.info(f"Deezer download complete: {out_path} ({file_size / 1048576:.1f} MB, {actual_quality})")
            return out_path

        except Exception as e:
            logger.error(f"Download error for {display_name}: {e}")
            try:
                os.remove(out_path)
            except OSError:
                pass
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['error'] = str(e)
            return None

    # ─── Download Status ─────────────────────────────────────────

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """Return all active downloads."""
        with self._download_lock:
            return [self._to_status(dl) for dl in self.active_downloads.values()]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """Get status of a specific download."""
        with self._download_lock:
            dl = self.active_downloads.get(download_id)
            return self._to_status(dl) if dl else None

    async def cancel_download(self, download_id: str, username: str = None,
                              remove: bool = False) -> bool:
        """Cancel a download."""
        with self._download_lock:
            dl = self.active_downloads.get(download_id)
            if not dl:
                return False
            dl['state'] = 'Cancelled'
            if remove:
                del self.active_downloads[download_id]
        return True

    async def clear_all_completed_downloads(self) -> bool:
        """Remove all terminal downloads."""
        terminal_states = {'Completed, Succeeded', 'Cancelled', 'Errored', 'Aborted'}
        with self._download_lock:
            to_remove = [k for k, v in self.active_downloads.items() if v['state'] in terminal_states]
            for k in to_remove:
                del self.active_downloads[k]
        return True

    def _to_status(self, dl: dict) -> DownloadStatus:
        """Convert internal dict to DownloadStatus."""
        return DownloadStatus(
            id=dl['id'],
            filename=dl['filename'],
            username=dl['username'],
            state=dl['state'],
            progress=dl['progress'],
            size=dl['size'],
            transferred=dl['transferred'],
            speed=dl['speed'],
            file_path=dl.get('file_path'),
        )

    # ─── Utilities ───────────────────────────────────────────────

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Sanitize a string for use as a filename."""
        import re
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        name = name.strip('. ')
        return name[:200] if name else 'unknown'
