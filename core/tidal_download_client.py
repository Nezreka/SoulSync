"""
Tidal Download Client
Alternative music download source using tidalapi.

This client provides:
- Tidal search with metadata
- Device flow authentication (link.tidal.com)
- HiRes/Lossless/High quality audio downloads via Tidal v2 trackManifests endpoint
- Drop-in replacement compatible with Soulseek interface
"""

import os
import re
import asyncio
import uuid
import time
import shutil
import subprocess
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin

try:
    import tidalapi
except ImportError:
    tidalapi = None

import requests as http_requests

from utils.logging_config import get_logger
from config.settings import config_manager

# Import Soulseek data structures for drop-in replacement compatibility
from core.soulseek_client import TrackResult, AlbumResult, DownloadStatus

logger = get_logger("tidal_download_client")


# Quality tiers definitions (used for display in search results)
QUALITY_MAP = {
    'low': {
        'label': 'AAC 96kbps',
        'extension': 'm4a',
        'bitrate': 96,
        'codec': 'aac',
    },
    'high': {
        'label': 'AAC 320kbps',
        'extension': 'm4a',
        'bitrate': 320,
        'codec': 'aac',
    },
    'lossless': {
        'label': 'FLAC 16-bit/44.1kHz',
        'extension': 'flac',
        'bitrate': 1411,
        'codec': 'flac',
    },
    'hires': {
        'label': 'FLAC 24-bit/96kHz',
        'extension': 'flac',
        'bitrate': 9216,
        'codec': 'flac',
    },
}

# HLS-specific format mapping for v2 trackManifests endpoint
HLS_QUALITY_MAP = {
    'hires': {
        'formats': ['FLAC_HIRES'],
        'manifest_type': 'HLS',
        'extension': 'flac',
    },
    'lossless': {
        'formats': ['FLAC'],
        'manifest_type': 'HLS',
        'extension': 'flac',
    },
    'high': {
        'formats': ['AACLC'],
        'manifest_type': 'HLS',
        'extension': 'm4a',
    },
    'low': {
        'formats': ['HEAACV1'],
        'manifest_type': 'HLS',
        'extension': 'm4a',
    },
}

HLS_MAP_TAG_RE = re.compile(r'#EXT-X-MAP:.*URI="([^"]+)"')


