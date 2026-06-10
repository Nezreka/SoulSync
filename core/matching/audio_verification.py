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
from difflib import SequenceMatcher

# Thresholds — the single definition both paths share.
MIN_ACOUSTID_SCORE = 0.80       # Minimum fingerprint score to trust a match.
TITLE_MATCH_THRESHOLD = 0.70    # Title similarity to consider a match.
ARTIST_MATCH_THRESHOLD = 0.60   # Artist similarity to consider a match.


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
