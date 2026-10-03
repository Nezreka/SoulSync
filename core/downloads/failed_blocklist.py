"""Persistent blocklist of downloads that failed import terminally.

This is NOT the user's download blocklist (files they flagged as bad matches)
and NOT the quarantine (files parked for review): when a download burns
through every quarantine retry and the import gives up for good, the file's
fingerprint lands here for 90 days so the next search doesn't pick the same
poisoned file again.

Fingerprint: SHA1 of ``service | normalized artist | normalized title |
size``. Soulseek peers all collapse to the service ``soulseek`` — a username
is a peer, not a source.

Everything fails open: a blocklist read or write must never sink a download
or an import.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("downloads.failed_blocklist")

EXPIRY_DAYS = 90
MAX_ENTRIES = 5000

# Usernames that ARE the service; anything else on a download is a peer.
_SERVICE_USERNAMES = frozenset({
    'youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr',
    'soundcloud', 'amazon', 'torrent', 'usenet',
})

try:  # pragma: no cover - import-time fallback only
    from core.text.normalize import normalize_key as _normalize_key
except Exception:  # noqa: BLE001
    def _normalize_key(text: Any) -> str:
        return str(text or '').strip().casefold()


def normalize_service(service: Any) -> str:
    """Soulseek peers collapse to ``soulseek``; service names pass through."""
    svc = str(service or '').strip().casefold()
    if not svc:
        return ''
    if 'soulseek' in svc or svc in ('slskd', 'slsk'):
        return 'soulseek'
    return svc


def service_for_username(username: Any) -> str:
    """The blocklist service for a download's username: a service username
    stays itself, a peer collapses to ``soulseek``."""
    name = str(username or '').strip().casefold()
    if name in _SERVICE_USERNAMES:
        return name
    return 'soulseek' if name else ''


def fingerprint(service: Any, artist: Any, title: Any, size: Any = 0) -> str:
    """SHA1 over service + normalized artist/title + size bytes."""
    try:
        size_n = int(size or 0)
    except (TypeError, ValueError):
        size_n = 0
    basis = '\x1f'.join((normalize_service(service), _normalize_key(artist),
                          _normalize_key(title), str(size_n)))
    return hashlib.sha1(basis.encode('utf-8')).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')


# ---- storage ------------------------------------------------------------------

def record(database, *, service: Any = '', artist: Any = '', title: Any = '',
           size: Any = 0, reason: str = '') -> bool:
    """Block a fingerprint for EXPIRY_DAYS. Re-recording refreshes the
    expiry. Oldest rows past MAX_ENTRIES are dropped. Fail-open."""
    fp = fingerprint(service, artist, title, size)
    try:
        size_n = int(size or 0)
    except (TypeError, ValueError):
        size_n = 0
    try:
        return bool(database.record_failed_download(
            fp, normalize_service(service), str(artist or '')[:300],
            str(title or '')[:300], size_n, str(reason or '')[:200],
            _stamp(_now() + timedelta(days=EXPIRY_DAYS)), MAX_ENTRIES))
    except Exception as exc:  # noqa: BLE001 - the blocklist never sinks a write
        logger.debug("failed-blocklist record failed: %s", exc)
        return False


def is_blocked(database, *, service: Any = '', artist: Any = '',
               title: Any = '', size: Any = 0) -> bool:
    """Is this fingerprint currently blocked? Fail-open: False on any error."""
    fp = fingerprint(service, artist, title, size)
    try:
        return bool(database.is_download_blocked(fp, _stamp(_now())))
    except Exception as exc:  # noqa: BLE001 - an unreadable blocklist blocks nothing
        logger.debug("failed-blocklist check failed: %s", exc)
        return False


def list_entries(database, limit: int = 200) -> List[Dict[str, Any]]:
    """Newest first, for the API. Fail-open: []."""
    try:
        return database.list_failed_downloads(limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("failed-blocklist list failed: %s", exc)
        return []


def remove(database, fingerprint_value: str) -> bool:
    """Unblock one fingerprint. Fail-open: False on any error."""
    try:
        return bool(database.remove_failed_download(fingerprint_value))
    except Exception as exc:  # noqa: BLE001
        logger.debug("failed-blocklist remove failed: %s", exc)
        return False


def clear_expired(database) -> int:
    """Delete expired rows. Fail-open: 0 on any error."""
    try:
        return int(database.clear_expired_failed_downloads(_stamp(_now())))
    except Exception as exc:  # noqa: BLE001
        logger.debug("failed-blocklist expiry sweep failed: %s", exc)
        return 0


# ---- call sites -----------------------------------------------------------------

def is_candidate_blocked(database, candidate: Any, source_name: str = '') -> bool:
    """Would the candidate worker skip this candidate? Fail-open: False."""
    try:
        username = getattr(candidate, 'username', '') or ''
        service = service_for_username(username)
        if not service and source_name:
            service = normalize_service(source_name)
        return is_blocked(
            database,
            service=service or 'soulseek',
            artist=getattr(candidate, 'artist', '') or '',
            title=getattr(candidate, 'title', '') or '',
            size=getattr(candidate, 'size', 0) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("failed-blocklist candidate check failed: %s", exc)
        return False


def record_task_giveup(database, task: Dict[str, Any], reason: str = '') -> bool:
    """Fingerprint a download task's file at a terminal import give-up — the
    file burned every quarantine retry. Uses the wanted track's identity and
    the picked candidate's advertised size, which is what the worker-side
    check reproduces for future candidates. Fail-open."""
    try:
        track = task.get('track_info') or {}
        artist = track.get('artist') or task.get('artist') or ''
        title = (track.get('track') or track.get('title')
                 or task.get('track') or task.get('title') or '')
        if not str(artist or '').strip() and not str(title or '').strip():
            return False
        picked = task.get('picked_candidate') or {}
        size = picked.get('size') or task.get('size') or 0
        service = service_for_username(task.get('username'))
        return record(database, service=service or 'soulseek', artist=artist,
                      title=title, size=size, reason=reason)
    except Exception as exc:  # noqa: BLE001
        logger.debug("failed-blocklist give-up record failed: %s", exc)
        return False


def entry_fingerprint(entry: Optional[Dict[str, Any]]) -> str:
    """Recompute a listed entry's fingerprint (for tests)."""
    entry = entry or {}
    return fingerprint(entry.get('service'), entry.get('artist'),
                       entry.get('title'), entry.get('size_bytes'))


# ---- HTTP payloads -------------------------------------------------------------

def list_route(database, limit: int = 200) -> tuple:
    """Data for GET /api/downloads/failed-blocklist. Never raises."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    try:
        return {'entries': list_entries(database, limit=limit)}, 200
    except Exception as exc:  # noqa: BLE001 - fail open
        return {'message': str(exc)}, 500


def delete_route(database, fingerprint_value) -> tuple:
    """Data for DELETE /api/downloads/failed-blocklist. Never raises."""
    if not fingerprint_value:
        return {'message': "Missing 'fingerprint' in body."}, 400
    try:
        if remove(database, fingerprint_value):
            return {'message': 'Entry removed.'}, 200
        return {'message': 'No blocklist entry with that fingerprint.'}, 404
    except Exception as exc:  # noqa: BLE001 - fail open
        return {'message': str(exc)}, 500