class TidalDownloadClient:
    """
    Tidal download client using tidalapi.
    Provides search, matching, and download capabilities as a drop-in alternative to YouTube/Soulseek.
    """

    def __init__(self, download_path: str = None):
        if tidalapi is None:
            logger.warning("tidalapi not installed — Tidal downloads unavailable")

        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')

        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Tidal download client using download path: {self.download_path}")

        self.shutdown_check = None

        self.session: Optional['tidalapi.Session'] = None
        self._init_session()

        self._device_auth_future = None
        self._device_auth_link = None

        # Engine reference is populated by set_engine() at registration
        # time. Until then dispatch returns None — orchestrator wires
        # this immediately so the only None case is tests that bypass
        # the orchestrator.
        self._engine = None

    def set_engine(self, engine):
        """Engine callback — gives the client access to the central
        thread worker + state store. Engine calls this during
        ``register_plugin`` if the plugin defines it."""
        self._engine = engine

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def _init_session(self):
        if tidalapi is None:
            return

        self.session = tidalapi.Session()

        saved = config_manager.get('tidal_download.session', {})
        token_type = saved.get('token_type', '')
        access_token = saved.get('access_token', '')
        refresh_token = saved.get('refresh_token', '')
        expiry_time = saved.get('expiry_time', 0)

        if token_type and access_token:
            try:
                expiry_dt = datetime.fromtimestamp(expiry_time, tz=timezone.utc) if expiry_time else None

                restored = self.session.load_oauth_session(
                    token_type=token_type,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expiry_time=expiry_dt,
                )
                if restored and self.session.check_login():
                    logger.info("Restored Tidal download session from saved tokens")
                    self._save_session()
                    return
                else:
                    logger.warning("Saved Tidal session tokens are invalid/expired")
            except Exception as e:
                logger.warning(f"Could not restore Tidal session: {e}")

    def _save_session(self):
        if not self.session:
            return
        config_manager.set('tidal_download.session', {
            'token_type': self.session.token_type or '',
            'access_token': self.session.access_token or '',
            'refresh_token': self.session.refresh_token or '',
            'expiry_time': self.session.expiry_time.timestamp() if self.session.expiry_time else 0,
        })

    def is_authenticated(self) -> bool:
        if not self.session:
            return False
        try:
            return self.session.check_login()
        except Exception:
            return False

    def start_device_auth(self) -> Optional[Dict[str, str]]:
        if tidalapi is None:
            return None

        try:
            if not self.session:
                self.session = tidalapi.Session()

            login, future = self.session.login_oauth()
            self._device_auth_future = future
            raw_uri = login.verification_uri_complete or f"link.tidal.com/{login.user_code}"
            if not raw_uri.startswith(('http://', 'https://')):
                raw_uri = f"https://{raw_uri}"
            self._device_auth_link = {
                'verification_uri': raw_uri,
                'user_code': login.user_code,
            }
            logger.info(f"Tidal device auth started — code: {login.user_code}")
            return self._device_auth_link

        except Exception as e:
            logger.error(f"Failed to start Tidal device auth: {e}")
            return None

    def check_device_auth(self) -> Dict[str, Any]:
        if not self._device_auth_future:
            return {'status': 'error', 'message': 'No auth in progress'}

        try:
            if self._device_auth_future.running():
                return {
                    'status': 'pending',
                    'verification_uri': self._device_auth_link.get('verification_uri', ''),
                    'user_code': self._device_auth_link.get('user_code', ''),
                }

            result = self._device_auth_future.result(timeout=0)
            if self.session and self.session.check_login():
                self._save_session()
                logger.info("Tidal device auth completed successfully")
                return {'status': 'completed', 'message': 'Authenticated successfully'}
            else:
                return {'status': 'error', 'message': 'Auth completed but session invalid'}

        except Exception as e:
            logger.error(f"Tidal device auth check error: {e}")
            return {'status': 'error', 'message': str(e)}

    def is_available(self) -> bool:
        return tidalapi is not None and self.is_authenticated()

    def is_configured(self) -> bool:
        return self.is_available()

    async def check_connection(self) -> bool:
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.is_available)
        except Exception as e:
            logger.error(f"Tidal connection check failed: {e}")
            return False

    _QUALIFIER_KEYWORDS = frozenset({
        'remix', 'mix', 'edit', 'version', 'dub', 'rmx', 'vip', 'cut',
        'rework', 'bootleg', 'flip',
        'live', 'concert', 'unplugged', 'acoustic', 'session',
        'instrumental', 'karaoke', 'demo', 'bonus',
        'extended', 'radio',
    })

    @classmethod
    def _extract_qualifiers(cls, query: str) -> List[str]:
        if not query:
            return []
        found = []
        q_lower = query.lower()
        for kw in cls._QUALIFIER_KEYWORDS:
            if re.search(r'\b' + re.escape(kw) + r'\b', q_lower):
                found.append(kw)
        return found

    @staticmethod
    def _track_name_contains_qualifiers(track_name: str, qualifiers: List[str]) -> bool:
        if not qualifiers:
            return True
        if not track_name:
            return False
        name_lower = track_name.lower()
        for kw in qualifiers:
            if not re.search(r'\b' + re.escape(kw) + r'\b', name_lower):
                return False
        return True

    @staticmethod
    def _generate_shortened_queries(original: str) -> List[str]:
        variants: List[str] = []
        seen = {original.strip().lower()}

        def _add(candidate: str) -> None:
            candidate = candidate.strip()
            if candidate and candidate.lower() not in seen:
                variants.append(candidate)
                seen.add(candidate.lower())

        _add(re.sub(r'\s*[\(\[][^\)\]]*[\)\]]\s*$', '', original))
        _add(re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', ' ', original))

        tokens = original.split()

        if len(tokens) >= 3:
            _add(' '.join(tokens[:-1]))

        if len(tokens) >= 4:
            _add(' '.join(tokens[:-2]))

        if len(tokens) >= 5:
            _add(' '.join(tokens[:-3]))

        if len(tokens) >= 7:
            _add(' '.join(tokens[:len(tokens) // 2 + 1]))

        return variants

    async def search(self, query: str, timeout: int = None, progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        if not self.is_available():
            logger.warning("Tidal not available for search (not authenticated)")
            return ([], [])

        if not query or not isinstance(query, str):
            logger.warning(f"Invalid Tidal search query: {query!r}")
            return ([], [])

        logger.info(f"Searching Tidal for: {query}")

        try:
            queries_to_try = [query] + self._generate_shortened_queries(query)
            queries_to_try = queries_to_try[:5]

            required_qualifiers = self._extract_qualifiers(query)

            tidal_tracks: list = []
            successful_query: Optional[str] = None
            last_error: Optional[Exception] = None
            any_fallback_filtered_out = False

            loop = asyncio.get_event_loop()
            for attempt_idx, attempt_query in enumerate(queries_to_try):
                try:
                    q_copy = attempt_query

                    def _search(q=q_copy):
                        results = self.session.search(q, models=[tidalapi.media.Track], limit=50)
                        return results.get('tracks', []) if isinstance(results, dict) else []

                    found = await loop.run_in_executor(None, _search)

                    if found:
                        is_fallback = attempt_idx > 0
                        if is_fallback and required_qualifiers:
                            filtered = [
                                t for t in found
                                if self._track_name_contains_qualifiers(getattr(t, 'name', ''), required_qualifiers)
                            ]
                            if filtered:
                                tidal_tracks = filtered
                                successful_query = attempt_query
                                logger.info(
                                    f"Tidal fallback kept {len(filtered)}/{len(found)} tracks "
                                    f"after qualifier filter {required_qualifiers} for '{attempt_query}'"
                                )
                                break
                            else:
                                any_fallback_filtered_out = True
                                logger.debug(
                                    f"Tidal fallback '{attempt_query}' returned {len(found)} tracks "
                                    f"but none matched original qualifiers {required_qualifiers} — "
                                    f"trying next variant"
                                )
                                if attempt_idx < len(queries_to_try) - 1:
                                    await asyncio.sleep(0.1)
                                continue
                        else:
                            tidal_tracks = found
                            successful_query = attempt_query
                            break

                    if attempt_idx < len(queries_to_try) - 1:
                        logger.debug(f"Tidal returned 0 results for '{attempt_query}' — trying shortened variant")
                        await asyncio.sleep(0.1)
                except Exception as e:
                    last_error = e
                    logger.debug(f"Tidal search attempt {attempt_idx + 1} failed: {e}")

            if not tidal_tracks:
                if last_error is not None:
                    import traceback
                    tb_str = ''.join(traceback.format_exception(
                        type(last_error), last_error, last_error.__traceback__
                    ))
                    logger.error(
                        f"Tidal search failed after {len(queries_to_try)} attempts: {last_error}\n{tb_str}"
                    )
                elif any_fallback_filtered_out:
                    logger.warning(
                        f"No Tidal results for '{query}' — fallbacks found broader matches but "
                        f"none preserved required qualifiers {required_qualifiers}"
                    )
                else:
                    logger.warning(f"No Tidal results for: {query}")
                return ([], [])

            if successful_query and successful_query != query:
                logger.info(f"Tidal fallback query succeeded: '{successful_query}' (original: '{query}')")

            quality_key = config_manager.get('tidal_download.quality', 'lossless')
            quality_info = QUALITY_MAP.get(quality_key, QUALITY_MAP['lossless'])

            track_results = []
            for track in tidal_tracks:
                try:
                    track_result = self._tidal_to_track_result(track, quality_info)
                    track_results.append(track_result)
                except Exception as e:
                    logger.debug(f"Skipping track conversion error: {e}")

            logger.info(f"Found {len(track_results)} Tidal tracks")
            return (track_results, [])

        except Exception as e:
            logger.error(f"Tidal search orchestration failed: {e}")
            import traceback
            traceback.print_exc()
            return ([], [])

    def _tidal_to_track_result(self, track, quality_info: dict) -> TrackResult:
        artist_name = track.artist.name if track.artist else 'Unknown Artist'
        title = track.name or 'Unknown Title'
        album_name = track.album.name if track.album else None

        duration_ms = int(track.duration * 1000) if track.duration else None

        display_name = f"{artist_name} - {title}"
        filename = f"{track.id}||{display_name}"

        track_result = TrackResult(
            username='tidal',
            filename=filename,
            size=0,
            bitrate=quality_info.get('bitrate'),
            duration=duration_ms,
            quality=quality_info.get('codec', 'flac'),
            free_upload_slots=999,
            upload_speed=999999,
            queue_length=0,
            artist=artist_name,
            title=title,
            album=album_name,
            track_number=track.track_num,
            _source_metadata={
                'source': 'tidal',
                'track_id': track.id,
                'artist_id': track.artist.id if track.artist else None,
                'isrc': track.isrc or None,
                'bpm': track.bpm if track.bpm and track.bpm > 0 else None,
                'copyright': track.copyright or None,
            },
        )

        return track_result

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

        access_token = self.session.access_token
        if not access_token:
            logger.error("No Tidal access token available")
            return None

        url = f"https://openapi.tidal.com/v2/trackManifests/{track_id}"
        params = [
            ('adaptive', 'true'),
            ('manifestType', 'HLS'),
            ('uriScheme', 'HTTPS'),
            ('usage', 'DOWNLOAD'),
        ]
        for fmt in formats:
            params.append(('formats', fmt))

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/vnd.api+json',
        }

        try:
            response = http_requests.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            data = response.json()
        except http_requests.HTTPError as e:
            logger.warning(f"Failed to fetch HLS manifest for track {track_id}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch HLS manifest for track {track_id}: {e}")
            return None

        try:
            attrs = data.get('data', {}).get('attributes', {})
            uri = attrs.get('uri')
        except (AttributeError, KeyError) as e:
            logger.warning(f"Failed to extract playlist URI from manifest response for track {track_id}: {e}")
            return None

        if not uri:
            logger.warning(f"No playlist URI in manifest for track {track_id}")
            return None

        try:
            playlist_resp = http_requests.get(uri, allow_redirects=True, timeout=30)
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
                variant_resp = http_requests.get(playlist_uri, allow_redirects=True, timeout=30)
                variant_resp.raise_for_status()
                variant_text = variant_resp.text
                init_uri, segment_uris = self._parse_hls_playlist(variant_text, playlist_uri)
            except Exception as e:
                logger.warning(f"Failed to fetch variant playlist for track {track_id}: {e}")
                return None

        if init_uri:
            logger.info(f"Tidal HLS manifest for track {track_id}: "
                        f"init segment + {len(segment_uris)} segments ({quality})")
        else:
            logger.info(f"Tidal HLS manifest for track {track_id}: "
                        f"{len(segment_uris)} segments ({quality})")

        return {
            'init_uri': init_uri,
            'segment_uris': segment_uris,
            'extension': QUALITY_MAP.get(quality, {}).get('extension', 'flac'),
            'codec': QUALITY_MAP.get(quality, {}).get('codec', 'flac'),
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

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        if '||' not in filename:
            logger.error(f"Invalid filename format: {filename}")
            return None
        if self._engine is None:
            logger.error("Tidal client has no engine reference — cannot dispatch download")
            return None

        track_id_str, display_name = filename.split('||', 1)
        try:
            track_id = int(track_id_str)
        except ValueError:
            logger.error(f"Invalid Tidal track ID: {track_id_str}")
            return None

        logger.info(f"Starting Tidal download: {display_name}")

        return self._engine.worker.dispatch(
            source_name='tidal',
            target_id=track_id,
            display_name=display_name,
            original_filename=filename,
            impl_callable=self._download_sync,
            extra_record_fields={
                'track_id': track_id,
                'display_name': display_name,
            },
        )

    def _download_sync(self, download_id: str, track_id: int, display_name: str) -> Optional[str]:
        if not self.session or not self.session.check_login():
            logger.error("Tidal session not authenticated")
            return None

        quality_key = config_manager.get('tidal_download.quality', 'lossless')
        chain = ['hires', 'lossless', 'high', 'low']
        start = chain.index(quality_key) if quality_key in chain else 1
        allow_fallback = config_manager.get('tidal_download.allow_fallback', True)
        chain = chain[start:] if allow_fallback else [quality_key]

        MIN_AUDIO_SIZE = 100 * 1024

        for q_key in chain:
            if self.shutdown_check and self.shutdown_check():
                logger.info("Shutdown detected, aborting Tidal download")
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

                logger.info(f"Downloading from Tidal ({q_key}): {out_filename} "
                            f"({total_segments} segments)")

                downloaded = 0
                speed_start = time.time()
                segments_completed = 0

                if self._engine is not None:
                    self._engine.update_record('tidal', download_id, {'size': 0})

                with intermediate_path.open('wb') as output_file:
                    if init_uri:
                        if self.shutdown_check and self.shutdown_check():
                            logger.info("Shutdown detected, aborting Tidal download")
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
                            logger.info("Shutdown detected, aborting Tidal download")
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

                logger.info(f"Tidal download complete ({q_key}): {out_path} "
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
                resp = http_requests.get(url, allow_redirects=True, timeout=30)
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
                logger.warning(f"Tidal segment download failed (attempt {attempt + 1}/4), "
                              f"retrying in 2s: {url}")
                time.sleep(2)

        raise last_error

    def _update_download_progress(self, download_id: str, downloaded: int,
                                  segments_completed: int, total_segments: int,
                                  speed_start: float):
        if self._engine is None:
            return
        record = self._engine.get_record('tidal', download_id)
        if record is None:
            return

        now = time.time()
        elapsed_total = now - speed_start
        speed = int(downloaded / elapsed_total) if elapsed_total > 0 else 0

        progress = record.get('progress', 0.0)
        if total_segments > 0:
            progress = round(min((segments_completed / total_segments) * 100, 99.9), 1)

        time_remaining = None
        if speed > 0:
            remaining_bytes = downloaded * (total_segments / max(segments_completed, 1)) - downloaded
            if remaining_bytes > 0:
                time_remaining = int(remaining_bytes / speed)

        self._engine.update_record('tidal', download_id, {
            'transferred': downloaded,
            'speed': speed,
            'progress': progress,
            'time_remaining': time_remaining,
        })

    def _record_to_status(self, record):
        return DownloadStatus(
            id=record['id'],
            filename=record['filename'],
            username=record['username'],
            state=record['state'],
            progress=record['progress'],
            size=record.get('size', 0),
            transferred=record.get('transferred', 0),
            speed=record.get('speed', 0),
            time_remaining=record.get('time_remaining'),
            file_path=record.get('file_path'),
        )

    async def get_all_downloads(self) -> List[DownloadStatus]:
        if self._engine is None:
            return []
        return [
            self._record_to_status(record)
            for record in self._engine.iter_records_for_source('tidal')
        ]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        if self._engine is None:
            return None
        record = self._engine.get_record('tidal', download_id)
        return self._record_to_status(record) if record is not None else None

    async def cancel_download(self, download_id: str, username: str = None, remove: bool = False) -> bool:
        if self._engine is None:
            return False
        if self._engine.get_record('tidal', download_id) is None:
            logger.warning(f"Tidal download {download_id} not found")
            return False
        self._engine.update_record('tidal', download_id, {'state': 'Cancelled'})
        logger.info(f"Marked Tidal download {download_id} as cancelled")
        if remove:
            self._engine.remove_record('tidal', download_id)
            logger.info(f"Removed Tidal download {download_id} from queue")
        return True

    async def clear_all_completed_downloads(self) -> bool:
        if self._engine is None:
            return True
        try:
            terminal = {'Completed, Succeeded', 'Cancelled', 'Errored', 'Aborted'}
            for record in list(self._engine.iter_records_for_source('tidal')):
                if record.get('state') in terminal:
                    self._engine.remove_record('tidal', record['id'])
            return True
        except Exception as e:
            logger.error(f"Error clearing downloads: {e}")
            return False
