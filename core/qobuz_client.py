"""
Qobuz Download Client
Alternative music download source using Qobuz's API.

This client provides:
- Qobuz search with metadata
- Email/password authentication
- Hi-Res/Lossless/MP3 quality audio downloads
- Drop-in replacement compatible with Soulseek interface

Requires a paid Qobuz subscription.
"""

import os
import re
import hashlib
import time
import asyncio
import uuid
import threading
import base64
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path

import requests as http_requests

from utils.logging_config import get_logger
from config.settings import config_manager

# Import Soulseek data structures for drop-in replacement compatibility
from core.soulseek_client import TrackResult, AlbumResult, DownloadStatus

logger = get_logger("qobuz_client")

QOBUZ_API_BASE = "https://www.qobuz.com/api.json/0.2/"

# ── Module-level rate limiting (shared across ALL QobuzClient instances) ──
_qobuz_api_lock = threading.Lock()
_qobuz_last_api_call = 0.0
_QOBUZ_MIN_INTERVAL = 1.0  # 1 request/sec (60/min, matches streamrip default)

# Global rate limit ban state (like Spotify's pattern)
_qobuz_rate_limit_until = 0.0
_qobuz_rate_limit_lock = threading.Lock()


def _qobuz_throttle():
    """Enforce minimum interval between Qobuz API calls across all instances."""
    global _qobuz_last_api_call
    with _qobuz_api_lock:
        now = time.time()
        elapsed = now - _qobuz_last_api_call
        if elapsed < _QOBUZ_MIN_INTERVAL:
            time.sleep(_QOBUZ_MIN_INTERVAL - elapsed)
        _qobuz_last_api_call = time.time()

    from core.api_call_tracker import api_call_tracker
    api_call_tracker.record_call('qobuz')


def _qobuz_set_rate_limit(retry_after: float = 60.0):
    """Set a global rate limit ban for all Qobuz instances."""
    global _qobuz_rate_limit_until
    with _qobuz_rate_limit_lock:
        _qobuz_rate_limit_until = time.time() + retry_after
        logger.warning(f"Qobuz global rate limit set for {retry_after}s")


def _qobuz_is_rate_limited() -> bool:
    """Check if Qobuz is currently rate limited."""
    with _qobuz_rate_limit_lock:
        return time.time() < _qobuz_rate_limit_until

# Quality tier definitions (format_id values)
QOBUZ_QUALITY_MAP = {
    'mp3': {
        'format_id': 5,
        'label': 'MP3 320kbps',
        'extension': 'mp3',
        'bitrate': 320,
        'codec': 'mp3',
    },
    'lossless': {
        'format_id': 6,
        'label': 'FLAC 16-bit/44.1kHz (CD)',
        'extension': 'flac',
        'bitrate': 1411,
        'codec': 'flac',
    },
    'hires': {
        'format_id': 7,
        'label': 'FLAC 24-bit/96kHz (Hi-Res)',
        'extension': 'flac',
        'bitrate': 4608,
        'codec': 'flac',
    },
    'hires_max': {
        'format_id': 27,
        'label': 'FLAC 24-bit/192kHz (Hi-Res Max)',
        'extension': 'flac',
        'bitrate': 9216,
        'codec': 'flac',
    },
}


