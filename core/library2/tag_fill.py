"""Keep file tags filled from the library.

An import fills what the library knows into the tags its file left blank
(#2217). What the library learns AFTER the import (a genre from enrichment, a
disc total, a year from a media server sync) used to stay in the library: the
tag check filed a Library Re-tag finding for every blank and someone had to
apply them. This fills them instead, when the user already lets SoulSync
write tags.

One setting, ``metadata_enhancement.fill_empty_tags``:

* ``off``: nothing is written on its own. Findings are filed as before.
* ``fill`` (the default while post-processing writes tags): a tag that is
  blank in the file and known to the library is written. A tag the file
  already has is never touched, and that is checked again at write time, so
  an external tagger that got there first wins.
* ``update``: blanks are filled and the tags that differ from the library are
  replaced, the same write as applying the finding. Genre and year
  differences the Re-tag settings leave quiet are still left alone.

With post-processing off the mode is ``off`` whatever is stored: the user
said SoulSync does not write their files.

One place does the writing (``fill_file``). Two callers feed it: the Library
Re-tag job, for the files it compares, and a light queue in the repair worker
that works through the open tag findings in small batches, off the request
path (``drain``). Every write is kept in ``lib2_tag_fills`` with the value
before and after, so it can be listed ("filled 214 tags this week") and
undone, one file or one run at a time.

A file is left alone, and its finding stays for a person, when it is
hand-tagged (or its album is), its album or artist is locked for tags, it
sits in a folder SoulSync is still working in (downloads, staging, the
torrent and usenet download folders, which is also where a seeding torrent's
data lives), it changed in the last few minutes or another writer holds it,
it cannot be written (a read-only mount), its finding was dismissed, or the
audio check of the write fails (the write is then not made at all).

No loops and no fights: a field is filled at most once per file. A field
SoulSync filled that is blank again was cleared by someone, and an undone
fill was not wanted, so neither is filled again; the finding is filed for a
person instead. A field whose write did not read back is held the same way.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.tag_fill")

SETTING = 'metadata_enhancement.fill_empty_tags'
MODES = ('off', 'fill', 'update')

#: the tags a fill may write: the ones the tag check compares, plus a cover
#: when the file has none and the library already has one for the album
FILL_FIELDS = ('title', 'artist', 'album', 'album_artist', 'year', 'genre',
               'track_number', 'disc_number', 'total_tracks', 'total_discs')
_NUMBER_FIELDS = frozenset({'track_number', 'disc_number', 'total_tracks', 'total_discs'})

#: a file changed this recently is someone's work in progress
RECENT_CHANGE_SECONDS = 10 * 60
#: how much one pass of the queue does, and how often it comes back
QUEUE_BATCH = 100
QUEUE_INTERVAL_SECONDS = 10 * 60
QUEUE_PAUSE_SECONDS = 0.05

#: folders SoulSync downloads into or works in before a file reaches the
#: library. a library file inside one is in someone's hands (a torrent that
#: is still seeding reads the very bytes a tag write would change)
_WORKING_FOLDER_KEYS = ('soulseek.download_path', 'import.staging_path',
                        'download_source.torrent_download_path',
                        'download_source.usenet_download_path',
                        'post_processing.download_path')

TAG_FILLS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_tag_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    source TEXT NOT NULL,
    mode TEXT NOT NULL,
    file_id INTEGER,
    track_id INTEGER,
    album_id INTEGER,
    path TEXT,
    fields_json TEXT NOT NULL DEFAULT '[]',
    cover TEXT,
    hold_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'filled',
    filled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    undone_at TIMESTAMP,
    undo_json TEXT
)
"""


