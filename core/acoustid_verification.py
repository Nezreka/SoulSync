"""
AcoustID Verification Service

Verifies downloaded audio files match expected track metadata by comparing
title/artist from AcoustID fingerprint results against the expected track info.

If the audio fingerprint confidently identifies a DIFFERENT song than expected,
the file is flagged as incorrect.
"""

import re
import threading
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, Tuple, List
from enum import Enum
from utils.logging_config import get_logger
from core.acoustid_client import AcoustIDClient
from core.matching_engine import MusicMatchingEngine
from core.matching.version_mismatch import is_acceptable_version_mismatch
from core.matching.script_compat import is_cross_script_mismatch
from core.musicbrainz_client import MusicBrainzClient

logger = get_logger("acoustid.verification")

# Thresholds
MIN_ACOUSTID_SCORE = 0.80       # Minimum AcoustID fingerprint score to trust
TITLE_MATCH_THRESHOLD = 0.70    # Title similarity needed to consider a match
ARTIST_MATCH_THRESHOLD = 0.60   # Artist similarity needed to consider a match

# Single matching-engine instance so version detection reuses the same patterns
# used by the pre-download Soulseek matcher (remix / live / acoustic /
# instrumental / etc). detect_version_type doesn't use self state, so one
# shared instance is fine.
_match_engine_for_version = MusicMatchingEngine()


def _detect_title_version(title: str) -> str:
    """Return version label for a track title.

    Returns ``'original'`` when no version marker is detected, otherwise one
    of the labels produced by ``MusicMatchingEngine.detect_version_type``
    (``'instrumental'``, ``'live'``, ``'acoustic'``, ``'remix'``, etc).
    """
    if not title:
        return 'original'
    version_type, _ = _match_engine_for_version.detect_version_type(title)
    return version_type


class VerificationResult(Enum):
    """Possible outcomes of audio verification."""
    PASS = "pass"       # Title/artist match - file is correct
    FAIL = "fail"       # Title/artist mismatch - wrong file downloaded
    SKIP = "skip"       # Genuinely couldn't verify (no match in DB) - continue normally
    DISABLED = "disabled"  # Verification not enabled
    ERROR = "error"     # Lookup errored (invalid key / rate limit / no backend) - continue, but flag it


# normalize() + similarity() + the alias-aware comparison now live in the shared
# decision core (core/matching/audio_verification.py) so import-time verification
# and the library scan share ONE definition — the <>-strip fix, CJK handling and
# thresholds can't drift apart again. Names kept (`_normalize` etc.) for existing
# importers/tests.
from core.matching.audio_verification import (  # noqa: E402
    normalize as _normalize,
    similarity as _similarity,
    _alias_aware_artist_sim,
    _find_best_title_artist_match as _core_find_best_title_artist_match,
    evaluate as _core_evaluate,
    Decision as _CoreDecision,
)


def _find_best_title_artist_match(recordings, expected_title, expected_artist,
                                  expected_artist_aliases=None):
    """Back-compat wrapper around the shared core matcher (keeps the
    ``expected_artist_aliases`` kwarg name for existing callers/tests)."""
    return _core_find_best_title_artist_match(
        recordings, expected_title, expected_artist, expected_artist_aliases,
    )


# Shared MusicBrainz client for enrichment lookups
_mb_client = None
_mb_client_lock = threading.Lock()

# Shared MusicBrainzService for alias lookups (issue #442). Service
# layer wraps the raw client + adds caching + DB access — all of which
# the alias resolution chain (library DB → cache → live MB) needs.
_mb_service = None
_mb_service_lock = threading.Lock()

MAX_MB_ENRICHMENT_LOOKUPS = 3


def _get_mb_client() -> MusicBrainzClient:
    """Get or create a shared MusicBrainz client instance."""
    global _mb_client
    if _mb_client is None:
        with _mb_client_lock:
            if _mb_client is None:
                _mb_client = MusicBrainzClient()
    return _mb_client


def _get_mb_service():
    """Get or create a shared MusicBrainzService instance.

    Used by the alias-resolution chain in `verify_audio_file`. Lazy
    init so importing this module doesn't trigger a DB connection on
    paths that never run AcoustID verification (test runs, dry runs).
    """
    global _mb_service
    if _mb_service is None:
        with _mb_service_lock:
            if _mb_service is None:
                from core.musicbrainz_service import MusicBrainzService
                from database.music_database import get_database
                _mb_service = MusicBrainzService(get_database())
    return _mb_service


