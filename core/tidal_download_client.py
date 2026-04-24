"""
Tidal Download Client
Alternative music download source using tidalapi.

This client provides:
- Tidal search with metadata
- Device flow authentication (link.tidal.com)
- HiRes/Lossless/High quality audio downloads
- Drop-in replacement compatible with Soulseek interface
"""

import os
import re
import asyncio
import uuid
import threading
import shutil
import subprocess
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime, timezone

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


# Quality tier definitions
QUALITY_MAP = {
    'low': {
        'tidal_quality': 'LOW' if tidalapi is None else None,  # set dynamically
        'label': 'AAC 96kbps',
        'extension': 'm4a',
        'bitrate': 96,
        'codec': 'aac',
    },
    'high': {
        'tidal_quality': 'HIGH' if tidalapi is None else None,
        'label': 'AAC 320kbps',
        'extension': 'm4a',
        'bitrate': 320,
        'codec': 'aac',
    },
    'lossless': {
        'tidal_quality': 'LOSSLESS' if tidalapi is None else None,
        'label': 'FLAC 16-bit/44.1kHz',
        'extension': 'flac',
        'bitrate': 1411,
        'codec': 'flac',
    },
    'hires': {
        'tidal_quality': 'HI_RES_LOSSLESS' if tidalapi is None else None,
        'label': 'FLAC 24-bit/96kHz',
        'extension': 'flac',
        'bitrate': 9216,
        'codec': 'flac',
    },
}

# Initialize quality map with actual tidalapi constants if available
if tidalapi is not None:
    QUALITY_MAP['low']['tidal_quality'] = tidalapi.Quality.low_96k
    QUALITY_MAP['high']['tidal_quality'] = tidalapi.Quality.low_320k
    QUALITY_MAP['lossless']['tidal_quality'] = tidalapi.Quality.high_lossless
    QUALITY_MAP['hires']['tidal_quality'] = tidalapi.Quality.hi_res_lossless


# Ordering of Tidal's audioQuality values, worst to best. Used to accept
# tier upgrades (Tidal serving higher than the user asked) while still
# rejecting downgrades. Values are the strings tidalapi's `Quality` enum
# exposes — and the strings Tidal's API returns in the `audioQuality`
# field. `HI_RES` (legacy MQA) isn't in the modern `Quality` enum but
# may still come back for old catalog tracks; we rank it below
# `HI_RES_LOSSLESS` so it's treated as a downgrade when the user asked
# for true HiRes lossless.
_QUALITY_RANK = {
    'LOW': 1,
    'HIGH': 2,
    'LOSSLESS': 3,
    'HI_RES': 4,
    'HI_RES_LOSSLESS': 5,
}


def _verify_stream_tier(stream, q_info: dict, q_key: str) -> Tuple[bool, Optional[str]]:
    """Return ``(True, None)`` when the tier Tidal actually served is
    acceptable (same as requested, or a higher tier), ``(False, reason)``
    when Tidal silently downgraded.

    Tidal's API degrades quality without raising: ask for HI_RES_LOSSLESS
    on a track that's only in LOW_320K and you get LOW_320K back with no
    error. The downloader used to accept that and write the resulting
    AAC file, which defeated "HiRes only" with no fallback and made the
    worker's fallback chain ineffective (every tier "succeeded" at the
    first one that returned anything).

    We accept upgrades because Tidal occasionally serves a higher tier
    than requested on tracks flagged as such in its catalog — rejecting
    a higher quality than asked for would be user-hostile.

    Defensive paths:
    - No ``audio_quality`` on the stream (older tidalapi builds): pass
      through, let the pre-existing codec / file-size guards decide.
    - QUALITY_MAP entry without ``tidal_quality`` (tidalapi wasn't
      importable at module load): pass through for the same reason.
    - Unrecognized served quality value (new Tidal tier we haven't
      mapped yet): reject, surfacing a "can't verify" reason so the
      next tier gets a chance or the final diagnostic names the
      unknown value.
    """
    served = getattr(stream, 'audio_quality', None)
    expected = q_info.get('tidal_quality')
    if served is None or expected is None:
        return True, None

    # Both sides may be enum instances (str subclass) or plain strings;
    # coerce to str to compare values only.
    served_str = str(served)
    expected_str = str(expected)

    if served_str == expected_str:
        return True, None

    served_rank = _QUALITY_RANK.get(served_str)
    expected_rank = _QUALITY_RANK.get(expected_str)

    if expected_rank is None:
        # Shouldn't happen — every entry in QUALITY_MAP resolves to a
        # known tier. If it does, don't reject valid downloads.
        return True, None

    if served_rank is None:
        return False, (
            f"{q_key}: Tidal returned unrecognized audioQuality "
            f"'{served_str}' — can't verify the tier matches '{expected_str}'"
        )

    if served_rank >= expected_rank:
        return True, None

    return False, (
        f"{q_key}: Tidal served '{served_str}' instead of "
        f"'{expected_str}' — account tier, track licensing, "
        f"or region doesn't permit {q_key} for this track"
    )