def ensure_schema(conn) -> None:
    conn.execute(TAG_FILLS_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lib2_tag_fills_file ON lib2_tag_fills(file_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lib2_tag_fills_batch ON lib2_tag_fills(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lib2_tag_fills_at ON lib2_tag_fills(filled_at)")


def _config(config=None):
    if config is not None:
        return config
    from core.metadata.common import get_config_manager
    return get_config_manager()


def fill_mode(config=None) -> str:
    """What SoulSync may write into files on its own: off, fill or update.

    Unset, it is ``fill`` while post-processing writes tags: the same promise
    an import keeps since #2217, extended to what the library learns later.
    Nothing an install has today is replaced by default (``update`` is only
    ever a choice). Post-processing off is always ``off``."""
    try:
        config = _config(config)
        if config.get('metadata_enhancement.enabled', True) is False:
            return 'off'
        raw = config.get(SETTING, None)
        if isinstance(raw, str) and raw.strip().lower() in MODES:
            return raw.strip().lower()
        return 'fill'
    except Exception:  # noqa: BLE001 - an unreadable setting writes nothing
        return 'off'


def now_batch(source: str) -> str:
    return f"{source}:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}"


# ── what a file may have written ─────────────────────────────────────────────

def _check_fields(checks: Dict[str, Any], mode: str) -> List[str]:
    """The tag fields the file's last check says a fill (or an update) would
    write. ``differs`` (a genre or year left quiet) is never one."""
    from core.library2.retag import _CHECK_FIELDS
    wanted = ('missing', 'mismatch') if mode == 'update' else ('missing',)
    fields = set()
    for key, state in (checks or {}).items():
        field = _CHECK_FIELDS.get(key)
        if field in FILL_FIELDS and state in wanted:
            fields.add(field)
    if checks.get('edition') == 'unknown':
        # the track sits at a different number on each edition on record:
        # no number is a fact yet
        fields -= _NUMBER_FIELDS
    # a cover only where the file has none and the library already shows one
    # for the album. never fetched from a source here, never replaced
    if checks.get('cover') == 'missing' and checks.get('artwork_database') == 'correct':
        fields.add('cover_art')
    return sorted(fields)


def _held_fields(conn, file_id: int) -> set:
    """Fields never filled again for this file: filled once already (blank
    again means someone cleared it), undone, or written without reading
    back."""
    held = set()
    for fields_json, hold_json in conn.execute(
            "SELECT fields_json, hold_json FROM lib2_tag_fills WHERE file_id=?", (int(file_id),)):
        for raw, take in ((fields_json, lambda f: f.get('key')), (hold_json, lambda f: f)):
            try:
                for item in json.loads(raw or '[]'):
                    key = take(item) if isinstance(item, (dict, str)) else None
                    if key:
                        held.add(key)
            except (TypeError, ValueError, AttributeError):
                continue
    # a cover added once is the same: the next missing cover was removed
    if conn.execute("SELECT 1 FROM lib2_tag_fills WHERE file_id=? AND cover IS NOT NULL LIMIT 1",
                    (int(file_id),)).fetchone():
        held.add('cover_art')
    return held


def _working_folders(config) -> List[str]:
    folders = []
    try:
        library = os.path.realpath(str(config.get('soulseek.transfer_path', '') or '')) \
            if config.get('soulseek.transfer_path', '') else None
    except Exception:  # noqa: BLE001
        library = None
    for key in _WORKING_FOLDER_KEYS:
        try:
            raw = config.get(key, '')
        except Exception:  # noqa: BLE001
            raw = ''
        if not raw or not isinstance(raw, str):
            continue
        folder = os.path.realpath(raw)
        # a working folder that IS the library (or holds it) would skip every
        # file: that is a setup where nothing is in flight, not everything
        if library and (library == folder or library.startswith(folder.rstrip(os.sep) + os.sep)):
            continue
        folders.append(folder.rstrip(os.sep) + os.sep)
    return folders


def _in_folder(path: str, folders: Iterable[str]) -> bool:
    real = os.path.realpath(path)
    return any(real.startswith(folder) for folder in folders)


def _writable(path: str) -> bool:
    """The file and its folder: an atomic save writes a copy beside it."""
    return os.access(path, os.W_OK) and os.access(os.path.dirname(path) or '.', os.W_OK)


def _dismissed(conn, path: str, track_id) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM repair_findings WHERE finding_type='library_retag' AND status='dismissed' "
            "AND (file_path=? OR (file_path IS NULL AND entity_id=?)) LIMIT 1",
            (path, f"lib2:{track_id}")).fetchone() is not None
    except Exception:  # noqa: BLE001 - no findings table, nothing dismissed
        return False


def _locked(conn, album_id, artist_id) -> Optional[str]:
    from core.library2 import locks
    held = locks.lock_map(conn)
    if not held:
        return None
    if album_id and locks.blocks('album', album_id, 'tags', 'Fill empty tags', known=held):
        return 'album'
    if artist_id and locks.blocks('artist', artist_id, 'tags', 'Fill empty tags', known=held):
        return 'artist'
    return None


def _file_row(conn, file_id: int):
    return conn.execute(
        "SELECT f.id, f.path, f.track_id, f.tags_json, COALESCE(f.file_state,'active') AS state, "
        "t.album_id, a.primary_artist_id FROM lib2_track_files f "
        "JOIN lib2_tracks t ON t.id=f.track_id JOIN lib2_albums a ON a.id=t.album_id WHERE f.id=?",
        (int(file_id),)).fetchone()


def plan_file(database, file_id: int, mode: str, config=None) -> Dict[str, Any]:
    """What a fill would write into one file, or why it leaves it alone.

    ``{'fields': [...], 'path': resolved, ...}`` or ``{'skip': reason}``.
    Reads the database and stats the file; writes nothing."""
    from core.library2.paths import resolve_lib2_path
    from core.library2.status import _coerce_tags
    from core.library2.validation import album_is_hand_tagged
    from core.repair_jobs.base import hand_tagged_path_keys, is_hand_tagged_path

    config = _config(config)
    if mode not in ('fill', 'update'):
        return {'skip': 'off'}
    with closing(database._get_connection()) as conn:
        row = _file_row(conn, file_id)
        if not row or row['state'] == 'deleted':
            return {'skip': 'gone'}
        checks = (_coerce_tags(row['tags_json']).get('_validation') or {}).get('checks') or {}
        fields = _check_fields(checks, mode)
        if not fields:
            return {'skip': 'nothing'}
        ensure_schema(conn)
        held = _held_fields(conn, file_id)
        fields = [f for f in fields if f not in held]
        if not fields:
            return {'skip': 'held'}
        if album_is_hand_tagged(conn, row['track_id']):
            return {'skip': 'hand_tagged'}
        locked = _locked(conn, row['album_id'], row['primary_artist_id'])
        if locked:
            return {'skip': 'locked'}
        if _dismissed(conn, row['path'], row['track_id']):
            return {'skip': 'dismissed'}
    path = resolve_lib2_path(row['path'], config)
    if not path or not os.path.isfile(path):
        return {'skip': 'missing'}
    keys = hand_tagged_path_keys(database)
    if is_hand_tagged_path(row['path'], keys) or is_hand_tagged_path(path, keys):
        return {'skip': 'hand_tagged'}
    if _in_folder(path, _working_folders(config)):
        return {'skip': 'in_progress'}
    try:
        if time.time() - os.path.getmtime(path) < RECENT_CHANGE_SECONDS:
            return {'skip': 'in_progress'}
    except OSError:
        return {'skip': 'missing'}
    if not _writable(path):
        return {'skip': 'read_only'}
    return {'fields': fields, 'path': path, 'stored_path': row['path'], 'track_id': int(row['track_id']),
            'album_id': row['album_id']}


def _record(database, *, batch_id, source, mode, file_id, plan, change, hold) -> Optional[int]:
    with closing(database._get_connection()) as conn:
        ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO lib2_tag_fills(batch_id,source,mode,file_id,track_id,album_id,path,"
            "fields_json,cover,hold_json,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (batch_id, source, mode, int(file_id), plan.get('track_id'), plan.get('album_id'),
             plan.get('stored_path') or plan.get('path'),
             json.dumps((change or {}).get('fields') or []), (change or {}).get('cover'),
             json.dumps(sorted(hold)), 'filled' if change else 'held'))
        conn.commit()
        return cur.lastrowid


def fill_file(database, file_id: int, *, mode: Optional[str] = None, config=None,
              batch_id: Optional[str] = None, source: str = 'queue') -> Dict[str, Any]:
    """Fill one file's blank tags from the library, and keep what changed.

    Returns ``{'written': n_fields, 'cover': bool, 'fill_id': id}`` or
    ``{'skip': reason}`` / ``{'error': ...}``. Never raises."""
    from core.library2 import locks, retag
    from core.library2.status import _coerce_tags
    from core.metadata.common import get_file_lock

    config = _config(config)
    mode = mode or fill_mode(config)
    try:
        plan = plan_file(database, file_id, mode, config)
    except Exception as exc:  # noqa: BLE001
        logger.debug("fill plan failed for file %s: %s", file_id, exc, exc_info=True)
        return {'error': str(exc)}
    if plan.get('skip'):
        return plan
    lock = get_file_lock(plan['path'])
    if not lock.acquire(blocking=False):
        return {'skip': 'in_progress'}
    try:
        fields = plan['fields']
        cover = 'cover_art' in fields
        options = {'mode': 'overwrite' if mode == 'update' else 'fill_missing',
                   'cover_art': 'fill_missing' if cover else 'skip', 'lyrics': 'skip',
                   'fields': fields}
        with locks.automation(), locks.doing('Fill empty tags'):
            stats = retag.write_tags(database, [plan['track_id']], embed_cover=cover,
                                     protect_hand_tagged=True, file_ids=[int(file_id)],
                                     options=options, record_changes=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("fill write failed for file %s: %s", file_id, exc, exc_info=True)
        return {'error': str(exc)}
    finally:
        lock.release()
    if stats.get('failed'):
        # the audio check refused the write, or the file could not be saved:
        # nothing changed, and the finding stays for a person
        error = ((stats.get('errors') or [{}])[0]).get('error') or 'Tag write failed'
        logger.info("Fill empty tags left %s alone: %s", plan.get('stored_path'), error)
        return {'error': error}
    if stats.get('locked'):
        return {'skip': 'locked'}
    change = next((c for c in stats.get('changes') or [] if c.get('file_id') == int(file_id)), None)
    if change and not change.get('fields') and not change.get('cover'):
        change = None
    # what the check still flags after the write is held: writing it again
    # would write it again on every pass
    with closing(database._get_connection()) as conn:
        row = conn.execute('SELECT tags_json FROM lib2_track_files WHERE id=?', (int(file_id),)).fetchone()
    checks = (_coerce_tags(row[0] if row else None).get('_validation') or {}).get('checks') or {}
    still = set(_check_fields(checks, mode)) & set(plan['fields'])
    if not change and not still:
        return {'skip': 'nothing'}
    fill_id = _record(database, batch_id=batch_id or now_batch(source), source=source, mode=mode,
                      file_id=file_id, plan=plan, change=change, hold=still)
    if change:
        logger.debug("Filled %s tag(s) in %s", len(change.get('fields') or []), plan.get('stored_path'))
    return {'written': len((change or {}).get('fields') or []), 'cover': bool((change or {}).get('cover')),
            'fill_id': fill_id, 'held': sorted(still)}


# ── the queue ────────────────────────────────────────────────────────────────

def _finding_files(details: Dict[str, Any]) -> List[int]:
    ids = []
    validation = details.get('validation') or {}
    for value in [validation.get('file_id'), *(details.get('file_ids') or [])]:
        try:
            if value is not None and int(value) not in ids:
                ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def drain(database, config=None, *, limit: int = QUEUE_BATCH, should_stop: Optional[Callable[[], bool]] = None,
          pause: float = QUEUE_PAUSE_SECONDS, batch_id: Optional[str] = None) -> Dict[str, Any]:
    """One pass of the queue: the open Library Re-tag findings, oldest first,
    up to ``limit`` files. The finding is only the pointer: each file is
    planned from its current tags, so a finding that is out of date writes
    nothing it should not. A filled file's finding closes through the tag
    check the write runs (or stays open for what differs)."""
    config = _config(config)
    mode = fill_mode(config)
    stats = {'mode': mode, 'files': 0, 'fields': 0, 'covers': 0, 'skipped': {}, 'errors': 0}
    if mode == 'off':
        return stats
    batch_id = batch_id or now_batch('queue')
    seen, last, done = set(), 0, 0
    while done < limit and not (should_stop and should_stop()):
        with closing(database._get_connection()) as conn:
            try:
                rows = conn.execute(
                    "SELECT id, details_json FROM repair_findings WHERE finding_type='library_retag' "
                    "AND status='pending' AND id>? ORDER BY id LIMIT 200", (last,)).fetchall()
            except Exception:  # noqa: BLE001 - no findings table yet
                return stats
        if not rows:
            break
        for finding_id, details_json in rows:
            last = int(finding_id)
            try:
                details = json.loads(details_json or '{}')
            except (TypeError, ValueError):
                continue
            for file_id in _finding_files(details):
                if file_id in seen or done >= limit or (should_stop and should_stop()):
                    continue
                seen.add(file_id)
                result = fill_file(database, file_id, mode=mode, config=config,
                                   batch_id=batch_id, source='queue')
                if result.get('skip'):
                    stats['skipped'][result['skip']] = stats['skipped'].get(result['skip'], 0) + 1
                    continue
                done += 1
                if result.get('error'):
                    stats['errors'] += 1
                elif result.get('written') or result.get('cover'):
                    stats['files'] += 1
                    stats['fields'] += result.get('written') or 0
                    stats['covers'] += 1 if result.get('cover') else 0
                if pause:
                    time.sleep(pause)
    if stats['files']:
        logger.info("Fill empty tags: %s tag(s) in %s file(s)%s", stats['fields'], stats['files'],
                    f", {stats['covers']} cover(s)" if stats['covers'] else '')
    return stats


# ── history and undo ─────────────────────────────────────────────────────────

def _row_dict(row) -> Dict[str, Any]:
    item = dict(row)
    for key, default in (('fields_json', []), ('hold_json', []), ('undo_json', None)):
        raw = item.pop(key, None)
        try:
            item[key[:-5]] = json.loads(raw) if raw else default
        except (TypeError, ValueError):
            item[key[:-5]] = default
    return item


def summary(database, *, days: int = 7, limit: int = 20) -> Dict[str, Any]:
    """What was filled lately, for the Tools page: totals over ``days`` and
    the most recent runs, newest first."""
    days = max(1, min(int(days or 7), 365))
    with closing(database._get_connection()) as conn:
        ensure_schema(conn)
        since = f'-{days} days'
        rows = conn.execute(
            "SELECT batch_id, source, MIN(filled_at) AS at, COUNT(*) AS files, "
            "SUM(json_array_length(fields_json)) AS fields, SUM(cover IS NOT NULL) AS covers, "
            "SUM(status='undone') AS undone FROM lib2_tag_fills WHERE status<>'held' "
            "GROUP BY batch_id ORDER BY at DESC LIMIT ?", (max(1, min(int(limit or 20), 200)),)).fetchall()
        totals = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(json_array_length(fields_json)),0), COALESCE(SUM(cover IS NOT NULL),0) "
            "FROM lib2_tag_fills WHERE status='filled' AND filled_at >= datetime('now', ?)", (since,)).fetchone()
    return {'days': days, 'files': int(totals[0] or 0), 'fields': int(totals[1] or 0),
            'covers': int(totals[2] or 0),
            'runs': [{'batch_id': r['batch_id'], 'source': r['source'], 'at': r['at'],
                      'files': int(r['files'] or 0), 'fields': int(r['fields'] or 0),
                      'covers': int(r['covers'] or 0), 'undone': int(r['undone'] or 0)} for r in rows]}


