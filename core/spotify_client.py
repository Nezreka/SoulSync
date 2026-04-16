import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
from typing import Dict, List, Optional, Any
import re
import time
import threading
from functools import wraps
from dataclasses import dataclass
from utils.logging_config import get_logger
from config.settings import config_manager
from core.metadata_cache import get_metadata_cache

logger = get_logger("spotify_client")

def _upgrade_spotify_image_url(url: str) -> str:
    """Upgrade a Spotify CDN image URL to the highest available resolution.

    Album art URLs use the prefix 'ab67616d' and encode size as the 4-char hex
    segment following '0000' (e.g. 'b273' = 640x640, '1e02' = 300x300).
    Replacing it with '82c1' requests the original uploaded master (up to 2000px+).

    This only applies to album art (ab67616d prefix). Artist images use a different
    prefix (ab676161) and the 82c1 trick does not work for them — those are left
    as-is since Spotify already returns them sorted largest-first.
    """
    if not url or 'i.scdn.co' not in url:
        return url
    return re.sub(r'(/image/ab67616d)0000[0-9a-f]{4}', r'\g<1>000082c1', url)

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 0.35  # Default: 350ms between API calls (~171/min, under Spotify's ~180/min limit)

def _get_min_api_interval():
    """Get configurable API interval from settings, falling back to default."""
    try:
        from config.settings import config_manager
        val = config_manager.get('spotify.min_api_interval', None)
        if val is not None:
            return max(0.1, float(val))  # Floor at 100ms to prevent abuse
    except Exception:
        pass
    return MIN_API_INTERVAL

# Request queuing for burst handling
import queue
_request_queue = queue.Queue()
_queue_processor_running = False

# Global rate limit ban state — when Spotify returns a long Retry-After (>60s),
# we set this so ALL API calls are suppressed until the ban expires.
_rate_limit_lock = threading.Lock()
_rate_limit_until = 0       # Unix timestamp when the ban expires (0 = not banned)
_rate_limit_retry_after = 0  # Original Retry-After value in seconds
_rate_limit_endpoint = None  # Which function triggered the ban
_rate_limit_set_at = 0       # When the ban was set
_rate_limit_ban_ended_at = 0  # When the last ban expired naturally (for post-ban cooldown)
_rate_limit_hit_count = 0    # How many times we've been rate limited recently (for escalation)
_rate_limit_first_hit = 0    # Timestamp of the first hit in the current escalation window

# Threshold: if Retry-After exceeds this, activate global ban instead of sleeping
_LONG_RATE_LIMIT_THRESHOLD = 60  # seconds

# After a ban expires, wait this long before making any auth probe calls.
# This prevents the "immediate re-probe → re-ban" cycle where Spotify's server-side
# cooldown outlasts the Retry-After value they sent us.
_POST_BAN_COOLDOWN = 300  # 5 minutes

# Escalation: if we get rate limited again within this window, increase ban duration
_ESCALATION_WINDOW = 3600   # 1 hour — if re-limited within this, escalate
_ESCALATION_MAX = 14400     # 4 hours max ban
_BASE_UNKNOWN_BAN = 1800    # 30 min default when Retry-After header is missing
_BASE_MAX_RETRIES_BAN = 14400  # 4 hours default when spotipy exhausted all retries (severe rate limit)

class SpotifyRateLimitError(Exception):
    """Raised when Spotify API calls are blocked due to active global rate limit ban."""
    def __init__(self, retry_after, endpoint=None):
        self.retry_after = retry_after
        self.endpoint = endpoint
        super().__init__(f"Spotify rate limited for {retry_after}s (triggered by {endpoint})")


def _set_global_rate_limit(retry_after_seconds, endpoint_name, has_real_header=False):
    """Activate the global rate limit ban. Escalates duration on repeated hits."""
    global _rate_limit_until, _rate_limit_retry_after, _rate_limit_endpoint, _rate_limit_set_at
    global _rate_limit_hit_count, _rate_limit_first_hit
    with _rate_limit_lock:
        now = time.time()

        # Escalation: if we're hitting rate limits repeatedly, increase the ban
        if not has_real_header:
            # Only escalate when we don't have a real Retry-After (i.e., we're guessing)
            if now - _rate_limit_first_hit < _ESCALATION_WINDOW and _rate_limit_first_hit > 0:
                _rate_limit_hit_count += 1
            else:
                # New escalation window
                _rate_limit_hit_count = 1
                _rate_limit_first_hit = now

            if _rate_limit_hit_count > 1:
                # Double the ban for each repeated hit, up to max
                escalated = retry_after_seconds * (2 ** (_rate_limit_hit_count - 1))
                retry_after_seconds = min(escalated, _ESCALATION_MAX)
                logger.warning(
                    f"Rate limit escalation: hit #{_rate_limit_hit_count} within window, "
                    f"ban escalated to {retry_after_seconds}s"
                )

        new_until = now + retry_after_seconds
        # Only update if this extends the existing ban
        if new_until > _rate_limit_until:
            _rate_limit_until = new_until
            _rate_limit_retry_after = retry_after_seconds
            _rate_limit_endpoint = endpoint_name
            _rate_limit_set_at = now
            logger.warning(
                f"GLOBAL RATE LIMIT ACTIVATED: {retry_after_seconds}s ban "
                f"(expires {time.strftime('%H:%M:%S', time.localtime(new_until))}) "
                f"triggered by {endpoint_name}"
            )
            # Record event for debug diagnostics
            try:
                from core.api_call_tracker import api_call_tracker
                escalated = _rate_limit_hit_count > 1
                api_call_tracker.record_event(
                    'spotify', 'rate_limit_ban',
                    endpoint=endpoint_name,
                    duration=retry_after_seconds,
                    detail=f'{"escalation #" + str(_rate_limit_hit_count) if escalated else "initial"}'
                         f'{", real Retry-After" if has_real_header else ", estimated"}'
                )
            except Exception:
                pass


def _is_globally_rate_limited():
    """Check if the global rate limit ban is active."""
    global _rate_limit_ban_ended_at
    with _rate_limit_lock:
        if _rate_limit_until <= 0:
            return False
        if time.time() >= _rate_limit_until:
            # Ban expired — record when it ended so post-ban cooldown can apply
            if _rate_limit_ban_ended_at < _rate_limit_until:
                _rate_limit_ban_ended_at = time.time()
                logger.info("Rate limit ban expired, entering post-ban cooldown period")
            return False
        return True


def _is_in_post_ban_cooldown():
    """Check if we're in the post-ban cooldown period.
    After a ban expires, we wait _POST_BAN_COOLDOWN seconds before allowing
    auth probes to prevent the re-probe → re-ban cycle."""
    with _rate_limit_lock:
        if _rate_limit_ban_ended_at <= 0:
            return False
        elapsed = time.time() - _rate_limit_ban_ended_at
        if elapsed < _POST_BAN_COOLDOWN:
            return True
        return False


def _get_post_ban_cooldown_remaining():
    """Get remaining seconds in post-ban cooldown, or 0 if not in cooldown."""
    with _rate_limit_lock:
        if _rate_limit_ban_ended_at <= 0:
            return 0
        remaining = _POST_BAN_COOLDOWN - (time.time() - _rate_limit_ban_ended_at)
        return max(0, int(remaining))


