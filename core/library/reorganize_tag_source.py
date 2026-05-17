"""Build reorganize-planning metadata from a file's embedded tags
instead of from a live metadata-source API call.

Issue #592 (tacobell444): when a library has been carefully enriched
+ tagged, doing a fresh API lookup at reorganize time can introduce
inconsistencies (provider naming drift, version-mismatches, missing
album-level metadata for niche releases). The user's own embedded
tags are usually the most stable source of truth for an enriched
library — and using them costs zero API calls.

This module is the pure tag-to-context adapter. It turns the dict
that ``core.library.file_tags.read_embedded_tags`` returns into the
``api_album`` / ``api_track`` shapes that
``library_reorganize._build_post_process_context`` already consumes.
That keeps the downstream pipeline path-builder, post-process
helpers, AcoustID, etc.) completely unchanged: tag-mode just produces
the same input shape via a different upstream route.

Pure helpers — no IO inside the extractors so every shape is
test-pinnable. The wrapper :func:`read_album_track_from_file` does
the file IO via ``read_embedded_tags`` and then routes through the
extractors.

Returns ``None`` (extractors) / ``(None, None, reason)`` (wrapper)
when the embedded tags are missing fields essential for reorganize
(track title, album name, or track artist). The plan layer surfaces
that as an unmatched item with a clear reason — same UX as when the
metadata-API call returns no candidate. No silent degradation."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple


# Tokens we accept as valid `releasetype` / `albumtype` values.
# Mirrors the canonical set the rest of the metadata pipeline uses
# (`core/metadata/album_tracks.py:_normalize_album_type`).
_VALID_ALBUM_TYPES = frozenset({'album', 'single', 'ep', 'compilation'})


# Match a 4-digit year anywhere in a date-like string ("2020",
# "2020-01-15", "2020/01/15", "Jan 5, 2020", etc.).
_YEAR_RE = re.compile(r'(\d{4})')


# Separators we split a single artist field on to recover a list.
# Mirrors the same separator set ``core/metadata/artist_resolution.py``
# uses when normalizing soulseek matched-download artist strings.
_ARTIST_SPLIT_RE = re.compile(
    r'\s*(?:,|;|/|&| feat\. | feat | ft\. | ft | featuring | x | with )\s*',
    re.IGNORECASE,
)


def _stringify(value: Any) -> str:
    """Coerce an embedded-tag value into a clean string."""
    if value is None:
        return ''
    return str(value).strip()


def _parse_int_first(value: Any) -> Optional[int]:
    """Parse a track/disc number that may arrive as ``"5"``, ``"5/12"``,
    ``5``, ``5.0`` or even ``"05"``. Returns the leading integer, or
    ``None`` when no integer is recoverable.

    Defensive against the trailing-``/N`` shape ID3 stores: ``TRCK =
    "5/12"`` means "track 5 of 12", and we want ``5``."""
    if value is None:
        return None
    if isinstance(value, (int,)):
        return value
    if isinstance(value, float):
        return int(value)
    s = _stringify(value)
    if not s:
        return None
    head = s.split('/', 1)[0].strip()
    try:
        return int(head)
    except (TypeError, ValueError):
        try:
            return int(float(head))
        except (TypeError, ValueError):
            return None


def _parse_int_total(value: Any) -> Optional[int]:
    """Parse the trailing ``N`` of an ID3-style ``"5/12"`` value, or
    return the parsed value when it's a plain integer string."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = _stringify(value)
    if not s:
        return None
    if '/' in s:
        tail = s.split('/', 1)[1].strip()
        try:
            return int(tail)
        except (TypeError, ValueError):
            return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _normalize_year(value: Any) -> str:
    """Extract a 4-digit year from a date-like field. Returns '' when
    no year is extractable. Reorganize templates only use the year
    portion of release dates, so we don't need to preserve the full
    date string."""
    s = _stringify(value)
    if not s:
        return ''
    m = _YEAR_RE.search(s)
    return m.group(1) if m else ''


def _normalize_album_type(value: Any) -> str:
    """Lowercase + validate the ``releasetype`` tag against the canonical
    token set. Returns '' for unknown values so the downstream path
    builder falls back to its default."""
    s = _stringify(value).lower()
    if s in _VALID_ALBUM_TYPES:
        return s
    return ''


def _split_artists(value: Any) -> List[str]:
    """Split an artist-string field into a list. Handles common
    separators (``,``, ``;``, ``/``, ``&``, ``feat``, ``ft``, ``x``,
    ``with``). Strips whitespace, drops empties, dedupes (case-
    insensitive) while preserving order."""
    s = _stringify(value)
    if not s:
        return []
    parts = _ARTIST_SPLIT_RE.split(s)
    seen: set = set()
    out: List[str] = []
    for p in parts:
        cleaned = p.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _resolve_track_artists(tags: Dict[str, Any]) -> List[str]:
    """Resolve the per-track artist list from embedded tags. Prefers a
    multi-value ``artists`` tag (TXXX:Artists / Vorbis ``artists``)
    over splitting the single-string ``artist`` tag, which is exactly
    the precedence the post-download enrichment uses."""
    artists_value = tags.get('artists')
    if artists_value:
        # Multi-value tag readers may already have joined with ', '.
        # Re-split to recover the list.
        parts = _split_artists(artists_value)
        if parts:
            return parts
    return _split_artists(tags.get('artist') or '')


