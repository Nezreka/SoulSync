"""Find a matching AcoustID candidate for an expected (title, artist).

AcoustID returns multiple recordings per fingerprint — same audio can
correspond to multiple MusicBrainz recordings (different releases,
different metadata-quality entries, sample / cover-version collisions).
The "top" recording AcoustID returns isn't always the one whose
metadata matches the user's expected track.

Both the post-download verifier (`core/acoustid_verification.py`) and
the AcoustID library scanner (`core/repair_jobs/acoustid_scanner.py`)
need to ask: "given these candidates, does ANY of them match
(expected_title, expected_artist) by title+artist similarity?" The
verifier had its own inline loop; the scanner only checked the top
match → false positives whenever the wrong-credited recording out-
ranked the right-credited one.

This module is the single shared boundary for that question.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("matching.acoustid_candidates")


def find_matching_recording(
    recordings: Iterable[Dict[str, Any]],
    expected_title: str,
    expected_artist: str,
    *,
    title_threshold: float = 0.70,
    artist_threshold: float = 0.60,
    similarity: Optional[Callable[[str, str], float]] = None,
    artist_similarity: Optional[Callable[[str, str], float]] = None,
    skip_predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Tuple[Optional[Dict[str, Any]], float, float]:
    """Return the first AcoustID candidate whose metadata passes both
    title + artist similarity thresholds.

    Args:
        recordings: AcoustID recording dicts. Each must carry ``title``
            and ``artist`` strings; entries without both are skipped.
        expected_title: The track title the caller expected.
        expected_artist: The artist the caller expected.
        title_threshold: Minimum title similarity to accept (default 0.70).
        artist_threshold: Minimum artist similarity to accept (default 0.60).
        similarity: ``(a, b) -> float`` for title comparison. Defaults
            to a lowercase exact-equals stub when not supplied — callers
            should pass their stricter normaliser (verifier passes its
            parenthetical-stripping ``_similarity``; scanner passes
            its own).
        artist_similarity: ``(expected, actual) -> float`` for artist
            comparison. Lets callers supply alias-aware comparison
            (verifier wraps ``_alias_aware_artist_sim``; scanner wraps
            ``artist_names_match``). Defaults to ``similarity`` if
            unset.
        skip_predicate: Optional ``(recording_dict) -> bool``. When
            truthy, the candidate is skipped (used by the verifier to
            drop wrong-version recordings — instrumental vs vocal etc).

    Returns:
        ``(recording, title_sim, artist_sim)`` for the first matching
        candidate, or ``(None, best_title_sim, best_artist_sim)`` when
        none match. The non-None ``best_*`` values let callers report
        the closest near-miss when they need to log why nothing matched.

    Iteration order matches the input order (typically AcoustID's own
    fingerprint-confidence ranking). Returns on first match — does NOT
    score every candidate looking for the highest sim.
    """
    if not expected_title or not expected_artist:
        return None, 0.0, 0.0

    sim = similarity or _default_similarity
    asim = artist_similarity or sim

    best_title_sim = 0.0
    best_artist_sim = 0.0

    for rec in recordings or ():
        if not isinstance(rec, dict):
            continue
        rec_title = (rec.get('title') or '').strip()
        rec_artist = (rec.get('artist') or '').strip()
        if not rec_title or not rec_artist:
            continue
        if skip_predicate and skip_predicate(rec):
            continue

        title_sim = sim(expected_title, rec_title)
        if title_sim > best_title_sim:
            best_title_sim = title_sim

        artist_sim = asim(expected_artist, rec_artist)
        if artist_sim > best_artist_sim:
            best_artist_sim = artist_sim

        if title_sim >= title_threshold and artist_sim >= artist_threshold:
            return rec, title_sim, artist_sim

    return None, best_title_sim, best_artist_sim


def _default_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a.lower().strip() == b.lower().strip() else 0.0


# ────────────────────────────────────────────────────────────────────
# Duration guard — codex item (5).
# ────────────────────────────────────────────────────────────────────


def duration_mismatches_strongly(
    expected_seconds: Optional[float],
    candidate_seconds: Optional[float],
    *,
    abs_tolerance_s: float = 60.0,
    rel_tolerance: float = 0.35,
) -> bool:
    """Return True when the candidate's duration is too far from expected
    to confidently treat it as the same recording.

    Catches fingerprint hash collisions (the reporter's 17-minute
    mashup → 5-minute Japanese hiphop track case). When EITHER duration
    is unknown / non-positive, returns False — no behavior change.

    Threshold: drift greater than max(``abs_tolerance_s``,
    ``rel_tolerance * expected``). The relative term scales with track
    length so a 20% mismatch on a 3-minute track and a 20% mismatch on
    a 30-minute mix are both treated as suspicious.
    """
    if not expected_seconds or expected_seconds <= 0:
        return False
    if not candidate_seconds or candidate_seconds <= 0:
        return False
    drift = abs(float(candidate_seconds) - float(expected_seconds))
    threshold = max(abs_tolerance_s, rel_tolerance * float(expected_seconds))
    return drift > threshold
