import os
import math
import requests
import time
import threading
from typing import Dict, List, Optional, Any
from urllib.parse import urlsplit, urlunsplit
from utils.logging_config import get_logger

logger = get_logger("musicbrainz_client")

# Lucene query-syntax characters that must be backslash-escaped when a
# user-supplied value is interpolated into a query (e.g. artist names like
# "Sunn O)))", "Anthony Green (Saosin)", "Therapy?", "!!!"). Without this an
# unbalanced paren or a stray ?/* either breaks the field group (returning
# unrelated results) or yields zero hits.
_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')


def _escape_lucene(text: str) -> str:
    """Backslash-escape Lucene special characters in a user-supplied term."""
    return ''.join('\\' + ch if ch in _LUCENE_SPECIAL else ch for ch in text)


# Global rate limiting variables
_last_api_call_time = 0
_api_call_lock = threading.Lock()
MIN_API_INTERVAL = 1.05  # MusicBrainz allows about 1 req/sec per egress IP.
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_MAX_RETRIES = 2
TRANSIENT_STATUS_CODES = {429, 503, 504}


def _config_setting(env_name: str, config_key: str) -> Any:
    value = os.environ.get(env_name)
    if value is not None:
        return value
    try:
        from core.settings import config_manager
        return config_manager.get(config_key, None)
    except Exception:
        return None


def _float_setting(env_name: str, config_key: str, default: float) -> float:
    value = _config_setting(env_name, config_key)
    try:
        if value is None or value == '':
            return default
        return max(0.1, float(value))
    except (TypeError, ValueError):
        return default


def _int_setting(env_name: str, config_key: str, default: int) -> int:
    value = _config_setting(env_name, config_key)
    try:
        if value is None or value == '':
            return default
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _is_transient_musicbrainz_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.exceptions.ReadTimeout, requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    response = getattr(exc, 'response', None)
    status_code = getattr(response, 'status_code', None)
    if status_code in TRANSIENT_STATUS_CODES:
        return True
    message = str(exc).lower()
    return 'rate limit' in message or 'read timed out' in message or '503' in message or '429' in message

def _server_settings():
    """Resolve current settings; environment overrides persisted configuration."""
    return validate_server_settings(
        _config_setting('SOULSYNC_MUSICBRAINZ_BASE_URL', 'musicbrainz.base_url'),
        _config_setting('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', 'musicbrainz.request_interval'))


def validate_server_settings(raw, raw_interval):
    """Normalize and validate a server URL and interval without network access."""
    try:
        parsed = urlsplit(str(raw or MusicBrainzClient.BASE_URL).strip())
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
                or any(c.isspace() for c in str(raw or ''))):
            raise ValueError('expected HTTP(S) URL without credentials, query or fragment')
        _port = parsed.port  # Validate malformed/out-of-range ports, including IPv6 URLs.
        path = parsed.path.rstrip('/')
        if not path.endswith('/ws/2'):
            path += '/ws/2'
        url = urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))
    except ValueError as exc:
        raise ValueError('Invalid musicbrainz.base_url / SOULSYNC_MUSICBRAINZ_BASE_URL') from exc
    try:
        interval = MIN_API_INTERVAL if raw_interval in (None, '') else float(raw_interval)
        if isinstance(raw_interval, bool) or not math.isfinite(interval) or interval < 0:
            raise ValueError('expected finite nonnegative seconds')
    except (ValueError, TypeError) as exc:
        raise ValueError('Invalid musicbrainz.request_interval / SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL') from exc
    hostname = parsed.hostname.lower().rstrip('.')
    if hostname == 'musicbrainz.org' or hostname.endswith('.musicbrainz.org'):
        interval = max(MIN_API_INTERVAL, interval)
    return url, interval


def _wait_for_musicbrainz_slot(interval: float = MIN_API_INTERVAL) -> None:
    global _last_api_call_time

    with _api_call_lock:
        current_time = time.monotonic()
        time_since_last_call = current_time - _last_api_call_time

        if time_since_last_call < interval:
            sleep_time = interval - time_since_last_call
            time.sleep(sleep_time)

        _last_api_call_time = time.monotonic()

    from core.api_call_tracker import api_call_tracker
    api_call_tracker.record_call('musicbrainz')

