"""Pure planning logic for the library re-tag job.

Given a source album's metadata + tracklist and the library's tracks (with their
*current* file tags), this works out — per track — exactly which tags would
change (the dry-run diff the finding shows) and the ``db_data`` payload to feed
``core.tag_writer.write_tags_to_file`` at apply time.

No file IO, no network, no DB: the job feeds in current tags + fetched source
data, so all the matching/diff logic stays unit-testable. Tags are only ever
ADDED/overwritten per-field — never a full tag-block wipe.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

# Fields this job manages. Keys are the internal/display names; the diff and the
# write payload are both built from these.
MANAGED_FIELDS = ('title', 'artist', 'album', 'year', 'genre', 'track_number', 'disc_number')

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


def _current_value(current_tags: Dict[str, Any], field: str):
    if field == 'artist':
        # _read_tags stores album_artist + artist; prefer album_artist for the album-level compare.
        return current_tags.get('album_artist') or current_tags.get('artist') or ''
    if field == 'genre':
        return current_tags.get('genre') or ''
    return current_tags.get(field)


def _display(value) -> str:
    if isinstance(value, list):
        return ', '.join(str(v) for v in value)
    return '' if value is None else str(value)


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ''
    if isinstance(value, list):
        return len(value) == 0
    return False


def plan_track(current_tags: Dict[str, Any], source_track: Any, album_meta: Dict[str, Any],
               mode: str = MODE_OVERWRITE) -> Dict[str, Any]:
    """Diff one library track's current tags against the source target.

    Returns ``{changes, db_data}`` where ``changes`` is ``{field: {old, new}}``
    for display, and ``db_data`` is the (minimal) payload for
    ``write_tags_to_file`` — it contains ONLY the fields that should be written
    under ``mode``, so applying never touches unrelated/unchanged tags.
    """
    target = _target_for_track(source_track, album_meta)
    changes: Dict[str, Dict[str, str]] = {}
    db_data: Dict[str, Any] = {}

    for field in MANAGED_FIELDS:
        new_val = target.get(field)
        if _is_empty(new_val):
            continue  # source gave us nothing for this field — leave the file alone
        old_val = _current_value(current_tags, field)

        if mode == MODE_FILL_MISSING and not _is_empty(old_val):
            continue  # fill-missing only writes blanks

        old_disp, new_disp = _display(old_val), _display(new_val)
        if old_disp == new_disp:
            continue  # already correct — nothing to write

        changes[field] = {'old': old_disp, 'new': new_disp}
        # Map managed field → write_tags_to_file db_data key.
        if field == 'title':
            db_data['title'] = new_val
        elif field == 'artist':
            db_data['artist_name'] = new_val           # = album artist for the writer
            ta = target.get('track_artist')
            if ta and ta != new_val:
                db_data['track_artist'] = ta
        elif field == 'album':
            db_data['album_title'] = new_val
        elif field == 'year':
            db_data['year'] = new_val
        elif field == 'genre':
            db_data['genres'] = new_val                # list
        elif field == 'track_number':
            db_data['track_number'] = new_val
        elif field == 'disc_number':
            db_data['disc_number'] = new_val

    # Always carry track_count alongside a track_number write (writers want both).
    if 'track_number' in db_data and target.get('track_count'):
        db_data['track_count'] = target['track_count']

    return {'changes': changes, 'db_data': db_data}
