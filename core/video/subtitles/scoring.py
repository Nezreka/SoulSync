"""Subtitle candidate scoring ("Replace Bazarr" Phase 2).

Hash-first, Bazarr-style: a file-hash match dominates (it proves the subtitle was
timed for THIS exact release), then filename/release-name similarity, then
download count as a tiebreaker. A minimum-score threshold gates downloads — below
it, the candidate is treated as a miss.

Pure functions; no network, no DB. Isolated: stdlib only.
"""

from __future__ import annotations

import math
import os
import re
from difflib import SequenceMatcher

# Score components. A hash match alone (100) always clears the default threshold;
# a filename similarity of ~0.5 with decent downloads also clears it; weak
# matches stay below.
HASH_MATCH_SCORE = 100.0
FILENAME_WEIGHT = 50.0
DOWNLOAD_COUNT_WEIGHT = 10.0

#: Below this, a candidate is treated as a miss (never downloaded). Tunable via
#: the ``subtitle_min_score`` setting; this is the fallback when unset.
# Measured (Oct 2026, hostile reviews): legitimate non-hash subtitles score
# 27.4+ (release-group/source tokens drag similarity to ~0.55-0.72); unrelated
# garbage caps at ~21 even with 2M downloads. 24.0 keeps margin on both sides
# for the UNRELATED case. Honest limit: token overlap can't distinguish close
# relatives ("The Matrix" vs "The Matrix Reloaded" scores ~42) — that scoping
# is the provider search's job (movie-scoped queries); the threshold is the
# second line of defense, not the first.
DEFAULT_MIN_SCORE = 24.0


def opensubtitles_hash(path: str) -> str | None:
    """The OpenSubtitles file hash: size + uint64 sums of the first and last 64KB.

    Returns the 16-hex-digit string, or None when the file is unreadable or too
    small (< 128KB — needs both head and tail). Never raises.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size < 131072:
        return None
    h = size & 0xFFFFFFFFFFFFFFFF
    try:
        with open(path, "rb") as f:
            for offset in (0, size - 65536):
                f.seek(offset)
                chunk = f.read(65536)
                # Sum as little-endian uint64s, folding a short tail read.
                for i in range(0, len(chunk) - 7, 8):
                    h = (h + int.from_bytes(chunk[i:i + 8], "little")) & 0xFFFFFFFFFFFFFFFF
    except OSError:
        return None
    return "%016x" % h


_EP_TAG = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")

def _episode_tag(s: str) -> tuple[int, int] | None:
    """(season, episode) from an S01E02-style tag, else None."""
    m = _EP_TAG.search(str(s or ""))
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2))
    except (TypeError, ValueError):
        return None


def _stem(s: str) -> str:
    """Basename without the file extension (mkv/srt/…), for title comparison."""
    s = str(s or "")
    base = os.path.basename(s)
    root, ext = os.path.splitext(base)
    return root if ext and len(ext) <= 5 else base


def _norm(s: str) -> str:
    """Lowercase, punctuation → spaces, collapsed whitespace — for fuzzy compare.

    Keeps non-Latin letters/digits: CJK/Arabic/... titles must survive
    normalization, not collapse to empty.
    """
    s = str(s or "").lower()
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def filename_similarity(a: str, b: str) -> float:
    """0..1 similarity of two release/filenames after normalization. Pure.

    Compares extension-stripped stems so 'title.mkv' vs 'title' scores 1.0.
    """
    sa, sb = _stem(a).strip().lower(), _stem(b).strip().lower()
    if sa and sb and sa == sb:
        return 1.0
    na, nb = _norm(sa), _norm(sb)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def download_count_bonus(download_count: int | None) -> float:
    """0..DOWNLOAD_COUNT_WEIGHT log-scaled tiebreaker. ~2000 downloads saturates."""
    try:
        dl = int(download_count or 0)
    except (TypeError, ValueError):
        dl = 0
    if dl <= 0:
        return 0.0
    return min(DOWNLOAD_COUNT_WEIGHT, math.log10(1 + dl) * 3.0)


def score_candidate(candidate, query) -> float:
    """Score one SubtitleCandidate against a SubtitleQuery. Higher is better.

    ``candidate`` needs ``hash_match``/``title``/``download_count``;
    ``query`` needs ``filename``. Missing pieces score 0 — never raises.
    """
    # Wrong-episode veto: a candidate tagged S01E02 for an S01E01 file is never
    # the right subtitle, no matter how similar the rest of the title is.
    # Only applies when BOTH sides carry a parseable tag.
    try:
        q_tag = _episode_tag(getattr(query, "filename", None))
        c_tag = _episode_tag(getattr(candidate, "title", None))
        if q_tag is not None and c_tag is not None and q_tag != c_tag:
            return 0.0
    except Exception:  # noqa: BLE001 - veto is best-effort
        pass
    score = 0.0
    try:
        if bool(getattr(candidate, "hash_match", False)):
            score += HASH_MATCH_SCORE
        filename = getattr(query, "filename", None)
        title = getattr(candidate, "title", None)
        if filename and title:
            score += filename_similarity(str(title), str(filename)) * FILENAME_WEIGHT
        score += download_count_bonus(getattr(candidate, "download_count", 0))
    except Exception:  # noqa: BLE001 - scoring is best-effort, never fatal
        pass
    return score


def select_best(candidates, query, min_score: float = DEFAULT_MIN_SCORE):
    """Highest-scoring candidate at or above ``min_score``, else None.

    Ties (including all-zero scores) resolve to the provider's original order —
    ``max`` keeps the first maximal element, and providers return best-first.
    """
    try:
        threshold = float(min_score)
    except (TypeError, ValueError):
        threshold = DEFAULT_MIN_SCORE
    best = None
    best_score = -1.0
    for cand in (candidates or []):
        s = score_candidate(cand, query)
        if s > best_score:
            best, best_score = cand, s
    return best if best is not None and best_score >= threshold else None


__all__ = [
    "opensubtitles_hash",
    "filename_similarity",
    "download_count_bonus",
    "score_candidate",
    "select_best",
    "HASH_MATCH_SCORE",
    "FILENAME_WEIGHT",
    "DOWNLOAD_COUNT_WEIGHT",
    "DEFAULT_MIN_SCORE",
]
