"""Shared title-similarity check for "does this MBID actually point at this song".

Extracted out of ``core/repair_jobs/mbid_mismatch_detector.py`` (the repair job that
compares a file's embedded MusicBrainz recording ID against the title MusicBrainz
returns for it) so the playlist-export MBID waterfall
(``core/exports/export_sources.py``) can run the exact same check before trusting a
DB/file MBID rung, without the two call sites drifting apart on what counts as "close
enough".
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

# Below this SequenceMatcher ratio, two (normalized) titles are different songs.
TITLE_SIMILARITY_THRESHOLD = 0.55


def normalize_title(s: Optional[str]) -> str:
    """Lowercase, strip whitespace and common suffixes for comparison."""
    if not s:
        return ''
    s = s.lower().strip()
    # Strip parentheticals like (Live), (Remastered), (feat. X)
    s = re.sub(r'\s*\(.*?\)\s*', ' ', s)
    # Strip brackets like [Deluxe Edition]
    s = re.sub(r'\s*\[.*?\]\s*', ' ', s)
    return s.strip()


def title_matches(file_title: Optional[str], mb_title: Optional[str],
                   threshold: float = TITLE_SIMILARITY_THRESHOLD) -> bool:
    """True when two titles are similar enough to be considered the same track."""
    a = normalize_title(file_title)
    b = normalize_title(mb_title)
    if not a or not b:
        return True  # Can't compare, assume OK
    if a == b:
        return True
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold


__all__ = ["TITLE_SIMILARITY_THRESHOLD", "normalize_title", "title_matches"]
