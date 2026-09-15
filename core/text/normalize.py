"""Shared text-normalization helpers.

Extracted from `MusicDatabase._normalize_for_comparison` so callers
outside the database layer (matching engine, sync candidate pool,
import comparisons) don't have to reach across the module boundary
into a leading-underscore "private" method.

Pure functions, no I/O.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

try:
    from unidecode import unidecode as _unidecode
    _HAS_UNIDECODE = True
except ImportError:
    _unidecode = None  # type: ignore[assignment]
    _HAS_UNIDECODE = False
    logger.warning("unidecode not available, accent matching may be limited")


@lru_cache(maxsize=131072)
def normalize_for_comparison(text: str) -> str:
    """Lowercase + strip whitespace + fold accents to ASCII.

    cached: the matcher normalizes the same search title and the same pool
    titles once per candidate, thousands of times per playlist.

    ``é → e``, ``ñ → n``, ``Björk → bjork``. Used as the dictionary key
    for the sync candidate pool and for fuzzy library lookups where
    diacritic differences must NOT split a single artist into two pool
    entries.

    Empty / falsy input returns ``""`` so callers can blindly key dicts
    with the result.
    """
    if not text:
        return ""
    if _HAS_UNIDECODE:
        text = _unidecode(text)
    return text.lower().strip()


_NON_ALNUM_RE = re.compile(r'[^a-z0-9]')


def normalize_key(text: str) -> str:
    """normalize_for_comparison, then every non-alphanumeric character
    dropped: 'AC/DC' and 'ACDC', 'The   Beatles' and 'the beatles' key the
    same. the search library-check's ownership key, and artists.name_key."""
    return _NON_ALNUM_RE.sub('', normalize_for_comparison(text or ''))