class QobuzClient:
    """
    Qobuz download client using Qobuz REST API.
    Provides search, matching, and download capabilities as a drop-in alternative to Soulseek/YouTube/Tidal.
    """

    def __init__(self, download_path: str = None):
        # Use Soulseek download path for consistency (post-processing expects files here)
        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')

        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Qobuz client using download path: {self.download_path}")

        # Callback for shutdown check
        self.shutdown_check = None

        # HTTP session
        self.session = http_requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
        })

        # Auth state
        self.app_id: Optional[str] = None
        self.app_secret: Optional[str] = None
        self.user_auth_token: Optional[str] = None
        self.user_info: Optional[Dict] = None
        self._auth_error: Optional[str] = None

        # Download queue management
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._download_lock = threading.Lock()

        # Try to restore saved session
        self._restore_session()

    def set_shutdown_check(self, check_callable):
        """Set a callback function to check for system shutdown"""
        self.shutdown_check = check_callable

    # ===================== Auth =====================

    def _restore_session(self):
        """Try to restore saved session from config."""
        saved = config_manager.get('qobuz.session', {})
        app_id = saved.get('app_id', '')
        app_secret = saved.get('app_secret', '')
        user_auth_token = saved.get('user_auth_token', '')

        if app_id and app_secret and user_auth_token:
            self.app_id = app_id
            self.app_secret = app_secret
            self.user_auth_token = user_auth_token
            self.session.headers.update({
                'X-App-Id': self.app_id,
                'X-User-Auth-Token': self.user_auth_token,
            })

            # Verify the token is still valid
            try:
                resp = self.session.get(
                    QOBUZ_API_BASE + 'user/get',
                    params={'user_id': 'me'},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self.user_info = data
                    logger.info(f"Restored Qobuz session for user: {data.get('display_name', data.get('email', 'unknown'))}")
                    return
                else:
                    logger.warning(f"Saved Qobuz session invalid (HTTP {resp.status_code})")
            except Exception as e:
                logger.warning(f"Could not verify saved Qobuz session: {e}")

            # Token invalid, clear it
            self.user_auth_token = None
            self.session.headers.pop('X-User-Auth-Token', None)

    def _save_session(self):
        """Persist session to config."""
        config_manager.set('qobuz.session', {
            'app_id': self.app_id or '',
            'app_secret': self.app_secret or '',
            'user_auth_token': self.user_auth_token or '',
        })

    def _extract_app_credentials(self) -> bool:
        """
        Extract app_id and app_secret from Qobuz web player bundle.

        The secret is obfuscated across three base64 fragments tied to timezone entries:
        1. initialSeed() calls pair a seed with a timezone name
        2. Timezone objects have info and extras fields
        3. Concatenate seed + info + extras, drop last 44 chars, base64 decode

        Returns True if successful.
        """
        try:
            logger.info("Extracting Qobuz app credentials from web player...")

            # Step 1: Fetch login page to find bundle.js URL
            login_page = self.session.get('https://play.qobuz.com/login', timeout=15)
            if login_page.status_code != 200:
                logger.error(f"Could not fetch Qobuz login page: HTTP {login_page.status_code}")
                return False

            # Find bundle.js URL in the HTML
            bundle_pattern = r'<script\s+src="(/resources/\d+\.\d+\.\d+-[a-z]\d+/bundle\.js)"'
            match = re.search(bundle_pattern, login_page.text)
            if not match:
                bundle_pattern = r'<script\s+src="([^"]*bundle[^"]*\.js)"'
                match = re.search(bundle_pattern, login_page.text)

            if not match:
                logger.error("Could not find bundle.js URL in Qobuz login page")
                return False

            bundle_url = 'https://play.qobuz.com' + match.group(1)
            logger.info(f"Found bundle URL: {bundle_url}")

            # Step 2: Download the bundle
            bundle_resp = self.session.get(bundle_url, timeout=30)
            if bundle_resp.status_code != 200:
                logger.error(f"Could not download Qobuz bundle: HTTP {bundle_resp.status_code}")
                return False

            bundle_text = bundle_resp.text

            # Step 3: Extract app_id
            app_id_match = re.search(r'production:\{api:\{appId:"(\d{9})"', bundle_text)
            if not app_id_match:
                app_id_match = re.search(r'app_id\s*[:=]\s*"(\d{9})"', bundle_text)

            if not app_id_match:
                logger.error("Could not extract app_id from Qobuz bundle")
                return False

            self.app_id = app_id_match.group(1)
            logger.info(f"Extracted app_id: {self.app_id}")

            # Step 4: Extract seed + timezone pairs from initialSeed() calls
            from collections import OrderedDict
            seed_timezone_regex = re.compile(
                r'[a-z]\.initialSeed\("(?P<seed>[\w=]+)",\s*window\.utimezone\.(?P<timezone>[a-z]+)\)'
            )

            secrets = OrderedDict()
            for m in seed_timezone_regex.finditer(bundle_text):
                seed = m.group("seed")
                timezone = m.group("timezone")
                secrets[timezone] = [seed]

            if not secrets:
                logger.warning("No initialSeed() calls found in bundle — trying fallback extraction")
                return self._extract_app_credentials_fallback(bundle_text)

            logger.info(f"Found {len(secrets)} seed/timezone pairs: {list(secrets.keys())}")

            # Step 5: Extract info + extras for each timezone
            timezones_pattern = "|".join([tz.capitalize() for tz in secrets.keys()])
            info_extras_regex = re.compile(
                rf'name:"\w+/(?P<timezone>{timezones_pattern})",info:"(?P<info>[\w=]+)",extras:"(?P<extras>[\w=]+)"'
            )

            for m in info_extras_regex.finditer(bundle_text):
                timezone = m.group("timezone").lower()
                info = m.group("info")
                extras = m.group("extras")
                if timezone in secrets:
                    secrets[timezone].extend([info, extras])

            # Step 6: Decode each candidate secret
            # Concatenate 3 fragments, drop last 44 chars, base64 decode
            decoded_secrets = []
            for tz, fragments in secrets.items():
                if len(fragments) != 3:
                    logger.debug(f"Timezone {tz} has {len(fragments)} fragments (need 3), skipping")
                    continue

                combined = "".join(fragments)
                trimmed = combined[:-44]

                try:
                    decoded = base64.b64decode(trimmed).decode("utf-8")
                    if decoded and len(decoded) >= 30:
                        decoded_secrets.append((tz, decoded))
                        logger.debug(f"Decoded candidate secret from {tz}: {decoded[:8]}...")
                except (base64.binascii.Error, UnicodeDecodeError) as e:
                    logger.debug(f"Failed to decode secret from {tz}: {e}")
                    continue

            if not decoded_secrets:
                logger.warning("No valid secrets decoded — trying fallback")
                return self._extract_app_credentials_fallback(bundle_text)

            # Step 7: Validate which secret works by test-signing an API call
            for tz, secret in decoded_secrets:
                if self._test_secret(secret):
                    self.app_secret = secret
                    logger.info(f"Found working app_secret via timezone: {tz}")
                    return True

            logger.error(f"None of {len(decoded_secrets)} decoded secrets passed validation")
            return False

        except Exception as e:
            logger.error(f"Failed to extract Qobuz app credentials: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _extract_app_credentials_fallback(self, bundle_text: str) -> bool:
        """Fallback: try direct hex string extraction from bundle."""
        secret_matches = re.findall(r'["\']([a-f0-9]{32})["\']', bundle_text)
        for secret_candidate in secret_matches:
            if self._test_secret(secret_candidate):
                self.app_secret = secret_candidate
                logger.info("Found working app_secret via direct hex extraction")
                return True

        logger.error("Could not extract working app_secret from Qobuz bundle (all methods exhausted)")
        return False

    def _test_secret(self, secret: str) -> bool:
        """Test if an app_secret works by making a signed stream URL request."""
        if not self.app_id or not secret:
            return False

        try:
            ts = int(time.time())
            # Sign a request for track_id=1 with format_id=27 (same as qobuz-dl validation)
            sig_raw = f"trackgetFileUrlformat_id27intentstreamtrack_id1{ts}{secret}"
            sig = hashlib.md5(sig_raw.encode()).hexdigest()

            resp = self.session.get(
                QOBUZ_API_BASE + 'track/getFileUrl',
                params={
                    'track_id': 1,
                    'format_id': 27,
                    'intent': 'stream',
                    'request_ts': ts,
                    'request_sig': sig,
                },
                headers={'X-App-Id': self.app_id},
                timeout=10,
            )

            # 400 = "Invalid Request Signature" means bad secret
            # 200/401/403 = signature was accepted (just auth/permission issue)
            is_valid = resp.status_code != 400
            if is_valid:
                logger.debug(f"Secret test passed (HTTP {resp.status_code})")
            else:
                logger.debug(f"Secret test failed (HTTP 400 — invalid signature)")
            return is_valid

        except Exception as e:
            logger.debug(f"Secret test exception: {e}")
            return False

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """
        Login to Qobuz with email/password.

        Returns dict with status info:
            {'status': 'success'|'error', 'message': '...', 'user': {...}}
        """
        self._auth_error = None

        try:
            # Step 1: Extract app credentials if we don't have them
            if not self.app_id or not self.app_secret:
                if not self._extract_app_credentials():
                    self._auth_error = 'Could not extract Qobuz app credentials. Qobuz may have updated their web player.'
                    return {'status': 'error', 'message': self._auth_error}

            # Step 2: Login with email/password
            self.session.headers['X-App-Id'] = self.app_id

            resp = self.session.get(
                QOBUZ_API_BASE + 'user/login',
                params={
                    'email': email,
                    'password': password,
                    'app_id': self.app_id,
                },
                timeout=15,
            )

            if resp.status_code == 401:
                self._auth_error = 'Invalid email or password'
                return {'status': 'error', 'message': self._auth_error}
            elif resp.status_code == 400:
                data = resp.json() if resp.text else {}
                self._auth_error = data.get('message', 'Login failed — check your credentials')
                return {'status': 'error', 'message': self._auth_error}
            elif resp.status_code != 200:
                self._auth_error = f'Qobuz API error (HTTP {resp.status_code})'
                return {'status': 'error', 'message': self._auth_error}

            data = resp.json()

            # Extract user auth token
            self.user_auth_token = data.get('user_auth_token')
            if not self.user_auth_token:
                self._auth_error = 'No auth token in response'
                return {'status': 'error', 'message': self._auth_error}

            self.user_info = data.get('user', {})
            self.session.headers['X-User-Auth-Token'] = self.user_auth_token

            # Check subscription status
            subscription = self.user_info.get('credential', {})
            sub_label = subscription.get('label', 'Unknown')

            # Save session
            self._save_session()

            display_name = self.user_info.get('display_name', self.user_info.get('email', email))
            logger.info(f"Qobuz login successful: {display_name} (plan: {sub_label})")

            return {
                'status': 'success',
                'message': f'Logged in as {display_name}',
                'user': {
                    'display_name': display_name,
                    'subscription': sub_label,
                    'email': self.user_info.get('email', email),
                },
            }

        except Exception as e:
            self._auth_error = str(e)
            logger.error(f"Qobuz login failed: {e}")
            import traceback
            traceback.print_exc()
            return {'status': 'error', 'message': self._auth_error}

    def login_with_token(self, token: str) -> Dict[str, Any]:
        """
        Login to Qobuz with a user_auth_token pasted from the browser.
        Bypasses email/password login (and any CAPTCHA) entirely.
        """
        self._auth_error = None
        try:
            # Step 1: Extract app credentials if we don't have them
            if not self.app_id or not self.app_secret:
                if not self._extract_app_credentials():
                    self._auth_error = 'Could not extract Qobuz app credentials. Qobuz may have updated their web player.'
                    return {'status': 'error', 'message': self._auth_error}

            # Step 2: Set the token and validate it
            self.user_auth_token = token.strip()
            self.session.headers['X-App-Id'] = self.app_id
            self.session.headers['X-User-Auth-Token'] = self.user_auth_token

            resp = self.session.get(
                QOBUZ_API_BASE + 'user/get',
                params={'user_id': 'me'},
                timeout=15,
            )

            if resp.status_code != 200:
                self.user_auth_token = None
                self.session.headers.pop('X-User-Auth-Token', None)
                self._auth_error = f'Invalid token (HTTP {resp.status_code})'
                return {'status': 'error', 'message': self._auth_error}

            data = resp.json()
            self.user_info = data

            # Check subscription
            subscription = data.get('credential', {})
            sub_label = subscription.get('label', 'Unknown')

            # Save session
            self._save_session()

            display_name = data.get('display_name', data.get('email', 'unknown'))
            logger.info(f"Qobuz token login successful: {display_name} (plan: {sub_label})")

            return {
                'status': 'success',
                'message': f'Logged in as {display_name}',
                'user': {
                    'display_name': display_name,
                    'subscription': sub_label,
                    'email': data.get('email', ''),
                },
            }

        except Exception as e:
            self._auth_error = str(e)
            logger.error(f"Qobuz token login failed: {e}")
            return {'status': 'error', 'message': self._auth_error}

    def logout(self):
        """Clear Qobuz session."""
        self.user_auth_token = None
        self.user_info = None
        self.app_id = None
        self.app_secret = None
        self._auth_error = None
        self.session.headers.pop('X-User-Auth-Token', None)
        self.session.headers.pop('X-App-Id', None)
        config_manager.set('qobuz.session', {})
        logger.info("Qobuz session cleared")

    def is_authenticated(self) -> bool:
        """Check if we have a valid Qobuz session."""
        return bool(self.user_auth_token and self.app_id and self.app_secret)

    # ===================== Search =====================

    def is_available(self) -> bool:
        """Check if Qobuz client is available and authenticated."""
        return self.is_authenticated()

    def is_configured(self) -> bool:
        """Check if Qobuz client is configured (matches Soulseek interface)."""
        return self.is_available()

    async def check_connection(self) -> bool:
        """Test if Qobuz is accessible (async, Soulseek-compatible)."""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.is_available)
        except Exception as e:
            logger.error(f"Qobuz connection check failed: {e}")
            return False

    def _api_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make an authenticated API request to Qobuz."""
        if not self.is_authenticated():
            logger.warning("Qobuz not authenticated")
            return None

        if _qobuz_is_rate_limited():
            logger.debug(f"Qobuz rate limited, skipping {endpoint}")
            return None

        _qobuz_throttle()

        try:
            resp = self.session.get(
                QOBUZ_API_BASE + endpoint,
                params=params or {},
                timeout=15,
            )

            if resp.status_code == 401:
                logger.warning("Qobuz auth token expired")
                self.user_auth_token = None
                return None
            elif resp.status_code == 429:
                retry_after = float(resp.headers.get('Retry-After', 60))
                _qobuz_set_rate_limit(retry_after)
                return None
            elif resp.status_code != 200:
                logger.warning(f"Qobuz API error: {endpoint} returned HTTP {resp.status_code}")
                return None

            return resp.json()

        except Exception as e:
            logger.error(f"Qobuz API request failed ({endpoint}): {e}")
            return None

    # ── Enrichment API Methods ──

    def search_artist(self, name: str):
        """Search for an artist by name. Returns first result as raw dict or None."""
        try:
            data = self._api_request('artist/search', {
                'query': name,
                'limit': 1,
            })
            if data and 'artists' in data:
                items = data['artists'].get('items', [])
                if items:
                    return items[0]
            return None
        except Exception as e:
            logger.error(f"Error searching Qobuz artist: {e}")
            return None

    def search_album(self, artist: str, title: str):
        """Search for an album by artist + title. Returns first result as raw dict or None."""
        try:
            query = f"{artist} {title}" if artist else title
            data = self._api_request('album/search', {
                'query': query,
                'limit': 1,
            })
            if data and 'albums' in data:
                items = data['albums'].get('items', [])
                if items:
                    return items[0]
            return None
        except Exception as e:
            logger.error(f"Error searching Qobuz album: {e}")
            return None

    def search_track(self, artist: str, title: str):
        """Search for a track by artist + title. Returns first result as raw dict or None."""
        try:
            query = f"{artist} {title}" if artist else title
            data = self._api_request('track/search', {
                'query': query,
                'limit': 1,
            })
            if data and 'tracks' in data:
                items = data['tracks'].get('items', [])
                if items:
                    return items[0]
            return None
        except Exception as e:
            logger.error(f"Error searching Qobuz track: {e}")
            return None

    def get_artist(self, artist_id):
        """Get full artist details by Qobuz ID."""
        try:
            data = self._api_request('artist/get', {
                'artist_id': artist_id,
                'extra': 'albums',
            })
            return data
        except Exception as e:
            logger.error(f"Error getting Qobuz artist {artist_id}: {e}")
            return None

    def get_album(self, album_id):
        """Get full album details by Qobuz ID."""
        try:
            data = self._api_request('album/get', {
                'album_id': album_id,
                'extra': 'tracks',
            })
            return data
        except Exception as e:
            logger.error(f"Error getting Qobuz album {album_id}: {e}")
            return None

    def get_track(self, track_id):
        """Get full track details by Qobuz ID."""
        try:
            data = self._api_request('track/get', {
                'track_id': track_id,
            })
            return data
        except Exception as e:
            logger.error(f"Error getting Qobuz track {track_id}: {e}")
            return None

    async def search(self, query: str, timeout: int = None, progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """
        Search Qobuz for tracks (async, Soulseek-compatible interface).

        Returns:
            Tuple of (track_results, album_results). Album results always empty.
        """
        if not self.is_available():
            logger.warning("Qobuz not available for search (not authenticated)")
            return ([], [])

        logger.info(f"Searching Qobuz for: {query}")

        try:
            loop = asyncio.get_event_loop()

            def _search():
                return self._api_request('track/search', {
                    'query': query,
                    'limit': 50,
                })

            data = await loop.run_in_executor(None, _search)

            if not data or 'tracks' not in data:
                logger.warning(f"No Qobuz results for: {query}")
                return ([], [])

            tracks_data = data['tracks'].get('items', [])
            if not tracks_data:
                return ([], [])

            # Get configured quality for display
            quality_key = config_manager.get('qobuz.quality', 'lossless')
            quality_info = QOBUZ_QUALITY_MAP.get(quality_key, QOBUZ_QUALITY_MAP['lossless'])

            track_results = []
            for track in tracks_data:
                try:
                    track_result = self._qobuz_to_track_result(track, quality_info)
                    if track_result:
                        track_results.append(track_result)
                except Exception as e:
                    logger.debug(f"Skipping track conversion error: {e}")

            logger.info(f"Found {len(track_results)} Qobuz tracks")
            return (track_results, [])

        except Exception as e:
            logger.error(f"Qobuz search failed: {e}")
            import traceback
            traceback.print_exc()
            return ([], [])

    def _qobuz_to_track_result(self, track: Dict, quality_info: dict) -> Optional[TrackResult]:
        """Convert Qobuz track dict to TrackResult (Soulseek-compatible format)."""
        track_id = track.get('id')
        if not track_id:
            return None

        # Check if track is streamable
        if not track.get('streamable', False):
            return None

        performer = track.get('performer', {})
        artist_name = performer.get('name', 'Unknown Artist') if isinstance(performer, dict) else str(performer)
        title = track.get('title', 'Unknown Title')

        # Clean up title — Qobuz sometimes appends version info
        version = track.get('version')
        if version and version not in title:
            title = f"{title} ({version})"

        album_data = track.get('album', {})
        album_name = album_data.get('title', None) if isinstance(album_data, dict) else None

        # Duration in milliseconds
        duration_s = track.get('duration')
        duration_ms = int(duration_s * 1000) if duration_s else None

        # Determine actual max quality available for this track
        hires_streamable = track.get('hires_streamable', False)
        max_bit_depth = album_data.get('maximum_bit_depth', 16) if isinstance(album_data, dict) else 16
        max_sample_rate = album_data.get('maximum_sampling_rate', 44.1) if isinstance(album_data, dict) else 44.1

        # Build quality display string
        if hires_streamable and max_bit_depth >= 24:
            actual_quality = f"FLAC {max_bit_depth}-bit/{max_sample_rate}kHz"
            actual_bitrate = quality_info.get('bitrate', 1411)
        else:
            actual_quality = quality_info.get('codec', 'flac')
            actual_bitrate = quality_info.get('bitrate', 1411)

        # Encode track_id in filename (same pattern as YouTube/Tidal: "id||display_name")
        display_name = f"{artist_name} - {title}"
        filename = f"{track_id}||{display_name}"

        # Album cover URL
        album_image = None
        if isinstance(album_data, dict) and album_data.get('image'):
            album_image = album_data['image'].get('large', album_data['image'].get('small'))

        track_result = TrackResult(
            username='qobuz',
            filename=filename,
            size=0,  # Unknown until download
            bitrate=actual_bitrate,
            duration=duration_ms,
            quality=actual_quality,
            free_upload_slots=999,
            upload_speed=999999,
            queue_length=0,
            artist=artist_name,
            title=title,
            album=album_name,
            track_number=track.get('track_number'),
        )

        return track_result

    # ===================== Download =====================

    def _get_stream_url(self, track_id, format_id: int) -> Optional[Dict]:
        """
        Get a signed stream URL for a Qobuz track.

        Returns dict with 'url', 'format_id', 'mime_type', etc. or None.
        """
        if not self.app_secret:
            logger.error("No app_secret available for stream URL signing")
            return None

        if _qobuz_is_rate_limited():
            logger.debug("Qobuz rate limited, skipping stream URL request")
            return None

        _qobuz_throttle()

        ts = str(int(time.time()))
        sig_raw = f"trackgetFileUrlformat_id{format_id}intentstreamtrack_id{track_id}{ts}{self.app_secret}"
        sig = hashlib.md5(sig_raw.encode()).hexdigest()

        try:
            resp = self.session.get(
                QOBUZ_API_BASE + 'track/getFileUrl',
                params={
                    'track_id': str(track_id),
                    'format_id': format_id,
                    'intent': 'stream',
                    'request_ts': ts,
                    'request_sig': sig,
                },
                timeout=15,
            )

            if resp.status_code == 401:
                logger.warning("Qobuz stream URL auth failed — token may be expired")
                return None
            elif resp.status_code == 429:
                retry_after = float(resp.headers.get('Retry-After', 60))
                _qobuz_set_rate_limit(retry_after)
                return None
            elif resp.status_code == 400:
                data = resp.json() if resp.text else {}
                logger.warning(f"Qobuz stream URL rejected: {data.get('message', 'unknown error')}")
                return None
            elif resp.status_code != 200:
                logger.warning(f"Qobuz stream URL failed: HTTP {resp.status_code}")
                return None

            data = resp.json()
            if 'url' not in data:
                logger.warning("No URL in Qobuz stream response")
                return None

            return data

        except Exception as e:
            logger.error(f"Failed to get Qobuz stream URL: {e}")
            return None

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """
        Download a Qobuz track (async, Soulseek-compatible interface).

        Returns download_id immediately and runs download in background thread.

        Args:
            username: Ignored for Qobuz (always "qobuz")
            filename: Encoded as "track_id||display_name"
            file_size: Ignored
        """
        try:
            if '||' not in filename:
                logger.error(f"Invalid filename format: {filename}")
                return None

            track_id_str, display_name = filename.split('||', 1)
            try:
                track_id = int(track_id_str)
            except ValueError:
                logger.error(f"Invalid Qobuz track ID: {track_id_str}")
                return None

            logger.info(f"Starting Qobuz download: {display_name}")

            download_id = str(uuid.uuid4())

            with self._download_lock:
                self.active_downloads[download_id] = {
                    'id': download_id,
                    'filename': filename,
                    'username': 'qobuz',
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

            # Start download in background thread
            download_thread = threading.Thread(
                target=self._download_thread_worker,
                args=(download_id, track_id, display_name, filename),
                daemon=True,
            )
            download_thread.start()

            logger.info(f"Qobuz download {download_id} started in background")
            return download_id

        except Exception as e:
            logger.error(f"Failed to start Qobuz download: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _download_thread_worker(self, download_id: str, track_id: int, display_name: str, original_filename: str):
        """Background thread worker for downloading Qobuz tracks."""
        try:
            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['state'] = 'InProgress, Downloading'

            file_path = self._download_sync(download_id, track_id, display_name)

            if file_path:
                with self._download_lock:
                    if download_id in self.active_downloads:
                        self.active_downloads[download_id]['state'] = 'Completed, Succeeded'
                        self.active_downloads[download_id]['progress'] = 100.0
                        self.active_downloads[download_id]['file_path'] = file_path

                logger.info(f"Qobuz download {download_id} completed: {file_path}")
            else:
                with self._download_lock:
                    if download_id in self.active_downloads:
                        self.active_downloads[download_id]['state'] = 'Errored'

                logger.error(f"Qobuz download {download_id} failed")

        except Exception as e:
            logger.error(f"Qobuz download thread failed for {download_id}: {e}")
            import traceback
            traceback.print_exc()

            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['state'] = 'Errored'

    def _download_sync(self, download_id: str, track_id: int, display_name: str) -> Optional[str]:
        """
        Synchronous download method (runs in background thread).

        Returns file path if successful, None otherwise.
        """
        if not self.is_authenticated():
            logger.error("Qobuz not authenticated")
            return None

        try:
            # Determine quality
            quality_key = config_manager.get('qobuz.quality', 'lossless')
            quality_info = QOBUZ_QUALITY_MAP.get(quality_key, QOBUZ_QUALITY_MAP['lossless'])

            # Quality fallback chain: hires_max → hires → lossless → mp3
            quality_chain = ['hires_max', 'hires', 'lossless', 'mp3']
            start_idx = quality_chain.index(quality_key) if quality_key in quality_chain else 2
            allow_fallback = config_manager.get('qobuz.allow_fallback', True)
            chain = quality_chain[start_idx:] if allow_fallback else [quality_key]

            stream_data = None
            actual_quality = None
            for q_key in chain:
                q_info = QOBUZ_QUALITY_MAP[q_key]
                stream_data = self._get_stream_url(track_id, q_info['format_id'])
                if stream_data and 'url' in stream_data:
                    actual_quality = q_info
                    logger.info(f"Got Qobuz stream at quality: {q_key} ({q_info['label']})")
                    break
                else:
                    logger.debug(f"Quality {q_key} unavailable, trying next")

            if not stream_data or 'url' not in stream_data:
                logger.error("No Qobuz stream available at any quality")
                return None

            # Qobuz returns sample=True for 30-second previews (no subscription or region-restricted)
            if stream_data.get('sample', False):
                logger.warning(f"Qobuz returned a 30s sample for '{display_name}' — "
                               f"track may require a Qobuz subscription or is region-restricted. Skipping.")
                return None

            download_url = stream_data['url']

            # Determine file extension from stream response
            mime_type = stream_data.get('mime_type', '')
            if 'flac' in mime_type.lower():
                extension = 'flac'
            elif 'mpeg' in mime_type.lower() or 'mp3' in mime_type.lower():
                extension = 'mp3'
            else:
                extension = actual_quality.get('extension', 'flac') if actual_quality else 'flac'

            # Build output filename
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', display_name)
            out_filename = f"{safe_name}.{extension}"
            out_path = self.download_path / out_filename

            # Check for shutdown
            if self.shutdown_check and self.shutdown_check():
                logger.info("Server shutting down, aborting Qobuz download")
                return None

            # Download with progress tracking
            logger.info(f"Downloading from Qobuz: {out_filename}")
            response = http_requests.get(download_url, stream=True, timeout=120)
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            chunk_size = 64 * 1024  # 64KB chunks
            start_time = time.time()

            with self._download_lock:
                if download_id in self.active_downloads:
                    self.active_downloads[download_id]['size'] = total_size

            with open(out_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue

                    # Check for shutdown or cancellation
                    cancelled = False
                    with self._download_lock:
                        if download_id in self.active_downloads:
                            cancelled = self.active_downloads[download_id].get('state') == 'Cancelled'
                    if cancelled or (self.shutdown_check and self.shutdown_check()):
                        reason = "cancelled" if cancelled else "server shutting down"
                        logger.info(f"Aborting Qobuz download mid-stream: {reason}")
                        break

                    f.write(chunk)
                    downloaded += len(chunk)

                    # Calculate progress and speed
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0

                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        remaining_bytes = total_size - downloaded
                        time_remaining = remaining_bytes / speed if speed > 0 else None
                    else:
                        progress = 0
                        time_remaining = None

                    with self._download_lock:
                        if download_id in self.active_downloads:
                            self.active_downloads[download_id]['transferred'] = downloaded
                            self.active_downloads[download_id]['progress'] = round(progress, 1)
                            self.active_downloads[download_id]['speed'] = int(speed)
                            self.active_downloads[download_id]['time_remaining'] = time_remaining

            # If download was aborted (shutdown/cancel), clean up partial file
            abort_check = False
            with self._download_lock:
                if download_id in self.active_downloads:
                    abort_check = self.active_downloads[download_id].get('state') == 'Cancelled'
            if abort_check or (self.shutdown_check and self.shutdown_check()):
                out_path.unlink(missing_ok=True)
                return None

            # Validate file size (Qobuz streams are DRM-free so this is mainly for network errors)
            MIN_AUDIO_SIZE = 100 * 1024  # 100KB
            if downloaded < MIN_AUDIO_SIZE:
                logger.error(
                    f"Qobuz download too small ({downloaded} bytes) — likely an error. "
                    f"Expected audio file for '{display_name}'. Deleting."
                )
                out_path.unlink(missing_ok=True)
                return None

            # Safety net: detect 30-second samples by checking actual file duration.
            # Qobuz previews are valid audio files (~2-5MB) that pass the size check above.
            try:
                from mutagen import File as MutagenFile
                audio = MutagenFile(str(out_path))
                if audio and audio.info and audio.info.length:
                    duration_s = audio.info.length
                    if duration_s < 35:
                        logger.warning(
                            f"Qobuz download is only {duration_s:.0f}s — likely a 30s sample/preview "
                            f"for '{display_name}'. Deleting."
                        )
                        out_path.unlink(missing_ok=True)
                        return None
            except Exception as e:
                logger.debug(f"Could not check audio duration (non-fatal): {e}")

            final_size = out_path.stat().st_size if out_path.exists() else 0
            logger.info(f"Qobuz download complete: {out_path} ({final_size / (1024*1024):.1f} MB)")
            return str(out_path)

        except Exception as e:
            logger.error(f"Qobuz download failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ===================== Status / Cancel / Clear =====================

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """Get all active downloads (matches Soulseek interface)."""
        download_statuses = []

        with self._download_lock:
            for download_id, info in self.active_downloads.items():
                status = DownloadStatus(
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
                download_statuses.append(status)

        return download_statuses

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """Get status of a specific download (matches Soulseek interface)."""
        with self._download_lock:
            if download_id not in self.active_downloads:
                return None

            info = self.active_downloads[download_id]
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

    async def cancel_download(self, download_id: str, username: str = None, remove: bool = False) -> bool:
        """Cancel an active download (matches Soulseek interface)."""
        try:
            with self._download_lock:
                if download_id not in self.active_downloads:
                    logger.warning(f"Download {download_id} not found")
                    return False

                self.active_downloads[download_id]['state'] = 'Cancelled'
                logger.info(f"Marked Qobuz download {download_id} as cancelled")

                if remove:
                    del self.active_downloads[download_id]
                    logger.info(f"Removed Qobuz download {download_id} from queue")

            return True
        except Exception as e:
            logger.error(f"Failed to cancel download {download_id}: {e}")
            return False

    async def clear_all_completed_downloads(self) -> bool:
        """Clear all terminal downloads from the list (matches Soulseek interface)."""
        try:
            with self._download_lock:
                ids_to_remove = [
                    did for did, info in self.active_downloads.items()
                    if info.get('state', '') in ('Completed, Succeeded', 'Cancelled', 'Errored', 'Aborted')
                ]
                for did in ids_to_remove:
                    del self.active_downloads[did]

            return True
        except Exception as e:
            logger.error(f"Error clearing downloads: {e}")
            return False