def batch_files(database, batch_id: str, *, limit: int = 500) -> List[Dict[str, Any]]:
    with closing(database._get_connection()) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT id, batch_id, source, mode, file_id, track_id, album_id, path, fields_json, cover, "
            "hold_json, status, filled_at, undone_at, undo_json FROM lib2_tag_fills "
            "WHERE batch_id=? AND status<>'held' ORDER BY id LIMIT ?",
            (str(batch_id), max(1, min(int(limit or 500), 5000)))).fetchall()
    return [_row_dict(r) for r in rows]


def _same(current: Any, written: Any) -> bool:
    from core.tag_writer import _normalize_for_compare
    if isinstance(current, (list, tuple)):
        current = ', '.join(str(v) for v in current)
    if current in (None, ''):
        return written in (None, '')
    return _normalize_for_compare(str(current)) == _normalize_for_compare(str(written))


def undo_fill(database, fill_id: int, config=None) -> Dict[str, Any]:
    """Put one file's filled tags back to blank.

    Only a field still holding what the fill wrote goes back: a tag someone
    set since is theirs and is named in ``kept``. A cover the fill added is
    taken out again. The fields undone are never filled again for this file;
    their finding comes back for a person to decide."""
    from core.library2.paths import resolve_lib2_path
    from core.library2.retag import _persist_file_tags
    from core.library2.tag_cache import read_tag_snapshot
    from core.repair_jobs.base import hand_tagged_path_keys, is_hand_tagged_path
    from core.tag_writer import restore_tag_fields

    config = _config(config)
    with closing(database._get_connection()) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT * FROM lib2_tag_fills WHERE id=?", (int(fill_id),)).fetchone()
    if not row:
        return {'success': False, 'error': 'Nothing recorded under that id'}
    item = _row_dict(row)
    if item['status'] != 'filled':
        return {'success': False, 'error': 'This fill was already undone'}
    path = resolve_lib2_path(item['path'], config)
    if not path or not os.path.isfile(path):
        return {'success': False, 'error': f"File no longer on disk: {os.path.basename(item['path'] or '')}"}
    keys = hand_tagged_path_keys(database)
    if is_hand_tagged_path(item['path'], keys) or is_hand_tagged_path(path, keys):
        return {'success': False, 'error': 'This file is hand-tagged now, so its tags were left alone'}
    current = read_tag_snapshot(path)
    if current.get('error'):
        return {'success': False, 'error': current['error']}
    before, kept = {}, []
    for field in item['fields']:
        key = field.get('key')
        if not key:
            continue
        if _same(current.get(key), field.get('after')):
            before[key] = field.get('before') or ''
        else:
            kept.append(key)
    if item.get('cover') == 'added':
        if current.get('has_cover_art'):
            before['cover_art'] = ''
    restored = []
    if before:
        result = restore_tag_fields(path, before)
        if not result.get('success'):
            return {'success': False, 'retryable': True,
                    'error': result.get('error') or 'The old tags could not be written back'}
        restored = result.get('restored_fields') or []
        kept += result.get('kept_fields') or []
    hold = sorted({*item['hold'], *[f.get('key') for f in item['fields'] if f.get('key')],
                   *(['cover_art'] if item.get('cover') else [])})
    with closing(database._get_connection()) as conn:
        conn.execute(
            "UPDATE lib2_tag_fills SET status='undone', undone_at=CURRENT_TIMESTAMP, hold_json=?, undo_json=? "
            "WHERE id=? AND status='filled'",
            (json.dumps(hold), json.dumps({'restored': sorted(set(restored)), 'kept': sorted(set(kept))}),
             int(fill_id)))
        conn.commit()
    if item.get('file_id'):
        try:
            _persist_file_tags(database, int(item['file_id']), read_tag_snapshot(path), config, 'undo')
        except Exception as exc:  # noqa: BLE001 - the tags are back; the next scan catches up
            logger.debug("tag cache refresh after fill undo failed for %s: %s", path, exc)
    return {'success': True, 'restored': sorted(set(restored)), 'kept': sorted(set(kept))}


