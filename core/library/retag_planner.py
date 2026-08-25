"""Matching + planning for the library re-tag job.

Given a source album's metadata + tracklist and the library's tracks (with their
*current* file tags), this works out — per track — exactly which tags would
change (the dry-run diff the finding shows) and the ``db_data`` payload to feed
``core.tag_writer.write_tags_to_file`` at apply time.

The diff itself is NOT decided here. ``core.tag_writer.build_tag_diff`` makes
it, because that is the function ``write_tags_to_file`` agrees with by
construction: the #800 placeholder guard, the #824 date normalisation and the
genre-subset guard all live in that pair. This module used to carry its own
comparison and knew none of them, so it reported changes the writer then
refused — and since a pending finding is refreshed in place rather than
re-inserted, those never went away.

What is left here is the part the shared engine has no opinion about: pairing a
library track to a source track, shaping the source's values into a write
payload, and honouring ``mode``.

No file IO, no network, no DB: the job feeds in current tags + fetched source
data, so all of it stays unit-testable. Tags are only ever ADDED/overwritten
per-field — never a full tag-block wipe.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from core.tag_writer import build_tag_diff

# Modes: overwrite everything the source provides, or only fill blanks.
MODE_OVERWRITE = 'overwrite'
MODE_FILL_MISSING = 'fill_missing'


def _get(obj: Any, *keys: str, default=None):
    """First non-empty value across keys, from a dict or an object."""
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else getattr(obj, k, None)
        if v not in (None, ''):
            return v
    return default


def _first_artist(obj: Any) -> str:
    arts = _get(obj, 'artists', 'artist', 'artist_name')
    if isinstance(arts, list) and arts:
        a0 = arts[0]
        return ((a0.get('name') if isinstance(a0, dict) else str(a0)) or '').strip()
    if isinstance(arts, dict):
        return (arts.get('name') or '').strip()
    return str(arts).strip() if arts else ''


def _genres_list(obj: Any) -> List[str]:
    g = _get(obj, 'genres', 'genre')
    if isinstance(g, list):
        return [str(x).strip() for x in g if str(x).strip()]
    if isinstance(g, str) and g.strip():
        return [p.strip() for p in g.split(',') if p.strip()]
    return []


def _year(obj: Any) -> str:
    v = _get(obj, 'year', 'release_date', 'date')
    if not v:
        return ''
    m = re.search(r'\d{4}', str(v))
    return m.group(0) if m else ''


def _int_or_none(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm_title(s: Any) -> str:
    s = (s or '')
    s = s.lower() if isinstance(s, str) else str(s).lower()
    s = re.sub(r'[\(\[].*?[\)\]]', ' ', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(s.split())


def match_source_tracks(
    source_tracks: List[Any],
    library_tracks: List[Dict[str, Any]],
    title_threshold: float = 0.6,
) -> List[Tuple[Dict[str, Any], Optional[Any]]]:
    """Pair each library track to a source track.

    Disc+track number is authoritative; falls back to title similarity. A source
    track is consumed once. Returns ``[(library_track, source_track_or_None)]``
    in library order, so unmatched library tracks surface as ``None``.
    """
    by_pos: Dict[Tuple[int, int], int] = {}
    for i, st in enumerate(source_tracks):
        t = _int_or_none(_get(st, 'track_number'))
        if t is None:
            continue
        d = _int_or_none(_get(st, 'disc_number', default=1)) or 1
        by_pos.setdefault((d, t), i)

    used: set = set()
    pairs: List[Tuple[Dict[str, Any], Optional[Any]]] = []
    for lt in library_tracks:
        t = _int_or_none(lt.get('track_number'))
        d = _int_or_none(lt.get('disc_number')) or 1
        idx = by_pos.get((d, t)) if t is not None else None
        if idx is not None and idx not in used:
            used.add(idx)
            pairs.append((lt, source_tracks[idx]))
            continue
        # Title-similarity fallback over still-unused source tracks.
        lt_norm = _norm_title(lt.get('title'))
        best_idx, best_score = None, 0.0
        if lt_norm:
            for i, st in enumerate(source_tracks):
                if i in used:
                    continue
                score = SequenceMatcher(None, lt_norm, _norm_title(_get(st, 'name', 'title', 'track_name'))).ratio()
                if score > best_score:
                    best_score, best_idx = score, i
        if best_idx is not None and best_score >= title_threshold:
            used.add(best_idx)
            pairs.append((lt, source_tracks[best_idx]))
        else:
            pairs.append((lt, None))
    return pairs


def _target_for_track(source_track: Any, album_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Normalized target tag values from the source for one track."""
    album_artist = _first_artist(album_meta)
    track_artist = _first_artist(source_track) or album_artist
    return {
        'title': (_get(source_track, 'name', 'title', 'track_name') or '').strip(),
        'artist': album_artist,                 # album-level artist
        'track_artist': track_artist,           # per-track (may equal album artist)
        'album': (_get(album_meta, 'name', 'title', 'album_name') or '').strip(),
        'year': _year(album_meta),
        'genre': _genres_list(album_meta),      # list
        'track_number': _int_or_none(_get(source_track, 'track_number')),
        'disc_number': _int_or_none(_get(source_track, 'disc_number', default=1)) or 1,
        'track_count': _int_or_none(_get(album_meta, 'total_tracks', 'track_count')),
    }


