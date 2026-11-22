import requests
import time
import threading
from typing import Dict, Optional, Any, List
from functools import wraps
from core.http_error_status import http_error_status
from utils.logging_config import get_logger

logger = get_logger("lastfm_client")

# ── key state ──
# from 1 december 2026 a last.fm api key needs a pro subscription, and a key
# without one is refused. the refusal is remembered per key for a while so
# nothing hammers last.fm, then tried again: a user who subscribes gets it
# back on their own. a new key starts clean (the memory is keyed by the key).

LASTFM_PRO_MESSAGE = (
    "Last.fm now requires a Pro subscription for API keys (from 1 December 2026). "
    "Your key was not accepted. SoulSync falls back to free sources; scrobble "
    "with ListenBrainz instead."
)
# the history import has no free stand-in, so it says so
LASTFM_IMPORT_REJECTED_MESSAGE = (
    "Last.fm now needs a Pro subscription for API keys; your key was not accepted. "
    "ListenBrainz import works without one."
)
# 10 invalid api key, 26 suspended api key. 4 (authentication failed, no
# permission for the service) only counts on unsigned reads: on a signed call
# it can be the secret or the session
KEY_REJECTED_CODES = frozenset({10, 26})
READ_KEY_REJECTED_CODES = frozenset({4, 10, 26})
KEY_REJECTED_HTTP = frozenset({401, 403})
KEY_REJECT_TTL = 6 * 3600

# cached reads: catalogue facts barely move, a user's charts do
CATALOGUE_TTL = 30 * 86400
USER_TTL = 6 * 3600

_key_state: Dict[str, Dict[str, Any]] = {}
_key_state_lock = threading.Lock()


