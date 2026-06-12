"""Export an artist roster — watchlist OR library — to JSON / CSV / plain text
(corruption's request).

Pure shaping + formatting so it's the single source of truth and unit-testable —
web_server fetches the artists (normalizing each source's fields onto the canonical
``*_artist_id`` keys below) and hands them here; the UI just picks options and
downloads. Always exports the name + whatever source IDs each artist has;
``include_links`` adds external discography URLs; ``extra_fields`` passes through
source-specific extras (e.g. library album/track counts) in a stable order.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Dict, List, Optional

# Canonical id field → external URL builder.
_LINKS = {
    'spotify_artist_id':     lambda i: f'https://open.spotify.com/artist/{i}',
    'musicbrainz_artist_id': lambda i: f'https://musicbrainz.org/artist/{i}',
    'deezer_artist_id':      lambda i: f'https://www.deezer.com/artist/{i}',
    'discogs_artist_id':     lambda i: f'https://www.discogs.com/artist/{i}',
    'itunes_artist_id':      lambda i: f'https://music.apple.com/artist/{i}',
    'tidal_artist_id':       lambda i: f'https://tidal.com/artist/{i}',
    'qobuz_artist_id':       lambda i: f'https://www.qobuz.com/artist/{i}',
}
# Stable order so CSV columns + JSON keys are deterministic. amazon carries an id
# but no clean public URL.
_ID_FIELDS = ['spotify_artist_id', 'musicbrainz_artist_id', 'deezer_artist_id',
              'discogs_artist_id', 'itunes_artist_id', 'tidal_artist_id',
              'qobuz_artist_id', 'amazon_artist_id']

VALID_FORMATS = ('json', 'csv', 'txt')


def _name(a: Dict[str, Any]) -> str:
    return str(a.get('artist_name') or a.get('name') or '').strip()


def _short(field: str) -> str:
    return field.replace('_artist_id', '')


def _row(a: Dict[str, Any], include_links: bool, extra_fields: List[str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {'name': _name(a)}
    for f in _ID_FIELDS:
        if a.get(f):
            row[f] = str(a[f])
    for f in extra_fields:
        if a.get(f) not in (None, ''):
            row[f] = a[f]
    if include_links:
        links = {_short(f): b(a[f]) for f, b in _LINKS.items() if a.get(f)}
        if links:
            row['links'] = links
    return row


def build_artist_export(artists: Optional[List[Dict[str, Any]]],
                        fmt: str = 'json', include_links: bool = False,
                        extra_fields: Optional[List[str]] = None) -> str:
    """Return the roster serialized in ``fmt`` (json | csv | txt).

    - ``txt``  → one artist name per line.
    - ``csv``  → name + each source-id column + ``extra_fields`` columns (+ a
      *_url column per service when ``include_links``).
    - ``json`` → a list of objects: name, present source ids, present extras, and
      a ``links`` map when ``include_links``.
    """
    artists = artists or []
    extra_fields = list(extra_fields or [])
    fmt = (fmt or 'json').lower()
    if fmt not in VALID_FORMATS:
        fmt = 'json'

    if fmt == 'txt':
        return '\n'.join(n for n in (_name(a) for a in artists) if n)

    if fmt == 'csv':
        cols = ['name'] + _ID_FIELDS + extra_fields
        if include_links:
            cols += [f'{_short(f)}_url' for f in _LINKS]
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(cols)
        for a in artists:
            line = [_name(a)] + [str(a.get(f) or '') for f in _ID_FIELDS]
            line += [str(a.get(f) if a.get(f) is not None else '') for f in extra_fields]
            if include_links:
                line += [_LINKS[f](a[f]) if a.get(f) else '' for f in _LINKS]
            w.writerow(line)
        return out.getvalue()

    return json.dumps([_row(a, include_links, extra_fields) for a in artists],
                      indent=2, ensure_ascii=False)


def export_mime_and_ext(fmt: str):
    """(content-type, file extension) for a format."""
    return {
        'json': ('application/json', 'json'),
        'csv': ('text/csv', 'csv'),
        'txt': ('text/plain', 'txt'),
    }.get((fmt or 'json').lower(), ('application/json', 'json'))


__all__ = ['build_artist_export', 'export_mime_and_ext', 'VALID_FORMATS']
