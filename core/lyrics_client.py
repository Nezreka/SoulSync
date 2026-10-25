#!/usr/bin/env python3

import os
import random
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests

from utils.logging_config import get_logger

logger = get_logger("lyrics_client")

# Transport timeout: (connect_timeout, read_timeout)
# Connect: 3.05s (slightly more than standard 3s SYN retry).
# Read: 10.0s (LRClib is fast; gives a 30x safety margin without holding threads).
DEFAULT_LRCLIB_TIMEOUT = (3.05, 10.0)

# Bounded retries for TRANSIENT LRCLib failures (429 / 5xx / timeouts /
# connection errors) — #1879. A definitive 404 is never retried, and a
# transient failure never falls through to the search fallback: firing a
# second request while rate-limited only digs the hole deeper.
MAX_LRCLIB_ATTEMPTS = 3          # 1 initial try + 2 retries
MAX_RETRY_DELAY_SECONDS = 30.0   # cap on any single Retry-After / backoff wait
MAX_TOTAL_RETRY_WAIT_SECONDS = 20.0  # cap on ALL retry waiting per call, so a
# lookup sharing a gthread with HTTP handlers can never park it for a
# minute-plus behind stacked Retry-After waits (gunicorn timeout is 120s)
# The download/import pipeline fetches lyrics inline for every track. They
# are best effort there (the Lyrics Filler picks up misses later), so a
# pipeline lookup never waits out a shared cooldown: uncapped, the FIRST
# slot wait of every imported track sat out the whole thing, up to 300s.
PIPELINE_MAX_WAIT_SECONDS = 5.0

# Lookup outcomes. The old contract collapsed all three into "no lyrics",
# so a 429 during a Lyrics Filler scan was recorded as a definitive
# negative and the repair worker told the user the lyrics were "no longer
# available" when LRCLib had merely asked us to slow down (#1879).
STATUS_FOUND = 'found'
STATUS_ABSENT = 'absent'         # LRCLib definitively has no lyrics (404 / empty search)
STATUS_UNKNOWN = 'unknown'       # transient or permanent error — NOT a negative


@dataclass
class LyricsFetchResult:
    """Tri-state outcome of an LRCLib lookup (#1879).

    ``status`` is one of STATUS_FOUND / STATUS_ABSENT / STATUS_UNKNOWN.
    ``data`` is the lyrics object when found. ``reason`` is a
    user-presentable explanation for UNKNOWN outcomes (e.g. "LRCLib
    rate-limited (429) — retry later"); ``error_kind`` is the machine
    classification ('rate_limited', 'server', 'timeout', 'network',
    'permanent', 'not_found', 'unavailable').
    """
    status: str
    data: object = None
    reason: str = ''
    error_kind: str = ''
    status_code: int = None
    retry_after: float = None


