"""Service connection test — lifted from web_server.py.

The function body is byte-identical to the original. soulseek_client,
qobuz_enrichment_worker, hydrabase_client, docker_resolve_url, and
docker_resolve_path are injected at runtime because they live in
web_server.py and are constructed there.
"""
import logging
import os

import requests

from config.settings import config_manager
from core.jellyfin_client import JellyfinClient
from core.metadata.registry import get_primary_source
from core.plex_client import PlexClient
from core.spotify_client import SpotifyClient
from core.tidal_client import TidalClient
from utils.async_helpers import run_async

logger = logging.getLogger(__name__)


def _get_metadata_fallback_source():
    """Mirror of web_server._get_metadata_fallback_source — delegates to registry."""
    return get_primary_source()


# Injected at runtime via init().
soulseek_client = None
qobuz_enrichment_worker = None
hydrabase_client = None
docker_resolve_url = None
docker_resolve_path = None


def init(
    soulseek_client_obj,
    qobuz_worker,
    hydrabase_client_obj,
    docker_resolve_url_fn,
    docker_resolve_path_fn,
):
    """Bind web_server-side helpers/globals so the lifted body can resolve them."""
    global soulseek_client, qobuz_enrichment_worker, hydrabase_client
    global docker_resolve_url, docker_resolve_path
    soulseek_client = soulseek_client_obj
    qobuz_enrichment_worker = qobuz_worker
    hydrabase_client = hydrabase_client_obj
    docker_resolve_url = docker_resolve_url_fn
    docker_resolve_path = docker_resolve_path_fn


