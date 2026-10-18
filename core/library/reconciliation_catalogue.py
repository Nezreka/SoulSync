"""Run-scoped MusicBrainz evidence, persistent bounded responses and timings.

Only reconciliation installs this context. Other consumers retain their existing
request behavior. Cache entries are evidence inputs, never approval or plan state.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import logging
import threading
import time

import requests

log = logging.getLogger(__name__)
current = ContextVar('reconciliation_catalogue', default=None)
_flights = {}
_flight_lock = threading.Lock()
MAX_ROWS = 25_000
MAX_BYTES = 256 * 1024 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
RICH_RELEASE = {'recordings', 'artist-credits', 'release-groups', 'labels'}


@contextmanager
def connection(db):
    conn = db._get_connection()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_catalogue_cache (
        cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, status INTEGER NOT NULL,
        fetched_at REAL NOT NULL, expires_at REAL NOT NULL, last_access REAL NOT NULL,
        byte_size INTEGER NOT NULL, payload_hash TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_reconcile_catalogue_expiry ON reconcile_catalogue_cache(expires_at)')
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_scan_metrics (
        run_id INTEGER PRIMARY KEY, metrics_json TEXT NOT NULL)''')


def evict(conn, *, now=None, max_rows=MAX_ROWS, max_bytes=MAX_BYTES):
    now = time.time() if now is None else now
    removed = conn.execute('DELETE FROM reconcile_catalogue_cache WHERE expires_at <= ?', (now,)).rowcount
    count, size = conn.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM reconcile_catalogue_cache').fetchone()
    while count > max_rows or size > max_bytes:
        rows = conn.execute('SELECT cache_key,byte_size FROM reconcile_catalogue_cache ORDER BY last_access LIMIT 256').fetchall()
        if not rows:
            break
        victims = []
        for key, length in rows:
            if count <= max_rows and size <= max_bytes:
                break
            victims.append((key,))
            count -= 1
            size -= length
        conn.executemany('DELETE FROM reconcile_catalogue_cache WHERE cache_key=?', victims)
        removed += len(victims)
    return removed


@contextmanager
def stage(name):
    session = current.get()
    start = time.perf_counter()
    try:
        yield
    finally:
        if session is not None:
            session.seconds[name] += time.perf_counter() - start


def timed(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with stage(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def _response(raw, status, url):
    response = requests.Response()
    response.status_code = status
    response._content = raw.encode('utf-8')
    response.encoding = 'utf-8'
    response.url = url
    response.headers['Content-Type'] = 'application/json'
    return response


def _valid(path, payload):
    if not isinstance(payload, dict) or 'error' in payload:
        return False
    parts = path.strip('/').split('/')
    if len(parts) == 2:
        if not isinstance(payload.get('id'), str) or not payload['id']:
            return False
        if parts[0] == 'release':
            # All release requests include recordings. An omitted tracklist is
            # an incomplete response, not evidence of an empty release.
            media = payload.get('media')
            return isinstance(media, list) and all(
                isinstance(medium, dict) and isinstance(medium.get('tracks'), list)
                for medium in media)
        return True
    plural = {'release': 'releases', 'release-group': 'release-groups',
              'recording': 'recordings', 'artist': 'artists'}.get(parts[0])
    return plural is not None and isinstance(payload.get(plural), list)


class CatalogueSession:
    def __init__(self, db, *, force=False, enabled=True):
        self.db, self.force, self.enabled = db, force, enabled
        self.started = time.time()
        self.memory = {}
        self.counts, self.seconds = Counter(), Counter()
        self.failed = False
        self.album_failed = False
        self.pending = None
        self.created = 0
        self.slowest = []
        self.touches = set()
        self.writes = 0
        self.store_available = enabled
        if enabled:
            try:
                with connection(db) as conn:
                    ensure_schema(conn)
            except Exception:
                self.store_available = False
                self.counts['cache_errors'] += 1

    @contextmanager
    def album(self, ordinal, *, caches=()):
        self.pending, self.album_failed = [], False
        start = time.perf_counter()
        before = self.seconds.copy()
        complete = False
        try:
            yield
            complete = True
        finally:
            pending, self.pending = self.pending, None
            if self.album_failed or not complete:
                # Provider adapters may have memoized their empty failure value.
                # Never let the next album treat that as a successful negative.
                for cache in caches:
                    cache.clear()
            if complete and not self.album_failed:
                for kind, callback in pending:
                    with stage('plan_publication_inclusive' if kind == 'plan' else 'finding_persistence'):
                        value = callback()
                    if kind == 'finding':
                        self.created += bool(value)
                    elif not value:
                        self.failed = True
            self.slowest.append({'album_ordinal': ordinal, 'seconds': round(time.perf_counter() - start, 4),
                                 'stages': {key: round(value - before[key], 4)
                                            for key, value in self.seconds.items() if value > before[key]}})
            self.slowest = sorted(self.slowest, key=lambda x: x['seconds'], reverse=True)[:10]

    def _read(self, key):
        if not self.store_available or self.force:
            return None
        try:
            with connection(self.db) as conn:
                row = conn.execute('SELECT payload,status,expires_at FROM reconcile_catalogue_cache WHERE cache_key=?', (key,)).fetchone()
            if row and row[2] > self.started:
                json.loads(row[0])
                self.touches.add(key)
                self.counts['persistent_hits'] += 1
                return row[:2]
            if row:
                self.counts['expired'] += 1
        except Exception:
            self.counts['cache_errors'] += 1
        return None

    def _write(self, key, raw, status, ttl):
        if not self.store_available or len(raw.encode()) > MAX_RESPONSE:
            return
        now = time.time()
        try:
            with connection(self.db) as conn:
                conn.execute('''INSERT OR REPLACE INTO reconcile_catalogue_cache
                    VALUES (?,?,?,?,?,?,?,?)''',
                    (key, raw, status, now, now + ttl, now, len(raw.encode()), hashlib.sha256(raw.encode()).hexdigest()))
                # Bounded maintenance, amortized rather than a table scan on hits.
                self.writes += 1
                if self.writes % 64 == 0:
                    evict(conn)
        except Exception:
            self.counts['cache_errors'] += 1

    def request(self, endpoint, path, params, fetch):
        params = dict(params or {})
        parts = path.strip('/').split('/')
        if len(parts) == 2 and parts[0] == 'release':
            params['inc'] = '+'.join(sorted(RICH_RELEASE | set(filter(None, params.get('inc', '').split('+')))))
        elif params.get('inc'):
            params['inc'] = '+'.join(sorted(set(params['inc'].split('+'))))
        # Endpoint identity is hashed, so credentials cannot appear in persisted
        # keys or metrics; a configured mirror/account change cannot cross-hit.
        key = hashlib.sha256(json.dumps([1, 'musicbrainz', endpoint.rstrip('/'), path, params], sort_keys=True).encode()).hexdigest()
        operation = parts[0] if len(parts) == 2 else parts[0] + '_list'
        self.counts['requests'] += 1
        if key in self.memory and self.enabled:
            self.counts['memory_hits'] += 1
            raw, status = self.memory[key]
        else:
            cached = self._read(key)
            if cached is not None and cached[1] == 200 and not _valid(path, json.loads(cached[0])):
                self.counts['cache_errors'] += 1
                cached = None
            if cached is not None:
                raw, status = cached
            else:
                self.counts['misses'] += 1
                flight_key = (id(self.db), key)
                with _flight_lock:
                    owner = flight_key not in _flights
                    future = _flights.setdefault(flight_key, Future())
                try:
                    if owner:
                        self.counts['upstream_' + operation] += 1
                        try:
                            response = fetch(path, params=params)
                            payload = response.json()
                            if not _valid(path, payload):
                                self.counts['invalid_responses'] += 1
                                raise ValueError('Invalid MusicBrainz catalogue response')
                            raw, status = json.dumps(payload, separators=(',', ':')), response.status_code
                            empty = len(parts) == 1 and not any(isinstance(v, list) and v for v in payload.values())
                            ttl = 3600 if empty else 86400 if len(parts) == 1 else 7 * 86400
                            if empty:
                                self.counts['empty_success'] += 1
                        except requests.HTTPError as exc:
                            if exc.response is None or exc.response.status_code != 404:
                                raise
                            raw, status, ttl = '{}', 404, 3600
                            self.counts['confirmed_not_found'] += 1
                        self._write(key, raw, status, ttl)
                        future.set_result((raw, status))
                    else:
                        self.counts['shared_inflight'] += 1
                        with stage('shared_wait'):
                            raw, status = future.result()
                except Exception as exc:
                    self.failed = self.album_failed = True
                    self.counts['provider_failures'] += 1
                    if owner:
                        future.set_exception(exc)
                    raise
                finally:
                    if owner:
                        with _flight_lock:
                            _flights.pop(flight_key, None)
            if self.enabled:
                self.memory[key] = (raw, status)
        response = _response(raw, status, endpoint.rstrip('/') + path)
        response.raise_for_status()
        return response

    def finish(self):
        storage = {}
        if self.store_available:
            try:
                with connection(self.db) as conn:
                    conn.executemany('UPDATE reconcile_catalogue_cache SET last_access=? WHERE cache_key=? AND last_access<?',
                                     [(self.started, key, self.started - 3600) for key in self.touches])
                    evict(conn)
                    rows, size = conn.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM reconcile_catalogue_cache').fetchone()
                    storage = {'responses': rows, 'bytes': size}
            except Exception:
                self.counts['cache_errors'] += 1
        return {'counts': dict(self.counts), 'seconds': {k: round(v, 4) for k, v in self.seconds.items()},
                'slowest_albums': self.slowest, 'catalogue_incomplete': self.failed,
                'storage': storage, 'memory_payload_bytes': sum(len(raw.encode()) for raw, _ in self.memory.values()),
                'cache_enabled': self.enabled, 'force_refresh': self.force,
                'evidence': getattr(self, 'evidence_metrics', {})}


def catalogue_scan(fn):
    @wraps(fn)
    def run(self, context, *args, **kwargs):
        force = kwargs.pop('refresh_catalogue', False)
        settings = self._get_settings(context)
        session = CatalogueSession(context.db, force=force, enabled=settings.get('catalogue_cache_enabled', True))
        token = current.set(session)
        start = time.perf_counter()
        result = None
        try:
            result = fn(self, context, *args, **kwargs)
            if session.failed:
                result.stopped_early = 'Catalogue evidence is incomplete; prior album plans were retained. Retry analysis.'
            result.findings_created += session.created
            return result
        finally:
            session.seconds['total_wall'] = time.perf_counter() - start
            metrics = session.finish()
            metrics['seconds']['total_wall'] = round(time.perf_counter() - start, 4)
            context.reconciliation_metrics = metrics
            current.reset(token)
            if result is not None:
                result.metrics = metrics
            log.info('Reconciliation catalogue metrics: %s', json.dumps(metrics))
            if context.report_progress:
                context.report_progress(log_line='Catalogue metrics: ' + json.dumps(metrics))
    return run


def deferred(kind):
    """Publish album findings/plan only after all its catalogue reads succeed."""
    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            session = current.get()
            if session is not None and session.pending is not None:
                session.pending.append((kind, lambda: fn(*args, **kwargs)))
                if kind == 'finding':
                    return False
                from core.repair_review_identity import review_case_key
                return review_case_key(args[1], None)
            return fn(*args, **kwargs)
        return run
    return decorate