def _batch_fill_ids(database, batch_id: str) -> List[int]:
    with closing(database._get_connection()) as conn:
        ensure_schema(conn)
        return [r[0] for r in conn.execute(
            "SELECT id FROM lib2_tag_fills WHERE batch_id=? AND status='filled' ORDER BY id", (str(batch_id),))]


def undo_batch(database, batch_id: str, config=None, *, progress=None) -> Dict[str, Any]:
    """Undo every file one run filled. A file that cannot be put back (gone,
    hand-tagged since) is counted and the rest still go back."""
    ids = _batch_fill_ids(database, batch_id)
    if not ids:
        return {'success': False, 'error': 'Nothing left to undo in that run'}
    out = {'success': True, 'files': 0, 'failed': 0, 'errors': []}
    for index, fill_id in enumerate(ids):
        result = undo_fill(database, fill_id, config)
        if result.get('success'):
            out['files'] += 1
        else:
            out['failed'] += 1
            if len(out['errors']) < 5:
                out['errors'].append(result.get('error'))
        if progress:
            progress(index + 1, len(ids), out)
    return out


#: the one run undo that may be going on, for the Tools page to show
_UNDO_STATE: Dict[str, Any] = {'running': False}
_UNDO_GUARD = threading.Lock()


def undo_state() -> Dict[str, Any]:
    with _UNDO_GUARD:
        return dict(_UNDO_STATE)