def run_service_test(service, test_config):
    """
    Performs the actual connection test for a given service.
    This logic is adapted from your ServiceTestThread.
    It temporarily modifies the config, runs the test, then restores the config.
    """
    original_config = {}
    try:
        # 1. Save original config for the specific service
        original_config = config_manager.get(service, {})

        # 2. Temporarily set the new config for the test (with Docker URL resolution)
        for key, value in test_config.items():
            # Apply Docker URL resolution for URL/URI fields
            if isinstance(value, str) and ('url' in key.lower() or 'uri' in key.lower()):
                value = docker_resolve_url(value)
            config_manager.set(f"{service}.{key}", value)

        # 3. Run the test with the temporary config
        if service == "spotify":
            temp_client = SpotifyClient()
            
            # Check if Spotify credentials are configured
            spotify_config = config_manager.get('spotify', {})
            spotify_configured = bool(spotify_config.get('client_id') and spotify_config.get('client_secret'))
            
            if temp_client.is_authenticated():
                 # Determine which source is active
                 if temp_client.is_spotify_authenticated():
                     return True, "Spotify connection successful!"
                 else:
                     # Using fallback metadata source
                     fb_src = _get_metadata_fallback_source()
                     fallback_name = 'Deezer' if fb_src == 'deezer' else 'Discogs' if fb_src == 'discogs' else 'iTunes'
                     if spotify_configured:
                         return True, f"{fallback_name} connection successful! (Spotify configured but not authenticated)"
                     else:
                         return True, f"{fallback_name} connection successful! (Spotify not configured)"
            else:
                 return False, "Music service authentication failed. Check credentials and complete OAuth flow in browser if prompted."
        elif service == "tidal":
            temp_client = TidalClient()
            if temp_client.is_authenticated():
                user_info = temp_client.get_user_info()
                username = user_info.get('display_name', 'Tidal User') if user_info else 'Tidal User'
                return True, f"Tidal connection successful! Connected as: {username}"
            else:
                return False, "Tidal authentication failed. Please use the 'Authenticate' button and complete the flow in your browser."
        elif service == "plex":
            temp_client = PlexClient()
            if temp_client.is_connected():
                return True, f"Successfully connected to Plex server: {temp_client.server.friendlyName}"
            else:
                return False, "Could not connect to Plex. Check URL and Token."
        elif service == "jellyfin":
            temp_client = JellyfinClient()
            if temp_client.is_connected():
                # FIX: Check if server_info exists before accessing it.
                server_name = "Unknown Server"
                if hasattr(temp_client, 'server_info') and temp_client.server_info:
                    server_name = temp_client.server_info.get('ServerName', 'Unknown Server')
                return True, f"Successfully connected to Jellyfin server: {server_name}"
            else:
                return False, "Could not connect to Jellyfin. Check URL and API Key."
        elif service == "navidrome":
            # Test Navidrome connection using Subsonic API
            base_url = test_config.get('base_url', '')
            username = test_config.get('username', '')
            password = test_config.get('password', '')

            if not all([base_url, username, password]):
                return False, "Missing Navidrome URL, username, or password."

            try:
                import hashlib
                import random
                import string

                # Generate salt and token for Subsonic API authentication
                salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
                token = hashlib.md5((password + salt).encode()).hexdigest()

                # Test ping endpoint
                url = f"{base_url.rstrip('/')}/rest/ping"
                response = requests.get(url, params={
                    'u': username,
                    't': token,
                    's': salt,
                    'v': '1.16.1',
                    'c': 'soulsync',
                    'f': 'json'
                }, timeout=5)

                if response.status_code == 200:
                    data = response.json()
                    if data.get('subsonic-response', {}).get('status') == 'ok':
                        server_version = data.get('subsonic-response', {}).get('version', 'Unknown')
                        return True, f"Successfully connected to Navidrome server (v{server_version})"
                    else:
                        error = data.get('subsonic-response', {}).get('error', {})
                        return False, f"Navidrome authentication failed: {error.get('message', 'Unknown error')}"
                else:
                    return False, f"Could not connect to Navidrome server (HTTP {response.status_code})"

            except Exception as e:
                return False, f"Navidrome connection error: {str(e)}"
        elif service == "soulsync":
            transfer_path = docker_resolve_path(config_manager.get('soulseek.transfer_path', './Transfer'))
            if os.path.isdir(transfer_path):
                # Quick check — count a few audio files to confirm it's a music folder
                audio_exts = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav'}
                count = 0
                found_enough = False
                for _root, _dirs, files in os.walk(transfer_path):
                    for f in files:
                        if os.path.splitext(f)[1].lower() in audio_exts:
                            count += 1
                            if count >= 10:
                                found_enough = True
                                break
                    if found_enough:
                        break
                return True, f"SoulSync standalone ready! Output folder: {transfer_path}" + (f" ({count}+ audio files)" if count > 0 else " (empty)")
            else:
                return False, f"Output folder not found: {transfer_path}"
        elif service == "soulseek":
            if soulseek_client is None:
                return False, "Download orchestrator failed to initialize. Check server logs for startup errors."

            # Test the orchestrator's configured download source (not just Soulseek)
            download_mode = config_manager.get('download_source.mode', 'hybrid')

            if run_async(soulseek_client.check_connection()):
                # Success message based on active mode
                mode_messages = {
                    'soulseek': "Successfully connected to Soulseek network via slskd.",
                    'youtube': "YouTube download source ready.",
                    'tidal': "Tidal download source ready.",
                    'qobuz': "Qobuz download source ready.",
                    'hifi': "HiFi download source ready.",
                    'hybrid': "Download sources ready (Hybrid mode)."
                }
                message = mode_messages.get(download_mode, "Download source connected.")
                return True, message
            else:
                # Failure message based on active mode
                mode_errors = {
                    'soulseek': "slskd is not connected to the Soulseek network. Check slskd status and credentials.",
                    'youtube': "YouTube download source not available.",
                    'tidal': "Tidal download source not available. Check authentication.",
                    'qobuz': "Qobuz download source not available. Check authentication.",
                    'hifi': "HiFi download source not available. Public API instances may be down.",
                    'hybrid': "Could not connect to download sources. Check configuration."
                }
                error = mode_errors.get(download_mode, "Download source connection failed.")
                return False, error
        elif service == "listenbrainz":
            token = test_config.get('token', '')

            if not token:
                return False, "Missing ListenBrainz user token."

            try:
                # Test ListenBrainz API by validating the token
                custom_base = test_config.get('base_url', '').rstrip('/')
                if custom_base:
                    if not custom_base.endswith('/1'):
                        custom_base += '/1'
                    lb_api_base = custom_base
                else:
                    lb_api_base = "https://api.listenbrainz.org/1"
                url = f"{lb_api_base}/validate-token"
                headers = {
                    'Authorization': f'Token {token}'
                }
                response = requests.get(url, headers=headers, timeout=5)

                if response.status_code == 200:
                    data = response.json()
                    if data.get('valid'):
                        username = data.get('user_name', 'Unknown')
                        return True, f"Successfully connected to ListenBrainz! Connected as: {username}"
                    else:
                        return False, "Invalid ListenBrainz token."
                elif response.status_code == 401:
                    return False, "Invalid ListenBrainz token (unauthorized)."
                else:
                    return False, f"Could not connect to ListenBrainz (HTTP {response.status_code})"

            except Exception as e:
                return False, f"ListenBrainz connection error: {str(e)}"
        elif service == "acoustid":
            api_key = test_config.get('api_key', '')

            if not api_key:
                return False, "Missing AcoustID API key."

            try:
                from core.acoustid_client import AcoustIDClient, CHROMAPRINT_AVAILABLE, ACOUSTID_AVAILABLE, FPCALC_PATH

                if not ACOUSTID_AVAILABLE:
                    return False, "pyacoustid library not installed. Run: pip install pyacoustid"

                client = AcoustIDClient()

                # Override the cached API key with the test config key
                client._api_key = api_key

                # Check chromaprint/fpcalc availability
                if CHROMAPRINT_AVAILABLE and FPCALC_PATH:
                    fingerprint_status = f"fpcalc ready: {FPCALC_PATH}"
                elif CHROMAPRINT_AVAILABLE:
                    fingerprint_status = "Fingerprint backend available"
                else:
                    fingerprint_status = "fpcalc not found (will auto-download on first use)"

                # Validate API key with test request
                success, message = client.test_api_key()
                if success:
                    return True, f"AcoustID API key is valid! {fingerprint_status}"
                else:
                    return False, f"{message}. {fingerprint_status}"
            except Exception as e:
                return False, f"AcoustID test error: {str(e)}"
        elif service == "lastfm":
            api_key = test_config.get('api_key', '')

            if not api_key:
                return False, "Missing Last.fm API key."

            try:
                from core.lastfm_client import LastFMClient
                client = LastFMClient(api_key=api_key)
                if client.validate_api_key():
                    return True, "Successfully connected to Last.fm!"
                else:
                    return False, "Invalid Last.fm API key."
            except Exception as e:
                return False, f"Last.fm connection error: {str(e)}"
        elif service == "genius":
            access_token = test_config.get('access_token', '')

            if not access_token:
                return False, "Missing Genius access token."

            try:
                from core.genius_client import GeniusClient
                client = GeniusClient(access_token=access_token)
                if client.validate_token():
                    return True, "Successfully connected to Genius!"
                else:
                    return False, "Invalid Genius access token."
            except Exception as e:
                return False, f"Genius connection error: {str(e)}"
        elif service == "lidarr" or service == "lidarr_download":
            url = config_manager.get('lidarr_download.url', '')
            api_key = config_manager.get('lidarr_download.api_key', '')
            if not url or not api_key:
                return False, "Lidarr URL and API key are required."
            try:
                import requests as _req
                resp = _req.get(f"{url.rstrip('/')}/api/v1/system/status",
                                headers={'X-Api-Key': api_key}, timeout=10)
                if resp.ok:
                    version = resp.json().get('version', '?')
                    return True, f"Connected to Lidarr v{version}"
                return False, f"Lidarr returned HTTP {resp.status_code}"
            except Exception as e:
                return False, f"Lidarr connection error: {str(e)}"
        elif service == "itunes":
            # Public API — just confirm we can reach it with a cheap search
            try:
                storefront = config_manager.get('itunes.storefront', 'US') or 'US'
                resp = requests.get(
                    'https://itunes.apple.com/search',
                    params={'term': 'beatles', 'limit': 1, 'country': storefront, 'media': 'music'},
                    timeout=5,
                )
                if resp.ok and resp.json().get('resultCount', 0) >= 0:
                    return True, f"iTunes Search API reachable (storefront: {storefront})"
                return False, f"iTunes returned HTTP {resp.status_code}"
            except Exception as e:
                return False, f"iTunes connection error: {str(e)}"
        elif service == "deezer":
            # Public API — anon search works without credentials
            try:
                resp = requests.get(
                    'https://api.deezer.com/search/artist',
                    params={'q': 'beatles', 'limit': 1},
                    timeout=5,
                )
                if resp.ok and isinstance(resp.json(), dict):
                    return True, "Deezer Public API reachable"
                return False, f"Deezer returned HTTP {resp.status_code}"
            except Exception as e:
                return False, f"Deezer connection error: {str(e)}"
        elif service == "discogs":
            token = test_config.get('token', '') or config_manager.get('discogs.token', '')
            if not token:
                return False, "Missing Discogs personal token."
            try:
                resp = requests.get(
                    'https://api.discogs.com/database/search',
                    params={'q': 'beatles', 'per_page': 1},
                    headers={'Authorization': f'Discogs token={token}', 'User-Agent': 'SoulSync/1.0'},
                    timeout=10,
                )
                if resp.ok:
                    return True, "Discogs API reachable with provided token"
                if resp.status_code == 401:
                    return False, "Discogs token rejected (HTTP 401)"
                return False, f"Discogs returned HTTP {resp.status_code}"
            except Exception as e:
                return False, f"Discogs connection error: {str(e)}"
        elif service == "qobuz":
            try:
                if qobuz_enrichment_worker and qobuz_enrichment_worker.client and qobuz_enrichment_worker.client.is_authenticated():
                    return True, "Qobuz client authenticated"
                return False, "Qobuz not authenticated. Provide email/password or user auth token."
            except Exception as e:
                return False, f"Qobuz connection error: {str(e)}"
        elif service == "hydrabase":
            try:
                if hydrabase_client and hydrabase_client.is_connected():
                    return True, "Hydrabase connected"
                return False, "Hydrabase not connected. Configure URL + API key and click Connect."
            except Exception as e:
                return False, f"Hydrabase connection error: {str(e)}"
        elif service == "soundcloud":
            # Anonymous SoundCloud has no auth, so "test" really means
            # "is yt-dlp installed and can it reach SoundCloud right now."
            # This mirrors the /api/soundcloud/status check.
            try:
                from core.soundcloud_client import SoundcloudClient
                sc = SoundcloudClient()
                if not sc.is_available():
                    return False, "SoundCloud unavailable — yt-dlp not installed."
                # Run a tiny live probe via asyncio so the dashboard test
                # gives a meaningful pass/fail.
                import asyncio
                reachable = asyncio.new_event_loop().run_until_complete(sc.check_connection())
                if reachable:
                    return True, "SoundCloud reachable (anonymous)"
                return False, "SoundCloud unreachable — search probe failed. Try again."
            except Exception as e:
                return False, f"SoundCloud connection error: {str(e)}"
        return False, "Unknown service."
    except AttributeError as e:
        # This specifically catches the error you reported for Jellyfin
        if "'JellyfinClient' object has no attribute 'server_info'" in str(e):
            return False, "Connection failed. Please check your Jellyfin URL and API Key."
        else:
            return False, f"An unexpected error occurred: {e}"
    except Exception as e:
        import traceback
        traceback.print_exc()
        return False, str(e)
    finally:
        # 4. CRITICAL: Restore the original config
        if original_config:
            for key, value in original_config.items():
                config_manager.set(f"{service}.{key}", value)
            logger.debug(f"Restored original config for '{service}' after test.")