def _write_payload(target: Dict[str, Any]) -> Dict[str, Any]:
    """The complete ``db_data`` the source implies — every field it supplied.

    ``build_tag_diff`` compares a whole payload at once; :func:`plan_track`
    then keeps only the keys whose field really changed, so an apply still
    touches nothing the finding didn't show.
    """
    data: Dict[str, Any] = {}
    if target.get('title'):
        data['title'] = target['title']
    if target.get('artist'):
        data['artist_name'] = target['artist']            # album-level artist
    if target.get('track_artist'):
        data['track_artist'] = target['track_artist']     # per-track (compilations)
    if target.get('album'):
        data['album_title'] = target['album']
    if target.get('year'):
        # Deliberately the year and NOT ``release_date``. The source supplies an
        # album-level date while a file may carry a more specific one; with a
        # year-only value build_tag_diff preserves the file's (#824) instead of
        # flattening every dated file in the library on the first scan.
        data['year'] = target['year']
    if target.get('genre'):
        data['genres'] = target['genre']                  # list
    if target.get('track_number') is not None:
        data['track_number'] = target['track_number']
        if target.get('track_count'):
            data['track_count'] = target['track_count']   # writers want both
    if target.get('disc_number') is not None:
        data['disc_number'] = target['disc_number']
    return data


#: ``build_tag_diff``'s ``file_key`` -> the ``db_data`` keys that field writes.
#: Doubles as the list of fields this job manages: a diff row outside it (BPM)
#: is one nothing here supplies a value for.
_WRITE_KEYS = {
    'title': ('title',),
    'artist': ('track_artist',),
    'album': ('album_title',),
    'album_artist': ('artist_name',),
    'year': ('year',),
    'genre': ('genres',),
    'track_number': ('track_number', 'track_count'),
    'disc_number': ('disc_number',),
}


def plan_track(current_tags: Dict[str, Any], source_track: Any, album_meta: Dict[str, Any],
               mode: str = MODE_OVERWRITE) -> Dict[str, Any]:
    """Diff one library track's current tags against the source target.

    Returns ``{changes, db_data, protected}``:

    * ``changes`` — ``{field: {old, new}}`` for display
    * ``db_data`` — the MINIMAL payload for ``write_tags_to_file``: only the
      fields that should be written under ``mode``
    * ``protected`` — ``{field: {file, source}}`` for fields the writer's own
      guards hold back, so the finding can say "kept yours" instead of
      promising a change that will not happen

    The decision itself belongs to ``core.tag_writer.build_tag_diff``, which
    is the function ``write_tags_to_file`` agrees with — the placeholder guard
    (#800), the date normalisation (#824) and the genre-subset guard all live
    there. A second opinion here is a finding the fix cannot resolve.
    """
    target = _target_for_track(source_track, album_meta)
    payload = _write_payload(target)

    changes: Dict[str, Dict[str, str]] = {}
    protected: Dict[str, Dict[str, str]] = {}
    keep: set = set()

    for row in build_tag_diff(current_tags or {}, payload):
        field = row.get('file_key')
        if field not in _WRITE_KEYS:
            continue
        if row.get('protected'):
            protected[field] = {'file': row.get('file_value') or '',
                                'source': row.get('db_value') or ''}
            continue
        if not row.get('changed'):
            continue
        if mode == MODE_FILL_MISSING and str(row.get('file_value') or '').strip():
            continue                     # fill-missing only writes blanks
        changes[field] = {'old': row.get('file_value') or '',
                          'new': row.get('db_value') or ''}
        keep.update(_WRITE_KEYS[field])

    return {
        'changes': changes,
        'db_data': {k: v for k, v in payload.items() if k in keep},
        'protected': protected,
    }
