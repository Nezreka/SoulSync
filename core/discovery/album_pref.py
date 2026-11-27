"""the album a playlist entry names is part of which track it is (#2331).

classical pieces, standards and big hits sit on dozens of albums under the
same title and artist. a playlist entry that says "the 50 greatest pieces of
classical music" means that copy, so identification prefers a candidate on
that album and only falls back to another copy when the album isn't there.

a source with no album says nothing, so nothing here changes what it gets.
"""

from __future__ import annotations

import re
from typing import Any

from core.text.normalize import normalize_for_comparison

# how much an album hit counts when ranking candidates. bigger than the gaps
# between copies of one recording, smaller than a wrong title or artist
ALBUM_BONUS = 0.1

_BRACKETS = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def album_name(album: Any) -> str:
    """the album's name from a string, a {'name': ...} dict or an object."""
    if not album:
        return ''
    if isinstance(album, str):
        return album.strip()
    if isinstance(album, dict):
        return str(album.get('name') or album.get('title') or '').strip()
    return str(getattr(album, 'name', '') or getattr(album, 'title', '') or '').strip()


def _keys(name: str) -> set:
    folded = normalize_for_comparison(name)
    keys = {_NON_WORD.sub('', folded)}
    # "(remastered 2011)" or "[deluxe]" on one side only is still the album
    keys.add(_NON_WORD.sub('', _BRACKETS.sub('', folded)))
    keys.discard('')
    return keys


def same_album(source_album: Any, candidate_album: Any) -> bool:
    """whether two album names are the same album, spelling aside."""
    a, b = album_name(source_album), album_name(candidate_album)
    if not a or not b:
        return False
    return bool(_keys(a) & _keys(b))


def candidate_album(candidate: Any) -> Any:
    """the album of a search result (Track object) or a match dict."""
    if isinstance(candidate, dict):
        return candidate.get('album')
    return getattr(candidate, 'album', None)


def album_hit(source_album: Any, candidate: Any) -> bool:
    return same_album(source_album, candidate_album(candidate))


def album_rank(confidence: float, source_album: Any, candidate: Any, floor: float = 0.0) -> float:
    """confidence plus the album bonus, for picking between candidates only.
    the confidence itself is never changed. an album copy under ``floor``
    (the caller's minimum) gets no bonus, so it can't push out a copy that
    would have been accepted."""
    conf = confidence or 0.0
    if conf >= floor and album_hit(source_album, candidate):
        return conf + ALBUM_BONUS
    return conf


def cached_match_fits_album(source_album: Any, cached_match: Any) -> bool:
    """a discovery cache row is filed by title + artist, so it can hold a copy
    from another album. it only fits when the source names no album, the row
    names none, or they agree."""
    want = album_name(source_album)
    have = album_name(candidate_album(cached_match) if cached_match else None)
    if not want or not have:
        return True
    return same_album(want, have)


def album_query(title: str, artist: str, source_album: Any) -> str:
    """a search that names the album, so its copy is among the results.
    empty when the source names no album or the album is just the title
    (a single)."""
    album = album_name(source_album)
    if not album or same_album(album, title):
        return ''
    return f"{artist} {title} {album}".strip()


def best_with_album(score, source_album: Any, results: list, floor: float = 0.0):
    """score ``results`` the usual way, and when the source names an album,
    score that album's copies on their own too. ``score(results)`` returns
    ``(match, confidence)``. the album copy wins unless another copy beats it
    by more than ALBUM_BONUS, or scores under ``floor`` (the caller's
    minimum). returns ``(match, confidence, on_album)``."""
    match, confidence = score(results)
    if not album_name(source_album) or not results:
        return match, confidence, bool(match) and album_hit(source_album, match)
    on_album = [r for r in results if album_hit(source_album, r)]
    if not on_album:
        return match, confidence, False
    if match is not None and any(r is match for r in on_album):
        return match, confidence, True
    album_match, album_conf = score(on_album)
    if (album_match is not None and album_conf >= floor
            and album_conf + ALBUM_BONUS >= (confidence or 0.0)):
        return album_match, album_conf, True
    return match, confidence, False