def extract_track_meta_from_tags(tags: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build an ``api_track``-shaped dict from embedded tags.

    Returns ``None`` if essential fields are missing (title or
    artist). Caller surfaces that as an unmatched plan item.

    Output shape matches what ``library_reorganize._build_post_process_context``
    consumes (``name`` / ``track_number`` / ``disc_number`` /
    ``artists`` / ``duration_ms`` / ``id``)."""
    if not isinstance(tags, dict) or not tags:
        return None

    title = _stringify(tags.get('title'))
    if not title:
        return None

    artists = _resolve_track_artists(tags)
    if not artists:
        return None

    track_number = _parse_int_first(tags.get('tracknumber')) or 1
    disc_number = _parse_int_first(tags.get('discnumber')) or 1

    return {
        'name': title,
        'title': title,  # belt-and-braces — both keys are read downstream
        'track_number': track_number,
        'disc_number': disc_number,
        'artists': [{'name': a} for a in artists],
        'duration_ms': 0,  # not derivable from tags alone; set later from `duration`
        'id': '',  # tag-mode has no source ID; reorganize doesn't need one
        'uri': '',
    }


def extract_album_meta_from_tags(tags: Dict[str, Any]) -> Dict[str, Any]:
    """Build an ``api_album``-shaped dict from embedded tags.

    Falls back to empty / zero values when fields are missing — the
    path builder accepts those and uses its own defaults. The album
    name is the only field we can't fall back on; if missing the
    caller should treat the track as unmatched (handled by
    :func:`read_album_track_from_file`)."""
    if not isinstance(tags, dict):
        tags = {}

    album_name = _stringify(tags.get('album'))
    album_artist = _stringify(tags.get('albumartist') or tags.get('album_artist'))
    release_date = _normalize_year(tags.get('date') or tags.get('year') or tags.get('originaldate'))
    total_tracks = (
        _parse_int_total(tags.get('totaltracks'))
        or _parse_int_total(tags.get('tracktotal'))
        or _parse_int_total(tags.get('tracknumber'))  # may be "5/12"
        or 0
    )
    album_type = _normalize_album_type(tags.get('releasetype'))

    # `total_discs` only comes from explicit total signals: a
    # `totaldiscs` tag, or the trailing `/N` of an ID3-style
    # `discnumber = "1/2"`. A bare `discnumber = "1"` carries no total
    # and must NOT be treated as one (else single-disc albums would
    # claim total=1 and the path builder would still skip the
    # subfolder, but partial-album cases would underreport).
    total_discs = _parse_int_total(tags.get('totaldiscs')) or 0
    discnumber_raw = _stringify(tags.get('discnumber'))
    if '/' in discnumber_raw:
        explicit_total = _parse_int_total(discnumber_raw)
        if explicit_total:
            total_discs = max(total_discs, explicit_total)

    return {
        'id': '',
        'album_id': '',
        'name': album_name,
        'title': album_name,
        'release_date': release_date,
        'total_tracks': total_tracks,
        'total_discs': total_discs,
        'image_url': '',
        'images': [],
        'album_artist': album_artist,
        'album_type': album_type,
    }


def read_album_track_from_file(
    file_path: str,
    *,
    read_embedded_tags_fn=None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    """Read embedded tags from ``file_path`` and produce
    ``(album_meta, track_meta, error_reason)``.

    Returns ``(None, None, reason)`` when the file can't be opened,
    has no recognisable tags, or is missing essential fields (title
    or artist). The reason string is human-readable and suitable for
    surfacing directly in the reorganize preview/error UI.

    Args:
        file_path: Resolved on-disk path to the audio file.
        read_embedded_tags_fn: Optional override for the tag reader,
            used by tests to avoid real mutagen IO. Defaults to
            ``core.library.file_tags.read_embedded_tags``."""
    if not file_path or not isinstance(file_path, str):
        return None, None, 'No file path on track row.'

    if read_embedded_tags_fn is None:
        from core.library.file_tags import read_embedded_tags as _real_reader
        read_embedded_tags_fn = _real_reader

    result = read_embedded_tags_fn(file_path)
    if not isinstance(result, dict) or not result.get('available'):
        reason = (result or {}).get('reason') if isinstance(result, dict) else None
        return None, None, reason or 'Could not read embedded tags from file.'

    tags = result.get('tags') or {}
    track_meta = extract_track_meta_from_tags(tags)
    if track_meta is None:
        return None, None, 'Embedded tags missing required title or artist.'

    album_meta = extract_album_meta_from_tags(tags)
    if not album_meta.get('name'):
        return None, None, 'Embedded tags missing album name.'

    # Promote duration from the file-info block onto the track meta
    # so the path builder has a non-zero value if a downstream
    # consumer wants it.
    duration_seconds = result.get('duration') or 0
    try:
        track_meta['duration_ms'] = int(float(duration_seconds) * 1000)
    except (TypeError, ValueError):
        track_meta['duration_ms'] = 0

    return album_meta, track_meta, None


def normalize_resolved_path(file_path: Optional[str]) -> Optional[str]:
    """Defensive wrapper: returns the input only when it points at a
    real file. Saves the caller from another ``os.path.exists`` check
    in already-noisy code paths."""
    if not file_path:
        return None
    try:
        if not os.path.exists(file_path):
            return None
    except OSError:
        return None
    return file_path


__all__ = [
    'extract_track_meta_from_tags',
    'extract_album_meta_from_tags',
    'read_album_track_from_file',
    'normalize_resolved_path',
]