class LastFMKeyRejected(Exception):
    """last.fm refused the api key (no pro subscription, invalid, suspended)."""

    def __init__(self, message: str = LASTFM_PRO_MESSAGE, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


def _fingerprint(api_key: str) -> str:
    import hashlib
    return hashlib.sha256(str(api_key or '').strip().encode('utf-8')).hexdigest()[:16]


def note_key_rejected(api_key: str, code: Any = None, message: str = '') -> None:
    if not api_key:
        return
    fp = _fingerprint(api_key)
    with _key_state_lock:
        first = fp not in _key_state
        _key_state[fp] = {'at': time.time(), 'code': code, 'message': str(message or '')}
    if first:
        logger.warning(
            "Last.fm did not accept the API key (%s%s). Last.fm features fall back to "
            "free sources and try the key again in %dh.",
            f"error {code}" if code is not None else "refused",
            f": {message}" if message else "", KEY_REJECT_TTL // 3600)


def note_key_accepted(api_key: str) -> None:
    if not api_key:
        return
    fp = _fingerprint(api_key)
    with _key_state_lock:
        was = _key_state.pop(fp, None)
    if was is not None:
        logger.info("Last.fm accepts the API key again")


def key_rejection(api_key: str) -> Optional[Dict[str, Any]]:
    """the remembered refusal for this key, or None (never refused, or the
    memory ran out and it is due another try)."""
    if not api_key:
        return None
    fp = _fingerprint(api_key)
    with _key_state_lock:
        state = _key_state.get(fp)
        if state is None:
            return None
        if time.time() - state['at'] >= KEY_REJECT_TTL:
            _key_state.pop(fp, None)
            return None
        return dict(state)


def reset_key_state() -> None:
    with _key_state_lock:
        _key_state.clear()


def _configured_key() -> str:
    try:
        from core.settings import config_manager
        return str(config_manager.get('lastfm.api_key', '') or '').strip()
    except Exception:  # noqa: BLE001
        return ''


def lastfm_usable(api_key: Optional[str] = None) -> bool:
    """a key is set and last.fm has not refused it lately."""
    key = _configured_key() if api_key is None else str(api_key or '').strip()
    return bool(key) and key_rejection(key) is None


def lastfm_key_status(api_key: Optional[str] = None) -> Dict[str, Any]:
    """what the settings card and the routes show."""
    key = _configured_key() if api_key is None else str(api_key or '').strip()
    state = key_rejection(key) if key else None
    return {
        'configured': bool(key),
        'usable': bool(key) and state is None,
        'key_rejected': state is not None,
        'code': state.get('code') if state else None,
        'message': LASTFM_PRO_MESSAGE if state else '',
        'retry_at': (state['at'] + KEY_REJECT_TTL) if state else None,
    }

# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 0.2  # 200ms between calls (Last.fm allows 5 req/sec)


def _int_code(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def rate_limited(func):
    """Decorator to enforce rate limiting on Last.fm API calls"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        global _last_api_call_time

        # a refused key makes no request, so it waits for no slot
        owner = args[0] if args else None
        if isinstance(owner, LastFMClient) and key_rejection(owner.api_key):
            return func(*args, **kwargs)

        with _api_call_lock:
            current_time = time.time()
            time_since_last_call = current_time - _last_api_call_time

            # a clock that stepped back makes this negative: never sleep on that
            if 0 <= time_since_last_call < MIN_API_INTERVAL:
                sleep_time = MIN_API_INTERVAL - time_since_last_call
                time.sleep(sleep_time)

            _last_api_call_time = time.time()

        from core.api_call_tracker import api_call_tracker
        api_call_tracker.record_call('lastfm')

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            # Read the structured HTTP status first: the message can contain a
            # URL whose entity ID holds "429", which is not a rate limit.
            status = http_error_status(e)
            is_rate_limit = (status == 429 if status is not None
                             else "rate limit" in str(e).lower() or "429" in str(e))
            if is_rate_limit:
                logger.warning(f"Last.fm rate limit hit, implementing backoff: {e}")
                time.sleep(5.0)
            raise e
    return wrapper


class LastFMClient:
    """Client for interacting with the Last.fm API (metadata + scrobbling)"""

    BASE_URL = "https://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str = "", api_secret: str = "", session_key: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session_key = session_key
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'SoulSync/1.0',
            'Accept': 'application/json'
        })
        logger.info("Last.fm client initialized")

    def _sign_request(self, params: dict) -> str:
        """Generate MD5 API signature for write operations."""
        import hashlib
        # Sort params alphabetically, concatenate key+value pairs, append secret
        sorted_str = ''.join(f'{k}{v}' for k, v in sorted(params.items()))
        return hashlib.md5((sorted_str + self.api_secret).encode('utf-8')).hexdigest()

    def get_auth_url(self, callback_url: str) -> Optional[str]:
        """Generate the Last.fm authorization URL for scrobbling.

        User visits this URL, authorizes SoulSync, gets redirected back with a token.
        """
        if not self.api_key:
            return None
        return f"https://www.last.fm/api/auth/?api_key={self.api_key}&cb={callback_url}"

    def get_session_key(self, token: str) -> Optional[str]:
        """Exchange an auth token for a session key (one-time after user authorizes).

        Returns the session key string, or None on failure.
        """
        session = self.get_session(token)
        return session['key'] if session else None

    def get_session(self, token: str) -> Optional[Dict[str, str]]:
        """``{'key', 'name'}`` for an authorized token, None on failure. The
        name is the Last.fm account the user signed in as."""
        if not self.api_key or not self.api_secret or not token:
            return None

        params = {
            'method': 'auth.getSession',
            'api_key': self.api_key,
            'token': token,
        }
        params['api_sig'] = self._sign_request(params)
        params['format'] = 'json'

        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            data = response.json()
            session = data.get('session', {})
            key = session.get('key')
            if key:
                self.session_key = key
                logger.info(f"Last.fm session key obtained for user: {session.get('name', '?')}")
                return {'key': key, 'name': str(session.get('name') or '')}
            else:
                error = data.get('error', 'Unknown')
                logger.error(f"Last.fm auth failed: {data.get('message', error)}")
                return None
        except Exception as e:
            logger.error(f"Last.fm session key exchange failed: {e}")
            return None

    @property
    def key_rejected(self) -> bool:
        """True while last.fm refuses this client's key (the process-wide
        memory, KEY_REJECT_TTL). the enrichment worker reads this name."""
        return bool(self.api_key) and key_rejection(self.api_key) is not None

    def can_scrobble(self) -> bool:
        """Check if scrobbling is possible (has api_key, api_secret, and session_key)."""
        return bool(self.api_key and self.api_secret and self.session_key)

    @rate_limited
    def scrobble_tracks(self, tracks: List[Dict]) -> bool:
        """Scrobble up to 50 tracks at once via track.scrobble POST endpoint.

        Args:
            tracks: list of dicts with {artist, track, album, timestamp (unix int)}

        Returns:
            True if scrobble succeeded.
        """
        if not self.can_scrobble():
            return False

        if not tracks:
            return True

        # a refused key: the plays stay queued (unscrobbled) for when it works
        if key_rejection(self.api_key):
            return False

        # Last.fm accepts max 50 scrobbles per request
        batch = tracks[:50]

        params = {
            'method': 'track.scrobble',
            'api_key': self.api_key,
            'sk': self.session_key,
        }

        for i, t in enumerate(batch):
            ts = t.get('timestamp')
            if isinstance(ts, str):
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    if dt.tzinfo is None:
                        # listening_history stores UTC; a naive stamp is UTC,
                        # never system-local (else every scrobble outside UTC
                        # ships shifted by the local offset).
                        dt = dt.replace(tzinfo=timezone.utc)
                    ts = int(dt.timestamp())
                except Exception:
                    continue

            params[f'artist[{i}]'] = t.get('artist', '')
            params[f'track[{i}]'] = t.get('track', '')
            params[f'timestamp[{i}]'] = str(ts)
            if t.get('album'):
                params[f'album[{i}]'] = t['album']

        params['api_sig'] = self._sign_request(params)
        params['format'] = 'json'

        try:
            response = self.session.post(self.BASE_URL, data=params, timeout=15)
            data = response.json()
            if 'scrobbles' in data:
                accepted = data['scrobbles'].get('@attr', {}).get('accepted', 0)
                logger.info(f"Last.fm scrobbled {accepted}/{len(batch)} tracks")
                note_key_accepted(self.api_key)
                return True
            else:
                error = data.get('error', 'Unknown')
                if _int_code(error) in KEY_REJECTED_CODES:
                    note_key_rejected(self.api_key, _int_code(error), data.get('message', ''))
                    return False
                logger.warning(f"Last.fm scrobble failed: {data.get('message', error)}")
                return False
        except Exception as e:
            logger.error(f"Last.fm scrobble error: {e}")
            return False

    @rate_limited
    def update_now_playing(self, artist: str, track: str, album: str = '',
                           duration_seconds: int = 0) -> bool:
        """Tell Last.fm what is playing right now (track.updateNowPlaying).

        Not a scrobble: Last.fm shows it on the profile until the track ends.
        A refused key sends nothing.
        """
        if not self.can_scrobble() or not artist or not track:
            return False
        if key_rejection(self.api_key):
            return False
        params = {
            'method': 'track.updateNowPlaying',
            'api_key': self.api_key,
            'sk': self.session_key,
            'artist': artist,
            'track': track,
        }
        if album:
            params['album'] = album
        if duration_seconds and int(duration_seconds) > 0:
            params['duration'] = str(int(duration_seconds))
        params['api_sig'] = self._sign_request(params)
        params['format'] = 'json'
        try:
            response = self.session.post(self.BASE_URL, data=params, timeout=10)
            data = response.json()
            if 'nowplaying' in data:
                note_key_accepted(self.api_key)
                return True
            error = data.get('error', 'Unknown')
            if _int_code(error) in KEY_REJECTED_CODES:
                note_key_rejected(self.api_key, _int_code(error), data.get('message', ''))
                return False
            logger.debug(f"Last.fm now playing failed: {data.get('message', error)}")
            return False
        except Exception as e:
            logger.debug(f"Last.fm now playing error: {e}")
            return False

    def _make_request(self, method: str, params: Dict = None, timeout: int = 10, raise_on_transient: bool = False,
                      ignore_key_memory: bool = False) -> Optional[Dict]:
        """Make a request to the Last.fm API.

        Args:
            raise_on_transient: If True, raise exceptions on transient errors (timeouts, HTTP errors)
                instead of returning None. Used by get_*_info methods so the worker can distinguish
                'not found' (mark not_found, retry in 30 days) from 'API failed' (mark error, retry in 7 days).
                A refused key raises LastFMKeyRejected then, never reads as 'not found'.
            ignore_key_memory: ask even when the key was refused lately (the settings test).
        """
        if not self.api_key:
            logger.warning("Last.fm API key not configured")
            return None

        # a key last.fm refused lately: no request, same empty answer
        if not ignore_key_memory and key_rejection(self.api_key):
            if raise_on_transient:
                raise LastFMKeyRejected()
            return None

        request_params = {
            'method': method,
            'api_key': self.api_key,
            'format': 'json'
        }
        if params:
            request_params.update(params)

        try:
            response = self.session.get(
                self.BASE_URL,
                params=request_params,
                timeout=timeout
            )

            # the error body comes with a 4xx, so read it before raise_for_status
            try:
                data = response.json()
            except Exception:  # noqa: BLE001 - not json, raise_for_status decides
                data = None
            error_code = _int_code(data.get('error')) if isinstance(data, dict) and 'error' in data else None
            status = getattr(response, 'status_code', None)
            if error_code in READ_KEY_REJECTED_CODES or (
                    error_code is None and isinstance(status, int) and status in KEY_REJECTED_HTTP):
                note_key_rejected(self.api_key, error_code if error_code is not None else status,
                                  (data or {}).get('message', '') if isinstance(data, dict) else '')
                if raise_on_transient:
                    raise LastFMKeyRejected(code=error_code)
                return None

            response.raise_for_status()
            if data is None:
                data = response.json()

            # Last.fm returns errors inside the JSON
            if 'error' in data:
                error_code = data.get('error')
                error_msg = data.get('message', 'Unknown error')
                # Error 6 = "Artist/Album/Track not found" — not a real error
                if error_code == 6:
                    note_key_accepted(self.api_key)
                    return None
                # Transient errors: 11=Service Offline, 16=Temporarily Unavailable, 29=Rate Limit
                if raise_on_transient and error_code in (11, 16, 29):
                    raise Exception(f"Last.fm transient error ({error_code}): {error_msg}")
                logger.error(f"Last.fm API error ({error_code}): {error_msg}")
                return None

            note_key_accepted(self.api_key)
            return data

        except LastFMKeyRejected:
            raise
        except requests.exceptions.Timeout:
            logger.warning(f"Last.fm API timeout for method: {method}")
            if raise_on_transient:
                raise
            return None
        except Exception as e:
            logger.error(f"Last.fm API request error ({method}): {e}")
            if raise_on_transient:
                raise
            return None

    _throttled_request = rate_limited(_make_request)

    def _cached_request(self, method: str, params: Dict, ttl: float) -> Optional[Dict]:
        """a read through the provider cache: fresh row, else last.fm, else
        the stale row (a refused key or an outage keeps what we knew)."""
        from core import provider_cache

        def fetch():
            if not self.api_key or key_rejection(self.api_key):
                return None
            return self._throttled_request(method, params)

        data, how = provider_cache.cached('lastfm', method, params, ttl, fetch)
        if how == 'stale':
            logger.debug("Last.fm %s: serving the cached answer", method)
        return data

    # ── Artist Methods ──

    @rate_limited
    def search_artist(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Search for an artist by name.

        Returns:
            Artist dict with: name, mbid, url, listeners, image
        """
        data = self._make_request('artist.search', {
            'artist': artist_name,
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('artistmatches', {}).get('artist', [])
        if results and len(results) > 0:
            logger.debug(f"Found artist for query: {artist_name}")
            return results[0]

        logger.debug(f"No artist found for query: {artist_name}")
        return None

    def get_authenticated_username(self) -> Optional[str]:
        """Get the username of the authenticated Last.fm user via signed user.getInfo call."""
        if not self.api_key or not self.api_secret or not self.session_key:
            return None
        try:
            params = {
                'method': 'user.getInfo',
                'api_key': self.api_key,
                'sk': self.session_key,
                'format': 'json'
            }
            params['api_sig'] = self._sign_request({k: v for k, v in params.items() if k != 'format'})
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            username = data.get('user', {}).get('name')
            if username:
                logger.info(f"Last.fm authenticated user: {username}")
            return username
        except Exception as e:
            logger.error(f"Error getting Last.fm username: {e}")
            return None

    @rate_limited
    def get_user_recent_tracks(
        self,
        username: str,
        page: int = 1,
        limit: int = 200,
        from_ts: Optional[int] = None,
        to_ts: Optional[int] = None,
        extended: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Fetch one page of a user's Last.fm scrobbles.

        ``user.getRecentTracks`` is public for public profiles and pages up to
        200 rows. The importer owns pagination/resume; the client only exposes
        the raw page plus Last.fm's ``@attr`` counts.
        """
        if not username:
            return None
        params: Dict[str, Any] = {
            'user': username,
            'page': str(max(1, int(page or 1))),
            'limit': str(min(200, max(1, int(limit or 200)))),
        }
        if from_ts is not None:
            params['from'] = str(int(from_ts))
        if to_ts is not None:
            params['to'] = str(int(to_ts))
        if extended:
            params['extended'] = '1'
        return self._make_request('user.getRecentTracks', params, timeout=20, raise_on_transient=True)

    def get_user_top_artists(self, username: str, period: str = 'overall', limit: int = 200) -> list:
        """Fetch user's top artists from Last.fm.
        Args:
            username: Last.fm username
            period: overall|7day|1month|3month|6month|12month
            limit: max artists to return
        Returns:
            List of dicts with name, playcount, image_url
        """
        if not username:
            return []
        try:
            artists = []
            page = 1
            per_page = min(limit, 200)
            while len(artists) < limit:
                data = self._cached_request('user.getTopArtists', {
                    'user': username,
                    'period': period,
                    'limit': str(per_page),
                    'page': str(page)
                }, USER_TTL)
                if not data:
                    break
                items = data.get('topartists', {}).get('artist', [])
                if not items:
                    break
                for a in items:
                    image_url = None
                    images = a.get('image', [])
                    for img in reversed(images):  # largest first
                        if img.get('#text'):
                            image_url = img['#text']
                            break
                    artists.append({
                        'name': a.get('name', ''),
                        'playcount': int(a.get('playcount', 0)),
                        'image_url': image_url,
                    })
                    if len(artists) >= limit:
                        break
                # Check if more pages exist
                total_pages = int(data.get('topartists', {}).get('@attr', {}).get('totalPages', 1))
                if page >= total_pages:
                    break
                page += 1

            logger.info(f"Retrieved {len(artists)} top artists from Last.fm for {username}")
            return artists
        except Exception as e:
            logger.error(f"Error fetching Last.fm top artists: {e}")
            return []

    @rate_limited
    def get_artist_info(self, artist_name: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed artist info including bio, tags, stats.

        Returns:
            Artist dict with: name, mbid, url, image, stats (listeners, playcount),
            similar (artists), tags (tag list), bio (summary, content)
        """
        data = self._make_request('artist.getinfo', {
            'artist': artist_name,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        artist = data.get('artist')
        if artist:
            logger.debug(f"Got artist info for: {artist_name}")
            return artist

        return None

    def get_artist_top_tags(self, artist_name: str) -> List[Dict[str, Any]]:
        """
        Get top tags for an artist (genres, styles, moods).

        Returns:
            List of tag dicts with: name, count, url
        """
        data = self._cached_request('artist.gettoptags', {
            'artist': artist_name,
            'autocorrect': 1
        }, CATALOGUE_TTL)
        if not data:
            return []

        tags = data.get('toptags', {}).get('tag', [])
        return tags if isinstance(tags, list) else [tags] if tags else []

    def get_artist_top_tracks(self, artist_name: str, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Get top tracks for an artist.

        Returns:
            List of track dicts with: name, playcount, listeners, url
        """
        data = self._cached_request('artist.gettoptracks', {
            'artist': artist_name,
            'autocorrect': 1,
            'limit': limit
        }, CATALOGUE_TTL)
        if not data:
            return []

        tracks = data.get('toptracks', {}).get('track', [])
        if not isinstance(tracks, list):
            tracks = [tracks] if tracks else []

        result = []
        for t in tracks:
            result.append({
                'name': t.get('name', ''),
                'playcount': int(t.get('playcount', 0)),
                'listeners': int(t.get('listeners', 0)),
                'url': t.get('url', ''),
            })
        return result

    def get_similar_artists(self, artist_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get similar artists.

        Returns:
            List of artist dicts with: name, mbid, match (similarity score), url, image
        """
        data = self._cached_request('artist.getsimilar', {
            'artist': artist_name,
            'autocorrect': 1,
            'limit': limit
        }, CATALOGUE_TTL)
        if not data:
            return []

        artists = data.get('similarartists', {}).get('artist', [])
        return artists if isinstance(artists, list) else [artists] if artists else []

    def get_tag_top_artists(self, tag: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Top artists for a Last.fm tag ("deep ambient techno", "shoegaze").

        Genre-level radio: a YouTube mix or a "this is what X sounds like"
        video has no artist to branch from, but its GENRE does. Returns
        artist dicts (name, mbid, url); empty when the tag is unknown.
        """
        if not str(tag or '').strip():
            return []
        data = self._cached_request('tag.gettopartists', {
            'tag': str(tag).strip(),
            'limit': limit,
        }, CATALOGUE_TTL)
        if not data:
            return []
        artists = (data.get('topartists') or {}).get('artist', [])
        return artists if isinstance(artists, list) else [artists] if artists else []

    # ── Album Methods ──

    @rate_limited
    def search_album(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for an album.

        Returns:
            Album dict with: name, artist, url, image
        """
        data = self._make_request('album.search', {
            'album': f"{artist_name} {album_title}",
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('albummatches', {}).get('album', [])
        if results and len(results) > 0:
            logger.debug(f"Found album for query: {artist_name} - {album_title}")
            return results[0]

        logger.debug(f"No album found for query: {artist_name} - {album_title}")
        return None

    @rate_limited
    def get_album_info(self, artist_name: str, album_title: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed album info including tags, tracks, wiki.

        Returns:
            Album dict with: name, artist, mbid, url, image, listeners, playcount,
            tracks (track list), tags (tag list), wiki (summary, content)
        """
        data = self._make_request('album.getinfo', {
            'artist': artist_name,
            'album': album_title,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        album = data.get('album')
        if album:
            logger.debug(f"Got album info for: {artist_name} - {album_title}")
            return album

        return None

    # ── Track Methods ──

    @rate_limited
    def search_track(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Search for a track.

        Returns:
            Track dict with: name, artist, url, listeners
        """
        data = self._make_request('track.search', {
            'track': track_title,
            'artist': artist_name,
            'limit': 5
        })
        if not data:
            return None

        results = data.get('results', {}).get('trackmatches', {}).get('track', [])
        if results and len(results) > 0:
            logger.debug(f"Found track for query: {artist_name} - {track_title}")
            return results[0]

        logger.debug(f"No track found for query: {artist_name} - {track_title}")
        return None

    @rate_limited
    def get_track_info(self, artist_name: str, track_title: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed track info including tags, wiki, play stats.

        Returns:
            Track dict with: name, mbid, url, duration, listeners, playcount,
            artist, album, toptags (tag list), wiki (summary, content)
        """
        data = self._make_request('track.getinfo', {
            'artist': artist_name,
            'track': track_title,
            'autocorrect': 1
        }, raise_on_transient=True)
        if not data:
            return None

        track = data.get('track')
        if track:
            logger.debug(f"Got track info for: {artist_name} - {track_title}")
            return track

        return None

    def get_similar_tracks(self, artist_name: str, track_title: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get tracks similar to the given track.

        Returns:
            List of track dicts with: name, artist, match (0.0–1.0), mbid
        """
        data = self._cached_request('track.getsimilar', {
            'artist': artist_name,
            'track': track_title,
            'autocorrect': 1,
            'limit': limit
        }, CATALOGUE_TTL)
        if not data:
            return []

        tracks = data.get('similartracks', {}).get('track', [])
        if not isinstance(tracks, list):
            tracks = [tracks] if tracks else []

        result = []
        for t in tracks:
            artist = t.get('artist', {})
            result.append({
                'name': t.get('name', ''),
                'artist': artist.get('name', '') if isinstance(artist, dict) else str(artist),
                'match': float(t.get('match', 0)),
                'mbid': t.get('mbid', ''),
            })
        return result

    # ── Utility Methods ──

    def get_best_image(self, images: List) -> Optional[str]:
        """
        Extract the best quality image URL from Last.fm image array.
        Last.fm returns images as [{#text: url, size: small/medium/large/extralarge/mega}]
        """
        if not images or not isinstance(images, list):
            return None

        # Prefer largest
        for size in ['mega', 'extralarge', 'large', 'medium', 'small']:
            for img in images:
                if isinstance(img, dict) and img.get('size') == size:
                    url = img.get('#text', '')
                    if url:
                        return url

        return None

    def extract_tags(self, tags_data, max_tags: int = 10) -> List[str]:
        """
        Extract tag names from Last.fm tags response.
        Filters out low-count tags and normalizes.
        """
        if not tags_data:
            return []

        tag_list = tags_data if isinstance(tags_data, list) else tags_data.get('tag', [])
        if not isinstance(tag_list, list):
            tag_list = [tag_list] if tag_list else []

        tags = []
        for tag in tag_list[:max_tags]:
            if isinstance(tag, dict):
                name = tag.get('name', '').strip()
                if name and len(name) > 1:
                    tags.append(name)
            elif isinstance(tag, str):
                tags.append(tag.strip())

        return tags

    def validate_api_key(self) -> bool:
        """Test if the API key is valid by making a simple request"""
        return self.check_api_key()[0]

    def check_api_key(self) -> tuple:
        """(ok, message) from a real request, past the remembered refusal, so a
        user who just subscribed to pro sees it work straight away."""
        if not self.api_key:
            return False, "Missing Last.fm API key."
        try:
            data = self._make_request('chart.gettopartists', {'limit': 1}, raise_on_transient=True,
                                      ignore_key_memory=True)
        except LastFMKeyRejected:
            return False, LASTFM_PRO_MESSAGE
        except Exception as e:  # noqa: BLE001
            import re
            return False, "Could not reach Last.fm: " + re.sub(r"api_key=[^&\s]+", "api_key=REDACTED", str(e))
        if data is None:
            return False, "Invalid Last.fm API key."
        return True, "Successfully connected to Last.fm!"
