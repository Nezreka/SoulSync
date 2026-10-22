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


# hyphen-shaped characters that mean the same thing as '-' in a name.
# musicbrainz writes alt-J with a U+2010 hyphen where apple/deezer use ascii
# (#1797). em dash stays out on purpose, it's usually a separator.
_DASH_VARIANTS = str.maketrans({
    '\u2010': '-',  # hyphen
    '\u2011': '-',  # non-breaking hyphen
    '\u2012': '-',  # figure dash
    '\u2013': '-',  # en dash
    '\u2212': '-',  # minus sign
    '\ufe63': '-',  # small hyphen-minus
    '\uff0d': '-',  # fullwidth hyphen-minus
})


def fold_dashes(text: str) -> str:
    """every hyphen variant becomes an ascii '-', nothing else changes.
    a grouping key, not a display name: callers keep the original."""
    if not text:
        return text or ''
    return text.translate(_DASH_VARIANTS)
