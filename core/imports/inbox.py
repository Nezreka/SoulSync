"""The import inbox: every staging item with the one state it is in.

the page used to show the same folder three ways. the worker's results feed
knew it as a history row, the albums tab as a tag group, the singles tab as
a checkbox per file, and none of them knew about the others. this joins
the three on the worker's own unit, the candidate (an album folder, a
loose-file group, or a single file), so one row can say "here is what is in
the folder and here is what has happened to it".

pure: takes candidates, per-file records, history rows and the worker's
live state, returns rows. the route is the only thing that touches the
worker or the db.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional

# history status -> inbox status. the worker's vocabulary is what it does;
# the inbox's is what the user has to do about it.
_HISTORY_STATUS = {
    'pending_review': 'needs_review',
    'needs_identification': 'needs_identify',
    'approved': 'queued',
    'processing': 'importing',
    'completed': 'imported',
    'partial': 'imported',
    'failed': 'failed',
    'rejected': 'dismissed',
}

# history rows whose files are gone from staging are worth keeping only when
# they are a record of something. a stale "needs identify" for a folder the
# user already moved away is noise.
_KEEP_WITHOUT_FILES = {'imported', 'failed', 'dismissed'}

# active-import phases -> inbox status
_LIVE_STATUS = {
    'queued': 'queued',
    'identifying': 'identifying',
    'matching': 'identifying',
    'processing': 'importing',
}

_FORMAT_LABELS = {
    '.flac': 'FLAC', '.mp3': 'MP3', '.m4a': 'AAC', '.aac': 'AAC', '.ogg': 'OGG',
    '.opus': 'OPUS', '.wav': 'WAV', '.wma': 'WMA', '.aiff': 'AIFF', '.aif': 'AIFF',
    '.ape': 'APE',
}


def format_label(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _FORMAT_LABELS.get(ext, ext.lstrip('.').upper() or '?')


def derive_status(history_status: Optional[str], live_status: Optional[str], in_staging: bool) -> str:
    """what the row says. live wins (the worker is on it right now), then the
    history row, then "waiting" for files nobody has looked at yet."""
    if live_status and live_status in _LIVE_STATUS:
        return _LIVE_STATUS[live_status]
    if history_status:
        return _HISTORY_STATUS.get(history_status, history_status)
    return 'waiting' if in_staging else 'imported'


def _parse_match(raw: Any) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    matches = []
    for m in data.get('matches') or []:
        if not isinstance(m, dict):
            continue
        track = m.get('track') if isinstance(m.get('track'), dict) else {}
        file_path = m.get('file') or ''
        matches.append({
            'track_name': m.get('track_name') or track.get('name') or '',
            'track_number': m.get('track_number') or track.get('track_number'),
            'file': os.path.basename(file_path) if file_path else '',
            'file_path': file_path,
            'confidence': float(m.get('confidence') or 0),
        })
    return {
        'matched_count': int(data.get('matched_count') or len([m for m in matches if m['file']])),
        'total_tracks': int(data.get('total_tracks') or 0),
        'matches': matches,
        'source': data.get('source') or None,
    }


def _file_row(path: str, record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    r = record or {}
    return {
        'filename': os.path.basename(path),
        'full_path': path,
        'rel_path': r.get('rel_path') or os.path.basename(path),
        'title': r.get('title') or '',
        'artist': r.get('albumartist') or r.get('artist') or '',
        'album': r.get('album') or '',
        'track_number': r.get('track_number'),
        'disc_number': r.get('disc_number'),
        'extension': os.path.splitext(path)[1].lower(),
        'format': format_label(path),
        'duration_ms': int(r.get('duration_ms') or 0),
        'bitrate': int(r.get('bitrate') or 0),
        'size': int(r.get('size') or 0),
    }


def _most_common(values: Iterable[str]) -> str:
    counts: Dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return ''
    return max(counts.items(), key=lambda kv: kv[1])[0]


def build_inbox(
    candidates: List[Any],
    records: List[Dict[str, Any]],
    history: List[Dict[str, Any]],
    active: List[Dict[str, Any]],
    staging_root: str = '',
) -> List[Dict[str, Any]]:
    """one row per candidate, plus history rows whose files are gone.

    ``candidates`` are the worker's FolderCandidate objects (anything with
    path / name / audio_files / folder_hash / is_single). ``records`` are the
    page scan's per-file rows keyed by full_path. ``history`` is
    auto_import_history, newest first. ``active`` is the worker's
    active_imports snapshot.
    """
    by_path = {r.get('full_path'): r for r in records if r.get('full_path')}
    live_by_hash = {a.get('folder_hash'): a for a in active if a.get('folder_hash')}

    # newest history row per hash, and per folder path as the fallback for a
    # folder whose file list changed since the worker last saw it.
    hist_by_hash: Dict[str, Dict[str, Any]] = {}
    hist_by_path: Dict[str, Dict[str, Any]] = {}
    for row in history:
        h = row.get('folder_hash')
        p = row.get('folder_path')
        if h and h not in hist_by_hash:
            hist_by_hash[h] = row
        if p and os.path.normpath(p) not in hist_by_path:
            hist_by_path[os.path.normpath(p)] = row

    rows: List[Dict[str, Any]] = []
    claimed_history_ids = set()

    for cand in candidates:
        files = [_file_row(f, by_path.get(f)) for f in sorted(cand.audio_files)]
        hist = hist_by_hash.get(cand.folder_hash) or hist_by_path.get(os.path.normpath(cand.path))
        if hist is not None:
            claimed_history_ids.add(hist.get('id'))
        live = live_by_hash.get(cand.folder_hash)
        status = derive_status(
            hist.get('status') if hist else None,
            live.get('status') if live else None,
            True,
        )
        match = _parse_match(hist.get('match_data')) if hist else None
        rel = os.path.relpath(cand.path, staging_root) if staging_root else cand.path
        if rel == '.':
            rel = ''
        rows.append({
            'key': cand.folder_hash,
            'kind': 'single' if getattr(cand, 'is_single', False) else 'album',
            'name': (hist or {}).get('album_name') or _most_common(f['album'] for f in files)
                    or (files[0]['title'] if getattr(cand, 'is_single', False) and files else cand.name),
            'artist': (hist or {}).get('artist_name') or _most_common(f['artist'] for f in files),
            'folder_name': cand.name,
            'folder_path': cand.path,
            'rel_path': rel,
            'in_staging': True,
            'files': files,
            'file_count': len(files),
            'total_duration_ms': sum(f['duration_ms'] for f in files),
            'total_size': sum(f['size'] for f in files),
            'formats': sorted({f['format'] for f in files}),
            'status': status,
            'confidence': float(hist['confidence']) if hist and hist.get('confidence') is not None else None,
            'image_url': (hist or {}).get('image_url'),
            'album_id': (hist or {}).get('album_id'),
            'identification_method': (hist or {}).get('identification_method'),
            'error_message': (hist or {}).get('error_message'),
            'match': match,
            'history_id': (hist or {}).get('id'),
            'created_at': (hist or {}).get('created_at'),
            'processed_at': (hist or {}).get('processed_at'),
            'live': {
                'track_index': live.get('track_index', 0),
                'track_total': live.get('track_total', 0),
                'track_name': live.get('track_name', ''),
            } if live else None,
        })

    for row in history:
        if row.get('id') in claimed_history_ids:
            continue
        status = derive_status(row.get('status'), None, False)
        if status not in _KEEP_WITHOUT_FILES:
            continue
        rows.append({
            'key': row.get('folder_hash') or f"history-{row.get('id')}",
            'kind': 'album' if (row.get('total_files') or 0) > 1 else 'single',
            'name': row.get('album_name') or row.get('folder_name') or '',
            'artist': row.get('artist_name') or '',
            'folder_name': row.get('folder_name') or '',
            'folder_path': row.get('folder_path') or '',
            'rel_path': row.get('folder_name') or '',
            'in_staging': False,
            'files': [],
            'file_count': int(row.get('total_files') or 0),
            'total_duration_ms': 0,
            'total_size': 0,
            'formats': [],
            'status': status,
            'confidence': float(row['confidence']) if row.get('confidence') is not None else None,
            'image_url': row.get('image_url'),
            'album_id': row.get('album_id'),
            'identification_method': row.get('identification_method'),
            'error_message': row.get('error_message'),
            'match': _parse_match(row.get('match_data')),
            'history_id': row.get('id'),
            'created_at': row.get('created_at'),
            'processed_at': row.get('processed_at'),
            'live': None,
        })

    return rows


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """the header strip's numbers."""
    staged = [r for r in rows if r['in_staging']]
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    return {
        'items': len(staged),
        'files': sum(r['file_count'] for r in staged),
        'size': sum(r['total_size'] for r in staged),
        'attention': sum(1 for r in staged if r['status'] in ('needs_review', 'needs_identify', 'failed', 'waiting')),
        'by_status': counts,
    }
