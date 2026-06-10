"""Shared audio-verification decision core (pure; no file/DB I/O).

Single source of truth for normalization + the PASS/SKIP/FAIL decision used by
BOTH import-time verification (``core/acoustid_verification.py``) and the library
scan (``core/repair_jobs/acoustid_scanner.py``). Historically each path had its
own ``_normalize`` and decision branches that drifted apart and produced
inconsistent results (a correct cross-script anime-OST track passed at import but
was false-flagged by the scan). Centralising the decision here means the
thresholds, normalization, alias-aware comparison, cross-script handling, version
gate and duration guard are defined exactly once.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audio_verification")

# Thresholds — the single definition both paths share.
MIN_ACOUSTID_SCORE = 0.80       # Minimum fingerprint score to trust a match.
TITLE_MATCH_THRESHOLD = 0.70    # Title similarity to consider a match.
ARTIST_MATCH_THRESHOLD = 0.60   # Artist similarity to consider a match.
CLEAR_MISMATCH_THRESHOLD = 0.30  # Below this artist sim = clear wrong song.


class Decision(Enum):
    PASS = "pass"
    SKIP = "skip"
    FAIL = "fail"


@dataclass
class Outcome:
    decision: Decision
    title_sim: float = 0.0
    artist_sim: float = 0.0
    matched_title: str = ""
    matched_artist: str = ""
    reason: str = ""


def normalize(text: str) -> str:
    """Normalize a title/artist for comparison.

    lowercase; strip ``()`` / ``[]`` / ``<>`` annotations (version tags,
    performer credits like ``<Vocal: MIKA KOBAYASHI>``); strip trailing
    version / featuring tags; KEEP CJK characters (``\\w`` is unicode-aware) so
    Japanese/Chinese/Korean titles produce a comparable form instead of an empty
    string; collapse whitespace.
    """
    if not text:
        return ""
    s = text.lower().strip()
    # Annotations that are metadata, not core identity.
    s = re.sub(r'\s*\([^)]*\)', '', s)
    s = re.sub(r'\s*\[[^\]]*\]', '', s)
    s = re.sub(r'\s*<[^>]*>', '', s)
    # Trailing featuring / version tags.
    s = re.sub(r'\s+(?:feat\.?|ft\.?|featuring)\s+.*$', '', s, flags=re.IGNORECASE)
    s = re.sub(
        r'\s*-\s*(?:vocal|instrumental|acoustic|live|remix|cover|clean|explicit|'
        r'radio\s*edit|original\s*mix|extended\s*mix|club\s*mix)\s*$',
        '', s, flags=re.IGNORECASE,
    )
    s = re.sub(r'\s*-\s*from\s+.+$', '', s, flags=re.IGNORECASE)
    # Drop remaining punctuation but keep word chars (incl. CJK) + spaces.
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def similarity(a: str, b: str) -> float:
    """Similarity (0.0–1.0) between two strings after normalization."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


_match_engine = None


def _detect_title_version(title: str) -> str:
    """Version label ('original'/'instrumental'/'live'/'remix'/...) for a title."""
    global _match_engine
    if not title:
        return 'original'
    if _match_engine is None:
        from core.matching_engine import MusicMatchingEngine
        _match_engine = MusicMatchingEngine()
    version_type, _ = _match_engine.detect_version_type(title)
    return version_type


def _alias_aware_artist_sim(expected_artist: str, actual_artist: str,
                            aliases: Optional[Any] = None) -> float:
    """Best artist similarity across (expected, *aliases) vs actual.

    Bridges cross-script artist comparisons (kanji↔romaji etc) when MusicBrainz
    aliases are available. ``aliases`` is an iterable of alias strings, or a
    callable resolving them lazily (only invoked when direct similarity falls
    below threshold — keeps the happy path lookup-free).
    """
    from core.matching.artist_aliases import artist_names_match

    direct = similarity(expected_artist, actual_artist)
    if aliases is None:
        return direct
    if direct >= ARTIST_MATCH_THRESHOLD:
        return direct
    resolved = aliases() if callable(aliases) else aliases
    if not resolved:
        return direct
    _matched, score = artist_names_match(
        expected_artist, actual_artist, aliases=resolved,
        threshold=ARTIST_MATCH_THRESHOLD, similarity=similarity,
    )
    # Diagnostic: an alias rescued a comparison direct similarity would have
    # failed. INFO since it's a user-visible decision (PASS instead of FAIL).
    if score >= ARTIST_MATCH_THRESHOLD and direct < ARTIST_MATCH_THRESHOLD:
        from core.matching.artist_aliases import best_alias_match
        winner, _ = best_alias_match(
            expected_artist, actual_artist, resolved, similarity=similarity,
        )
        logger.info(
            "Artist alias rescued comparison: expected=%r vs actual=%r "
            "(direct sim=%.2f, alias %r → score=%.2f)",
            expected_artist, actual_artist, direct, winner, score,
        )
    return score


def _find_best_title_artist_match(recordings, expected_title, expected_artist,
                                  aliases=None):
    """Return (best_recording, title_sim, artist_sim) — title weighted higher."""
    best_rec = None
    best_title_sim = 0.0
    best_artist_sim = 0.0
    best_combined = 0.0
    for rec in recordings:
        title = rec.get('title') or ''
        artist = rec.get('artist') or ''
        title_sim = similarity(expected_title, title)
        artist_sim = _alias_aware_artist_sim(expected_artist, artist, aliases)
        combined = (title_sim * 0.6) + (artist_sim * 0.4)
        if combined > best_combined:
            best_combined = combined
            best_rec = rec
            best_title_sim = title_sim
            best_artist_sim = artist_sim
    return best_rec, best_title_sim, best_artist_sim


