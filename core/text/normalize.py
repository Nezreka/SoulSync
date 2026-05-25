"""Shared text-normalization helpers.

Extracted from `MusicDatabase._normalize_for_comparison` so callers
outside the database layer (matching engine, sync candidate pool,
import comparisons) don't have to reach across the module boundary
into a leading-underscore "private" method.

Pure functions, no I/O.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from unidecode import unidecode as _unidecode
    _HAS_UNIDECODE = True
except ImportError:
    _unidecode = None  # type: ignore[assignment]
    _HAS_UNIDECODE = False
    logger.warning("unidecode not available, accent matching may be limited")


def normalize_for_comparison(text: str) -> str:
    """Lowercase + strip whitespace + fold accents to ASCII.

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
