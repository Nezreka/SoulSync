"""Assemble a per-track detail view for the download-modal "track detail" modal.

Merges a live download task with its ``library_history`` record (the same data
the Download History cards render) into one dict the frontend modal consumes.
Kept pure + importable so the merge + status classification are unit-tested
without Flask or the DB; the web endpoint is thin glue around build_track_detail.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# error_message substrings that mean "quarantined" (file recoverable) rather
# than a plain failure. Mirrors the download-modal status renderer.
_QUARANTINE_KEYWORDS = (
    'integrity check failed',
    'bit depth filter',
    'verification failed',
    'quarantin',
)


def classify_status_kind(status: str, error_message: str = '') -> str:
    """Map a raw task status to a UI 'kind' that drives the modal layout:
    completed / quarantined / failed / not_found / in_progress.
    """
    s = (status or '').lower()
    if s == 'completed':
        return 'completed'
    if s in ('failed', 'cancelled'):
        em = (error_message or '').lower()
        if any(k in em for k in _QUARANTINE_KEYWORDS):
            return 'quarantined'
        return 'failed'
    if s == 'not_found':
        return 'not_found'
    return 'in_progress'


def _first_artist(track_info: Dict[str, Any]) -> str:
    artists = track_info.get('artists') or []
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            return (first.get('name') or '').strip()
        return str(first).strip()
    return (track_info.get('artist') or track_info.get('artist_name') or '').strip()


def _album_name(track_info: Dict[str, Any]) -> str:
    album = track_info.get('album')
    if isinstance(album, dict):
        return (album.get('name') or '').strip()
    return (album or '').strip() if isinstance(album, str) else ''


def build_track_detail(task: Dict[str, Any], history: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge a download task (+ optional library_history row) into one detail dict.

    The task supplies live status/source/reason/quarantine id; the history row
    (when found) supplies the durable provenance — final file path, quality,
    AcoustID verdict, source, and the expected-vs-downloaded comparison.
    """
    ti = task.get('track_info') if isinstance(task.get('track_info'), dict) else {}
    status = task.get('status', '') or ''
    kind = classify_status_kind(status, task.get('error_message', '') or '')

    detail: Dict[str, Any] = {
        'task_id': task.get('task_id') or task.get('id') or '',
        'status': status,
        'status_kind': kind,
        'title': (ti.get('name') or '').strip(),
        'artist': _first_artist(ti),
        'album': _album_name(ti),
        'source': (task.get('username') or '').strip(),
        'reason': (task.get('error_message') or '').strip(),
        'quarantine_entry_id': task.get('quarantine_entry_id') or '',
        'file_path': (task.get('filename') or '').strip(),
        'quality': '',
        'acoustid_result': '',
        'thumb_url': '',
        'expected': {},
        'downloaded': {},
    }

    if history:
        detail['file_path'] = (history.get('file_path') or detail['file_path'])
        detail['quality'] = history.get('quality') or ''
        detail['acoustid_result'] = history.get('acoustid_result') or ''
        detail['source'] = history.get('download_source') or detail['source']
        detail['thumb_url'] = history.get('thumb_url') or ''
        detail['downloaded'] = {
            'title': history.get('title') or '',
            'artist': history.get('artist_name') or '',
            'album': history.get('album_name') or '',
        }
        detail['expected'] = {
            'title': history.get('source_track_title') or '',
            'artist': history.get('source_artist') or '',
        }
        # Fall back to history values when the task had none.
        detail['title'] = detail['title'] or detail['downloaded']['title']
        detail['artist'] = detail['artist'] or detail['downloaded']['artist']
        detail['album'] = detail['album'] or detail['downloaded']['album']

    return detail
