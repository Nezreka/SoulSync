"""multi-value tag values, the picard way (#1305).

picard writes a list as separate values: several LABEL fields in a vorbis
comment, one null-separated frame in id3v2.4, several freeform atoms in mp4.
soulsync wrote some of these as one joined string instead ("Columbia;Grass
Roots Entertainment;BMG Direct", "Pop, R&B"), which every reader takes as a
single value.
"""

from __future__ import annotations

import re
from typing import Iterable, List

_SEMI = re.compile(r"\s*;\s*")


def split_values(value, separators: str = ";") -> List[str]:
    """a list, or a string joined with any of ``separators``, as clean
    distinct values in order."""
    if value is None:
        return []
    items: Iterable = value if isinstance(value, (list, tuple)) else [value]
    pattern = re.compile("|".join(r"\s*" + re.escape(s) + r"\s*" for s in separators)) if separators else None
    out, seen = [], set()
    for item in items:
        parts = pattern.split(str(item)) if pattern else [str(item)]
        for part in parts:
            p = part.strip()
            key = p.lower()
            if p and key not in seen:
                seen.add(key)
                out.append(p)
    return out


def genre_values(genre, multi: bool) -> List[str]:
    """the GENRE tag's values: one per genre when multi-value writing is on,
    else the single joined string as before."""
    if not genre:
        return []
    if multi:
        return split_values(genre, ",;")
    return [genre if isinstance(genre, str) else ", ".join(split_values(genre, ",;"))]


__all__ = ["split_values", "genre_values"]
