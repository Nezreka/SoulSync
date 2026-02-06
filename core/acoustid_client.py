"""
AcoustID Client for audio fingerprinting and lookup.

Uses the pyacoustid library which handles:
- Fingerprint generation via chromaprint library
- AcoustID API lookups
- Rate limiting

The fpcalc binary is auto-downloaded if not found (Windows, macOS, Linux x86_64).
"""

import threading
import sys
import platform
import zipfile
import tarfile
import tempfile
import urllib.request
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
import os
import shutil
import logging.handlers

from utils.logging_config import get_logger
from config.settings import config_manager

# fpcalc binary location (downloaded automatically if needed)
FPCALC_BIN_DIR = Path(__file__).parent.parent / "bin"
CHROMAPRINT_VERSION = "1.5.1"

# Set up dedicated AcoustID logger with its own file
logger = get_logger("acoustid_client")

# Add dedicated file handler for AcoustID logs
_acoustid_log_path = Path(__file__).parent.parent / "logs" / "acoustid.log"
_acoustid_log_path.parent.mkdir(parents=True, exist_ok=True)
_acoustid_file_handler = logging.handlers.RotatingFileHandler(
    _acoustid_log_path, encoding='utf-8', maxBytes=5*1024*1024, backupCount=2
)
_acoustid_file_handler.setLevel(logging.DEBUG)
_acoustid_file_handler.setFormatter(logging.Formatter(
    fmt='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
logger.addHandler(_acoustid_file_handler)
logging.getLogger("newmusic.acoustid_verification").addHandler(_acoustid_file_handler)

# Check if pyacoustid is available
try:
    import acoustid
    ACOUSTID_AVAILABLE = True
    logger.info("pyacoustid library loaded successfully")
except ImportError:
    ACOUSTID_AVAILABLE = False
    logger.warning("pyacoustid library not installed - run: pip install pyacoustid")

def _get_fpcalc_download_url() -> Optional[str]:
    """Get the download URL for fpcalc based on current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Map architecture names
    if machine in ('x86_64', 'amd64'):
        arch = 'x86_64'
    elif machine in ('i386', 'i686', 'x86'):
        arch = 'i686'
    elif machine in ('arm64', 'aarch64'):
        arch = 'aarch64'
    else:
        logger.warning(f"Unknown architecture: {machine}")
        return None

    base_url = f"https://github.com/acoustid/chromaprint/releases/download/v{CHROMAPRINT_VERSION}"

    if system == 'windows':
        if arch == 'x86_64':
            return f"{base_url}/chromaprint-fpcalc-{CHROMAPRINT_VERSION}-windows-x86_64.zip"
    elif system == 'darwin':
        # Universal build supports both Intel and Apple Silicon natively
        return f"{base_url}/chromaprint-fpcalc-{CHROMAPRINT_VERSION}-macos-universal.tar.gz"
    elif system == 'linux':
        if arch == 'x86_64':
            return f"{base_url}/chromaprint-fpcalc-{CHROMAPRINT_VERSION}-linux-x86_64.tar.gz"

    logger.warning(f"No fpcalc download available for {system}-{arch}")
    return None


def _download_fpcalc() -> Optional[str]:
    """
    Download and extract fpcalc binary for the current platform.

    Returns:
        Path to fpcalc binary if successful, None otherwise.
    """
    url = _get_fpcalc_download_url()
    if not url:
        return None

    try:
        logger.info(f"Downloading fpcalc from: {url}")

        # Create bin directory
        FPCALC_BIN_DIR.mkdir(parents=True, exist_ok=True)

        # Download to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(url).suffix) as tmp:
            tmp_path = tmp.name
            urllib.request.urlretrieve(url, tmp_path)

        # Extract based on file type
        fpcalc_name = "fpcalc.exe" if platform.system().lower() == 'windows' else "fpcalc"
        fpcalc_dest = FPCALC_BIN_DIR / fpcalc_name

        if url.endswith('.zip'):
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                # Find fpcalc in the archive
                for name in zf.namelist():
                    if name.endswith(fpcalc_name):
                        # Extract to bin directory
                        with zf.open(name) as src, open(fpcalc_dest, 'wb') as dst:
                            dst.write(src.read())
                        break
        elif url.endswith('.tar.gz'):
            with tarfile.open(tmp_path, 'r:gz') as tf:
                for member in tf.getmembers():
                    if member.name.endswith('fpcalc'):
                        # Extract to bin directory
                        member.name = fpcalc_name
                        tf.extract(member, FPCALC_BIN_DIR)
                        break

        # Clean up temp file
        os.unlink(tmp_path)

        # Make executable on Unix
        if platform.system().lower() != 'windows':
            os.chmod(fpcalc_dest, 0o755)

        if fpcalc_dest.exists():
            logger.info(f"fpcalc downloaded successfully: {fpcalc_dest}")
            return str(fpcalc_dest)
        else:
            logger.error("fpcalc not found in downloaded archive")
            return None

    except Exception as e:
        logger.error(f"Failed to download fpcalc: {e}")
        return None


def _find_fpcalc() -> Optional[str]:
    """Find fpcalc binary, downloading if necessary."""
    # Check PATH first
    fpcalc = shutil.which("fpcalc") or shutil.which("fpcalc.exe")
    if fpcalc:
        return fpcalc

    # Check our bin directory
    fpcalc_name = "fpcalc.exe" if platform.system().lower() == 'windows' else "fpcalc"
    local_fpcalc = FPCALC_BIN_DIR / fpcalc_name
    if local_fpcalc.exists():
        return str(local_fpcalc)

    # Try to download
    return _download_fpcalc()


# Check if chromaprint/fpcalc is available for fingerprinting
CHROMAPRINT_AVAILABLE = False
FPCALC_PATH = None

if ACOUSTID_AVAILABLE:
    # Try to find or download fpcalc
    FPCALC_PATH = _find_fpcalc()
    if FPCALC_PATH:
        CHROMAPRINT_AVAILABLE = True
        logger.info(f"fpcalc binary ready: {FPCALC_PATH}")
        # Set environment variable so pyacoustid can find it
        os.environ['FPCALC'] = FPCALC_PATH
    else:
        logger.warning("fpcalc not available - fingerprinting will not work")


class AcoustIDClient:
    """
    Client for audio fingerprinting via pyacoustid.

    Usage:
        client = AcoustIDClient()
        available, reason = client.is_available()
        if available:
            result = client.fingerprint_and_lookup("/path/to/audio.mp3")
            if result:
                for mbid in result['recording_mbids']:
                    print(f"Match: {mbid}")
    """

    def __init__(self):
        """Initialize AcoustID client with settings from config."""
        self._api_key = None
        self._enabled = None

    @property
    def api_key(self) -> str:
        """Get API key from config (cached)."""
        if self._api_key is None:
            self._api_key = config_manager.get('acoustid.api_key', '')
        return self._api_key

    @property
    def enabled(self) -> bool:
        """Check if AcoustID verification is enabled in config."""
        if self._enabled is None:
            self._enabled = config_manager.get('acoustid.enabled', False)
        return self._enabled

    def is_available(self) -> Tuple[bool, str]:
        """
        Check if AcoustID verification is available and ready.

        Returns:
            Tuple of (is_available, reason_message)
        """
        if not ACOUSTID_AVAILABLE:
            return False, "pyacoustid library not installed"

        if not self.api_key:
            return False, "No AcoustID API key configured"

        if not self.enabled:
            return False, "AcoustID verification is disabled"

        # Check if chromaprint or fpcalc is available
        if not self._check_fingerprint_available():
            return False, "Chromaprint library not installed (install libchromaprint1)"

        return True, "AcoustID verification ready"

    def _check_fingerprint_available(self) -> bool:
        """Check if we can generate fingerprints (chromaprint lib or fpcalc)."""
        global CHROMAPRINT_AVAILABLE, FPCALC_PATH

        if CHROMAPRINT_AVAILABLE:
            return True

        # Try to find/download fpcalc if not already available
        FPCALC_PATH = _find_fpcalc()
        if FPCALC_PATH:
            CHROMAPRINT_AVAILABLE = True
            os.environ['FPCALC'] = FPCALC_PATH
            logger.info(f"fpcalc now available: {FPCALC_PATH}")
            return True

        return False

    def _find_test_audio_file(self) -> Optional[str]:
        """Find an audio file to use for testing the AcoustID API key."""
        audio_extensions = {'.mp3', '.flac', '.ogg', '.m4a', '.wav', '.wma', '.aac'}
        search_dirs = []

        # Check transfer and download paths from config
        transfer_path = config_manager.get('soulseek.transfer_path', '')
        download_path = config_manager.get('soulseek.download_path', '')
        if transfer_path:
            search_dirs.append(Path(transfer_path))
        if download_path:
            search_dirs.append(Path(download_path))

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            # Walk up to 2 levels deep to find an audio file quickly
            for depth, pattern in enumerate(['*', '*/*']):
                for f in search_dir.glob(pattern):
                    if f.is_file() and f.suffix.lower() in audio_extensions:
                        return str(f)
        return None

    def test_api_key(self) -> Tuple[bool, str]:
        """
        Validate the API key by fingerprinting a real audio file and looking it up.
        Falls back to a direct API call if no audio files are available.

        Returns:
            Tuple of (success, message)
        """
        if not self.api_key:
            return False, "No API key configured"

        import requests

        try:
            # Try to find a real audio file to fingerprint for an end-to-end test
            test_file = self._find_test_audio_file()

            if test_file and CHROMAPRINT_AVAILABLE:
                logger.info(f"Testing API key with real audio file: {test_file}")
                try:
                    result = self.fingerprint_and_lookup(test_file)
                    # If we get here without exception, the API key is valid
                    # (invalid keys raise or return error before results)
                    return True, "AcoustID API key is valid"
                except Exception as e:
                    error_str = str(e).lower()
                    if 'invalid' in error_str and 'api' in error_str:
                        return False, "Invalid AcoustID API key - get one from https://acoustid.org/new-application"
                    # Fingerprint/lookup failed for non-key reasons, fall through to direct test
                    logger.warning(f"Real file test failed ({e}), trying direct API call")

            # Fallback: direct API call with minimal fingerprint
            url = 'https://api.acoustid.org/v2/lookup'
            params = {
                'client': self.api_key,
                'duration': 187,
                'fingerprint': 'AQADtMkWaYkSZRGO',
                'meta': 'recordings'
            }

            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if data.get('status') == 'error':
                error = data.get('error', {})
                error_code = error.get('code', 0)
                error_msg = error.get('message', 'Unknown error')

                # Error code 4 is specifically "invalid API key"
                if error_code == 4:
                    return False, "Invalid AcoustID API key - get one from https://acoustid.org/new-application"
                return False, f"AcoustID API error: {error_msg}"

            # Status is 'ok' - key is valid
            return True, "AcoustID API key is valid"

        except requests.exceptions.Timeout:
            return False, "AcoustID API timeout - try again later"
        except requests.exceptions.RequestException as e:
            return False, f"Network error: {str(e)}"
        except Exception as e:
            logger.error(f"Error testing AcoustID API key: {e}")
            return False, f"Error: {str(e)}"

    def fingerprint_and_lookup(self, audio_file: str) -> Optional[Dict[str, Any]]:
        """
        Generate fingerprint and look up recording in AcoustID.

        This is the main method - combines fingerprinting and lookup in one call.

        Args:
            audio_file: Path to the audio file

        Returns:
            Dict with:
                'recordings': list of dicts with 'mbid', 'title', 'artist', 'score'
                'best_score': float (highest score across all results)
                'recording_mbids': list of unique MBIDs (for backward compat)
            Or None on error.
        """
        if not ACOUSTID_AVAILABLE:
            logger.debug("Cannot lookup: pyacoustid not available")
            return None

        if not self.api_key:
            logger.debug("Cannot lookup: no API key")
            return None

        if not os.path.isfile(audio_file):
            logger.warning(f"Cannot lookup: file not found: {audio_file}")
            return None

        try:
            import acoustid

            api_key_preview = f"{self.api_key[:8]}..." if self.api_key and len(self.api_key) > 8 else "NOT SET"
            logger.info(f"Fingerprinting and looking up: {audio_file} (API key: {api_key_preview})")

            # Use match() which handles fingerprinting + lookup + parsing
            logger.debug("Running acoustid.match()...")
            recordings = []
            seen_mbids = set()
            best_score = 0.0

            for result in acoustid.match(
                self.api_key,
                audio_file,
                parse=True
            ):
                # match() with parse=True returns (score, recording_id, title, artist)
                if not isinstance(result, tuple) or len(result) < 2:
                    logger.warning(f"Unexpected result format: {result}")
                    continue

                score = result[0]
                recording_id = result[1]
                title = result[2] if len(result) > 2 else None
                artist = result[3] if len(result) > 3 else None

                logger.debug(f"Got result: score={score}, id={recording_id}, title={title}, artist={artist}")

                if score > best_score:
                    best_score = score

                if recording_id and recording_id not in seen_mbids:
                    seen_mbids.add(recording_id)
                    recordings.append({
                        'mbid': recording_id,
                        'title': title,
                        'artist': artist,
                        'score': score,
                    })
                    logger.info(f"Found match: {title} by {artist} (MBID: {recording_id}, score: {score})")

            if not recordings:
                logger.info(f"No AcoustID matches found for: {audio_file}")
                return None

            logger.info(f"AcoustID found {len(recordings)} recording(s) (best score: {best_score:.2f})")
            return {
                'recordings': recordings,
                'best_score': best_score,
                'recording_mbids': list(seen_mbids),
            }

        except acoustid.NoBackendError:
            logger.error("Chromaprint library not found and fpcalc not available")
            return None
        except acoustid.FingerprintGenerationError as e:
            logger.warning(f"Failed to fingerprint {audio_file}: {e}")
            return None
        except acoustid.WebServiceError as e:
            # Log more details about the API error
            api_key_preview = f"{self.api_key[:8]}..." if self.api_key and len(self.api_key) > 8 else "???"
            logger.warning(f"AcoustID API error (key: {api_key_preview}): {e}")
            # Check for common errors
            error_str = str(e).lower()
            if 'invalid' in error_str or 'unknown' in error_str:
                logger.error("API key appears to be invalid - check your AcoustID settings")
            elif 'rate' in error_str or 'limit' in error_str:
                logger.warning("Rate limited by AcoustID - will retry later")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in AcoustID lookup: {e}", exc_info=True)
            return None

    def refresh_config(self):
        """Refresh cached config values (call after settings change)."""
        self._api_key = None
        self._enabled = None
