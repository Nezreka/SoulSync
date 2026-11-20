"""An album folder imports as one unit (#2220).

two things split one folder over several releases:

- the release the search picked lacks some of the folder's songs (a standard
  edition picked for a deluxe rip, one of a mixtape's several uploads). the
  rest stayed in Import, and

- whatever was left got identified again on its own. a handful of files
  scores the release search differently (track count is part of the score),
  so the leftovers went to another edition and the library showed the same
  album twice.

the helpers here are pure: what counts as another edition of a release, which
history row the leftovers' siblings were imported from, and whether the
leftovers' own tags still say it's that album.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

from core.imports.album_naming import get_base_album_name

# a run that did something with a track: its file went (or already was) there
_IMPORTED_STATUSES = frozenset({'completed', 'upgraded', 'skipped_owned'})


def edition_key(name: str) -> str:
    """the album's name without its edition note, folded for comparing.
    "T.R.U. (The Real University)" and "T.R.U. (The Real University)
    (Deluxe Edition)" share one."""
    text = unicodedata.normalize('NFKC', get_base_album_name(str(name or ''))).casefold()
    return ' '.join(part for part in re.split(r'\W+', text) if part)


def _result_artist(result: Any) -> str:
    artists = getattr(result, 'artists', None) or []
    first = artists[0] if artists else ''
    if isinstance(first, dict):
        first = first.get('name', '')
    return str(first or '')


def _folded(text: str) -> str:
    return ' '.join(str(text or '').casefold().split())


def edition_alternates(best: Any, results: Iterable[Any]) -> List[Dict[str, Any]]:
    """the other search hits that are the same album by the same artist, as
    plain dicts the worker can carry in its identification. the matcher
    tries them when the picked one leaves files behind."""
    key = edition_key(getattr(best, 'name', ''))
    if not key:
        return []
    artist = _folded(_result_artist(best))
    seen = {str(getattr(best, 'id', '') or '')}
    out: List[Dict[str, Any]] = []
    for result in results:
        rid = str(getattr(result, 'id', '') or '')
        if not rid or rid in seen:
            continue
        if edition_key(getattr(result, 'name', '')) != key:
            continue
        other = _folded(_result_artist(result))
        if artist and other and other != artist:
            continue
        seen.add(rid)
        image = getattr(result, 'image_url', '') or ''
        if not image and getattr(result, 'images', None):
            first = result.images[0]
            image = first.get('url', '') if isinstance(first, dict) else str(first)
        out.append({
            'album_id': rid,
            'album_name': getattr(result, 'name', '') or '',
            'image_url': image,
            'release_date': getattr(result, 'release_date', '') or '',
            'total_tracks': getattr(result, 'total_tracks', 0) or 0,
        })
    return out


def _load(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sibling_release(rows: Iterable[Dict[str, Any]],
                    file_names: Iterable[str]) -> Optional[Dict[str, Any]]:
    """the release the rest of this folder went to, or None.

    ``rows``: the folder's history rows, newest first. a row counts when it
    names a release (album id + source), imported at least one track, and
    every file in the folder now was one of that run's files: a leftover, a
    track that failed, or a quality duplicate. a new album dropped into the
    same folder shares no file with the run and gets identified on its own.
    """
    current = {os.path.basename(str(f)) for f in file_names if f}
    if not current:
        return None
    for row in rows:
        data = _load(row.get('match_data'))
        if not data or not row.get('album_id') or not data.get('source'):
            continue
        matches = [m for m in data.get('matches') or [] if isinstance(m, dict)]
        if not any(m.get('import_status') in _IMPORTED_STATUSES for m in matches):
            continue
        run_files = {os.path.basename(str(m.get('file') or '')) for m in matches}
        run_files.update(os.path.basename(str(f)) for f in data.get('unmatched_files') or [])
        if not current <= run_files:
            continue
        return {
            'album_id': str(row['album_id']),
            'album_name': row.get('album_name') or '',
            'artist_name': row.get('artist_name') or '',
            'image_url': row.get('image_url') or '',
            'source': data['source'],
            'total_tracks': int(data.get('total_tracks') or 0),
        }
    return None


def tags_agree_with_album(album_tags: Iterable[str], album_name: str,
                          similarity=None) -> bool:
    """whether the leftovers' album tags still say they're ``album_name``.
    untagged files say nothing against it. most of the tagged ones have to
    agree: a stray from another album in the same folder keeps its own
    identification."""
    tagged = [t for t in album_tags if str(t or '').strip()]
    if not tagged:
        return True
    key = edition_key(album_name)

    def agrees(tag: str) -> bool:
        if edition_key(tag) == key:
            return True
        if similarity is None:
            return False
        try:
            return similarity(tag, album_name) >= 0.8
        except Exception:
            return False

    return sum(1 for t in tagged if agrees(t)) * 2 > len(tagged)


__all__ = [
    'edition_key',
    'edition_alternates',
    'sibling_release',
    'tags_agree_with_album',
]