def _get_rate_limit_info():
    """Get current rate limit ban details. Returns None if not rate limited."""
    with _rate_limit_lock:
        if _rate_limit_until <= 0:
            return None
        now = time.time()
        remaining = _rate_limit_until - now
        if remaining <= 0:
            return None
        return {
            'active': True,
            'remaining_seconds': int(remaining),
            'retry_after': _rate_limit_retry_after,
            'endpoint': _rate_limit_endpoint,
            'set_at': _rate_limit_set_at,
            'expires_at': _rate_limit_until
        }


def _clear_rate_limit():
    """Manually clear the global rate limit ban AND post-ban cooldown.
    Used by disconnect/reconnect so the user can immediately retry."""
    global _rate_limit_until, _rate_limit_retry_after, _rate_limit_endpoint, _rate_limit_set_at, _rate_limit_ban_ended_at
    global _rate_limit_hit_count, _rate_limit_first_hit
    with _rate_limit_lock:
        _rate_limit_until = 0
        _rate_limit_retry_after = 0
        _rate_limit_endpoint = None
        _rate_limit_set_at = 0
        _rate_limit_ban_ended_at = 0
        _rate_limit_hit_count = 0
        _rate_limit_first_hit = 0
    logger.info("Global rate limit ban cleared (including post-ban cooldown)")


def _detect_and_set_rate_limit(exception, endpoint_name="unknown"):
    """Check if a Spotify exception is a 429 rate limit and activate global ban if so.
    Returns True if rate limit was detected."""
    error_str = str(exception)
    # Check both string matching and http_status attribute (SpotifyException has it)
    is_429 = getattr(exception, 'http_status', None) == 429
    is_rate_limit_str = "429" in error_str or "rate limit" in error_str.lower()

    if is_429 or is_rate_limit_str:
        # Try to extract Retry-After from exception headers
        retry_after = None
        has_real_header = False

        # Method 1: SpotifyException.headers (set by spotipy with retries=0)
        exc_headers = getattr(exception, 'headers', None)
        if exc_headers and hasattr(exc_headers, 'get'):
            retry_after = exc_headers.get('Retry-After') or exc_headers.get('retry-after')
            if retry_after:
                logger.info(f"Extracted Retry-After from exception headers: {retry_after}")
            else:
                logger.debug(f"Exception has headers but no Retry-After key. Headers type: {type(exc_headers).__name__}, keys: {list(exc_headers.keys())[:10] if hasattr(exc_headers, 'keys') else 'N/A'}")

        # Method 2: Parse from error message (some spotipy versions embed it)
        if not retry_after:
            import re
            ra_match = re.search(r'[Rr]etry[- ][Aa]fter[:\s]+(\d+)', error_str)
            if ra_match:
                retry_after = ra_match.group(1)
                logger.info(f"Extracted Retry-After from error message: {retry_after}")

        if retry_after:
            try:
                delay = int(retry_after)
                has_real_header = True
                logger.info(f"Rate limit detected on {endpoint_name} — Retry-After header: {delay}s")
            except (ValueError, TypeError):
                delay = _BASE_UNKNOWN_BAN
                logger.warning(f"Rate limit detected on {endpoint_name} — unparseable Retry-After: {retry_after}")
        else:
            # No Retry-After header available
            if "max retries" in error_str.lower():
                # Spotipy exhausted all retries on 429s — this is a severe ban.
                # Spotify's actual Retry-After is consumed internally by spotipy and not
                # passed in the exception. Use a long default to avoid re-triggering.
                delay = _BASE_MAX_RETRIES_BAN  # 4 hours
            else:
                delay = _BASE_UNKNOWN_BAN  # 30 min
            logger.warning(f"Rate limit detected on {endpoint_name} — no Retry-After header, using {delay}s default")

        _set_global_rate_limit(delay, endpoint_name, has_real_header=has_real_header)
        return True
    return False