def _parse_retry_after(headers) -> float:
    """Retry-After header → seconds (delta-seconds or HTTP-date), or None."""
    if not headers:
        return None
    try:
        value = headers.get('Retry-After') or headers.get('retry-after')
    except Exception:
        return None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(str(value))
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        from datetime import datetime
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def classify_lyrics_error(exc) -> LyricsFetchResult:
    """Classify an exception from the lrclib client into a fetch result.

    Duck-typed on purpose: lrclibapi's APIError family carries
    ``status_code`` / ``headers``, requests' transport errors carry
    neither, and callers/tests use fakes — so read the attributes that
    exist and fall back to the exception type/name.

    - 404 / NotFoundError      → ABSENT (definitive, never retried)
    - 429 / RateLimitError     → UNKNOWN, transient (Retry-After honoured)
    - 5xx / ServerError        → UNKNOWN, transient
    - timeout / connection err → UNKNOWN, transient
    - any other 4xx            → UNKNOWN, permanent query error (still NOT
                                 a definitive negative: the lyrics may
                                 exist, the query was rejected)
    - anything else            → UNKNOWN, permanent
    """
    status_code = getattr(exc, 'status_code', None)
    headers = getattr(exc, 'headers', None)
    response = getattr(exc, 'response', None)
    if status_code is None and response is not None:
        status_code = getattr(response, 'status_code', None)
    if headers is None and response is not None:
        headers = getattr(response, 'headers', None)
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    retry_after = _parse_retry_after(headers)
    name = type(exc).__name__

    if status_code == 404 or name == 'NotFoundError':
        return LyricsFetchResult(STATUS_ABSENT, error_kind='not_found',
                                 status_code=404, reason='LRCLib has no lyrics for this track')
    if status_code == 429 or name == 'RateLimitError':
        return LyricsFetchResult(STATUS_UNKNOWN, error_kind='rate_limited', status_code=429,
                                 retry_after=retry_after,
                                 reason='LRCLib rate-limited (429) — retry later')
    if (status_code is not None and 500 <= status_code < 600) or name == 'ServerError':
        return LyricsFetchResult(STATUS_UNKNOWN, error_kind='server', status_code=status_code,
                                 retry_after=retry_after,
                                 reason=f'LRCLib server error ({status_code}) — retry later'
                                        if status_code else 'LRCLib server error — retry later')
    if isinstance(exc, requests.exceptions.Timeout) or name in ('ReadTimeout', 'ConnectTimeout', 'Timeout'):
        return LyricsFetchResult(STATUS_UNKNOWN, error_kind='timeout',
                                 reason='LRCLib request timed out — retry later')
    if isinstance(exc, requests.exceptions.ConnectionError) or name == 'ConnectionError':
        return LyricsFetchResult(STATUS_UNKNOWN, error_kind='network',
                                 reason='Could not reach LRCLib — retry later')
    if status_code is not None and 400 <= status_code < 500:
        return LyricsFetchResult(STATUS_UNKNOWN, error_kind='permanent', status_code=status_code,
                                 reason=f'LRCLib rejected the request ({status_code})')
    # No exception text in the reason: it is user-presentable, and a raw
    # transport error can embed the request URL (query terms included).
    # The full exception is logged at the call site instead.
    return LyricsFetchResult(STATUS_UNKNOWN, error_kind='permanent', status_code=status_code,
                             reason='LRCLib lookup failed')


_TRANSIENT_KINDS = frozenset({'rate_limited', 'server', 'timeout', 'network'})


class TimeoutSession(requests.Session):
    """requests.Session that injects default transport timeouts if not specified."""

    def __init__(self, timeout=DEFAULT_LRCLIB_TIMEOUT):
        super().__init__()
        self.default_timeout = timeout

    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.default_timeout)
        return super().request(method, url, **kwargs)