def evaluate(expected_title: str, expected_artist: str,
             recordings: List[dict], *, fingerprint_score: float,
             file_duration_s: Optional[float] = None,
             aliases_provider: Optional[Any] = None) -> Outcome:
    """Decide PASS / SKIP / FAIL for a fingerprinted file against expected
    title/artist. Pure: no I/O. Shared by import verification and library scan.

    ``aliases_provider``: iterable or callable of expected-artist aliases
    (kanji/cyrillic/etc) used to bridge cross-script comparisons.
    ``file_duration_s``: when provided, a strong duration mismatch downgrades a
    would-be FAIL to SKIP (fingerprint hash collision guard, used by the scan).
    """
    from core.matching.script_compat import is_cross_script_mismatch
    from core.matching.acoustid_candidates import (
        duration_mismatches_strongly, find_matching_recording,
    )
    from core.matching.version_mismatch import is_acceptable_version_mismatch

    best_rec, title_sim, artist_sim = _find_best_title_artist_match(
        recordings, expected_title, expected_artist, aliases_provider,
    )
    if not best_rec:
        return Outcome(Decision.SKIP, reason="No recordings with title/artist info")

    matched_title = best_rec.get('title', '?') or '?'
    matched_artist = best_rec.get('artist', '?') or '?'

    def out(dec, reason):
        return Outcome(dec, title_sim, artist_sim, matched_title, matched_artist, reason)

    # Version gate: original vs instrumental/live/remix is a real difference.
    expected_version = _detect_title_version(expected_title)
    matched_version = _detect_title_version(matched_title)
    if expected_version != matched_version:
        if not is_acceptable_version_mismatch(
            expected_version, matched_version,
            fingerprint_score=fingerprint_score,
            title_similarity=title_sim, artist_similarity=artist_sim,
        ):
            return out(Decision.FAIL,
                       f"Version mismatch: expected ({expected_version}) "
                       f"but file is ({matched_version})")

    # Clean match.
    if title_sim >= TITLE_MATCH_THRESHOLD and artist_sim >= ARTIST_MATCH_THRESHOLD:
        return out(Decision.PASS, "Audio verified")

    # Title matches, artist doesn't — cover/collab vs genuinely wrong.
    if title_sim >= TITLE_MATCH_THRESHOLD and artist_sim < ARTIST_MATCH_THRESHOLD:
        for rec in recordings:
            if _alias_aware_artist_sim(
                expected_artist, rec.get('artist', ''), aliases_provider,
            ) >= ARTIST_MATCH_THRESHOLD:
                return out(Decision.PASS, "Expected artist found in AcoustID results")
        if artist_sim < CLEAR_MISMATCH_THRESHOLD:
            if file_duration_s and duration_mismatches_strongly(
                file_duration_s, best_rec.get('duration') or best_rec.get('length')):
                return out(Decision.SKIP, "Duration mismatch (fingerprint collision)")
            return out(Decision.FAIL,
                       f"Audio mismatch: '{matched_title}' by '{matched_artist}' "
                       f"— expected artist not found")
        return out(Decision.SKIP, "Title matches but artist ambiguous (cover/collab?)")

    # Title doesn't match — scan all recordings for a version-matched hit.
    def _title_sim(a, b):
        return similarity(a, b)

    def _artist_sim(ea, aa):
        return _alias_aware_artist_sim(ea, aa, aliases_provider)

    candidate = None
    for rec in recordings:
        if _detect_title_version(rec.get('title') or '') != expected_version:
            continue
        if (similarity(expected_title, rec.get('title') or '') >= TITLE_MATCH_THRESHOLD
                and _alias_aware_artist_sim(
                    expected_artist, rec.get('artist', ''), aliases_provider,
                ) >= ARTIST_MATCH_THRESHOLD):
            candidate = rec
            break
    if candidate is not None:
        return out(Decision.PASS, "Scan match found in AcoustID results")

    # High-confidence / cross-script skips (don't quarantine a correct file).
    has_non_ascii = (any(ord(c) > 127 for c in (expected_title or ''))
                     or any(ord(c) > 127 for c in matched_title))
    language_script_skip = (fingerprint_score >= 0.95 and has_non_ascii
                            and artist_sim >= ARTIST_MATCH_THRESHOLD)
    high_confidence_strong_match_skip = (fingerprint_score >= 0.95
                                         and title_sim >= 0.80
                                         and artist_sim >= ARTIST_MATCH_THRESHOLD)
    cross_script_artist_skip = (fingerprint_score >= MIN_ACOUSTID_SCORE
                                and artist_sim >= ARTIST_MATCH_THRESHOLD
                                and is_cross_script_mismatch(expected_artist, matched_artist))
    if (language_script_skip or high_confidence_strong_match_skip
            or cross_script_artist_skip):
        return out(Decision.SKIP, "Likely same song in different language/script")

    if file_duration_s and duration_mismatches_strongly(
            file_duration_s, best_rec.get('duration') or best_rec.get('length')):
        return out(Decision.SKIP, "Duration mismatch (fingerprint collision)")

    return out(Decision.FAIL,
               f"Audio mismatch: file identified as '{matched_title}' by "
               f"'{matched_artist}', expected '{expected_title}' by '{expected_artist}'")