def start_undo_batch(database, batch_id: str, config=None) -> Dict[str, Any]:
    """Undo a run in the background: a job run can have filled thousands of
    files, more than a request should wait for. One at a time."""
    ids = _batch_fill_ids(database, batch_id)
    if not ids:
        return {'success': False, 'error': 'Nothing left to undo in that run'}
    with _UNDO_GUARD:
        if _UNDO_STATE.get('running'):
            return {'success': False, 'error': 'Another run is being undone'}
        _UNDO_STATE.clear()
        _UNDO_STATE.update(running=True, batch_id=batch_id, done=0, total=len(ids), files=0, failed=0)

    def progress(done, total, out):
        with _UNDO_GUARD:
            _UNDO_STATE.update(done=done, total=total, files=out['files'], failed=out['failed'])

    def run():
        try:
            out = undo_batch(database, batch_id, config, progress=progress)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Undoing filled tags for %s failed: %s", batch_id, exc, exc_info=True)
            out = {'success': False, 'error': str(exc)}
        with _UNDO_GUARD:
            _UNDO_STATE.update(running=False, result=out)

    threading.Thread(target=run, daemon=True, name='tag-fill-undo').start()
    return {'success': True, 'started': True, 'total': len(ids)}


def record_job_changes(database, changes: List[Dict[str, Any]], *, batch_id: str, mode: str,
                       track_id=None, album_id=None) -> int:
    """Keep a Re-tag job's own automatic write (its Apply everything switch)
    the same way, so it can be listed and undone too."""
    written = 0
    for change in changes or []:
        if not change.get('fields') and not change.get('cover'):
            continue
        _record(database, batch_id=batch_id, source='job', mode=mode, file_id=change.get('file_id'),
                plan={'track_id': track_id, 'album_id': album_id, 'stored_path': change.get('path')},
                change=change, hold=set())
        written += 1
    return written


__all__ = ['MODES', 'SETTING', 'fill_mode', 'plan_file', 'fill_file', 'drain', 'summary',
           'batch_files', 'undo_fill', 'undo_batch', 'start_undo_batch', 'undo_state',
           'record_job_changes', 'ensure_schema']