class LyricsClient:
    """
    Minimal, elegant LRClib client for automatic lyrics fetching.
    Generates .lrc sidecar files during post-processing.

    All LRCLib traffic goes through here: every call is paced by
    core.lrclib_throttle (one process-wide budget), transient failures
    (429/5xx/timeouts) are retried a bounded number of times honouring
    Retry-After, and outcomes are tri-state (found / absent / unknown)
    so a rate limit is never mistaken for "no lyrics" (#1879).
    """

    def __init__(self):
        self.api = None
        # Test seams (also handy for embedders): instances built via
        # __new__ in tests skip __init__, so readers use getattr defaults.
        self._sleep = time.sleep
        self.max_attempts = MAX_LRCLIB_ATTEMPTS
        self._init_api()

    def _init_api(self):
        """Initialize LRClib API with graceful fallback and transport timeouts."""
        try:
            from lrclib import LrcLibAPI
            session = TimeoutSession()
            # Identifying User-Agent WITH the project link — LRCLib's
            # higher allowance tier is for clients it can identify (#1879).
            self.api = LrcLibAPI(user_agent="SoulSync/1.0 (https://github.com/Nezreka/SoulSync)",
                                 session=session)
            logger.debug("LRClib API client initialized with transport timeouts")
        except ImportError:
            logger.warning("LRClib API not available - lyrics functionality disabled")
            self.api = None
        except Exception as e:
            logger.error(f"Error initializing LRClib API: {e}")
            self.api = None

    # ── low-level call: pacing + bounded transient retry ─────────────────

    def _call_lrclib(self, description, fn, max_wait_seconds=None):
        """Run one LRCLib call with pacing and bounded transient retries.

        Returns ``(data, None)`` on success or ``(None, LyricsFetchResult)``
        describing the failure outcome. ABSENT (404) and permanent errors
        are never retried; transient errors retry up to ``max_attempts``
        total tries — subject to the shared throttle cooldown and the
        per-call wait budget below, either of which can cut the retries
        short (a headerless 429 sets a 30s cooldown against a 20s
        budget, so in practice that call returns after one attempt and
        the NEXT caller is the one that retries).

        The wait budget is measured in ELAPSED time (monotonic), not just
        explicit backoff sleeps: a retry's throttle wait can include a
        shared rate-limit cooldown set by another caller, and that wait
        parks this thread just the same. Once elapsed time exceeds
        MAX_TOTAL_RETRY_WAIT_SECONDS the transient outcome is returned
        instead of retrying again.

        ``max_wait_seconds`` is for interactive callers (the player
        endpoint): when the throttle cannot offer a slot within that
        wait, no request is fired at all and a busy UNKNOWN comes back,
        rather than parking an HTTP worker for the length of a cooldown
        (which can exceed the 120s gunicorn timeout).
        """
        from core import lrclib_throttle
        attempts = max(1, int(getattr(self, 'max_attempts', MAX_LRCLIB_ATTEMPTS) or 1))
        sleeper = getattr(self, '_sleep', None) or time.sleep
        started = time.monotonic()
        last = None
        for attempt in range(attempts):
            elapsed = time.monotonic() - started
            if attempt > 0 and elapsed > MAX_TOTAL_RETRY_WAIT_SECONDS:
                logger.warning("%s %s — elapsed wait budget (%.0fs) exhausted after "
                               "%d attempt(s); giving up for now",
                               description, last.error_kind if last else 'transient',
                               MAX_TOTAL_RETRY_WAIT_SECONDS, attempt)
                return None, last
            # Cap the throttle wait itself by the REMAINING per-call
            # budget on retries: checking the budget before an uncapped
            # wait would still let a long shared cooldown park this
            # thread well past it (the check passes at ~1.5s elapsed,
            # then wait_for_slot sits out a 30s cooldown).
            wait_cap = max_wait_seconds
            if attempt > 0:
                remaining = MAX_TOTAL_RETRY_WAIT_SECONDS - elapsed
                wait_cap = remaining if wait_cap is None else min(wait_cap, remaining)
            if not lrclib_throttle.wait_for_slot(max_wait_seconds=wait_cap):
                if attempt > 0 and last is not None:
                    # A retry whose slot lies past the wait cap (the
                    # per-call budget or the caller's own cap): report
                    # the real transient outcome, not a busy.
                    logger.warning("%s %s — next slot is past the %.0fs wait cap; "
                                   "giving up for now",
                                   description, last.error_kind, wait_cap)
                    return None, last
                # Interactive caller and the next slot is too far away —
                # say busy, don't park. Name the actual constraint: a
                # rate-limit cooldown if one is in effect, otherwise the
                # shared request window is simply full.
                if lrclib_throttle.status()['cooldown_remaining'] > 0:
                    busy_reason = 'LRCLib busy (rate-limit cooldown) — retry later'
                else:
                    busy_reason = 'LRCLib busy (request budget full) — retry later'
                return None, LyricsFetchResult(
                    STATUS_UNKNOWN, error_kind='rate_limited',
                    reason=busy_reason)
            try:
                return fn(), None
            except Exception as e:
                outcome = classify_lyrics_error(e)
                if outcome.status == STATUS_ABSENT or outcome.error_kind not in _TRANSIENT_KINDS:
                    if outcome.status != STATUS_ABSENT:
                        logger.debug("%s failed (%s): %s", description, outcome.error_kind, e)
                    return None, outcome
                last = outcome
                # LRCLib explicitly pushed back — every caller backs off
                # together, not just this one. A 429 earns the shared
                # cooldown its Retry-After asks for (30s default); a lone
                # 5xx without one earns only the floor, so one hiccup
                # doesn't park every caller for half a minute.
                if outcome.error_kind == 'rate_limited':
                    lrclib_throttle.note_rate_limited(outcome.retry_after)
                elif outcome.error_kind == 'server':
                    lrclib_throttle.note_rate_limited(
                        outcome.retry_after if outcome.retry_after is not None else 5)
                if attempt + 1 >= attempts:
                    logger.warning("%s failed after %d attempts (%s): %s",
                                   description, attempts, outcome.error_kind, e)
                    return None, outcome
                if outcome.retry_after is not None:
                    # Honour the server's Retry-After exactly (capped) —
                    # no jitter on top of an explicit instruction.
                    delay = min(outcome.retry_after, MAX_RETRY_DELAY_SECONDS)
                else:
                    delay = min(2.0 ** attempt, MAX_RETRY_DELAY_SECONDS)
                    delay += random.uniform(0, 0.5)
                elapsed_now = time.monotonic() - started
                cooldown_left = lrclib_throttle.status()['cooldown_remaining']
                wait_limit = MAX_TOTAL_RETRY_WAIT_SECONDS
                if max_wait_seconds is not None:
                    wait_limit = min(wait_limit, max_wait_seconds)
                if elapsed_now + delay > wait_limit \
                        or elapsed_now + cooldown_left > wait_limit:
                    # Waiting longer would park this thread past the
                    # wait limit — either the backoff itself is too
                    # long, or the shared cooldown this failure just set
                    # (30s default for a headerless 429) already runs
                    # past it, in which case the retry's slot wait would
                    # be refused anyway and the backoff sleep is wasted.
                    # Report the transient outcome now; the caller can
                    # retry later.
                    logger.warning("%s %s (attempt %d/%d) — retry wait (backoff %.1fs, "
                                   "cooldown %.1fs) would exceed the %.0fs wait limit; "
                                   "giving up for now",
                                   description, outcome.error_kind, attempt + 1, attempts,
                                   delay, cooldown_left, wait_limit)
                    return None, outcome
                logger.debug("%s %s (attempt %d/%d) — retrying in %.1fs",
                             description, outcome.error_kind, attempt + 1, attempts, delay)
                sleeper(delay)
        return None, last  # pragma: no cover - loop always returns

    # ── fetch strategy: exact match, then search — tri-state ────────────

    def lookup_remote_lyrics(self, track_name: str, artist_name: str,
                             album_name: str = None, duration_seconds: int = None,
                             max_wait_seconds: float = None) -> LyricsFetchResult:
        """LRClib fetch — exact match (with duration) then search fallback.

        Tri-state (#1879): FOUND carries the lyrics object, ABSENT means
        LRCLib definitively has nothing (exact 404 AND search empty), and
        UNKNOWN means the lookup could not be completed (429/5xx/timeout/
        permanent query error) — callers must not record UNKNOWN as a
        negative. A transient exact-match failure does NOT fall through
        to search: a second request while rate-limited makes it worse.
        A permanent exact-match failure (e.g. a 400 for an empty artist
        name) DOES fall through, as the old code did: search takes
        different parameters and may still hit. ``max_wait_seconds``
        is forwarded to the throttle for interactive callers.
        """
        if not self.api:
            return LyricsFetchResult(STATUS_UNKNOWN, error_kind='unavailable',
                                     reason='LRCLib API not available')
        # Strategy 1: Exact match with duration (most accurate)
        if duration_seconds and album_name:
            data, failure = self._call_lrclib(
                'Exact match',
                lambda: self.api.get_lyrics(
                    track_name=track_name, artist_name=artist_name,
                    album_name=album_name, duration=duration_seconds),
                max_wait_seconds=max_wait_seconds)
            if data is not None:
                return LyricsFetchResult(STATUS_FOUND, data=data)
            if failure is not None and failure.status != STATUS_ABSENT \
                    and failure.error_kind != 'permanent':
                # Transient failure (429/5xx/timeout, incl. a busy
                # refusal for an interactive caller): do NOT fire the
                # search fallback — a second request while LRCLib is
                # pushing back makes it worse, and a busy refusal means
                # the caller already declined to wait for a slot.
                return failure
            # Fall through to search on: ABSENT (404), a permanent query
            # error (400 — search takes different params and may hit),
            # or a None-returning client (failure is None).
            exact_failure = failure
        else:
            exact_failure = None
        # Strategy 2: Search without duration
        results, failure = self._call_lrclib(
            'Search fallback',
            lambda: self.api.search_lyrics(
                track_name=track_name, artist_name=artist_name),
            max_wait_seconds=max_wait_seconds)
        if failure is not None:
            return failure
        if results:
            return LyricsFetchResult(STATUS_FOUND, data=results[0])
        if exact_failure is not None and exact_failure.error_kind == 'permanent':
            # The exact query was REJECTED (400) and search found
            # nothing. That is not proof LRCLib has no lyrics; the
            # query itself was broken (e.g. an empty artist name).
            # Stay UNKNOWN so the track is not recorded as definitively
            # lyric-less. Deliberately conservative: if the 400 was
            # caused by the album/duration (which search doesn't send),
            # the track stays UNKNOWN and is simply re-probed on the
            # next scan — extra traffic, but never a false negative.
            return exact_failure
        return LyricsFetchResult(STATUS_ABSENT, error_kind='not_found',
                                 reason='LRCLib has no lyrics for this track')

    def _fetch_remote_lyrics(self, track_name: str, artist_name: str,
                             album_name: str = None, duration_seconds: int = None):
        """Back-compat wrapper: the lyrics_data object or None.

        None covers BOTH absent and unknown — new callers should use
        lookup_remote_lyrics() / has_remote_lyrics() so a transient
        failure is not mistaken for a definitive negative (#1879).
        """
        result = self.lookup_remote_lyrics(track_name, artist_name, album_name, duration_seconds)
        return result.data if result.status == STATUS_FOUND else None

    def check_remote_lyrics(self, track_name: str, artist_name: str,
                            album_name: str = None, duration_seconds: int = None,
                            max_wait_seconds: float = None) -> LyricsFetchResult:
        """Tri-state availability check with usable-content semantics.

        Like lookup_remote_lyrics, except FOUND additionally requires the
        record to carry synced or plain lyrics text (a bare record with no
        lyrics content counts as ABSENT, matching has_remote_lyrics'
        historical contract).
        """
        result = self.lookup_remote_lyrics(track_name, artist_name, album_name,
                                           duration_seconds, max_wait_seconds=max_wait_seconds)
        if result.status != STATUS_FOUND:
            return result
        data = result.data
        if getattr(data, 'synced_lyrics', None) or getattr(data, 'plain_lyrics', None):
            return result
        return LyricsFetchResult(STATUS_ABSENT, error_kind='not_found',
                                 reason='LRCLib has no lyrics for this track')

    def has_remote_lyrics(self, track_name: str, artist_name: str,
                          album_name: str = None, duration_seconds: int = None):
        """Tri-state (#1879): True if LRClib has (synced OR plain) lyrics,
        False if it definitively does not, None if the lookup could not be
        completed (429/5xx/timeout) — an unknown is NOT a negative.

        Powers the Missing Lyrics maintenance job's scan so it only
        surfaces tracks that are actually fixable (instrumentals return
        nothing → never flagged), without recording rate-limited tracks
        as lyric-less.
        """
        result = self.check_remote_lyrics(track_name, artist_name, album_name, duration_seconds)
        if result.status == STATUS_FOUND:
            return True
        if result.status == STATUS_ABSENT:
            return False
        # Debug, not warning: callers that surface the outcome (scan,
        # repair worker, player endpoint) log/report the reason
        # themselves, and _call_lrclib already warned once on exhaustion.
        logger.debug("Lyrics availability unknown for '%s' — %s: %s",
                     track_name, result.error_kind, result.reason)
        return None

    def create_lrc_file(self, audio_file_path: str, track_name: str, artist_name: str,
                       album_name: str = None, duration_seconds: int = None,
                       max_wait_seconds: float = None) -> bool:
        """Bool wrapper over create_lrc_file_detailed (see there)."""
        ok, _reason = self.create_lrc_file_detailed(
            audio_file_path, track_name, artist_name, album_name, duration_seconds,
            max_wait_seconds=max_wait_seconds)
        return ok

    def create_lrc_file_detailed(self, audio_file_path: str, track_name: str, artist_name: str,
                                 album_name: str = None, duration_seconds: int = None,
                                 max_wait_seconds: float = None):
        """
        Create .lrc sidecar file for the given audio file.

        Returns ``(ok, reason)``: ``(True, None)`` on success;
        ``(False, None)`` when LRCLib definitively has no lyrics;
        ``(False, reason)`` when the fetch could not be completed
        (rate-limited / server error / timeout) or writing failed —
        so callers can surface the real reason instead of claiming the
        lyrics are "no longer available" (#1879). ``max_wait_seconds``
        caps each throttle wait (see _call_lrclib).
        """
        if not self.api:
            logger.debug("LRClib API not available - skipping lyrics")
            return False, 'LRCLib API not available'

        try:
            # Generate LRC file path (same name as audio file, .lrc extension)
            lrc_path = os.path.splitext(audio_file_path)[0] + '.lrc'
            txt_path = os.path.splitext(audio_file_path)[0] + '.txt'

            # Sidecar already exists — skip the LRClib fetch but still
            # re-embed the lyrics in the audio file's tag. The retag
            # flow clears all tags including USLT and then runs this
            # helper to restore them; without the embed step the
            # LYRICS tag stays empty even though the .lrc is right
            # there next to the file (Discord report — Netti93).
            if os.path.exists(lrc_path) or os.path.exists(txt_path):
                existing_path = lrc_path if os.path.exists(lrc_path) else txt_path
                try:
                    with open(existing_path, 'r', encoding='utf-8') as f:
                        existing_lyrics = f.read().strip()
                    if existing_lyrics:
                        self._embed_lyrics(audio_file_path, existing_lyrics)
                        logger.debug(
                            "Re-embedded lyrics from existing %s for: %s",
                            os.path.basename(existing_path),
                            os.path.basename(audio_file_path),
                        )
                except Exception as e:
                    logger.debug(
                        "Could not re-embed lyrics from existing sidecar %s: %s",
                        os.path.basename(existing_path), e,
                    )
                return True, None

            # Fetch lyrics from LRClib
            logger.debug(f"Fetching lyrics for: {artist_name} - {track_name}")
            fetch = self.check_remote_lyrics(
                track_name, artist_name, album_name, duration_seconds,
                max_wait_seconds=max_wait_seconds)

            if fetch.status == STATUS_UNKNOWN:
                # Transient/permanent failure — NOT "no lyrics". The
                # reason travels with the return value and every caller
                # surfaces it (worker error, scan log, endpoint response,
                # import log), so this stays debug to avoid logging the
                # same failure twice (#1879).
                logger.debug(f"Lyrics fetch incomplete for {artist_name} - {track_name}: {fetch.reason}")
                return False, fetch.reason

            # No lyrics found (definitive)
            if fetch.status != STATUS_FOUND:
                logger.debug(f"No lyrics found for: {artist_name} - {track_name}")
                return False, None

            lyrics_data = fetch.data
            # LRClib API provides synced_lyrics (timestamped) and plain_lyrics (text only)
            synced = getattr(lyrics_data, 'synced_lyrics', None)
            plain = getattr(lyrics_data, 'plain_lyrics', None)

            logger.debug(f"Synced lyrics available: {bool(synced)}")
            logger.debug(f"Plain lyrics available: {bool(plain)}")

            if not synced and not plain:
                logger.debug(f"No usable lyrics content for: {artist_name} - {track_name}")
                return False, None

            if synced:
                # Synced lyrics have timestamps → valid .lrc format
                with open(lrc_path, 'w', encoding='utf-8') as f:
                    f.write(synced)
                # Embed synced lyrics in audio tags
                self._embed_lyrics(audio_file_path, synced)
                logger.info(f"Created synced LRC + embedded: {os.path.basename(lrc_path)}")
            else:
                # Plain lyrics only → write as .txt (not .lrc, which requires timestamps)
                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write(plain)
                # Still embed plain lyrics in audio tags (players can display unsynced lyrics)
                self._embed_lyrics(audio_file_path, plain)
                logger.info(f"Created plain lyrics .txt + embedded: {os.path.basename(txt_path)}")
            return True, None

        except Exception as e:
            logger.error(f"Error creating LRC file for {track_name}: {e}")
            # Generic reason, not str(e): the reason is user-presentable
            # (the repair worker returns it verbatim) and a raw exception
            # can carry file paths / OS messages. Detail lives in the log.
            return False, 'Lyrics fetch/write failed — see app log for details'


    def _embed_lyrics(self, audio_file_path: str, lyrics_text: str):
        """Embed lyrics directly into audio file tags."""
        try:
            from mutagen import File as MutagenFile
            from mutagen.flac import FLAC
            from mutagen.oggvorbis import OggVorbis
            from mutagen.mp4 import MP4
            from mutagen.id3 import ID3, USLT

            audio = MutagenFile(audio_file_path)
            if audio is None:
                return

            if audio.tags is None:
                return  # Don't create tags just for lyrics

            if isinstance(audio.tags, ID3):
                audio.tags.delall('USLT')
                audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=lyrics_text))
                audio.save(v1=0, v2_version=4)
            elif isinstance(audio, (FLAC, OggVorbis)) or type(audio).__name__ == 'OggOpus':
                audio['lyrics'] = [lyrics_text]
                if isinstance(audio, FLAC):
                    audio.save(deleteid3=True)
                else:
                    audio.save()
            elif isinstance(audio, MP4):
                audio['\xa9lyr'] = [lyrics_text]
                audio.save()

            logger.debug(f"Embedded lyrics in: {os.path.basename(audio_file_path)}")
        except Exception as e:
            logger.warning(f"Could not embed lyrics in {os.path.basename(audio_file_path)}: {e}")


# Global instance for easy import
lyrics_client = LyricsClient()
