"""Persistent, bounded-retry audio evidence for library reconciliation.

One connection is held for the scan and committed after each folder. A path's
stat tuple skips fingerprinting unchanged audio; the fingerprint key skips
AcoustID lookup after a rename or tag edit. Only confirmed ok/no-match results
are cached. API and backend failures remain retryable.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from core.imports.staging import AUDIO_EXTENSIONS
from core.repair_jobs.base import walk_library
from utils.logging_config import get_logger


logger = get_logger('library.reconciliation_evidence')
_LOOKUP_RETRY_DELAYS = (1, 2, 4, 8)


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


class ReconciliationEvidence:
    def __init__(self, db, acoustid_client):
        self.conn = db._get_connection()
        self.client = acoustid_client
        self.metrics = {'fingerprints': 0, 'fingerprint_hits': 0,
                        'lookups': 0, 'lookup_hits': 0}
        self.lookup_error = None
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
        ''')
        self.conn.execute('DELETE FROM reconcile_acoustid_results WHERE expires_at <= ?',
                          (int(time.time()),))

    def close(self):
        self.conn.commit()
        self.conn.close()

    def checkpoint(self):
        self.conn.commit()

    def identify(self, file_path: str) -> dict[str, Any]:
        try:
            stat = os.stat(file_path)
        except OSError as exc:
            return {'status': 'not_found', 'recordings': [], 'error': str(exc)}
        row = self.conn.execute('''
            SELECT duration_s, fingerprint FROM reconcile_file_fingerprints
            WHERE file_path = ? AND size_bytes = ? AND mtime_ns = ?
        ''', (file_path, stat.st_size, stat.st_mtime_ns)).fetchone()
        if row:
            duration, fingerprint = row
            self.metrics['fingerprint_hits'] += 1
        else:
            generated = self.client.fingerprint_audio_file(file_path)
            self.metrics['fingerprints'] += 1
            if generated.get('status') != 'ok':
                return {**generated, 'recordings': []}
            duration = generated['duration_s']
            fingerprint = generated['fingerprint']
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

        # One exhausted service failure is enough to stop new requests for
        # this run. Cached evidence remains usable, so the reconciliation job
        # can still finish the rest of the library without hammering AcoustID.
        if self.lookup_error is not None:
            return {**self.lookup_error, 'fingerprint_key': key, 'deferred': True}

        for delay in (*_LOOKUP_RETRY_DELAYS, None):
            result = self.client.lookup_fingerprint_with_status(fingerprint, duration)
            self.metrics['lookups'] += 1
            if result.get('status') != 'error' or result.get('invalid_key') or delay is None:
                break
            logger.warning('AcoustID lookup failed; retrying in %ss: %s',
                           delay, result.get('error') or 'unknown error')
            time.sleep(delay)
        if result.get('status') in ('ok', 'no_match'):
            ttl = 30 * 86400 if result['status'] == 'ok' else 86400
            self.conn.execute('''
                INSERT OR REPLACE INTO reconcile_acoustid_results
                    (lookup_key, result_json, expires_at) VALUES (?, ?, ?)
            ''', (key, json.dumps(result), int(time.time()) + ttl))
        elif result.get('status') == 'error':
            self.lookup_error = dict(result)
        return {**result, 'fingerprint_key': key}
