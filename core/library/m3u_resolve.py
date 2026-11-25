"""find the library file for each track of a playlist m3u.

the library loads once (``get_tracks_for_m3u_resolution``) and is indexed in
memory by artist. a track is filed under every artist it's credited to: the
album artist, each per-track credit and the per-track credit text. a playlist
track looks itself up by its whole credit first, then by each artist in it,
split the way the importer splits credits, so a run of pieces the library
knows as one artist ("Above & Beyond") stays one name. titles are compared
after that, as before.

before this a library track was filed only under its album artist and the
playlist credit was looked up whole, so every "A & B" track came out missing
(#2214).
"""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional

try:
    from unidecode import unidecode as _unidecode
except ImportError:  # pragma: no cover - unidecode ships with the app
    def _unidecode(text):
        return text


def norm(text: str) -> str:
    return _unidecode(text).lower().strip() if text else ''


def clean(text: str) -> str:
    s = norm(text)
    s = re.sub(r'\s*[\[\(].*?[\]\)]', '', s)
    s = re.sub(r'\s*-\s*', ' ', s)
    s = re.sub(r'\s*feat\..*', '', s)
    s = re.sub(r'\s*featuring.*', '', s)
    s = re.sub(r'\s*ft\..*', '', s)
    s = re.sub(r'\s*\d{4}\s*remaster.*', '', s)
    s = re.sub(r'\s*remaster(ed)?.*', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _credit_pieces(credit: str, known) -> List[str]:
    from core.library2.importer import split_known_artist_credits
    return split_known_artist_credits(credit, known)


class LibraryIndex:
    """library rows filed under every artist they're credited to."""

    def __init__(self, rows: Iterable[Dict[str, Any]]):
        rows = list(rows)
        # the artist names the library has, for keeping "A & B" whole when
        # it's one artist
        self.known = set()
        for row in rows:
            for name in [row.get('artist')] + list(row.get('credits') or []):
                key = clean(name or '')
                if key:
                    self.known.add(key)
        self.by_artist: Dict[str, list] = defaultdict(list)
        for row in rows:
            path = row.get('file_path')
            if not path:
                continue
            entry = (norm(row.get('title') or ''), clean(row.get('title') or ''), path)
            keys = []
            names = [row.get('artist')] + list(row.get('credits') or [])
            text = row.get('track_artist') or ''
            if text:
                names.append(text)
                names.extend(_credit_pieces(text, self._known))
            for name in names:
                key = clean(name or '')
                if key and key not in keys:
                    keys.append(key)
            for key in keys:
                self.by_artist[key].append(entry)

    def _known(self, name: str) -> bool:
        return clean(name) in self.known

    def _pick(self, name: str, candidates) -> Optional[str]:
        from core.text.title_match import choose_best_title_candidate
        if not candidates:
            return None
        return choose_best_title_candidate(
            norm(name), clean(name), candidates,
            lambda left, right: SequenceMatcher(None, left, right).ratio())

    def resolve(self, name: str, artist: str) -> Optional[str]:
        """the file for one playlist track, or None."""
        if not name or not artist:
            return None
        whole = clean(artist)
        hit = self._pick(name, self.by_artist.get(whole))
        if hit:
            return hit
        pooled, seen = [], set()
        for piece in _credit_pieces(artist, self._known):
            key = clean(piece)
            if not key or key == whole:
                continue
            for entry in self.by_artist.get(key, ()):
                if entry[2] not in seen:
                    seen.add(entry[2])
                    pooled.append(entry)
        return self._pick(name, pooled)


def resolve_m3u_paths(rows: Iterable[Dict[str, Any]],
                      tracks: List[Dict[str, Any]]) -> Dict[int, Optional[str]]:
    """{index in ``tracks``: library file path or None} for ``[{name, artist}]``."""
    index = LibraryIndex(rows)
    return {idx: index.resolve(track.get('name', '') or '', track.get('artist', '') or '')
            for idx, track in enumerate(tracks)}


__all__ = ["LibraryIndex", "clean", "norm", "resolve_m3u_paths"]
