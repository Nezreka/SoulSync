"""
AcoustID Verification Service

Verifies downloaded audio files match expected track metadata by comparing
title/artist from AcoustID fingerprint results against the expected track info.

If the audio fingerprint confidently identifies a DIFFERENT song than expected,
the file is flagged as incorrect.
"""

import re
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, Tuple, List
from enum import Enum
from utils.logging_config import get_logger
from core.acoustid_client import AcoustIDClient

logger = get_logger("acoustid_verification")

# Thresholds
MIN_ACOUSTID_SCORE = 0.80       # Minimum AcoustID fingerprint score to trust
TITLE_MATCH_THRESHOLD = 0.70    # Title similarity needed to consider a match
ARTIST_MATCH_THRESHOLD = 0.60   # Artist similarity needed to consider a match


class VerificationResult(Enum):
    """Possible outcomes of audio verification."""
    PASS = "pass"       # Title/artist match - file is correct
    FAIL = "fail"       # Title/artist mismatch - wrong file downloaded
    SKIP = "skip"       # Could not verify (error or unavailable) - continue normally
    DISABLED = "disabled"  # Verification not enabled


def _normalize(text: str) -> str:
    """Normalize a string for comparison: lowercase, strip parentheticals, punctuation."""
    if not text:
        return ""
    s = text.lower().strip()
    # Remove common parenthetical suffixes like (Live), (Remastered), (Radio Edit)
    s = re.sub(r'\s*\((?:live|remaster(?:ed)?|deluxe|bonus|radio\s*edit|single\s*version|visualize.*?)\)', '', s, flags=re.IGNORECASE)
    # Remove featuring info: "(feat. ...)", "(ft. ...)", "(featuring ...)"
    s = re.sub(r'\s*\((?:feat\.?|ft\.?|featuring)\s+[^)]*\)', '', s, flags=re.IGNORECASE)
    # Remove trailing featuring info: "feat. ...", "ft. ...", "featuring ..."
    s = re.sub(r'\s+(?:feat\.?|ft\.?|featuring)\s+.*$', '', s, flags=re.IGNORECASE)
    # Remove non-alphanumeric except spaces
    s = re.sub(r'[^\w\s]', '', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _similarity(a: str, b: str) -> float:
    """Calculate similarity between two strings (0.0-1.0) after normalization."""
    na = _normalize(a)
    nb = _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _find_best_title_artist_match(
    recordings: List[Dict[str, Any]],
    expected_title: str,
    expected_artist: str,
) -> Tuple[Optional[Dict], float, float]:
    """
    Find the AcoustID recording that best matches expected title/artist.

    Returns:
        (best_recording, title_similarity, artist_similarity)
    """
    best_rec = None
    best_title_sim = 0.0
    best_artist_sim = 0.0
    best_combined = 0.0

    for rec in recordings:
        title = rec.get('title') or ''
        artist = rec.get('artist') or ''

        title_sim = _similarity(expected_title, title)
        artist_sim = _similarity(expected_artist, artist)
        # Weight title higher since that's the primary identifier
        combined = (title_sim * 0.6) + (artist_sim * 0.4)

        if combined > best_combined:
            best_combined = combined
            best_rec = rec
            best_title_sim = title_sim
            best_artist_sim = artist_sim

    return best_rec, best_title_sim, best_artist_sim


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

            # Step 2: Fingerprint and lookup in AcoustID
            logger.info(f"Fingerprinting and looking up: {audio_file_path}")
            acoustid_result = self.acoustid_client.fingerprint_and_lookup(audio_file_path)

            if not acoustid_result:
                return VerificationResult.SKIP, "Track not found in AcoustID database"

            recordings = acoustid_result.get('recordings', [])
            best_score = acoustid_result.get('best_score', 0)

            if not recordings:
                return VerificationResult.SKIP, "AcoustID returned no recordings"

            logger.debug(
                f"AcoustID returned {len(recordings)} recording(s) "
                f"(best fingerprint score: {best_score:.2f})"
            )

            # Step 3: Check fingerprint confidence
            if best_score < MIN_ACOUSTID_SCORE:
                msg = f"AcoustID fingerprint score too low ({best_score:.2f}) to verify"
                logger.info(msg)
                return VerificationResult.SKIP, msg

            # Step 4: Find best title/artist match among AcoustID results
            best_rec, title_sim, artist_sim = _find_best_title_artist_match(
                recordings, expected_track_name, expected_artist_name
            )

            if not best_rec:
                return VerificationResult.SKIP, "No recordings with title/artist info"

            matched_title = best_rec.get('title', '?')
            matched_artist = best_rec.get('artist', '?')

            logger.info(
                f"Best match: '{matched_title}' by '{matched_artist}' "
                f"(title_sim={title_sim:.2f}, artist_sim={artist_sim:.2f})"
            )

            # Step 5: Decide pass/fail based on similarity
            if title_sim >= TITLE_MATCH_THRESHOLD and artist_sim >= ARTIST_MATCH_THRESHOLD:
                msg = (
                    f"Audio verified: '{matched_title}' by '{matched_artist}' "
                    f"matches expected '{expected_track_name}' by '{expected_artist_name}' "
                    f"(title={title_sim:.0%}, artist={artist_sim:.0%})"
                )
                logger.info(f"AcoustID verification PASSED - {msg}")
                return VerificationResult.PASS, msg

            # Title matches but artist doesn't — could be a cover or collab, skip
            if title_sim >= TITLE_MATCH_THRESHOLD and artist_sim < ARTIST_MATCH_THRESHOLD:
                # Check if the expected artist appears anywhere in the AcoustID results
                for rec in recordings:
                    if _similarity(expected_artist_name, rec.get('artist', '')) >= ARTIST_MATCH_THRESHOLD:
                        msg = (
                            f"Audio verified: found '{expected_track_name}' by '{expected_artist_name}' "
                            f"in AcoustID results"
                        )
                        logger.info(f"AcoustID verification PASSED (secondary match) - {msg}")
                        return VerificationResult.PASS, msg

                msg = (
                    f"Title matches but artist unclear: "
                    f"AcoustID='{matched_title}' by '{matched_artist}', "
                    f"expected '{expected_track_name}' by '{expected_artist_name}'"
                )
                logger.info(f"AcoustID verification SKIPPED - {msg}")
                return VerificationResult.SKIP, msg

            # Title doesn't match — check ALL recordings for any title/artist match
            # (the best combined match might not be the right one if there are many results)
            for rec in recordings:
                t = rec.get('title') or ''
                a = rec.get('artist') or ''
                if (_similarity(expected_track_name, t) >= TITLE_MATCH_THRESHOLD and
                        _similarity(expected_artist_name, a) >= ARTIST_MATCH_THRESHOLD):
                    msg = (
                        f"Audio verified: found '{t}' by '{a}' in AcoustID results "
                        f"matching expected '{expected_track_name}' by '{expected_artist_name}'"
                    )
                    logger.info(f"AcoustID verification PASSED (scan match) - {msg}")
                    return VerificationResult.PASS, msg

            # No match found — this file is likely wrong
            # Report what AcoustID thinks the file actually is (top result by score)
            top = recordings[0]
            top_title = top.get('title', '?')
            top_artist = top.get('artist', '?')

            msg = (
                f"Audio mismatch: file identified as '{top_title}' by '{top_artist}', "
                f"expected '{expected_track_name}' by '{expected_artist_name}' "
                f"(title={title_sim:.0%}, artist={artist_sim:.0%})"
            )
            logger.warning(f"AcoustID verification FAILED - {msg}")
            return VerificationResult.FAIL, msg

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