class TidalDownloadClient:
    """
    Tidal download client using tidalapi.
    Provides search, matching, and download capabilities as a drop-in alternative to YouTube/Soulseek.
    """

    def __init__(self, download_path: str = None):
        if tidalapi is None:
            logger.warning("tidalapi not installed — Tidal downloads unavailable")

        # Use Soulseek download path for consistency (post-processing expects files here)
        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')

        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Tidal download client using download path: {self.download_path}")

        # Callback for shutdown check (avoids circular imports)
        self.shutdown_check = None

        # tidalapi session
        self.session: Optional['tidalapi.Session'] = None
        self._init_session()

        # Download queue management (mirrors YouTube's download tracking)
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._download_lock = threading.Lock()

        # Device auth state
        self._device_auth_future = None
        self._device_auth_link = None

    def set_shutdown_check(self, check_callable):
        """Set a callback function to check for system shutdown"""
        self.shutdown_check = check_callable

    # ===================== Auth =====================

    def _init_session(self):
        """Create a tidalapi session and try to restore saved tokens."""
        if tidalapi is None:
            return

        self.session = tidalapi.Session()

        # Try to restore saved session
        saved = config_manager.get('tidal_download.session', {})
        token_type = saved.get('token_type', '')
        access_token = saved.get('access_token', '')
        refresh_token = saved.get('refresh_token', '')
        expiry_time = saved.get('expiry_time', 0)

        if token_type and access_token:
            try:
                # Convert stored float timestamp back to datetime for tidalapi
                expiry_dt = datetime.fromtimestamp(expiry_time, tz=timezone.utc) if expiry_time else None

                # tidalapi's load_oauth_session restores from saved tokens
                restored = self.session.load_oauth_session(
                    token_type=token_type,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expiry_time=expiry_dt,
                )
                if restored and self.session.check_login():
                    logger.info("Restored Tidal download session from saved tokens")
                    self._save_session()  # refresh may have rotated tokens
                    return
                else:
                    logger.warning("Saved Tidal session tokens are invalid/expired")
            except Exception as e:
                logger.warning(f"Could not restore Tidal session: {e}")

    def _save_session(self):
        """Persist session tokens to config."""
        if not self.session:
            return
        config_manager.set('tidal_download.session', {
            'token_type': self.session.token_type or '',
            'access_token': self.session.access_token or '',
            'refresh_token': self.session.refresh_token or '',
            'expiry_time': self.session.expiry_time.timestamp() if self.session.expiry_time else 0,
        })

    def is_authenticated(self) -> bool:
        """Check if we have a valid Tidal session."""
        if not self.session:
            return False
        try:
            return self.session.check_login()
        except Exception:
            return False

    def start_device_auth(self) -> Optional[Dict[str, str]]:
        """
        Start the device-code OAuth flow.
        Returns dict with 'verification_uri' and 'user_code', or None on error.
        """
        if tidalapi is None:
            return None

        try:
            if not self.session:
                self.session = tidalapi.Session()

            login, future = self.session.login_oauth()
            self._device_auth_future = future
            self._device_auth_link = {
                'verification_uri': login.verification_uri_complete or f"https://link.tidal.com/{login.user_code}",
                'user_code': login.user_code,
            }
            logger.info(f"Tidal device auth started — code: {login.user_code}")
            return self._device_auth_link

        except Exception as e:
            logger.error(f"Failed to start Tidal device auth: {e}")
            return None

    def check_device_auth(self) -> Dict[str, Any]:
        """
        Check if device auth has completed.
        Returns {'status': 'pending'|'completed'|'error', ...}
        """
        if not self._device_auth_future:
            return {'status': 'error', 'message': 'No auth in progress'}

        try:
            if self._device_auth_future.running():
                return {
                    'status': 'pending',
                    'verification_uri': self._device_auth_link.get('verification_uri', ''),
                    'user_code': self._device_auth_link.get('user_code', ''),
                }

            # Future is done — check result
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

    # ===================== Search =====================

    def is_available(self) -> bool:
        """Check if Tidal download client is available (tidalapi installed and authenticated)."""
        return tidalapi is not None and self.is_authenticated()

    def is_configured(self) -> bool:
        """Check if Tidal client is configured and ready (matches Soulseek interface)."""
        return self.is_available()

    async def check_connection(self) -> bool:
        """Test if Tidal is accessible (async, Soulseek-compatible)."""
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.is_available)
        except Exception as e:
            logger.error(f"Tidal connection check failed: {e}")
            return False

    # Words that distinguish a specific audio variant from the original track.
    # If any of these appear in a query, the fallback-retry results must also
    # contain them — otherwise we'd silently downgrade a "(Live)" or
    # "(Acoustic)" search to the studio version when shortened queries match
    # too broadly.
    _QUALIFIER_KEYWORDS = frozenset({
        'remix', 'mix', 'edit', 'version', 'dub', 'rmx', 'vip', 'cut',
        'rework', 'bootleg', 'flip',
        'live', 'concert', 'unplugged', 'acoustic', 'session',
        'instrumental', 'karaoke', 'demo', 'bonus',
        'extended', 'radio',
    })

    @classmethod
    def _extract_qualifiers(cls, query: str) -> List[str]:
        """Return the qualifier keywords that appear as whole words in the
        query (case-insensitive). Word-boundary match prevents false hits like
        "edit" matching "edition" or "mix" matching "remix"."""
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
        """True iff the track name contains every qualifier as a whole word."""
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
        """Generate progressively-shorter variants of a search query.

        Tidal's search engine chokes on long queries with lots of qualifiers
        (remix credits, edit labels, bonus-disc markers). When the original
        returns 0 results, we retry with shortened variants in order of
        conservativeness — the first variant that returns results wins.

        Variants are returned in priority order. Dedupes against the original
        and against previously-added variants so we never issue duplicate
        requests.
        """
        variants: List[str] = []
        seen = {original.strip().lower()}

        def _add(candidate: str) -> None:
            candidate = candidate.strip()
            if candidate and candidate.lower() not in seen:
                variants.append(candidate)
                seen.add(candidate.lower())

        # 1. Strip a trailing parenthetical/bracketed suffix.
        #    "Song (Radio Edit)" → "Song"
        _add(re.sub(r'\s*[\(\[][^\)\]]*[\)\]]\s*$', '', original))

        # 2. Strip ALL parentheticals/brackets (mid-string too).
        #    "Song (feat X) [Remix]" → "Song"
        _add(re.sub(r'\s*[\(\[][^\)\]]*[\)\]]', ' ', original))

        tokens = original.split()

        # 3. Drop the last token — covers trailing 1-word modifiers
        #    (e.g. "… Remix", "… Extended").
        if len(tokens) >= 3:
            _add(' '.join(tokens[:-1]))

        # 4. Drop the last two tokens.
        if len(tokens) >= 4:
            _add(' '.join(tokens[:-2]))

        # 5. Drop the last three tokens — covers "fred v remix" style
        #    3-word modifiers common in remix/bonus track names.
        if len(tokens) >= 5:
            _add(' '.join(tokens[:-3]))

        # 6. Aggressive: keep roughly the first half (rounded up).
        if len(tokens) >= 7:
            _add(' '.join(tokens[:len(tokens) // 2 + 1]))

        return variants

    async def search(self, query: str, timeout: int = None, progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """
        Search Tidal for tracks (async, Soulseek-compatible interface).

        Returns:
            Tuple of (track_results, album_results). Album results always empty.
        """
        if not self.is_available():
            logger.warning("Tidal not available for search (not authenticated)")
            return ([], [])

        # Defensive guard — None/empty query would blow up the shortener's
        # .strip() call. Match the original behaviour (log + empty tuple).
        if not query or not isinstance(query, str):
            logger.warning(f"Invalid Tidal search query: {query!r}")
            return ([], [])

        logger.info(f"Searching Tidal for: {query}")

        # Outer try/except preserves the original contract: any unexpected
        # error returns ([], []) so the caller can fall back to other sources
        # instead of crashing. Traceback is logged once, not per-attempt.
        try:
            # Build the retry ladder: original query, then progressively-shortened
            # variants. Capped at 5 total requests to avoid hammering Tidal on
            # genuinely-missing tracks, while still allowing enough variants to
            # cover multi-word trailing modifiers like remix credits.
            queries_to_try = [query] + self._generate_shortened_queries(query)
            queries_to_try = queries_to_try[:5]

            # Qualifier-aware safety net: if the original query contains variant
            # keywords (Live, Remix, Acoustic, Extended, etc.), fallback results
            # MUST still contain those qualifiers in their track names. Otherwise
            # a shortened query could silently downgrade "Song (Live)" to the
            # studio "Song" and the caller would download the wrong variant.
            required_qualifiers = self._extract_qualifiers(query)

            tidal_tracks: list = []
            successful_query: Optional[str] = None
            last_error: Optional[Exception] = None
            # Tracks whether ANY fallback attempt returned broader matches that
            # got rejected by the qualifier filter — used to give an accurate
            # "no qualifier-matching variant" log message at the end instead of
            # a generic "0 results".
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
                        # Fallback attempts get qualifier-filtered. We trust the
                        # original query to return only appropriate matches, but
                        # shortened queries are more permissive and can return
                        # wrong-variant tracks (e.g. studio when Live was asked
                        # for). Drop any result whose title doesn't carry all
                        # original qualifier words.
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
                        # Small pause so we're not hammering Tidal with rapid retries
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

            # Get configured quality for display
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
            # Unhandled error in the retry orchestration itself (not in an
            # individual attempt, which is already caught above). Preserves
            # the original contract of returning ([], []) on any failure so
            # the caller's fallback chain isn't broken.
            logger.error(f"Tidal search orchestration failed: {e}")
            import traceback
            traceback.print_exc()
            return ([], [])

    def _tidal_to_track_result(self, track, quality_info: dict) -> TrackResult:
        """Convert tidalapi Track to TrackResult (Soulseek-compatible format)."""
        artist_name = track.artist.name if track.artist else 'Unknown Artist'
        title = track.name or 'Unknown Title'
        album_name = track.album.name if track.album else None

        # Duration in milliseconds
        duration_ms = int(track.duration * 1000) if track.duration else None

        # Encode track_id in filename (same pattern as YouTube: "id||display_name")
        display_name = f"{artist_name} - {title}"
        filename = f"{track.id}||{display_name}"

        track_result = TrackResult(
            username='tidal',
            filename=filename,
            size=0,  # Unknown until download
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
        )

        return track_result

    # ===================== Download =====================

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """
        Download a Tidal track (async, Soulseek-compatible interface).

        Returns download_id immediately and runs download in background thread.

        Args:
            username: Ignored for Tidal (always "tidal")
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
                logger.error(f"Invalid Tidal track ID: {track_id_str}")
                return None

            logger.info(f"Starting Tidal download: {display_name}")

            download_id = str(uuid.uuid4())

            with self._download_lock:
                self.active_downloads[download_id] = {
                    'id': download_id,
                    'filename': filename,  # Keep original encoded format for context matching
                    'username': 'tidal',
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

            logger.info(f"Tidal download {download_id} started in background")
            return download_id

        except Exception as e:
            logger.error(f"Failed to start Tidal download: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _download_thread_worker(self, download_id: str, track_id: int, display_name: str, original_filename: str):
        """Background thread worker for downloading Tidal tracks."""
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

                logger.info(f"Tidal download {download_id} completed: {file_path}")
            else:
                with self._download_lock:
                    if download_id in self.active_downloads:
                        self.active_downloads[download_id]['state'] = 'Errored'

                logger.error(f"Tidal download {download_id} failed")

        except Exception as e:
            logger.error(f"Tidal download thread failed for {download_id}: {e}")
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
        if not self.session or not self.session.check_login():
            logger.error("Tidal session not authenticated")
            return None

        try:
            # Get track object
            track = self.session.track(track_id)
            if not track:
                logger.error(f"Could not fetch Tidal track: {track_id}")
                return None

            # Determine quality
            quality_key = config_manager.get('tidal_download.quality', 'lossless')
            quality_info = QUALITY_MAP.get(quality_key, QUALITY_MAP['lossless'])

            # Try quality fallback chain: hires → lossless → high → low
            # The entire download+validation is inside the loop so that garbage
            # files (stubs, empty HiRes responses) trigger a retry at the next tier.
            quality_chain = ['hires', 'lossless', 'high', 'low']
            start_idx = quality_chain.index(quality_key) if quality_key in quality_chain else 1
            allow_fallback = config_manager.get('tidal_download.allow_fallback', True)
            chain = quality_chain[start_idx:] if allow_fallback else [quality_key]

            MIN_AUDIO_SIZE = 100 * 1024  # 100KB

            quality_error_reasons = []

            for q_key in chain:
                q_info = QUALITY_MAP[q_key]

                # --- Step 1: Get stream ---
                try:
                    self.session.audio_quality = q_info['tidal_quality']
                    stream = track.get_stream()
                    if not stream or not stream.manifest_mime_type:
                        reason = f"{q_key}: no stream returned"
                        logger.warning(f"Quality {q_key} returned no stream, trying next")
                        quality_error_reasons.append(reason)
                        continue

                    ok, reason = _verify_stream_tier(stream, q_info, q_key)
                    if not ok:
                        logger.warning(reason)
                        quality_error_reasons.append(reason)
                        continue

                    logger.info(f"Got Tidal stream at quality: {q_key}")
                except Exception as e:
                    reason = f"{q_key}: {type(e).__name__}: {e}"
                    logger.warning(f"Quality {q_key} unavailable: {e}")
                    quality_error_reasons.append(reason)
                    continue

                # --- Step 2: Parse manifest ---
                manifest = stream.get_stream_manifest()
                urls = manifest.get_urls()
                if not urls:
                    reason = f"{q_key}: manifest returned no URLs"
                    logger.warning(f"No download URLs for quality {q_key}, trying next")
                    quality_error_reasons.append(reason)
                    continue

                download_url = urls[0]

                # Determine file extension from manifest codec (HiRes FLAC
                # can arrive wrapped in MP4 — unwrapped at Step 4).
                codec = manifest.get_codecs()
                if codec and 'flac' in codec.lower():
                    extension = 'flac'
                elif codec and ('mp4a' in codec.lower() or 'aac' in codec.lower()):
                    extension = 'm4a'
                elif codec and 'alac' in codec.lower():
                    extension = 'm4a'
                else:
                    extension = q_info.get('extension', 'flac')

                # Build output filename
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', display_name)
                out_filename = f"{safe_name}.{extension}"
                out_path = self.download_path / out_filename

                # Check for shutdown before downloading
                if self.shutdown_check and self.shutdown_check():
                    logger.info("Server shutting down, aborting Tidal download")
                    return None

                # --- Step 3: Download ---
                try:
                    logger.info(f"Downloading from Tidal ({q_key}): {out_filename}")
                    response = http_requests.get(download_url, stream=True, timeout=120)
                    response.raise_for_status()

                    total_size = int(response.headers.get('content-length', 0))
                    downloaded = 0
                    chunk_size = 64 * 1024  # 64KB chunks

                    with self._download_lock:
                        if download_id in self.active_downloads:
                            self.active_downloads[download_id]['size'] = total_size

                    with open(out_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=chunk_size):
                            if not chunk:
                                continue

                            if self.shutdown_check and self.shutdown_check():
                                logger.info("Server shutting down, aborting Tidal download mid-stream")
                                f.close()
                                out_path.unlink(missing_ok=True)
                                return None

                            f.write(chunk)
                            downloaded += len(chunk)

                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                            else:
                                progress = 0

                            with self._download_lock:
                                if download_id in self.active_downloads:
                                    self.active_downloads[download_id]['transferred'] = downloaded
                                    self.active_downloads[download_id]['progress'] = round(progress, 1)

                except Exception as dl_err:
                    logger.warning(f"Download failed at quality {q_key}: {dl_err}")
                    quality_error_reasons.append(f"{q_key}: download error: {type(dl_err).__name__}: {dl_err}")
                    out_path.unlink(missing_ok=True)
                    continue

                # --- Step 4: Validate ---
                if downloaded < MIN_AUDIO_SIZE:
                    logger.warning(
                        f"Tidal download too small at {q_key} ({downloaded} bytes) — "
                        f"likely a stub/preview for '{display_name}'. Trying next quality."
                    )
                    quality_error_reasons.append(f"{q_key}: file too small ({downloaded} bytes), likely a stub")
                    out_path.unlink(missing_ok=True)
                    continue

                # HiRes FLAC in MP4 container: extract raw FLAC with FFmpeg
                if extension == 'flac' and self._is_mp4_container(out_path):
                    extracted = self._extract_flac_from_mp4(out_path)
                    if extracted:
                        out_path = Path(extracted)
                    else:
                        logger.warning(
                            f"Cannot extract FLAC from MP4 container at {q_key} — "
                            f"deleting and trying next quality"
                        )
                        quality_error_reasons.append(f"{q_key}: FLAC extraction from MP4 container failed")
                        out_path.unlink(missing_ok=True)
                        continue

                # Final size check after any extraction
                final_size = out_path.stat().st_size if out_path.exists() else 0
                if final_size < MIN_AUDIO_SIZE:
                    logger.warning(
                        f"Final file too small after processing at {q_key} "
                        f"({final_size} bytes) — trying next quality"
                    )
                    quality_error_reasons.append(f"{q_key}: final file too small after extraction ({final_size} bytes)")
                    out_path.unlink(missing_ok=True)
                    continue

                # Success — file is valid
                logger.info(f"Tidal download complete ({q_key}): {out_path} ({final_size / (1024*1024):.1f} MB)")
                return str(out_path)

            # All quality tiers exhausted — build a diagnostic message
            # Re-use quality_key/allow_fallback already read above to stay consistent
            # with how the chain was built (avoids config-change-mid-download inconsistency).
            reasons_str = '; '.join(quality_error_reasons) if quality_error_reasons else 'unknown'
            if quality_key == 'hires' and not allow_fallback:
                hint = (
                    " HiRes quality is unavailable for this track on your account or in your region. "
                    "Enable 'Quality Fallback' in Tidal settings to fall back to Lossless automatically."
                )
            else:
                hint = ""
            logger.error(
                f"No Tidal quality tier produced a valid download for '{display_name}'."
                f"{hint} Failure reasons: [{reasons_str}]"
            )
            return None

        except Exception as e:
            logger.error(f"Tidal download failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _is_mp4_container(self, filepath: Path) -> bool:
        """Check if a file is actually an MP4 container (HiRes FLAC can be wrapped in MP4)."""
        try:
            with open(filepath, 'rb') as f:
                header = f.read(12)
                # MP4 files have 'ftyp' at offset 4
                return b'ftyp' in header
        except Exception:
            return False

    def _extract_flac_from_mp4(self, mp4_path: Path) -> Optional[str]:
        """Extract FLAC audio from MP4 container using FFmpeg."""
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg:
            # Also check tools directory
            tools_dir = Path(__file__).parent.parent / 'tools'
            ffmpeg_candidate = tools_dir / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
            if ffmpeg_candidate.exists():
                ffmpeg = str(ffmpeg_candidate)
            else:
                logger.warning("FFmpeg not found — cannot extract FLAC from MP4 container")
                return None

        flac_path = mp4_path.with_suffix('.flac')
        temp_path = mp4_path.with_suffix('.tmp.flac')

        try:
            result = subprocess.run(
                [ffmpeg, '-i', str(mp4_path), '-vn', '-acodec', 'copy', str(temp_path), '-y'],
                capture_output=True, text=True, timeout=120,
            )

            if result.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 0:
                mp4_path.unlink(missing_ok=True)
                temp_path.rename(flac_path)
                logger.info(f"Extracted FLAC from MP4 container: {flac_path.name}")
                return str(flac_path)
            else:
                logger.warning(f"FFmpeg extraction failed: {result.stderr[:200] if result.stderr else 'unknown error'}")
                temp_path.unlink(missing_ok=True)
                return None

        except Exception as e:
            logger.warning(f"FFmpeg extraction error: {e}")
            temp_path.unlink(missing_ok=True)
            return None

    # ===================== Status / Cancel / Clear =====================

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """Get all active downloads (matches Soulseek interface)."""
        download_statuses = []

        with self._download_lock:
            for _download_id, info in self.active_downloads.items():
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
                logger.info(f"Marked Tidal download {download_id} as cancelled")

                if remove:
                    del self.active_downloads[download_id]
                    logger.info(f"Removed Tidal download {download_id} from queue")

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
