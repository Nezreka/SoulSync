"""Pick the canonical album release by best-fit to the user's actual files.

Issue #765 / #767-Bug2: SoulSync never pins ONE canonical album version per
album, so the Library Reorganizer, Track Number Repair, and tagging each
re-resolve independently and can land on different releases (standard vs
deluxe; Spotify vs MusicBrainz track numbering) and contradict each other.

This module is the pure, testable heart of the fix: given the metadata of the
files actually on disk and a set of candidate releases, score each release by
how well it FITS those files and pick the best. "Best-fit to the files" means:

  - track-count fit   — a 17-track deluxe is a poor fit for 11 files on disk
  - duration alignment — each file should line up with a release track by length
  - title overlap      — a tiebreaker / sanity check

What this does and does NOT solve:
  - It DOES pick the right EDITION (standard vs deluxe) — the discriminating
    signal is track count + durations.
  - It does NOT (and cannot) decide which of two listings of the SAME album is
    "more correct" when they differ only in track numbering (same files match
    both equally). Instead ``pick_canonical_release`` is DETERMINISTIC and
    breaks ties toward the earlier candidate — so the caller passes candidates
    in source-priority order and every tool that reads the pinned result agrees
    on the same release. Agreement is what resolves #765, not picking a
    "winner" of the numbering disagreement.

Pure, no I/O. Callers fetch candidate tracklists and read on-disk file metadata;
this module only scores.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

# Weights for the three fit signals. Count + duration dominate because "matches
# my files" is fundamentally about having the right NUMBER of the right-LENGTH
# tracks; title is a tiebreaker. Missing signals are dropped and the present
# ones renormalized (see _combine).
_W_COUNT = 0.4
_W_DURATION = 0.4
_W_TITLE = 0.2

_DEFAULT_DURATION_TOLERANCE_MS = 3000  # ±3s — covers encode/version length jitter
_DEFAULT_MIN_SCORE = 0.5               # never pin below this — leave unresolved
_TITLE_FUZZY_THRESHOLD = 0.85


def _norm_title(text: str) -> str:
    """Lowercase, drop bracketed qualifiers ((feat. …), [Remastered]), strip
    punctuation, collapse whitespace."""
    if not text:
        return ""
    t = str(text).lower()
    t = re.sub(r"[\(\[].*?[\)\]]", "", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(t.split())


def _count_fit(n_files: int, n_release: int) -> float:
    """1.0 when track counts match; decays with the relative difference."""
    if n_files <= 0 or n_release <= 0:
        return 0.0
    return 1.0 - min(1.0, abs(n_files - n_release) / max(n_files, n_release))


def _duration_fit(
    file_tracks: List[Dict[str, Any]],
    release_tracks: List[Dict[str, Any]],
    tolerance_ms: int,
) -> Optional[float]:
    """Fraction of tracks that line up by duration (greedy nearest match within
    tolerance), over the larger of the two track counts — so missing or extra
    tracks are penalised. Returns ``None`` when neither side has durations."""
    f_durs = [int(f["duration_ms"]) for f in file_tracks if f.get("duration_ms")]
    r_durs = [int(r["duration_ms"]) for r in release_tracks if r.get("duration_ms")]
    if not f_durs or not r_durs:
        return None
    used = [False] * len(r_durs)
    matched = 0
    for fd in f_durs:
        best_j, best_diff = -1, tolerance_ms + 1
        for j, rd in enumerate(r_durs):
            if used[j]:
                continue
            diff = abs(fd - rd)
            if diff <= tolerance_ms and diff < best_diff:
                best_diff, best_j = diff, j
        if best_j >= 0:
            used[best_j] = True
            matched += 1
    denom = max(len(file_tracks), len(release_tracks))
    return matched / denom if denom else 0.0


def _title_fit(
    file_tracks: List[Dict[str, Any]],
    release_tracks: List[Dict[str, Any]],
) -> Optional[float]:
    """Fraction of files whose title matches some release title (exact-normalised
    or fuzzy), over the larger track count. ``None`` when titles are absent."""
    f_titles = [_norm_title(f.get("title", "")) for f in file_tracks]
    f_titles = [t for t in f_titles if t]
    r_titles = [_norm_title(r.get("title", "")) for r in release_tracks]
    r_titles = [t for t in r_titles if t]
    if not f_titles or not r_titles:
        return None
    r_set = set(r_titles)
    matched = 0
    for ft in f_titles:
        if ft in r_set or any(
            SequenceMatcher(None, ft, rt).ratio() >= _TITLE_FUZZY_THRESHOLD
            for rt in r_titles
        ):
            matched += 1
    denom = max(len(file_tracks), len(release_tracks))
    return matched / denom if denom else 0.0


def _combine(parts: List[Tuple[Optional[float], float]]) -> float:
    """Weighted mean over present (non-None) components, renormalising weights."""
    present = [(v, w) for v, w in parts if v is not None]
    total_w = sum(w for _, w in present)
    if total_w <= 0:
        return 0.0
    return sum(v * w for v, w in present) / total_w


def score_release_against_files(
    file_tracks: List[Dict[str, Any]],
    release_tracks: List[Dict[str, Any]],
    *,
    duration_tolerance_ms: int = _DEFAULT_DURATION_TOLERANCE_MS,
) -> float:
    """Score 0.0–1.0 of how well ``release_tracks`` fits the on-disk
    ``file_tracks``. Each track dict may carry ``duration_ms`` and ``title``;
    missing signals are dropped and the rest renormalised so the function never
    crashes on sparse metadata (it just leans on what's available)."""
    if not file_tracks or not release_tracks:
        return 0.0
    count = _count_fit(len(file_tracks), len(release_tracks))
    dur = _duration_fit(file_tracks, release_tracks, duration_tolerance_ms)
    title = _title_fit(file_tracks, release_tracks)
    return _combine([(count, _W_COUNT), (dur, _W_DURATION), (title, _W_TITLE)])


def pick_canonical_release(
    file_tracks: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    *,
    min_score: float = _DEFAULT_MIN_SCORE,
    duration_tolerance_ms: int = _DEFAULT_DURATION_TOLERANCE_MS,
) -> Tuple[Optional[Dict[str, Any]], float]:
    """Choose the best-fit candidate release for the on-disk files.

    ``candidates`` is a list of dicts each with a ``'tracks'`` list (plus any
    caller fields like ``source``/``album_id``, returned untouched). **Pass
    candidates in source-priority order** — ties break toward the EARLIER one,
    so the choice is deterministic and priority-respecting (this is what makes
    every tool agree, #765).

    Returns ``(best_candidate, score)``, or ``(None, best_score)`` when nothing
    clears ``min_score`` — so a low-confidence guess is never pinned (the caller
    leaves the album unresolved and falls back to today's behaviour)."""
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for cand in candidates:
        score = score_release_against_files(
            file_tracks, cand.get("tracks") or [],
            duration_tolerance_ms=duration_tolerance_ms,
        )
        # Strictly-greater so equal scores keep the earlier (higher-priority)
        # candidate — deterministic tiebreak.
        if score > best_score + 1e-9:
            best, best_score = cand, score
    if best is None or best_score < min_score:
        return None, best_score
    return best, best_score


__all__ = ["score_release_against_files", "pick_canonical_release"]
