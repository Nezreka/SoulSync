"""Persistent, bounded-retry audio evidence for library reconciliation.

One connection is held for the scan and committed after each folder. A path's
stat tuple skips fingerprinting unchanged audio; the fingerprint key skips
AcoustID lookup after a rename or tag edit. Only confirmed ok/no-match results
are cached. API and backend failures remain retryable.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from typing import Any

from core.imports.staging import AUDIO_EXTENSIONS
from core.repair_jobs.base import walk_library
from utils.logging_config import get_logger


logger = get_logger('library.reconciliation_evidence')
_LOOKUP_RETRY_DELAYS = (1, 2, 4, 8)
_LOOKUP_CIRCUIT_THRESHOLD = 3
_MIN_FINGERPRINT_LENGTH = 7


def file_identity(stat) -> dict:
    """Identity for reusing reviewed evidence without decoding the audio again."""
    return {'device': stat.st_dev, 'inode': stat.st_ino, 'ctime_ns': stat.st_ctime_ns}


def evidence_path_before_move(cursor, path):
    """Find the scanned cache key while both mount aliases still exist."""
    if not cursor.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_file_fingerprints'").fetchone():
        return path
    stat = os.stat(path)
    candidates = cursor.execute("""SELECT file_path FROM reconcile_file_fingerprints
        WHERE size_bytes=? AND mtime_ns=?""", (stat.st_size, stat.st_mtime_ns)).fetchall()
    for (candidate,) in candidates:
        try:
            if os.path.samefile(path, candidate):
                return candidate
        except OSError:
            continue
    return path


def preserve_audio_evidence(cursor, old_path, new_path, *, size_bytes, mtime_ns,
                            fingerprint_key=None, preserve_tags=False):
    """Carry evidence through our own guarded audio-preserving operation.

    Only a byte-preserving rename may retain native tag evidence. Tag writes
    invalidate it; unchanged size/mtime alone never authorizes an external edit.
    """
    tables = {row[0] for row in cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    stat = os.stat(new_path)
    if 'reconcile_file_tags' in tables:
        tags = cursor.execute("""SELECT tags_json FROM reconcile_file_tags
            WHERE file_path=? AND size_bytes=? AND mtime_ns=?""",
            (old_path, size_bytes, mtime_ns)).fetchone() if preserve_tags else None
        cursor.execute('DELETE FROM reconcile_file_tags WHERE file_path IN (?, ?)',
                       (old_path, new_path))
        if tags:
            cursor.execute('INSERT INTO reconcile_file_tags VALUES (?, ?, ?, ?)',
                           (new_path, stat.st_size, stat.st_mtime_ns, tags[0]))
    if 'reconcile_file_fingerprints' not in tables:
        return
    row = cursor.execute("""SELECT duration_s, fingerprint
        FROM reconcile_file_fingerprints
        WHERE file_path=? AND size_bytes=? AND mtime_ns=?""",
        (old_path, size_bytes, mtime_ns)).fetchone()
    if not row:
        return
    key = hashlib.sha256(f'{float(row[0]):.6f}:{row[1]}'.encode()).hexdigest()
    if fingerprint_key and fingerprint_key != key:
        return
    cursor.execute("""INSERT OR REPLACE INTO reconcile_file_fingerprints
        (file_path, size_bytes, mtime_ns, duration_s, fingerprint)
        VALUES (?, ?, ?, ?, ?)""",
        (new_path, stat.st_size, stat.st_mtime_ns, row[0], row[1]))
    if old_path != new_path:
        cursor.execute('DELETE FROM reconcile_file_fingerprints WHERE file_path=?', (old_path,))


def _usable_fingerprint(fingerprint: object, duration: object) -> bool:
    """Reject empty Chromaprint payloads before they reach AcoustID.

    ``fpcalc`` can occasionally report success with its six-character empty
    payload (``AQAAAA``).  It cannot identify audio, and AcoustID responds with
    a generic API error which previously made reconciliation skip the album.
    Real encoded fingerprints comfortably exceed this small structural floor,
    including short interludes.
    """
    try:
        duration_value = float(duration)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(duration_value) or duration_value <= 0:
        return False
    if isinstance(fingerprint, memoryview):
        fingerprint = fingerprint.tobytes()
    if not isinstance(fingerprint, (str, bytes)):
        return False
    return len(fingerprint.strip()) >= _MIN_FINGERPRINT_LENGTH


def folder_audio_path_digest(folder: str) -> str:
    """Cheap guard against a newly arrived track since a missing-slot scan."""
    if not folder or not os.path.isdir(folder):
        return ''
    paths = []
    for dirpath, _dirs, filenames in walk_library(folder):
        paths.extend(os.path.relpath(os.path.join(dirpath, name), folder)
                     for name in filenames
                     if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS)
    return hashlib.sha256('\n'.join(sorted(paths)).encode()).hexdigest()


def album_folders_audio_path_digest(folders: list[str]) -> str:
    """Guard a logical album that may currently span physical folders."""
    entries = [(os.path.normpath(folder), folder_audio_path_digest(folder))
               for folder in sorted(set(folders))]
    return hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest()


class ReconciliationEvidence:
    def __init__(self, db, acoustid_client):
        self.conn = db._get_connection()
        self.client = acoustid_client
        self.metrics = {'fingerprints': 0, 'fingerprint_hits': 0,
                        'lookups': 0, 'lookup_hits': 0,
                        'tag_reads': 0, 'tag_hits': 0,
                        'fingerprint_seconds': 0.0, 'lookup_seconds': 0.0,
                        'tag_seconds': 0.0}
        self.lookup_error = None
        self.consecutive_lookup_errors = 0
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS reconcile_file_fingerprints (
                file_path TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL, duration_s REAL NOT NULL,
                fingerprint TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reconcile_acoustid_results (
                lookup_key TEXT PRIMARY KEY, result_json TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reconcile_lookup_expiry
                ON reconcile_acoustid_results(expires_at);
            CREATE TABLE IF NOT EXISTS reconcile_file_tags (
                file_path TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL, tags_json TEXT NOT NULL
            );
        ''')
        self.conn.execute('DELETE FROM reconcile_acoustid_results WHERE expires_at <= ?',
                          (int(time.time()),))

    def file_evidence(self, path: str, tag_loader, *, audio: dict | None = None,
                      library_fields: dict | None = None) -> dict:
        """Load common evidence while keeping each caller's evidence scope.

        A scan supplies its already checked audio and database/stat/playlist
        fields. A manual preview intentionally has only tags and audio, as it
        did before this extraction; enriching it would change matching policy.
        """
        return {
            'path': path,
            'tags': self.read_tags(path, tag_loader),
            'acoustid': audio if audio is not None else self.identify(path),
            **(library_fields or {}),
        }

    def close(self):
        self.conn.commit()
        self.conn.close()

    def checkpoint(self):
        self.conn.commit()

    def read_tags(self, file_path: str, loader) -> dict[str, Any]:
        """Read tags once per unchanged file and persist the JSON-safe result."""
        try:
            stat = os.stat(file_path)
        except OSError:
            return loader(file_path)
        row = self.conn.execute('''
            SELECT tags_json FROM reconcile_file_tags
            WHERE file_path = ? AND size_bytes = ? AND mtime_ns = ?
        ''', (file_path, stat.st_size, stat.st_mtime_ns)).fetchone()
        if row:
            try:
                tags = json.loads(row[0])
                if (isinstance(tags, dict) and
                        all(key in tags for key in getattr(loader, 'required_cache_keys', ()))):
                    self.metrics['tag_hits'] += 1
                    return tags
            except (TypeError, ValueError):
                pass
        started = time.perf_counter()
        tags = loader(file_path)
        self.metrics['tag_seconds'] += time.perf_counter() - started
        self.metrics['tag_reads'] += 1
        self.conn.execute('''
            INSERT OR REPLACE INTO reconcile_file_tags
                (file_path, size_bytes, mtime_ns, tags_json)
            VALUES (?, ?, ?, ?)
        ''', (file_path, stat.st_size, stat.st_mtime_ns,
              json.dumps(tags, sort_keys=True, default=str)))
        return tags

    def identify(self, file_path: str) -> dict[str, Any]:
        try:
            stat = os.stat(file_path)
        except OSError as exc:
            return {'status': 'not_found', 'recordings': [], 'error': str(exc)}
        row = self.conn.execute('''
            SELECT duration_s, fingerprint FROM reconcile_file_fingerprints
            WHERE file_path = ? AND size_bytes = ? AND mtime_ns = ?
        ''', (file_path, stat.st_size, stat.st_mtime_ns)).fetchone()
        if row and _usable_fingerprint(row[1], row[0]):
            duration, fingerprint = row
            self.metrics['fingerprint_hits'] += 1
        else:
            if row:
                # Old versions cached fpcalc's empty AQAAAA payload.  Remove
                # it before retrying so a repaired decoder/file can recover.
                self.conn.execute(
                    'DELETE FROM reconcile_file_fingerprints WHERE file_path = ?',
                    (file_path,))
            started = time.perf_counter()
            generated = self.client.fingerprint_audio_file(file_path)
            self.metrics['fingerprint_seconds'] += time.perf_counter() - started
            self.metrics['fingerprints'] += 1
            if generated.get('status') != 'ok':
                return {**generated, 'recordings': []}
            duration = generated.get('duration_s')
            fingerprint = generated.get('fingerprint')
            if not _usable_fingerprint(fingerprint, duration):
                return {
                    'status': 'error',
                    'recordings': [],
                    'error': 'Chromaprint returned an empty or invalid fingerprint',
                }
            self.conn.execute('''
                INSERT OR REPLACE INTO reconcile_file_fingerprints
                    (file_path, size_bytes, mtime_ns, duration_s, fingerprint)
                VALUES (?, ?, ?, ?, ?)
            ''', (file_path, stat.st_size, stat.st_mtime_ns, duration, fingerprint))

        key = hashlib.sha256(f'{float(duration):.6f}:{fingerprint}'.encode()).hexdigest()
        cached = self.conn.execute('''
            SELECT result_json FROM reconcile_acoustid_results
            WHERE lookup_key = ? AND expires_at > ?
        ''', (key, int(time.time()))).fetchone()
        if cached:
            try:
                payload = json.loads(cached[0])
                if isinstance(payload, dict):
                    self.metrics['lookup_hits'] += 1
                    return {**payload, 'fingerprint_key': key}
            except (TypeError, ValueError):
                pass
            self.conn.execute('DELETE FROM reconcile_acoustid_results WHERE lookup_key = ?',
                              (key,))

        # Consecutive exhausted service failures stop new requests for this
        # run. Cached evidence remains usable, so the reconciliation job can
        # still finish without hammering AcoustID during a real outage.
        if self.lookup_error is not None:
            return {**self.lookup_error, 'fingerprint_key': key, 'deferred': True}

        started = time.perf_counter()
        try:
            for delay in (*_LOOKUP_RETRY_DELAYS, None):
                result = self.client.lookup_fingerprint_with_status(fingerprint, duration)
                self.metrics['lookups'] += 1
                if result.get('status') != 'error' or result.get('invalid_key') or delay is None:
                    break
                logger.warning('AcoustID lookup failed; retrying in %ss: %s',
                               delay, result.get('error') or 'unknown error')
                time.sleep(delay)
        finally:
            self.metrics['lookup_seconds'] += time.perf_counter() - started
        if result.get('status') in ('ok', 'no_match'):
            self.consecutive_lookup_errors = 0
            ttl = 30 * 86400 if result['status'] == 'ok' else 86400
            self.conn.execute('''
                INSERT OR REPLACE INTO reconcile_acoustid_results
                    (lookup_key, result_json, expires_at) VALUES (?, ?, ?)
            ''', (key, json.dumps(result), int(time.time()) + ttl))
        elif result.get('status') == 'error':
            self.consecutive_lookup_errors += 1
            if (result.get('invalid_key') or
                    self.consecutive_lookup_errors >= _LOOKUP_CIRCUIT_THRESHOLD):
                self.lookup_error = dict(result)
                # Tell the scanner this is no longer evidence about one file.
                # It can preserve affected folders while the circuit prevents
                # any further requests during a service outage.
                result = {**result, 'deferred': True}
        return {**result, 'fingerprint_key': key}
