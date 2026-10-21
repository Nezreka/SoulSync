"""Art for the downloads page rows: the artist's photo, and the album cover
when the task itself carried none.

A wishlist or sync task often has no artwork on its track_info, and nothing
ever gave a row the artist's picture. The library already knows both, so look
them up by name. The page polls every 2s, so answers (hits AND misses) are
cached per name for a while instead of hitting the db each poll.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("downloads.row_art")

# a new library scan can add a cover; ten minutes is soon enough to notice
_TTL_SECONDS = 600.0


def _first_artist(artist: str) -> str:
    # "A, B" / "A & B" rows: the first name is the one with a picture
    for sep in (', ', ' & ', ' feat. ', ' ft. ', ' x '):
        if sep in artist:
            return artist.split(sep, 1)[0]
    return artist


class RowArtLookup:
    """Name -> image url, cached. The two query callables take lowercased
    names/titles and return the db rows (see MusicDatabase
    get_artist_thumbs_by_names / get_album_thumbs_by_titles)."""

    def __init__(self, query_artists: Callable[[list], Iterable[tuple]],
                 query_albums: Callable[[list], Iterable[tuple]],
                 normalize: Callable[[str], Optional[str]] = lambda u: u,
                 ttl: float = _TTL_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._query_artists = query_artists
        self._query_albums = query_albums
        self._normalize = normalize
        self._ttl = ttl
        self._clock = clock
        self._artists: dict = {}   # lower name -> (url or None, stamp)
        self._albums: dict = {}    # lower title -> ({lower artist: url}, stamp)

    def _fresh(self, cache: dict, key) -> bool:
        hit = cache.get(key)
        return hit is not None and self._clock() - hit[1] < self._ttl

    def artist_images(self, names: Iterable[str]) -> dict:
        wanted = {n.strip().lower() for n in names if n and n.strip()}
        missing = [n for n in wanted if not self._fresh(self._artists, n)]
        if missing:
            found: dict = {}
            for name, url in self._query_artists(missing) or ():
                # library row wins over the watchlist one: it came first
                if name and url and name not in found:
                    found[name] = url
            now = self._clock()
            for n in missing:
                url = found.get(n)
                self._artists[n] = (self._normalize(url) if url else None, now)
        return {n: self._artists[n][0] for n in wanted if self._artists.get(n, (None,))[0]}

    def album_covers(self, titles: Iterable[str]) -> dict:
        wanted = {t.strip().lower() for t in titles if t and t.strip()}
        missing = [t for t in wanted if not self._fresh(self._albums, t)]
        if missing:
            found: dict = {t: {} for t in missing}
            for artist, title, url in self._query_albums(missing) or ():
                if title in found and url and artist not in found[title]:
                    found[title][artist] = url
            now = self._clock()
            for t in missing:
                self._albums[t] = ({a: self._normalize(u) for a, u in found[t].items()}, now)
        return {t: self._albums[t][0] for t in wanted if self._albums.get(t, ({},))[0]}


def attach_row_art(items: list, lookup: RowArtLookup) -> None:
    """Give each row `artist_image`, and fill an empty `artwork` from the
    library album of the same artist + title. Never overwrites art the task
    brought with it. One bad lookup never costs the page its rows."""
    try:
        artists = {_first_artist(str(i.get('artist') or '')) for i in items}
        images = lookup.artist_images(artists)
        need_cover = [i for i in items if not i.get('artwork') and i.get('album')]
        covers = lookup.album_covers(str(i.get('album')) for i in need_cover)
    except Exception as exc:  # noqa: BLE001 - art is decoration, rows are not
        logger.debug("row art lookup failed: %s", exc)
        return
    for item in items:
        artist = _first_artist(str(item.get('artist') or '')).strip().lower()
        if artist and artist in images:
            item['artist_image'] = images[artist]
        if not item.get('artwork') and item.get('album'):
            by_artist = covers.get(str(item['album']).strip().lower()) or {}
            url = by_artist.get(artist)
            if url:
                item['artwork'] = url