def _resolve_expected_artist_aliases(expected_artist_name: str) -> List[str]:
    """Look up alternate-spelling aliases for the expected artist.

    Issue #442 — bridges cross-script artist comparisons (Japanese
    kanji ↔ romanized, Cyrillic ↔ Latin, etc.) without forcing the
    verifier to know about the resolution chain. Best-effort: any
    failure (no MB service, network down, no library DB) returns
    empty list so verification falls back to the prior direct
    similarity check.
    """
    if not expected_artist_name:
        return []
    try:
        return _get_mb_service().lookup_artist_aliases(expected_artist_name)
    except Exception as e:
        logger.debug("alias lookup failed for %r: %s", expected_artist_name, e)
        return []


def _enrich_recordings_from_musicbrainz(
    recordings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Enrich recordings that are missing title/artist by looking up their
    MBIDs via MusicBrainz.

    AcoustID often returns recordings with title=None, artist=None even though
    the MBIDs are valid. This resolves the metadata so verification can compare
    title/artist instead of skipping.

    Args:
        recordings: List of recording dicts from fingerprint_and_lookup()

    Returns:
        The same list, with title/artist filled in where possible.
    """
    # Fast path: if any recording already has title AND artist, no enrichment needed
    if any(rec.get('title') and rec.get('artist') for rec in recordings):
        return recordings

    logger.info(f"Enriching {len(recordings)} recordings via MusicBrainz (all missing title/artist)...")

    mb = _get_mb_client()
    enriched_count = 0

    for rec in recordings[:MAX_MB_ENRICHMENT_LOOKUPS]:
        mbid = rec.get('mbid')
        if not mbid:
            continue

        try:
            data = mb.get_recording(mbid, includes=['artist-credits'])
            if not data:
                logger.debug(f"MusicBrainz returned no data for recording {mbid}")
                continue

            title = data.get('title')
            artist_credit = data.get('artist-credit', [])

            # Build artist string from artist-credit array
            # Each entry has {"artist": {"name": "..."}, "joinphrase": "..."}
            artist_parts = []
            for credit in artist_credit:
                name = credit.get('artist', {}).get('name', '')
                joinphrase = credit.get('joinphrase', '')
                if name:
                    artist_parts.append(name + joinphrase)
            artist = ''.join(artist_parts).strip() if artist_parts else None

            if title:
                rec['title'] = title
                logger.debug(f"Enriched {mbid}: title='{title}'")
            if artist:
                rec['artist'] = artist
                logger.debug(f"Enriched {mbid}: artist='{artist}'")

            if title or artist:
                enriched_count += 1

        except Exception as e:
            logger.debug(f"Failed to enrich recording {mbid}: {e}")
            continue

    logger.info(f"Enriched {enriched_count}/{min(len(recordings), MAX_MB_ENRICHMENT_LOOKUPS)} recordings from MusicBrainz")
    return recordings


class AcoustIDVerification:
    """
    Verification service that compares audio fingerprint identity
    against expected track metadata using title/artist matching.

    Design Principle: FAIL OPEN
    - Only returns FAIL when we are CONFIDENT the file is wrong
    - Any error or uncertainty results in SKIP (continue normally)
    - Never blocks downloads due to verification infrastructure issues

    Usage:
        verifier = AcoustIDVerification()
        result, message = verifier.verify_audio_file(
            "/path/to/downloaded.mp3",
            "Expected Song Title",
            "Expected Artist"
        )

        if result == VerificationResult.FAIL:
            # Move to quarantine
        else:
            # Continue with normal processing (PASS, SKIP, or DISABLED)
    """

    def __init__(self):
        """Initialize verification service."""
        self.acoustid_client = AcoustIDClient()

    def verify_audio_file(
        self,
        audio_file_path: str,
        expected_track_name: str,
        expected_artist_name: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Tuple[VerificationResult, str]:
        """
        Verify that an audio file matches expected track metadata.

        Compares title/artist from AcoustID fingerprint results against
        the expected track info. No MusicBrainz lookup needed.

        Args:
            audio_file_path: Path to the downloaded audio file
            expected_track_name: Track name we expected to download
            expected_artist_name: Artist name we expected
            context: Optional download context for logging/debugging

        Returns:
            Tuple of (VerificationResult, reason_message)
        """
        try:
            # Step 1: Check availability
            available, reason = self.acoustid_client.is_available()
            if not available:
                logger.debug(f"AcoustID verification skipped: {reason}")
                return VerificationResult.SKIP, reason

            # Step 2: Fingerprint and lookup in AcoustID (structured so an
            # actual error — invalid key / rate limit / no chromaprint — is
            # reported distinctly from a genuine no-match, instead of both
            # silently surfacing as "Skipped").
            logger.info(f"Fingerprinting and looking up: {audio_file_path}")
            lookup = self.acoustid_client.lookup_with_status(audio_file_path) or {}
            status = lookup.get('status')
            # Infer status by content when absent (a caller/stub that returned
            # just recordings): recordings => matched, none => no match.
            if status is None:
                status = 'ok' if lookup.get('recordings') else 'no_match'

            if status in ('error', 'no_backend', 'fingerprint_error', 'unavailable'):
                # Something is broken (not the track's fault) — never quarantine
                # on this; surface it so the user can fix it.
                return VerificationResult.ERROR, lookup.get('error', 'AcoustID lookup failed')

            if status != 'ok':
                # no_match / unsupported / not_found — genuinely could not verify.
                return VerificationResult.SKIP, lookup.get('error', 'No match in AcoustID database')

            acoustid_result = lookup
            recordings = acoustid_result.get('recordings', [])
            best_score = acoustid_result.get('best_score', 0)

            if not recordings:
                return VerificationResult.SKIP, "No match in AcoustID database"

            logger.debug(
                f"AcoustID returned {len(recordings)} recording(s) "
                f"(best fingerprint score: {best_score:.2f})"
            )

            # Step 3: Check fingerprint confidence
            if best_score < MIN_ACOUSTID_SCORE:
                msg = f"AcoustID fingerprint score too low ({best_score:.2f}) to verify"
                logger.info(msg)
                return VerificationResult.SKIP, msg

            # Enrich recordings that are missing title/artist via MusicBrainz lookup
            recordings = _enrich_recordings_from_musicbrainz(recordings)

            # Issue #442 — alias resolution is LAZY. We pass a memoising
            # thunk to the artist-comparison sites; it only fires the
            # multi-tier lookup (library DB → cache → live MB) when
            # direct artist similarity falls below threshold. Verifications
            # where the direct match already passes (the common case for
            # same-script artist names) never trigger any lookup work,
            # so the fix doesn't add a per-verification DB query for the
            # happy path. When the thunk DOES fire, the result is cached
            # in the closure so the 3 comparison sites within one
            # verification share a single resolution pass.
            _alias_cache: Dict[str, Any] = {}

            def _aliases_provider() -> List[str]:
                if 'value' not in _alias_cache:
                    resolved = _resolve_expected_artist_aliases(expected_artist_name)
                    _alias_cache['value'] = resolved
                    if resolved:
                        logger.debug(
                            "Resolved %d aliases for expected artist '%s'",
                            len(resolved), expected_artist_name,
                        )
                return _alias_cache['value']

            # Steps 4-5: delegate the PASS/SKIP/FAIL decision to the shared core
            # (core/matching/audio_verification.evaluate) so import verification
            # and the library scan apply identical logic.
            outcome = _core_evaluate(
                expected_track_name, expected_artist_name, recordings,
                fingerprint_score=best_score,
                aliases_provider=_aliases_provider,
            )
            logger.info(
                "Best match: '%s' by '%s' (title_sim=%.2f, artist_sim=%.2f) -> %s",
                outcome.matched_title, outcome.matched_artist,
                outcome.title_sim, outcome.artist_sim, outcome.decision.value,
            )
            _decision_map = {
                _CoreDecision.PASS: VerificationResult.PASS,
                _CoreDecision.SKIP: VerificationResult.SKIP,
                _CoreDecision.FAIL: VerificationResult.FAIL,
            }
            result = _decision_map[outcome.decision]
            if result == VerificationResult.PASS:
                logger.info("AcoustID verification PASSED - %s", outcome.reason)
            elif result == VerificationResult.FAIL:
                logger.warning("AcoustID verification FAILED - %s", outcome.reason)
            else:
                logger.info("AcoustID verification SKIPPED - %s", outcome.reason)
            return result, outcome.reason

        except Exception as e:
            # Any unexpected error -> SKIP (fail open)
            logger.error(f"Unexpected error during AcoustID verification: {e}")
            return VerificationResult.SKIP, f"Verification error: {str(e)}"

    def quick_check_available(self) -> Tuple[bool, str]:
        """
        Quick check if verification is available without doing a full verification.

        Returns:
            Tuple of (is_available, reason)
        """
        return self.acoustid_client.is_available()
