"""Turn a free-text Deezer download query into an exact-title query.

The download pipeline hands a source one string, e.g. ``"aulii cravalho how far
ill go"``. Deezer's plain ``/search`` ranks reprises, karaoke and key-shifted
copies above the real song and, for some songs, leaves the real one out of the
result page entirely. Deezer's ``track:"title"`` filter does find it, but the
filter needs the title on its own and the string does not say where the artist
ends.

The first plain search tells us: its results carry artist names, and a name that
appears in the query marks the artist part. What is left is the title.

Pure functions only (no network, no config), so they are unit-testable offline.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Tuple

_APOSTROPHES = "'’‘ʼ`´"
_QUOTES = '"“”'

# An artist this short matches too many unrelated words ("A", "M", "U2" is fine).
_MIN_ARTIST_CHARS = 2
_MIN_TITLE_CHARS = 2


def fold(text: str) -> str:
    """Lowercase, drop accents, delete apostrophes, other punctuation -> space.

    Apostrophes are DELETED (not turned into spaces) so ``Auli'i`` and the
    download query's ``aulii`` fold to the same thing.
    """
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    for ch in _APOSTROPHES:
        text = text.replace(ch, "")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def split_query_by_artist(
    query: str, artist_names: Iterable[str]
) -> Optional[Tuple[str, str]]:
    """Find an artist named at the start or end of ``query``.

    Returns ``(artist, title)`` in folded form, or None when no artist from
    ``artist_names`` borders the query or nothing is left for a title. The
    longest matching artist wins, so "Alan Menken" beats "Alan".
    """
    q = fold(query)
    if not q:
        return None

    best: Optional[Tuple[str, str]] = None
    seen = set()
    for name in artist_names or []:
        a = fold(name)
        if len(a) < _MIN_ARTIST_CHARS or a in seen:
            continue
        seen.add(a)
        title = None
        if q.startswith(a + " "):
            title = q[len(a):].strip()
        elif q.endswith(" " + a):
            title = q[: -len(a)].strip()
        if title is None or len(title) < _MIN_TITLE_CHARS:
            continue
        if best is None or len(a) > len(best[0]):
            best = (a, title)
    return best


def _clean_phrase(text: str) -> str:
    out = str(text or "")
    for ch in _QUOTES:
        out = out.replace(ch, " ")
    return re.sub(r"\s+", " ", out).strip()


def exact_title_queries(query: str, artist_names: Iterable[str]) -> List[str]:
    """Deezer queries that put the title in a ``track:"..."`` filter.

    With an artist found in the query: ``track:"title" artist``. The artist
    stays plain words, not ``artist:"..."``, because Deezer's artist filter
    returns nothing (it is broken on their side). Without one, the whole query
    is treated as the title.
    """
    split = split_query_by_artist(query, artist_names)
    if split:
        artist, title = split
        return [f'track:"{_clean_phrase(title)}" {_clean_phrase(artist)}']
    title = _clean_phrase(fold(query))
    return [f'track:"{title}"'] if len(title) >= _MIN_TITLE_CHARS else []
