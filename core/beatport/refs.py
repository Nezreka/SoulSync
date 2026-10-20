"""a stable name for every beatport list, so a list can be mirrored and
refreshed later.

the old chart cards used a random hash as the playlist id, which nothing could
ever turn back into a url, so beatport mirrors could never refresh. a ref names
the list itself:

    top-10, hype-10, top-100, hype-100          the homepage lists
    genre:<slug>:<id>:<list>                   a genre's top-10/hype-10/top-100/hype-100
    chart:<slug>:<id>                          a dj or editorial chart
    release:<slug>:<id>                        one release

refs only use characters that are safe in a url path segment (no slashes),
because the discovery endpoints carry them as /api/beatport/discovery/start/<ref>.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from core.beatport.next_data import BASE_URL

HOME_LISTS = ('top-10', 'hype-10', 'top-100', 'hype-100')
GENRE_LISTS = HOME_LISTS

_SLUG = r'[a-z0-9][a-z0-9-]*'
_GENRE_RE = re.compile(rf'^genre:({_SLUG}):(\d+):(top-10|hype-10|top-100|hype-100)$')
_ITEM_RE = re.compile(rf'^(chart|release):({_SLUG}):(\d+)$')
_URL_RE = re.compile(
    r'^https?://(?:www\.)?beatport\.com/(chart|release|genre)/([a-z0-9-]+)/(\d+)(?:/(top-100|hype-100|hype-10))?/?$',
    re.I)

_HOME_NAMES = {
    'top-10': 'Beatport Top 10',
    'hype-10': 'Hype Top 10',
    'top-100': 'Beatport Top 100',
    'hype-100': 'Hype Top 100',
}


@dataclass(frozen=True)
class BeatportRef:
    kind: str          # 'home', 'genre', 'chart', 'release'
    list_kind: str     # which next_data list holds the tracks
    slug: str = ''
    item_id: str = ''

    def __str__(self) -> str:
        if self.kind == 'home':
            return self.list_kind
        if self.kind == 'genre':
            return f"genre:{self.slug}:{self.item_id}:{self.list_kind}"
        return f"{self.kind}:{self.slug}:{self.item_id}"

    @property
    def url(self) -> str:
        """the page that holds this list."""
        if self.kind == 'home':
            # the top 10s live on the homepage, the 100s on their own pages
            return BASE_URL + ('/' if self.list_kind in ('top-10', 'hype-10') else f'/{self.list_kind}')
        if self.kind == 'genre':
            base = f"{BASE_URL}/genre/{self.slug}/{self.item_id}"
            return base if self.list_kind in ('top-10', 'hype-10') else f"{base}/{self.list_kind}"
        return f"{BASE_URL}/{self.kind}/{self.slug}/{self.item_id}"

    def default_name(self, genre_name: str = '') -> str:
        if self.kind == 'home':
            return _HOME_NAMES[self.list_kind]
        if self.kind == 'genre':
            label = genre_name or self.slug.replace('-', ' ').title()
            return f"{label} {_HOME_NAMES[self.list_kind].replace('Beatport ', '')}"
        return self.slug.replace('-', ' ').title()

    @property
    def refreshable(self) -> bool:
        """a release never changes, everything else is a live list."""
        return self.kind != 'release'


def parse_ref(text: object) -> Optional[BeatportRef]:
    """a ref string back into a BeatportRef, or None when it isn't one (an old
    random chart hash, junk)."""
    value = str(text or '').strip().lower()
    if value in HOME_LISTS:
        return BeatportRef('home', value)
    m = _GENRE_RE.match(value)
    if m:
        return BeatportRef('genre', m.group(3), m.group(1), m.group(2))
    m = _ITEM_RE.match(value)
    if m:
        return BeatportRef(m.group(1), 'chart' if m.group(1) == 'chart' else 'release',
                           m.group(2), m.group(3))
    return None


def ref_from_url(url: object) -> Optional[BeatportRef]:
    """the ref for a pasted beatport url. a bare genre url means its top 10."""
    m = _URL_RE.match(str(url or '').strip())
    if not m:
        return None
    kind, slug, item_id, suffix = m.group(1).lower(), m.group(2).lower(), m.group(3), m.group(4)
    if kind == 'genre':
        return BeatportRef('genre', (suffix or 'top-10').lower(), slug, item_id)
    if suffix:
        return None
    return BeatportRef(kind, 'chart' if kind == 'chart' else 'release', slug, item_id)


def genre_ref(slug: str, genre_id: object, list_kind: str) -> Optional[BeatportRef]:
    return parse_ref(f"genre:{slug}:{genre_id}:{list_kind}")


# a dated snapshot of a list: the list's ref, an '@', the day it was taken.
# it's a frozen mirror. it never refreshes (that's the point of it) but it can
# still be identified, synced and downloaded like any playlist.
_SNAPSHOT_RE = re.compile(r'^(.+)@(\d{4}-\d{2}-\d{2})$')


def snapshot_id(ref: object, day: str) -> Optional[str]:
    parsed = ref if isinstance(ref, BeatportRef) else parse_ref(ref)
    if parsed is None or not re.match(r'^\d{4}-\d{2}-\d{2}$', str(day or '')):
        return None
    return f"{parsed}@{day}"


def parse_snapshot(text: object):
    """(BeatportRef, day) for a snapshot id, or None."""
    m = _SNAPSHOT_RE.match(str(text or '').strip())
    if not m:
        return None
    ref = parse_ref(m.group(1))
    return (ref, m.group(2)) if ref else None