def rate_limited(func):
    """Decorator to enforce rate limiting on Spotify API calls with retry and exponential backoff"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_api_call_time

        # Pre-flight check: if globally rate limited, don't even attempt the API call.
        # Let the method body run so its internal is_spotify_authenticated() check
        # returns False and iTunes fallback logic can execute.
        if _is_globally_rate_limited():
            return func(*args, **kwargs)

        max_retries = 5

        for attempt in range(max_retries + 1):
            # Re-check ban before each retry — a previous attempt may have triggered one
            if _is_globally_rate_limited():
                raise SpotifyRateLimitError(0, func.__name__)

            # Enforce minimum interval between API calls (configurable via settings)
            _interval = _get_min_api_interval()
            with _api_call_lock:
                current_time = time.time()
                time_since_last_call = current_time - _last_api_call_time

                if time_since_last_call < _interval:
                    sleep_time = _interval - time_since_last_call
                    time.sleep(sleep_time)

                _last_api_call_time = time.time()

            from core.api_call_tracker import api_call_tracker
            api_call_tracker.record_call('spotify', endpoint=func.__name__)

            try:
                return func(*args, **kwargs)
            except SpotifyRateLimitError:
                raise  # Don't retry our own ban errors
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "rate limit" in error_str or "429" in str(e)
                is_server_error = "502" in str(e) or "503" in str(e)

                if is_rate_limit:
                    # Try to extract Retry-After from spotipy exception headers
                    retry_after = None
                    if hasattr(e, 'headers') and e.headers:
                        retry_after = e.headers.get('Retry-After') or e.headers.get('retry-after')

                    if retry_after:
                        try:
                            delay = int(retry_after)
                        except (ValueError, TypeError):
                            delay = None

                        # If Retry-After is long, activate global ban instead of sleeping
                        if delay and delay > _LONG_RATE_LIMIT_THRESHOLD:
                            _set_global_rate_limit(delay, func.__name__, has_real_header=True)
                            raise SpotifyRateLimitError(delay, func.__name__)

                        if delay:
                            delay = delay + 1
                        else:
                            delay = 3.0 * (2 ** attempt)
                    else:
                        delay = 3.0 * (2 ** attempt)  # 3, 6, 12, 24, 48

                    if attempt < max_retries:
                        logger.warning(f"Spotify rate limit hit, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries}): {func.__name__}")
                        time.sleep(delay)
                        continue
                    else:
                        # All retries exhausted on 429s — activate global ban.
                        # Don't trust the Retry-After header here — we already retried
                        # with it multiple times and still got 429'd, so it's too short.
                        _set_global_rate_limit(_BASE_MAX_RETRIES_BAN, func.__name__)

                elif is_server_error and attempt < max_retries:
                    delay = 2.0 * (2 ** attempt)  # 2, 4, 8, 16, 32
                    logger.warning(f"Spotify server error, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries}): {func.__name__}")
                    time.sleep(delay)
                    continue

                raise
    return wrapper

@dataclass
class Track:
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    popularity: int
    preview_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    image_url: Optional[str] = None
    release_date: Optional[str] = None
    album_type: Optional[str] = None
    total_tracks: Optional[int] = None

    @classmethod
    def from_spotify_track(cls, track_data: Dict[str, Any]) -> 'Track':
        # Extract album image (largest available — Spotify returns images sorted largest first)
        album_image_url = None
        if 'album' in track_data and 'images' in track_data['album']:
            images = track_data['album']['images']
            if images:
                album_image_url = _upgrade_spotify_image_url(images[0]['url'])

        return cls(
            id=track_data['id'],
            name=track_data['name'],
            artists=[artist['name'] for artist in track_data['artists']],
            album=track_data['album']['name'],
            duration_ms=track_data['duration_ms'],
            popularity=track_data.get('popularity', 0),
            preview_url=track_data.get('preview_url'),
            external_urls=track_data.get('external_urls'),
            image_url=album_image_url,
            release_date=track_data.get('album', {}).get('release_date'),
            album_type=track_data.get('album', {}).get('album_type'),
            total_tracks=track_data.get('album', {}).get('total_tracks')
        )

@dataclass
class Artist:
    id: str
    name: str
    popularity: int
    genres: List[str]
    followers: int
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    
    @classmethod
    def from_spotify_artist(cls, artist_data: Dict[str, Any]) -> 'Artist':
        # Get the largest image URL if available
        image_url = None
        if artist_data.get('images') and len(artist_data['images']) > 0:
            image_url = _upgrade_spotify_image_url(artist_data['images'][0]['url'])
        
        return cls(
            id=artist_data['id'],
            name=artist_data['name'],
            popularity=artist_data.get('popularity', 0),
            genres=artist_data.get('genres', []),
            followers=artist_data.get('followers', {}).get('total', 0),
            image_url=image_url,
            external_urls=artist_data.get('external_urls')
        )

@dataclass
class Album:
    id: str
    name: str
    artists: List[str]
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    artist_ids: Optional[List[str]] = None

    @classmethod
    def from_spotify_album(cls, album_data: Dict[str, Any]) -> 'Album':
        # Get the largest image URL if available
        image_url = None
        if album_data.get('images') and len(album_data['images']) > 0:
            image_url = _upgrade_spotify_image_url(album_data['images'][0]['url'])

        return cls(
            id=album_data['id'],
            name=album_data['name'],
            artists=[artist['name'] for artist in album_data['artists']],
            release_date=album_data.get('release_date', ''),
            total_tracks=album_data.get('total_tracks', 0),
            album_type=album_data.get('album_type', 'album'),
            image_url=image_url,
            external_urls=album_data.get('external_urls'),
            artist_ids=[artist['id'] for artist in album_data['artists']]
        )

@dataclass
class Playlist:
    id: str
    name: str
    description: Optional[str]
    owner: str
    public: bool
    collaborative: bool
    tracks: List[Track]
    total_tracks: int
    
    @classmethod
    def from_spotify_playlist(cls, playlist_data: Dict[str, Any], tracks: List[Track]) -> 'Playlist':
        return cls(
            id=playlist_data['id'],
            name=playlist_data['name'],
            description=playlist_data.get('description'),
            owner=playlist_data['owner']['display_name'],
            public=playlist_data['public'],
            collaborative=playlist_data['collaborative'],
            tracks=tracks,
            total_tracks=(playlist_data.get('tracks') or playlist_data.get('items') or {}).get('total', 0)
        )

class SpotifyClient:
    def __init__(self):
        self.sp: Optional[spotipy.Spotify] = None
        self.user_id: Optional[str] = None
        self._itunes_client = None  # Lazy-loaded iTunes fallback
        self._deezer_client = None  # Lazy-loaded Deezer fallback
        self._discogs_client = None  # Lazy-loaded Discogs fallback
        self._auth_cache_lock = threading.Lock()
        self._auth_cached_result: Optional[bool] = None
        self._auth_cache_time: float = 0
        self._AUTH_CACHE_TTL = 900  # 15 minutes — auth status doesn't change mid-session
        self._setup_client()

    def _is_spotify_id(self, id_str: str) -> bool:
        """Check if an ID is a Spotify ID (alphanumeric) vs a fallback source ID (numeric only)"""
        if not id_str:
            return False
        # Spotify IDs contain letters and numbers; iTunes/Deezer IDs are purely numeric
        return not id_str.isdigit()

    def _is_itunes_id(self, id_str: str) -> bool:
        """Check if an ID is numeric (iTunes or Deezer format, not Spotify)"""
        if not id_str:
            return False
        return id_str.isdigit()

    @property
    def _itunes(self):
        """Lazy-load iTunes client"""
        if self._itunes_client is None:
            from core.itunes_client import iTunesClient
            self._itunes_client = iTunesClient()
            logger.info("iTunes fallback client initialized")
        return self._itunes_client

    @property
    def _deezer(self):
        """Lazy-load Deezer client for metadata fallback"""
        if self._deezer_client is None:
            from core.deezer_client import DeezerClient
            self._deezer_client = DeezerClient()
            logger.info("Deezer fallback client initialized")
        return self._deezer_client

    @property
    def _discogs(self):
        """Lazy-load Discogs client for metadata fallback"""
        if self._discogs_client is None:
            from core.discogs_client import DiscogsClient
            self._discogs_client = DiscogsClient()
            logger.info("Discogs fallback client initialized")
        return self._discogs_client

    @property
    def _fallback_source(self) -> str:
        """Get configured primary metadata source for internal fallback routing."""
        try:
            return config_manager.get('metadata.fallback_source', 'deezer') or 'deezer'
        except Exception:
            return 'deezer'

    @property
    def _fallback(self):
        """Get the active fallback metadata client based on settings"""
        if self._fallback_source == 'deezer':
            return self._deezer
        if self._fallback_source == 'discogs':
            # Only use Discogs if token is configured
            token = config_manager.get('discogs.token', '')
            if token:
                return self._discogs
            return self._itunes  # Fall back to iTunes if no Discogs token
        return self._itunes

    def reload_config(self):
        """Reload configuration and re-initialize client"""
        self._invalidate_auth_cache()
        self._setup_client()
    
    def _setup_client(self):
        config = config_manager.get_spotify_config()
        
        if not config.get('client_id') or not config.get('client_secret'):
            logger.warning("Spotify credentials not configured")
            return
        
        try:
            auth_manager = SpotifyOAuth(
                client_id=config['client_id'],
                client_secret=config['client_secret'],
                redirect_uri=config.get('redirect_uri', "http://127.0.0.1:8888/callback"),
                scope="user-library-read user-read-private playlist-read-private playlist-read-collaborative user-read-email user-follow-read",
                cache_path='config/.spotify_cache'
            )
            
            self.sp = spotipy.Spotify(auth_manager=auth_manager, retries=0, requests_timeout=15)
            # retries=0: prevent spotipy from sleeping for Retry-After duration on 429s
            # (can be hours). Our rate_limited decorator + global ban handle retries instead.
            # requests_timeout=15: prevent any single request from hanging indefinitely.
            # Don't fetch user info on startup - do it lazily to avoid blocking UI
            self.user_id = None
            logger.info("Spotify client initialized (user info will be fetched when needed)")
            
        except Exception as e:
            logger.error(f"Failed to authenticate with Spotify: {e}")
            self.sp = None
    
    def is_authenticated(self) -> bool:
        """
        Check if client can service metadata requests.
        Returns True if Spotify is authenticated OR fallback (iTunes/Deezer) is available.
        For Spotify-specific auth check, use is_spotify_authenticated().
        """
        # If Spotify is authenticated, we're good
        if self.is_spotify_authenticated():
            return True

        # Fallback (iTunes or Deezer) is always available — no auth required
        return True

    def _invalidate_auth_cache(self):
        """Clear the auth cache so the next check makes a fresh API call"""
        with self._auth_cache_lock:
            self._auth_cached_result = None
            self._auth_cache_time = 0

    def is_spotify_authenticated(self) -> bool:
        """Check if Spotify client is specifically authenticated (not just iTunes fallback).
        Results are cached for 60 seconds to avoid excessive API calls.
        During rate limit bans and post-ban cooldown, returns False without making API calls."""
        if self.sp is None:
            return False

        # If globally rate limited, report as NOT authenticated so callers
        # skip Spotify and fall through to iTunes fallback naturally.
        # This prevents any API calls that could extend the ban.
        if _is_globally_rate_limited():
            return False

        # Post-ban cooldown: after a ban expires, don't probe Spotify immediately.
        # Spotify's server-side cooldown can outlast the Retry-After they sent us,
        # so probing right away would just re-trigger the ban.
        if _is_in_post_ban_cooldown():
            remaining = _get_post_ban_cooldown_remaining()
            logger.debug(f"Post-ban cooldown active ({remaining}s left), skipping auth probe")
            return False

        # Check cache first (lock only for brief read)
        with self._auth_cache_lock:
            if self._auth_cached_result is not None and (time.time() - self._auth_cache_time) < self._AUTH_CACHE_TTL:
                return self._auth_cached_result

        # Cache miss — make API call outside the lock.
        # Use a dedicated probe client (retries=0) so a 429 here propagates
        # immediately and we can detect long Retry-After bans.
        try:
            probe = spotipy.Spotify(auth_manager=self.sp.auth_manager, retries=0)
            probe.current_user()
            result = True
        except Exception as e:
            error_str = str(e)
            # Rate limit means we ARE authenticated — just throttled
            if "rate" in error_str.lower() or "429" in error_str:
                # ANY rate limit on the auth probe means Spotify is actively throttling us.
                # Always activate a global ban — even with a short or missing Retry-After.
                # Without this, the probe→429→probe cycle repeats every ~60s forever.
                retry_after = None
                if hasattr(e, 'headers') and e.headers:
                    retry_after = e.headers.get('Retry-After') or e.headers.get('retry-after')
                has_real_header = False
                try:
                    delay = int(retry_after) if retry_after else 0
                    if retry_after:
                        has_real_header = True
                except (ValueError, TypeError):
                    delay = 0
                # Minimum 30 min for auth probe 429s — these indicate persistent throttling
                ban_duration = max(delay, _BASE_UNKNOWN_BAN)
                _set_global_rate_limit(ban_duration, 'is_spotify_authenticated', has_real_header=has_real_header)
                logger.warning(f"Auth probe rate limited — activating {ban_duration}s global ban")
                result = True
            else:
                logger.debug(f"Spotify authentication check failed: {e}")
                result = False

        with self._auth_cache_lock:
            self._auth_cached_result = result
            self._auth_cache_time = time.time()

        return result

    def disconnect(self):
        """Disconnect Spotify: clear client, delete cache, invalidate auth cache, clear rate limit"""
        import os
        self.sp = None
        self.user_id = None
        self._invalidate_auth_cache()
        _clear_rate_limit()

        cache_path = 'config/.spotify_cache'
        try:
            if os.path.exists(cache_path):
                os.remove(cache_path)
                logger.info("Deleted Spotify cache file")
        except Exception as e:
            logger.warning(f"Failed to delete Spotify cache: {e}")

        logger.info("Spotify client disconnected")

    @staticmethod
    def is_rate_limited():
        """Check if Spotify is globally rate limited."""
        return _is_globally_rate_limited()

    @staticmethod
    def get_rate_limit_info():
        """Get rate limit ban details. Returns None if not rate limited."""
        return _get_rate_limit_info()

    @staticmethod
    def clear_rate_limit():
        """Manually clear the rate limit ban."""
        _clear_rate_limit()

    @staticmethod
    def get_post_ban_cooldown_remaining():
        """Get remaining seconds in post-ban cooldown, or 0 if not in cooldown."""
        return _get_post_ban_cooldown_remaining()


    def _ensure_user_id(self) -> bool:
        """Ensure user_id is loaded (may make API call)"""
        if self.user_id is None and self.sp is not None:
            try:
                user_info = self.sp.current_user()
                self.user_id = user_info['id']
                logger.info(f"Successfully authenticated with Spotify as {user_info['display_name']}")
                return True
            except Exception as e:
                logger.error(f"Failed to fetch user info: {e}")
                return False
        return self.user_id is not None
    
    @rate_limited
    def get_user_playlists(self) -> List[Playlist]:
        if not self.is_spotify_authenticated():
            logger.error("Not authenticated with Spotify")
            return []
        
        if not self._ensure_user_id():
            logger.error("Failed to get user ID")
            return []
        
        playlists = []
        
        try:
            results = self.sp.current_user_playlists(limit=50)
            
            while results:
                for playlist_data in results['items']:
                    # Spotify API already returns all playlists the user has access to
                    # (owned + followed), so no need to filter
                    logger.info(f"Fetching tracks for playlist: {playlist_data['name']}")
                    tracks = self._get_playlist_tracks(playlist_data['id'])
                    playlist = Playlist.from_spotify_playlist(playlist_data, tracks)
                    playlists.append(playlist)
                
                if results['next']:
                    with _api_call_lock:
                        elapsed = time.time() - _last_api_call_time
                        _pi = _get_min_api_interval()
                        if elapsed < _pi:
                            time.sleep(_pi - elapsed)
                        globals()['_last_api_call_time'] = time.time()
                    from core.api_call_tracker import api_call_tracker
                    api_call_tracker.record_call('spotify', endpoint='get_user_playlists_page')
                    results = self.sp.next(results)
                else:
                    results = None

            logger.info(f"Retrieved {len(playlists)} playlists")
            return playlists
            
        except Exception as e:
            logger.error(f"Error fetching user playlists: {e}")
            return []
    
    @rate_limited
    def get_user_playlists_metadata_only(self) -> List[Playlist]:
        """Get playlists without fetching all track details for faster loading"""
        if not self.is_spotify_authenticated():
            logger.error("Not authenticated with Spotify")
            return []
        
        if not self._ensure_user_id():
            logger.error("Failed to get user ID")
            return []
        
        playlists = []
        
        try:
            # Fetch all playlists using pagination
            limit = 50  # Maximum allowed by Spotify API
            offset = 0
            total_fetched = 0
            
            logger.info("Beginning fetch of user playlists...")
            
            while True:
                results = self.sp.current_user_playlists(limit=limit, offset=offset)
                
                if not results or 'items' not in results:
                    break
                    
                # Log expected total on first page
                if offset == 0:
                    expected_total = results.get('total', 'Unknown')
                    logger.info(f"Spotify reports {expected_total} total playlists to fetch.")
                
                batch_count = 0
                for playlist_data in results['items']:
                    try:
                        # Spotify API already returns all playlists the user has access to
                        # (owned + followed), so no need to filter
                        
                        # Handle potential missing owner data safely
                        if not playlist_data.get('owner'):
                            playlist_data['owner'] = {'display_name': 'Unknown Owner', 'id': 'unknown'}
                        elif not playlist_data['owner'].get('display_name'):
                            playlist_data['owner']['display_name'] = 'Unknown'

                        # Create playlist with empty tracks list for now
                        playlist = Playlist.from_spotify_playlist(playlist_data, [])
                        playlists.append(playlist)
                        batch_count += 1
                        
                    except Exception as p_error:
                        p_name = playlist_data.get('name', 'Unknown') if playlist_data else 'None'
                        logger.warning(f"Skipping malformed playlist '{p_name}': {p_error}")
                
                total_fetched += batch_count
                logger.info(f"Retrieved {batch_count} playlists in batch (offset {offset}), total so far: {total_fetched}")
                
                # Check if we've fetched all playlists
                if len(results['items']) < limit or not results.get('next'):
                    break
                    
                offset += limit
            
            logger.info(f"Retrieved {len(playlists)} total playlist metadata")
            return playlists

        except Exception as e:
            logger.error(f"Error fetching user playlists metadata: {e}")
            # Return partial results if we crashed mid-way but have some data
            if playlists:
                 logger.info(f"Returning {len(playlists)} playlists fetched before error.")
                 return playlists
            return []

    @rate_limited
    def get_saved_tracks_count(self) -> int:
        """Get the total count of user's saved/liked songs without fetching all tracks"""
        if not self.is_spotify_authenticated():
            logger.error("Not authenticated with Spotify")
            return 0

        try:
            # Just fetch first page to get the total count
            results = self.sp.current_user_saved_tracks(limit=1)
            if results and 'total' in results:
                total_count = results['total']
                logger.info(f"User has {total_count} saved tracks")
                return total_count
            return 0
        except Exception as e:
            logger.error(f"Error fetching saved tracks count: {e}")
            return 0

    @rate_limited
    def get_saved_tracks(self) -> List[Track]:
        """Fetch all user's saved/liked songs from Spotify"""
        if not self.is_spotify_authenticated():
            logger.error("Not authenticated with Spotify")
            return []

        tracks = []

        try:
            limit = 50  # Maximum allowed by Spotify API
            offset = 0
            total_fetched = 0

            while True:
                results = self.sp.current_user_saved_tracks(limit=limit, offset=offset)

                if not results or 'items' not in results:
                    break

                batch_count = 0
                for item in results['items']:
                    if item['track'] and item['track']['id']:
                        track = Track.from_spotify_track(item['track'])
                        tracks.append(track)
                        batch_count += 1

                total_fetched += batch_count
                logger.info(f"Retrieved {batch_count} saved tracks in batch (offset {offset}), total: {total_fetched}")

                # Check if we've fetched all saved tracks
                if len(results['items']) < limit or not results.get('next'):
                    break

                offset += limit

            logger.info(f"Retrieved {len(tracks)} total saved tracks")
            return tracks

        except Exception as e:
            logger.error(f"Error fetching saved tracks: {e}")
            return []

    @rate_limited
    def get_saved_albums(self, since_timestamp=None) -> list:
        """Fetch user's saved albums from Spotify library.

        Args:
            since_timestamp: Optional ISO timestamp string. If provided, stops fetching
                           when reaching albums saved before this time (incremental sync).

        Returns:
            List of dicts with album metadata ready for DB upsert.
        """
        if not self.is_spotify_authenticated():
            logger.error("Not authenticated with Spotify")
            return []

        albums = []

        try:
            limit = 50  # Maximum allowed by Spotify API
            offset = 0
            total_fetched = 0

            while True:
                results = self.sp.current_user_saved_albums(limit=limit, offset=offset)

                if not results or 'items' not in results:
                    break

                batch_count = 0
                stop_fetching = False

                for item in results['items']:
                    album_data = item.get('album')
                    added_at = item.get('added_at', '')

                    if not album_data or not album_data.get('id'):
                        continue

                    # Incremental sync: stop when we hit albums saved before last sync
                    if since_timestamp and added_at and added_at < since_timestamp:
                        stop_fetching = True
                        break

                    # Extract primary artist
                    artists = album_data.get('artists', [])
                    artist_name = artists[0]['name'] if artists else 'Unknown Artist'
                    artist_id = artists[0].get('id', '') if artists else ''

                    # Get best image
                    images = album_data.get('images', [])
                    image_url = images[0]['url'] if images else None

                    albums.append({
                        'spotify_album_id': album_data['id'],
                        'album_name': album_data.get('name', ''),
                        'artist_name': artist_name,
                        'artist_id': artist_id,
                        'release_date': album_data.get('release_date', ''),
                        'total_tracks': album_data.get('total_tracks', 0),
                        'album_type': album_data.get('album_type', 'album'),
                        'image_url': image_url,
                        'date_saved': added_at,
                    })
                    batch_count += 1

                total_fetched += batch_count
                logger.info(f"Retrieved {batch_count} saved albums in batch (offset {offset}), total: {total_fetched}")

                if stop_fetching:
                    logger.info(f"Incremental sync: reached albums saved before {since_timestamp}, stopping")
                    break

                # Check if we've fetched all saved albums
                if len(results['items']) < limit or not results.get('next'):
                    break

                offset += limit

            logger.info(f"Retrieved {len(albums)} total saved albums from Spotify library")
            return albums

        except Exception as e:
            logger.error(f"Error fetching saved albums: {e}")
            return []

    def _get_playlist_items_page(self, playlist_id: str, limit: int = 100, offset: int = 0) -> dict:
        """Fetch playlist items using the /items endpoint (Feb 2026 Spotify API migration).

        Spotipy's playlist_items() still uses the deprecated /tracks endpoint internally,
        which returns 403 for Development Mode apps after the Feb 2026 API changes.
        Tries the new /items endpoint first, falls back to spotipy's /tracks for
        Extended Quota Mode apps where /items may not be available yet.
        """
        plid = self.sp._get_id("playlist", playlist_id)
        try:
            return self.sp._get(
                f"playlists/{plid}/items",
                limit=limit,
                offset=offset,
                additional_types="track,episode"
            )
        except spotipy.SpotifyException as e:
            if e.http_status in (403, 404):
                # /items not available — fall back to old /tracks endpoint
                return self.sp.playlist_items(playlist_id, limit=limit, offset=offset)
            raise

    @rate_limited
    def _get_playlist_tracks(self, playlist_id: str) -> List[Track]:
        if not self.is_spotify_authenticated():
            return []

        tracks = []

        try:
            results = self._get_playlist_items_page(playlist_id, limit=100)

            while results:
                for item in results['items']:
                    # Handle both old API ('track') and new Feb 2026 API ('item') field names
                    track_data = item.get('track') or item.get('item')
                    if track_data and track_data.get('id'):
                        track = Track.from_spotify_track(track_data)
                        tracks.append(track)

                if results['next']:
                    with _api_call_lock:
                        elapsed = time.time() - _last_api_call_time
                        _pi = _get_min_api_interval()
                        if elapsed < _pi:
                            time.sleep(_pi - elapsed)
                        globals()['_last_api_call_time'] = time.time()
                    from core.api_call_tracker import api_call_tracker
                    api_call_tracker.record_call('spotify', endpoint='get_playlist_tracks_page')
                    results = self.sp.next(results)
                else:
                    results = None

            return tracks

        except Exception as e:
            logger.error(f"Error fetching playlist tracks: {e}")
            return []
    
    @rate_limited
    def get_playlist_by_id(self, playlist_id: str) -> Optional[Playlist]:
        if not self.is_spotify_authenticated():
            return None
        
        try:
            playlist_data = self.sp.playlist(playlist_id)
            tracks = self._get_playlist_tracks(playlist_id)
            return Playlist.from_spotify_playlist(playlist_data, tracks)
            
        except Exception as e:
            logger.error(f"Error fetching playlist {playlist_id}: {e}")
            return None
    
    @rate_limited
    def get_followed_artists(self) -> list:
        """Fetch all artists the user follows on Spotify.
        Returns list of dicts with id, name, image_url, genres.
        Requires user-follow-read scope — returns empty list on 403."""
        if not self.is_spotify_authenticated():
            return []
        try:
            artists = []
            after = None
            while True:
                results = self.sp.current_user_followed_artists(limit=50, after=after)
                if not results or 'artists' not in results:
                    break
                items = results['artists'].get('items', [])
                if not items:
                    break
                for a in items:
                    image_url = a['images'][0]['url'] if a.get('images') else None
                    artists.append({
                        'spotify_id': a['id'],
                        'name': a['name'],
                        'image_url': image_url,
                        'genres': a.get('genres', []),
                    })
                # Cursor-based pagination
                cursors = results['artists'].get('cursors', {})
                after = cursors.get('after')
                if not after:
                    break
                # Throttle pagination
                _pi = _get_min_api_interval()
                with _api_call_lock:
                    elapsed = time.time() - _last_api_call_time
                    if elapsed < _pi:
                        time.sleep(_pi - elapsed)
                    globals()['_last_api_call_time'] = time.time()
                from core.api_call_tracker import api_call_tracker
                api_call_tracker.record_call('spotify', endpoint='get_followed_artists_page')

            logger.info(f"Retrieved {len(artists)} followed artists from Spotify")
            return artists
        except Exception as e:
            if '403' in str(e) or 'Forbidden' in str(e):
                logger.warning("Spotify user-follow-read scope not granted — re-authorize to see followed artists")
                return []
            _detect_and_set_rate_limit(e, 'get_followed_artists')
            logger.error(f"Error fetching followed artists: {e}")
            return []

    @rate_limited
    def search_tracks(self, query: str, limit: int = 10, allow_fallback: bool = True) -> List[Track]:
        """Search for tracks.

        When allow_fallback is True, falls back to the configured metadata source
        if Spotify is unavailable or returns an error.
        """
        cache = get_metadata_cache()
        effective_limit = min(limit, 50)  # Spotify API max is 50

        # Check Spotify cache first so cached data remains usable even when
        # Spotify is temporarily unavailable or rate limited.
        cached_results = cache.get_search_results('spotify', 'track', query, effective_limit)
        if cached_results is not None:
            tracks = []
            for raw in cached_results:
                try:
                    tracks.append(Track.from_spotify_track(raw))
                except Exception:
                    pass
            if tracks:
                return tracks

        use_spotify = self.is_spotify_authenticated()

        if use_spotify:
            try:
                results = self.sp.search(q=query, type='track', limit=effective_limit)
                tracks = []
                raw_items = results['tracks']['items']

                for track_data in raw_items:
                    track = Track.from_spotify_track(track_data)
                    tracks.append(track)

                # Cache individual tracks + search mapping
                entries = [(td.get('id'), td) for td in raw_items if td.get('id')]
                if entries:
                    cache.store_entities_bulk('spotify', 'track', entries)
                    cache.store_search_results('spotify', 'track', query, effective_limit,
                                               [td.get('id') for td in raw_items if td.get('id')])

                return tracks

            except Exception as e:
                _detect_and_set_rate_limit(e, 'search_tracks')
                logger.error(f"Error searching tracks via Spotify: {e}")
                # Fall through to fallback

        # Fallback (iTunes or Deezer — configured in settings)
        if allow_fallback:
            logger.debug(f"Using {self._fallback_source} fallback for track search: {query}")
            return self._fallback.search_tracks(query, limit)
        return []

    @rate_limited
    def search_artists(self, query: str, limit: int = 10, allow_fallback: bool = True) -> List[Artist]:
        """Search for artists.

        When allow_fallback is True, falls back to the configured metadata source
        if Spotify is unavailable or returns an error.
        """
        cache = get_metadata_cache()
        # Check Spotify cache first so cached data remains usable even when
        # Spotify is temporarily unavailable or rate limited.
        cached_results = cache.get_search_results('spotify', 'artist', query, min(limit, 10))
        if cached_results is not None:
            artists = []
            for raw in cached_results:
                try:
                    artists.append(Artist.from_spotify_artist(raw))
                except Exception:
                    pass
            if artists:
                query_lower = query.lower().strip()
                artists.sort(key=lambda a: (0 if a.name.lower().strip() == query_lower else 1))
                return artists

        use_spotify = self.is_spotify_authenticated()

        if use_spotify:
            try:
                search_query = f'artist:{query}' if len(query.strip()) <= 4 else query
                results = self.sp.search(q=search_query, type='artist', limit=min(limit, 10))
                artists = []
                raw_items = results['artists']['items']

                for artist_data in raw_items:
                    artist = Artist.from_spotify_artist(artist_data)
                    artists.append(artist)

                # Cache individual artists + search mapping
                entries = [(ad.get('id'), ad) for ad in raw_items if ad.get('id')]
                if entries:
                    cache.store_entities_bulk('spotify', 'artist', entries)
                    cache.store_search_results('spotify', 'artist', query, min(limit, 10),
                                               [ad.get('id') for ad in raw_items if ad.get('id')])

                # Re-rank: boost exact name matches to the top
                query_lower = query.lower().strip()
                artists.sort(key=lambda a: (0 if a.name.lower().strip() == query_lower else 1))

                return artists

            except Exception as e:
                _detect_and_set_rate_limit(e, 'search_artists')
                logger.error(f"Error searching artists via Spotify: {e}")
                # Fall through to iTunes fallback

        # Fallback (iTunes or Deezer)
        if allow_fallback:
            logger.debug(f"Using {self._fallback_source} fallback for artist search: {query}")
            artists = self._fallback.search_artists(query, limit)
            query_lower = query.lower().strip()
            artists.sort(key=lambda a: (0 if a.name.lower().strip() == query_lower else 1))
            return artists
        return []

    @rate_limited
    def search_albums(self, query: str, limit: int = 10, allow_fallback: bool = True) -> List[Album]:
        """Search for albums.

        When allow_fallback is True, falls back to the configured metadata source
        if Spotify is unavailable or returns an error.
        """
        cache = get_metadata_cache()
        # Check Spotify cache first so cached data remains usable even when
        # Spotify is temporarily unavailable or rate limited.
        cached_results = cache.get_search_results('spotify', 'album', query, min(limit, 10))
        if cached_results is not None:
            albums = []
            for raw in cached_results:
                try:
                    albums.append(Album.from_spotify_album(raw))
                except Exception:
                    pass
            if albums:
                return albums

        use_spotify = self.is_spotify_authenticated()

        if use_spotify:
            try:
                results = self.sp.search(q=query, type='album', limit=min(limit, 10))
                albums = []
                raw_items = results['albums']['items']

                for album_data in raw_items:
                    album = Album.from_spotify_album(album_data)
                    albums.append(album)

                # Cache individual albums + search mapping (skip if full data already cached)
                entries = [(ad.get('id'), ad) for ad in raw_items if ad.get('id')]
                if entries:
                    cache.store_entities_bulk('spotify', 'album', entries, skip_if_exists=True)
                    cache.store_search_results('spotify', 'album', query, min(limit, 10),
                                               [ad.get('id') for ad in raw_items if ad.get('id')])

                return albums

            except Exception as e:
                _detect_and_set_rate_limit(e, 'search_albums')
                logger.error(f"Error searching albums via Spotify: {e}")
                # Fall through to iTunes fallback

        # Fallback (iTunes or Deezer)
        if allow_fallback:
            logger.debug(f"Using {self._fallback_source} fallback for album search: {query}")
            return self._fallback.search_albums(query, limit)
        return []
    
    @rate_limited
    def get_track_details(self, track_id: str, allow_fallback: bool = True) -> Optional[Dict[str, Any]]:
        """Get detailed track information.

        When allow_fallback is True, falls back to the configured metadata source
        for non-Spotify IDs or Spotify failure.
        """
        # Check cache — we store raw track_data, reconstruct enhanced on hit
        cache = get_metadata_cache()
        fallback_src = self._fallback_source
        source = fallback_src if self._is_itunes_id(track_id) else 'spotify'
        cached = cache.get_entity(source, 'track', track_id)
        if cached:
            if source == 'spotify':
                # Validate cache has full track data (not simplified from get_album_tracks)
                if 'album' in cached:
                    return self._build_enhanced_track(cached)
                # Simplified track cached by get_album_tracks — treat as cache miss
                logger.debug(f"Cache hit for track {track_id} lacks album data, fetching full data")
            elif allow_fallback:
                # Fallback cache hit — delegate to fallback client which reconstructs enhanced format
                return self._fallback.get_track_details(track_id)

        if self.is_spotify_authenticated():
            try:
                track_data = self.sp.track(track_id)

                # Enhance with additional useful metadata for our purposes
                if track_data:
                    # Cache the raw Spotify response
                    cache.store_entity('spotify', 'track', track_id, track_data)
                    return self._build_enhanced_track(track_data)
                return track_data

            except Exception as e:
                _detect_and_set_rate_limit(e, 'get_track_details')
                logger.error(f"Error fetching track details via Spotify: {e}")
                # Fall through to iTunes fallback

        # Fallback - only if ID is numeric (non-Spotify format)
        if allow_fallback and self._is_itunes_id(track_id):
            logger.debug(f"Using {fallback_src} fallback for track details: {track_id}")
            result = self._fallback.get_track_details(track_id)
            return result
        else:
            logger.debug(f"Cannot use fallback for Spotify track ID: {track_id}")
            return None

    @staticmethod
    def _build_enhanced_track(track_data: dict) -> dict:
        """Build enhanced track dict from raw Spotify track data."""
        return {
            'id': track_data['id'],
            'name': track_data['name'],
            'track_number': track_data['track_number'],
            'disc_number': track_data['disc_number'],
            'duration_ms': track_data['duration_ms'],
            'explicit': track_data['explicit'],
            'artists': [artist['name'] for artist in track_data['artists']],
            'primary_artist': track_data['artists'][0]['name'] if track_data['artists'] else None,
            'album': {
                'id': track_data['album']['id'],
                'name': track_data['album']['name'],
                'total_tracks': track_data['album']['total_tracks'],
                'release_date': track_data['album']['release_date'],
                'album_type': track_data['album']['album_type'],
                'artists': [artist['name'] for artist in track_data['album']['artists']]
            },
            'is_album_track': track_data['album']['total_tracks'] > 1,
            'raw_data': track_data
        }
    
    @rate_limited
    def get_track_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        # Check cache — use entity_id with '_features' suffix
        cache = get_metadata_cache()
        cache_key = f"{track_id}_features"
        cached = cache.get_entity('spotify', 'track', cache_key)
        if cached:
            return cached

        if not self.is_spotify_authenticated():
            return None

        try:
            features = self.sp.audio_features(track_id)
            result = features[0] if features else None
            if result:
                cache.store_entity('spotify', 'track', cache_key, result)
            return result

        except Exception as e:
            logger.error(f"Error fetching track features: {e}")
            return None
    
    @rate_limited
    def get_album(self, album_id: str, allow_fallback: bool = True) -> Optional[Dict[str, Any]]:
        """Get album information.

        When allow_fallback is True, falls back to the configured metadata source
        for non-Spotify IDs or Spotify failure.
        """
        # Check cache first
        cache = get_metadata_cache()
        fallback_src = self._fallback_source
        source = fallback_src if self._is_itunes_id(album_id) else 'spotify'
        cached = cache.get_entity(source, 'album', album_id)
        if cached:
            if source == 'spotify':
                # Validate cache has full album data (not simplified from artist_albums)
                if 'tracks' in cached:
                    return cached
                # Simplified album cached by get_artist_albums — treat as cache miss
                logger.debug(f"Cache hit for album {album_id} lacks tracks, fetching full data")
            elif allow_fallback:
                # Fallback cache hit — delegate to fallback client
                return self._fallback.get_album(album_id)

        if self.is_spotify_authenticated():
            try:
                album_data = self.sp.album(album_id)
                if album_data:
                    cache.store_entity('spotify', 'album', album_id, album_data)
                return album_data

            except Exception as e:
                _detect_and_set_rate_limit(e, 'get_album')
                logger.error(f"Error fetching album via Spotify: {e}")
                # Fall through to fallback

        # Fallback - only if ID is numeric (non-Spotify format)
        if allow_fallback and self._is_itunes_id(album_id):
            logger.debug(f"Using {fallback_src} fallback for album: {album_id}")
            return self._fallback.get_album(album_id)
        else:
            logger.debug(f"Cannot use fallback for Spotify album ID: {album_id}")
            return None
    
    @rate_limited
    def get_album_tracks(self, album_id: str, allow_fallback: bool = True) -> Optional[Dict[str, Any]]:
        """Get album tracks.

        When allow_fallback is True, falls back to the configured metadata source
        for non-Spotify IDs or Spotify failure.
        """
        # Cache key uses album_id with '_tracks' suffix to differentiate from album metadata
        cache = get_metadata_cache()
        fallback_src = self._fallback_source
        source = fallback_src if self._is_itunes_id(album_id) else 'spotify'
        cache_key = f"{album_id}_tracks"
        cached = cache.get_entity(source, 'album', cache_key)
        if cached:
            return cached

        if self.is_spotify_authenticated():
            try:
                # Get first page of tracks
                first_page = self.sp.album_tracks(album_id)
                if not first_page or 'items' not in first_page:
                    return None

                # Collect all tracks starting with first page
                all_tracks = first_page['items'][:]

                # Fetch remaining pages if they exist — throttle pagination
                next_page = first_page
                while next_page.get('next'):
                    with _api_call_lock:
                        elapsed = time.time() - _last_api_call_time
                        _pi = _get_min_api_interval()
                        if elapsed < _pi:
                            time.sleep(_pi - elapsed)
                        globals()['_last_api_call_time'] = time.time()
                    from core.api_call_tracker import api_call_tracker
                    api_call_tracker.record_call('spotify', endpoint='get_album_tracks_page')
                    next_page = self.sp.next(next_page)
                    if next_page and 'items' in next_page:
                        all_tracks.extend(next_page['items'])

                # Log success
                logger.info(f"Retrieved {len(all_tracks)} tracks for album {album_id}")

                # Return structure with all tracks
                result = first_page.copy()
                result['items'] = all_tracks
                result['next'] = None  # No more pages
                result['limit'] = len(all_tracks)  # Update to reflect all tracks fetched

                # Cache the aggregated result
                cache.store_entity('spotify', 'album', cache_key, result)

                # Also cache individual tracks opportunistically (skip if full data already cached)
                track_entries = []
                for track in all_tracks:
                    tid = track.get('id')
                    if tid:
                        track_entries.append((tid, track))
                if track_entries:
                    cache.store_entities_bulk('spotify', 'track', track_entries, skip_if_exists=True)

                return result

            except Exception as e:
                _detect_and_set_rate_limit(e, 'get_album_tracks')
                logger.error(f"Error fetching album tracks via Spotify: {e}")
                # Fall through to iTunes fallback

        # Fallback - only if ID is numeric (non-Spotify format)
        if allow_fallback and self._is_itunes_id(album_id):
            logger.debug(f"Using {fallback_src} fallback for album tracks: {album_id}")
            result = self._fallback.get_album_tracks(album_id)
            return result
        else:
            logger.debug(f"Cannot use fallback for Spotify album ID: {album_id}")
            return None
    
    @rate_limited
    def get_artist_albums(self, artist_id: str, album_type: str = 'album,single', limit: int = 10,
                          skip_cache: bool = False, max_pages: int = 0, allow_fallback: bool = True) -> List[Album]:
        """Get albums by artist ID.

        When allow_fallback is True, falls back to iTunes/Deezer if Spotify
        is not authenticated or errors.
        Set skip_cache=True for watchlist scans that need fresh data to detect new releases.
        Set max_pages to limit pagination (0 = fetch all). Spotify returns newest first,
        so max_pages=1 is sufficient for new release detection."""
        cache = get_metadata_cache()
        fallback_src = self._fallback_source
        source = fallback_src if self._is_itunes_id(artist_id) else 'spotify'
        cache_key = f"{artist_id}_albums_{album_type.replace(',', '_')}"

        # Check cache first (unless caller needs fresh data)
        if not skip_cache:
            cached = cache.get_entity(source, 'artist', cache_key)
            if cached:
                try:
                    albums_list = cached.get('_albums', cached) if isinstance(cached, dict) else cached
                    return [Album.from_spotify_album(ad) for ad in albums_list]
                except Exception:
                    pass  # Cache data incompatible, re-fetch

        if self.is_spotify_authenticated():
            try:
                albums = []
                raw_items = []
                # Spotify caps artist_albums at 10 per page
                results = self.sp.artist_albums(artist_id, album_type=album_type, limit=min(limit, 10))
                pages_fetched = 1

                while results:
                    for album_data in results['items']:
                        album = Album.from_spotify_album(album_data)
                        albums.append(album)
                        raw_items.append(album_data)

                    # Stop if we've hit the page limit (0 = unlimited)
                    if max_pages and pages_fetched >= max_pages:
                        break

                    # Get next batch if available — throttle pagination to respect rate limits
                    if results['next']:
                        # Enforce same rate limit as decorated calls
                        with _api_call_lock:
                            elapsed = time.time() - _last_api_call_time
                            _pi = _get_min_api_interval()
                            if elapsed < _pi:
                                time.sleep(_pi - elapsed)
                            globals()['_last_api_call_time'] = time.time()
                        from core.api_call_tracker import api_call_tracker
                        api_call_tracker.record_call('spotify', endpoint='get_artist_albums_page')
                        results = self.sp.next(results)
                        pages_fetched += 1
                    else:
                        results = None

                logger.info(f"Retrieved {len(albums)} albums for artist {artist_id}" +
                            (f" (page limit: {max_pages})" if max_pages else ""))

                # Cache the full artist albums result (wrapped in dict for cache compatibility)
                if raw_items:
                    cache.store_entity('spotify', 'artist', cache_key, {'name': f'albums_{artist_id}', '_albums': raw_items})
                    # Also cache individual albums opportunistically
                    entries = [(ad.get('id'), ad) for ad in raw_items if ad.get('id')]
                    if entries:
                        cache.store_entities_bulk('spotify', 'album', entries, skip_if_exists=True)

                return albums

            except Exception as e:
                _detect_and_set_rate_limit(e, 'get_artist_albums')
                logger.error(f"Error fetching artist albums via Spotify: {e}")
                # Fall through to iTunes fallback

        # Fallback - only if ID is numeric (non-Spotify format)
        if allow_fallback and self._is_itunes_id(artist_id):
            logger.debug(f"Using {fallback_src} fallback for artist albums: {artist_id}")
            return self._fallback.get_artist_albums(artist_id, album_type, limit)
        else:
            logger.debug(f"Cannot use fallback for Spotify artist ID: {artist_id}")
            return []

    @rate_limited
    def get_user_info(self) -> Optional[Dict[str, Any]]:
        if not self.is_spotify_authenticated():
            return None

        try:
            return self.sp.current_user()
        except Exception as e:
            logger.error(f"Error fetching user info: {e}")
            return None

    @rate_limited
    def get_artist(self, artist_id: str, allow_fallback: bool = True) -> Optional[Dict[str, Any]]:
        """
        Get full artist details.

        Args:
            artist_id: Artist ID (Spotify or fallback source depending on authentication)

        Returns:
            Dictionary with artist data including images, genres, popularity
        """
        # Check cache first (works even during rate limit bans)
        cache = get_metadata_cache()
        fallback_src = self._fallback_source
        source = fallback_src if self._is_itunes_id(artist_id) else 'spotify'
        cached = cache.get_entity(source, 'artist', artist_id)
        if cached:
            if source == 'spotify':
                return cached  # Spotify raw format is the expected format
            if allow_fallback:
                # Fallback cache hit — delegate to fallback client which reconstructs Spotify-compatible format
                return self._fallback.get_artist(artist_id)
            return None

        if self.is_spotify_authenticated():
            try:
                result = self.sp.artist(artist_id)
                if result:
                    cache.store_entity('spotify', 'artist', artist_id, result)
                return result
            except Exception as e:
                _detect_and_set_rate_limit(e, 'get_artist')
                logger.error(f"Error fetching artist via Spotify: {e}")
                # Fall through to iTunes fallback

        # Fallback - only if ID is numeric (non-Spotify format)
        if allow_fallback and self._is_itunes_id(artist_id):
            logger.debug(f"Using {fallback_src} fallback for artist: {artist_id}")
            return self._fallback.get_artist(artist_id)
        else:
            logger.debug(f"Cannot use fallback for Spotify artist ID: {artist_id}")
            return None

    @rate_limited
    def get_artists_batch(self, artist_ids: List[str]) -> Dict[str, Dict]:
        """Get multiple artists, using cache where possible, batch API for misses.
        Returns dict keyed by artist_id → artist data dict."""
        if not artist_ids:
            return {}

        cache = get_metadata_cache()
        found, missing = cache.get_entities_batch('spotify', 'artist', artist_ids)

        if missing and self.is_spotify_authenticated():
            try:
                # Spotify batch endpoint accepts up to 50 IDs
                for i in range(0, len(missing), 50):
                    chunk = missing[i:i + 50]
                    batch_result = self.sp.artists(chunk)
                    for artist_data in (batch_result or {}).get('artists', []):
                        if artist_data and artist_data.get('id'):
                            aid = artist_data['id']
                            cache.store_entity('spotify', 'artist', aid, artist_data)
                            found[aid] = artist_data
            except Exception as e:
                logger.error(f"Error in batch artist fetch: {e}")

        return found