class MusicBrainzClient:
    """Client for interacting with MusicBrainz API"""

    BASE_URL = "https://musicbrainz.org/ws/2"
    # MusicBrainz mandates a meaningful User-Agent with contact info. Falling back
    # to a bare name/version risks IP blocking under load — include the project
    # URL so MB operators have a way to reach us if we misbehave.
    DEFAULT_CONTACT = "https://github.com/Nezreka/SoulSync"

    def __init__(self, app_name: str = "SoulSync", app_version: str = "1.0", contact_email: str = ""):
        """
        Initialize MusicBrainz client

        Args:
            app_name: Name of the application
            app_version: Version of the application
            contact_email: Contact email or URL (defaults to project URL when empty)
        """
        self.base_url, self.request_interval = _server_settings()
        contact = contact_email or self.DEFAULT_CONTACT
        self.user_agent = f"{app_name}/{app_version} ( {contact} )"

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.user_agent,
            'Accept': 'application/json'
        })

        self.read_timeout = _float_setting('SOULSYNC_MUSICBRAINZ_READ_TIMEOUT', 'musicbrainz.read_timeout', DEFAULT_READ_TIMEOUT)
        self.connect_timeout = _float_setting('SOULSYNC_MUSICBRAINZ_CONNECT_TIMEOUT', 'musicbrainz.connect_timeout', DEFAULT_CONNECT_TIMEOUT)
        self.max_retries = _int_setting('SOULSYNC_MUSICBRAINZ_MAX_RETRIES', 'musicbrainz.max_retries', DEFAULT_MAX_RETRIES)

        logger.info(f"MusicBrainz client initialized with user agent: {self.user_agent}")

    def _get(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> requests.Response:
        """GET a MusicBrainz endpoint with shared pacing and transient retries."""
        # Resolve at request boundaries so every existing client sees UI saves.
        # Keep this pair local: an in-flight retry stays on its original server.
        base_url, interval = _server_settings()
        url = f"{base_url}{path}"
        attempts = self.max_retries + 1
        last_exc: Exception | None = None

        for attempt in range(attempts):
            _wait_for_musicbrainz_slot(interval)
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=(self.connect_timeout, self.read_timeout),
                    allow_redirects=False,
                )
                # Redirects must not send mirror traffic to an unpaced public server.
                if 300 <= response.status_code < 400:
                    raise requests.HTTPError(
                        "MusicBrainz API redirected; configure its final base URL", response=response)
                response.raise_for_status()
                return response
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_retries or not _is_transient_musicbrainz_error(exc):
                    raise
                backoff = min(8.0, 2.0 * (2 ** attempt))
                logger.warning(
                    "MusicBrainz transient request failure; retrying in %.1fs (%s/%s): %s",
                    backoff,
                    attempt + 1,
                    attempts,
                    exc,
                )
                time.sleep(backoff)

        raise last_exc or RuntimeError('MusicBrainz request failed')
    
    def search_artist(self, artist_name: str, limit: int = 10, strict: bool = True,
                      raise_on_error: bool = False) -> List[Dict[str, Any]]:
        """
        Search for artists by name.

        Args:
            artist_name: Name of the artist to search for
            limit: Maximum number of results to return
            strict: When True (default), builds a phrase-match query against
                the `artist` field only — correct for enrichment flows that
                already know the exact name. When False, sends a bare query
                which MusicBrainz matches against the alias, artist, AND
                sortname indexes — the right behavior for user-facing fuzzy
                search (finds "Metallica" from typing "metalica", matches
                aliased names, etc.).
            raise_on_error: Re-raise a transport failure instead of reporting
                it as an empty result. The default is the historical
                fail-soft behaviour, which suits callers that only want a
                best-effort list. It does NOT suit a caller that WRITES the
                answer down: a timeout and "MusicBrainz knows nobody by that
                name" are the same `[]` here, and the alias cache stored the
                second meaning for a month after seeing the first.

        Returns:
            List of artist results with id, name, score, etc. MusicBrainz
            assigns each result a `score` 0-100; the list is pre-sorted
            score-descending by the server.
        """
        try:
            # Escape quotes and backslashes for Lucene query
            safe_name = artist_name.replace('\\', '\\\\').replace('"', '\\"')

            if strict:
                query = f'artist:"{safe_name}"'
            else:
                # Bare query hits alias/artist/sortname indexes — much better
                # recall for user typing. Still Lucene-escaped via the API's
                # query parser.
                query = safe_name

            params = {
                'query': query,
                'fmt': 'json',
                'limit': limit
            }

            response = self._get("/artist", params=params)
            response.raise_for_status()

            data = response.json()
            artists = data.get('artists', [])

            logger.debug(f"Found {len(artists)} artists for query: {artist_name}")
            return artists

        except Exception as e:
            logger.error(f"Error searching for artist '{artist_name}': {e}")
            if raise_on_error:
                raise
            return []
    
    def search_release(self, album_name: str, artist_name: Optional[str] = None,
                       limit: int = 10, strict: bool = True) -> List[Dict[str, Any]]:
        """
        Search for releases (albums) by name.

        Args:
            album_name: Name of the album to search for
            artist_name: Optional artist name to narrow search
            limit: Maximum number of results to return
            strict: When True (default), builds a phrase-match Lucene query
                against the `release` and `artist` fields — correct for
                enrichment flows where exact name+artist are known. When
                False, sends a bare query (album + artist joined) so MB
                hits alias / sortname indexes and folds diacritics,
                dramatically improving recall for user-facing fuzzy
                lookups (e.g. the manual Fix popup).

        Returns:
            List of release results
        """
        try:
            if strict:
                # Escape quotes and backslashes for Lucene query
                safe_album = album_name.replace('\\', '\\\\').replace('"', '\\"')
                query = f'release:"{safe_album}"'

                if artist_name:
                    safe_artist = artist_name.replace('\\', '\\\\').replace('"', '\\"')
                    query += f' AND artist:"{safe_artist}"'
            else:
                # Loose title terms (no phrase quotes → diacritic folding and
                # alias/sortname recall, e.g. "Bjork" → "Björk"), but
                # FIELD-SCOPE the artist so it constrains rather than floating
                # as a free fuzzy term that lets unrelated releases whose titles
                # echo the artist name rank first (same root cause as #754).
                query = album_name
                if artist_name and artist_name.strip():
                    query += f' AND artist:({_escape_lucene(artist_name.strip())})'

            params = {
                'query': query,
                'fmt': 'json',
                'limit': limit
            }
            
            response = self._get("/release", params=params)
            response.raise_for_status()
            
            data = response.json()
            releases = data.get('releases', [])
            
            logger.debug(f"Found {len(releases)} releases for query: {album_name}")
            return releases
            
        except Exception as e:
            logger.error(f"Error searching for release '{album_name}': {e}")
            return []
    
    def search_recording(self, track_name: str, artist_name: Optional[str] = None,
                         limit: int = 10, strict: bool = True) -> List[Dict[str, Any]]:
        """
        Search for recordings (tracks) by name.

        Args:
            track_name: Name of the track to search for
            artist_name: Optional artist name to narrow search
            limit: Maximum number of results to return
            strict: When True (default), builds a phrase-match Lucene query
                against the `recording` and `artist` fields — correct for
                enrichment flows where exact name+artist are known. When
                False, sends a bare query (track + artist joined) so MB
                hits alias / sortname indexes and folds diacritics. The
                bare path also avoids the AND-clause that kills recall
                when either side mis-matches (e.g. "Bjork" vs canonical
                "Björk", or a track title with bracketed suffix like
                "(Live)" that strict phrase match rejects).

        Returns:
            List of recording results
        """
        try:
            if strict:
                # Escape quotes and backslashes for Lucene query
                safe_track = track_name.replace('\\', '\\\\').replace('"', '\\"')
                query = f'recording:"{safe_track}"'

                if artist_name:
                    safe_artist = artist_name.replace('\\', '\\\\').replace('"', '\\"')
                    query += f' AND artist:"{safe_artist}"'
            else:
                # Loose track terms (no phrase quotes → tolerant of bracketed
                # suffixes and diacritics, hitting MB's alias/sortname indexes),
                # but FIELD-SCOPE the artist so it actually constrains results.
                # A bare "track artist" blob let the artist float as a free
                # fuzzy term, so covers/karaoke whose TITLES contain the artist
                # name outranked the real recording (#754: "Sweet Child O Mine"
                # / "Guns N Roses" returned only covers). Scoping still folds
                # diacritics — artist:(Bjork) matches "Björk".
                query = track_name
                if artist_name and artist_name.strip():
                    query += f' AND artist:({_escape_lucene(artist_name.strip())})'

            params = {
                'query': query,
                'fmt': 'json',
                'limit': limit
            }

            response = self._get("/recording", params=params)
            response.raise_for_status()
            
            data = response.json()
            recordings = data.get('recordings', [])
            
            logger.debug(f"Found {len(recordings)} recordings for query: {track_name}")
            return recordings
            
        except Exception as e:
            logger.error(f"Error searching for recording '{track_name}': {e}")
            return []
    
    def browse_artist_release_groups(self, artist_mbid: str,
                                     release_types: Optional[List[str]] = None,
                                     limit: int = 100,
                                     offset: int = 0) -> List[Dict[str, Any]]:
        """Browse release-groups linked to an artist MBID.

        This is the correct MusicBrainz pattern for "give me this artist's
        discography" — text-based `/release?query=...` search would look at
        release TITLES (matching unrelated releases literally titled after
        the artist name), while browse walks the artist→release-group link
        directly.

        Args:
            artist_mbid: Artist's MusicBrainz ID
            release_types: Filter by primary type — any of 'album', 'single',
                'ep', 'compilation', 'soundtrack', 'live', etc. Combined with
                `|` per MB spec, e.g. `['album', 'ep']` → `type=album|ep`.
                None returns all types.
            limit: 1-100 (MB hard cap)
            offset: Pagination offset

        Returns:
            List of release-group dicts. Each has `id`, `title`, `primary-type`,
            `secondary-types`, `first-release-date`, `disambiguation`.
        """
        try:
            params = {'artist': artist_mbid, 'fmt': 'json', 'limit': min(limit, 100), 'offset': offset}
            if release_types:
                params['type'] = '|'.join(release_types)

            response = self._get("/release-group", params=params)
            response.raise_for_status()

            data = response.json()
            rgs = data.get('release-groups', [])
            logger.debug(f"Browsed {len(rgs)} release-groups for artist {artist_mbid}")
            return rgs
        except Exception as e:
            logger.error(f"Error browsing release-groups for artist {artist_mbid}: {e}")
            return []

    def browse_release_group_releases(self, release_group_mbid: str,
                                      limit: int = 100,
                                      offset: int = 0) -> List[Dict[str, Any]]:
        """Browse concrete releases that belong to a release-group.

        Release-groups identify the logical album; releases identify the
        actual edition the user may own (country, format, explicit/clean
        disambiguation, bonus tracks, track count). Manual import needs the
        latter so users can choose the matching tracklist.
        """
        try:
            params = {
                'release-group': release_group_mbid,
                'fmt': 'json',
                'limit': min(limit, 100),
                'offset': offset,
                'inc': 'artist-credits+media+labels+release-groups',
            }

            response = self._get("/release", params=params)
            response.raise_for_status()

            data = response.json()
            releases = data.get('releases', [])
            logger.debug(f"Browsed {len(releases)} releases for release-group {release_group_mbid}")
            return releases
        except Exception as e:
            logger.error(f"Error browsing releases for release-group {release_group_mbid}: {e}")
            return []

    def search_labels(self, label_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search record labels by name (labels feature). Each hit has `id`
        (MBID), `name`, `disambiguation`, `type` (imprint/production/etc.),
        `area`, `life-span`. Additive — no existing path calls this."""
        name = str(label_name or '').strip()
        if not name:
            return []
        try:
            response = self._get("/label", params={'query': f'label:"{_escape_lucene(name)}"', 'fmt': 'json',
                        'limit': min(limit, 25)})
            response.raise_for_status()
            return response.json().get('labels', [])
        except Exception as e:
            logger.error(f"Error searching labels for '{name}': {e}")
            return []

    def browse_label_releases(self, label_mbid: str, limit: int = 100,
                              offset: int = 0) -> List[Dict[str, Any]]:
        """Browse releases put out by a label (labels feature). Each release
        carries `artist-credit` (the REAL artist — never the label) and a
        `release-group` (the logical album, for collapsing editions). The
        caller collapses by release-group id to get distinct albums."""
        mbid = str(label_mbid or '').strip()
        if not mbid:
            return []
        try:
            response = self._get("/release", params={'label': mbid, 'fmt': 'json', 'limit': min(limit, 100),
                        'offset': offset, 'inc': 'artist-credits+release-groups'})
            response.raise_for_status()
            return response.json().get('releases', [])
        except Exception as e:
            logger.error(f"Error browsing releases for label {mbid}: {e}")
            return []

    def search_recordings_by_artist_mbid(self, artist_mbid: str,
                                         limit: int = 100) -> List[Dict[str, Any]]:
        """Search for recordings linked to an artist via Lucene `arid:` query.

        This is the counterpart to `browse_artist_release_groups` for tracks.
        The proper "browse" endpoint (`/recording?artist=<mbid>`) rejects
        `inc=releases`, so we can't get album context per recording from
        browse — only the track title/length/MBID. Without release info the
        user would see tracks with no album, which is useless.

        The search endpoint with a fielded `arid:<mbid>` query returns
        recordings with the `releases` array already embedded (including
        release-group, date, and media info), which is what the search-tab
        UI needs.

        Args:
            artist_mbid: Artist's MusicBrainz ID
            limit: 1-100 (MB hard cap)

        Returns:
            List of recording dicts with `id`, `title`, `length`, `score`,
            `artist-credit`, and `releases` (each with release-group + date).
        """
        try:
            params = {
                'query': f'arid:{artist_mbid}',
                'fmt': 'json',
                'limit': min(limit, 100),
            }

            response = self._get("/recording", params=params)
            response.raise_for_status()

            data = response.json()
            recs = data.get('recordings', [])
            logger.debug(f"Found {len(recs)} recordings for artist {artist_mbid}")
            return recs
        except Exception as e:
            logger.error(f"Error searching recordings for artist {artist_mbid}: {e}")
            return []

    def get_artist(self, mbid: str, includes: Optional[List[str]] = None,
                   raise_on_error: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get full artist details by MusicBrainz ID
        
        Args:
            mbid: MusicBrainz ID of the artist
            includes: Optional list of additional data to include (e.g., 'url-rels', 'genres')
            raise_on_error: Re-raise a transport failure instead of collapsing
                it into the same ``None`` a genuine miss returns. See
                :meth:`search_artist` — a caller that caches the answer has to
                be able to tell those apart.
            
        Returns:
            Artist data or None if not found
        """
        try:
            params = {'fmt': 'json'}
            if includes:
                params['inc'] = '+'.join(includes)
            
            response = self._get(f"/artist/{mbid}", params=params)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching artist {mbid}: {e}")
            if raise_on_error:
                raise
            return None
    
    def get_release(self, mbid: str, includes: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Get full release details by MusicBrainz ID
        
        Args:
            mbid: MusicBrainz ID of the release
            includes: Optional list of additional data to include
            
        Returns:
            Release data or None if not found
        """
        try:
            params = {'fmt': 'json'}
            if includes:
                params['inc'] = '+'.join(includes)
            
            response = self._get(f"/release/{mbid}", params=params)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching release {mbid}: {e}")
            return None
    
    def get_release_group(self, mbid: str, includes: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Get full release-group details by MBID.

        Release-groups are the 'canonical album' entity in MusicBrainz —
        they group every edition/reissue/region-specific release of the
        same logical album under one MBID. Use `inc=releases` to list the
        individual releases this group contains (each with its own
        tracklist); use `inc=artist-credits` for artist info.

        Args:
            mbid: Release-group's MusicBrainz ID
            includes: Optional list, e.g. ['releases', 'artist-credits']

        Returns:
            Release-group data or None if not found.
        """
        try:
            params = {'fmt': 'json'}
            if includes:
                params['inc'] = '+'.join(includes)
            response = self._get(f"/release-group/{mbid}", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching release-group {mbid}: {e}")
            return None

    def get_recording(self, mbid: str, includes: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        Get full recording details by MusicBrainz ID
        
        Args:
            mbid: MusicBrainz ID of the recording
            includes: Optional list of additional data to include
            
        Returns:
            Recording data or None if not found
        """
        try:
            params = {'fmt': 'json'}
            if includes:
                params['inc'] = '+'.join(includes)
            
            response = self._get(f"/recording/{mbid}", params=params)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.error(f"Error fetching recording {mbid}: {e}")
            return None
