"""Disposable pure matching results; never stores decisions or ready state."""
from __future__ import annotations

from collections import Counter, OrderedDict
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import logging
import math
from pathlib import Path
import sys
import time
import unicodedata
import zlib

from core.library.reconciliation_catalogue import current, stage

MAX_ROWS = 25_000
MAX_BYTES = 128 * 1024 * 1024
MAX_RESULT = 1024 * 1024
MAX_MEMORY = 32 * 1024 * 1024
RETENTION = 30 * 86400
SCHEMA = 3  # Structured matcher checks accompany each pair.
log = logging.getLogger(__name__)


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reconcile_match_cache (
        cache_key TEXT PRIMARY KEY, policy TEXT NOT NULL, payload TEXT NOT NULL,
        checksum TEXT NOT NULL, byte_size INTEGER NOT NULL,
        created_at REAL NOT NULL, last_access REAL NOT NULL,
        compute_seconds REAL NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_reconcile_match_access ON reconcile_match_cache(last_access)')


def evict(conn, *, now=None, max_rows=MAX_ROWS, max_bytes=MAX_BYTES):
    now = time.time() if now is None else now
    removed = conn.execute('DELETE FROM reconcile_match_cache WHERE last_access<?', (now - RETENTION,)).rowcount
    count, size = conn.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM reconcile_match_cache').fetchone()
    while count > max_rows or size > max_bytes:
        rows = conn.execute('SELECT cache_key,byte_size FROM reconcile_match_cache ORDER BY last_access LIMIT 256').fetchall()
        if not rows:
            break
        victims = []
        for key, length in rows:
            if count <= max_rows and size <= max_bytes:
                break
            victims.append((key,))
            count -= 1
            size -= length
        conn.executemany('DELETE FROM reconcile_match_cache WHERE cache_key=?', victims)
        removed += len(victims)
    return removed


def canonical(value):
    # Do not stringify unsupported evidence or silently accept NaN/Infinity.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def stored_payload(raw):
    """Repeated evidence labels compress well; keep the existing disk budget."""
    encoded = raw.encode()
    packed = b'Z1' + zlib.compress(encoded, level=3)
    return packed if len(packed) < len(encoded) else raw


def restored_payload(payload):
    if isinstance(payload, str):  # Cache entries written before compression.
        if len(payload.encode()) > MAX_RESULT:
            raise ValueError('Oversized matching result')
        return payload
    if not isinstance(payload, bytes) or not payload.startswith(b'Z1') or len(payload) > MAX_RESULT:
        raise ValueError('Invalid matching storage format')
    decoder = zlib.decompressobj()
    raw = decoder.decompress(payload[2:], MAX_RESULT + 1)
    if len(raw) > MAX_RESULT or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('Invalid compressed matching result')
    return raw.decode()


@lru_cache(maxsize=1)
def policy_identity():
    # Include transitive semantic helpers, not the application commit: a docs
    # deployment should hit, a normalization/solver/runtime change must miss.
    root = Path(__file__).resolve().parents[2]
    paths = ('core/library/reconciliation_plan.py', 'core/matching/assignment.py',
             'core/matching/audio_verification.py', 'core/matching/artist_aliases.py', 'core/text/fold.py',
             'core/text/title_match.py')
    sources = [hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths]
    return digest(canonical([SCHEMA, sys.implementation.name, list(sys.version_info[:3]),
                             unicodedata.unidata_version, sources]))


def input_key(policy, files, slots, forced_pairs, identity):
    # Keep original sequence and exact types. The entire tag/audio dictionaries
    # are conservative dependencies; irrelevant provider additions may miss but
    # cannot hide a new semantic input. UI and decision state stay outside.
    local = [{
        'path': f.get('path'), 'size_bytes': f.get('size_bytes'), 'mtime_ns': f.get('mtime_ns'),
        'duration_ms': f.get('duration_ms'), 'tags': f.get('tags'), 'acoustid': f.get('acoustid'),
        'companion_of': f.get('companion_of'),
        'db': {key: (f.get('db') or {}).get(key) for key in
               ('title', 'recording_disambiguation', 'owner_profile_id', 'artist_aliases')},
    } for f in files]
    remote = [{key: value for key, value in slot.items() if key != 'preview_url'} for slot in slots]
    return digest(canonical([policy, identity, local, remote, sorted((forced_pairs or {}).items())]))


def encode(result):
    return canonical({**result, 'competing_files': sorted(result['competing_files'].items())})


def decode(raw, files, slots, forced_pairs):
    result = json.loads(raw)
    if not isinstance(result, dict) or set(result) != {'pairs', 'extra_files', 'missing_slots', 'competing_files'}:
        raise ValueError('Invalid matching result schema')
    def indices(values, maximum):
        if not isinstance(values, list) or any(type(v) is not int or not 0 <= v < maximum for v in values):
            raise ValueError('Invalid matching indices')
        if len(set(values)) != len(values):
            raise ValueError('Repeated matching index')
        return set(values)
    pairs = result['pairs']
    if not isinstance(pairs, list):
        raise ValueError('Invalid pairs')
    used_files, used_slots = [], []
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != {'file_index', 'slot_index', 'score', 'reasons', 'duration_delta_ms', 'ambiguity_reasons', 'confidence', 'competitors', 'catalogue_disagreement', 'checks'}:
            raise ValueError('Invalid pair schema')
        if not isinstance(pair['checks'], list) or any(not isinstance(row, dict) for row in pair['checks']):
            raise ValueError('Invalid structured checks')
        if type(pair['catalogue_disagreement']) is not bool or not isinstance(pair['competitors'], list):
            raise ValueError('Invalid assignment explanation')
        for rival in pair['competitors']:
            if not isinstance(rival, dict):
                raise ValueError('Invalid assignment competitor')
            expected = ({'kind', 'slot_title', 'disc_number', 'track_number'}
                        if rival.get('kind') == 'release_slot' else
                        {'kind', 'file_path', 'current_title', 'assigned_title'})
            if set(rival) != expected or rival['kind'] not in ('release_slot', 'local_file'):
                raise ValueError('Invalid assignment competitor')
        used_files.append(pair['file_index'])
        used_slots.append(pair['slot_index'])
        if type(pair['score']) is not int or pair['score'] <= 0:
            raise ValueError('Invalid matching score')
        delta = pair['duration_delta_ms']
        if delta is not None and (type(delta) is not int or delta < 0):
            raise ValueError('Invalid matching duration')
        for field in ('reasons', 'ambiguity_reasons'):
            if not isinstance(pair[field], list) or any(not isinstance(v, str) for v in pair[field]):
                raise ValueError('Invalid matching explanation')
        if pair['confidence'] not in ('clear', 'likely', 'ambiguous', 'manual'):
            raise ValueError('Invalid matching confidence')
        manual = (forced_pairs or {}).get(pair['file_index']) == pair['slot_index']
        if (pair['confidence'] == 'manual') != manual:
            raise ValueError('Invalid manual confidence')
    a, b = indices(used_files, files), indices(result['extra_files'], files)
    c, d = indices(used_slots, slots), indices(result['missing_slots'], slots)
    if a & b or a | b != set(range(files)) or c & d or c | d != set(range(slots)):
        raise ValueError('Incomplete matching partition')
    competing = result['competing_files']
    if not isinstance(competing, list):
        raise ValueError('Invalid competitors')
    restored = {}
    for entry in competing:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError('Invalid competitor entry')
        index, rivals = entry
        if type(index) is not int or index not in b or index in restored or not indices(rivals, files) <= a:
            raise ValueError('Invalid competitor references')
        restored[index] = rivals
    result['competing_files'] = restored
    return result


class MatchingCache:
    def __init__(self, db, mode='off'):
        self.db = db
        self.conn = None
        self.mode = mode if mode in ('off', 'shadow', 'reuse') else 'off'
        self.counts = Counter()
        self.memory = OrderedDict()
        self.memory_bytes = 0
        self.touches = set()
        self.writes = 0
        self.disabled = False
        self.started = time.time()
        self.policy = ''
        self.store = self.mode != 'off'
        if self.store:
            try:
                self.policy = policy_identity()
                with self._connection() as conn:
                    ensure_schema(conn)
                self._compact_legacy_storage()
            except Exception:
                self.counts['cache_errors'] += 1
                self.store = False
                self.disabled = True

    def _compact_legacy_storage(self):
        # Preserve existing hits on upgrade. Commit bounded batches so review
        # decisions do not wait behind a transaction over the entire cache.
        after = ''
        while True:
            with self._connection() as conn:
                rows = conn.execute('''SELECT cache_key,payload,checksum,byte_size
                    FROM reconcile_match_cache WHERE typeof(payload)='text' AND cache_key>?
                    ORDER BY cache_key LIMIT 128''', (after,)).fetchall()
            if not rows:
                return
            after = rows[-1][0]
            updates = []
            for key, raw, checksum, old_size in rows:
                if len(raw.encode()) > MAX_RESULT or digest(raw) != checksum:
                    continue  # Normal reads reject and repair invalid entries.
                packed = stored_payload(raw)
                if isinstance(packed, bytes):
                    updates.append((packed, len(packed), key, checksum))
                    self.counts['storage_bytes_saved'] += old_size - len(packed)
            if updates:
                with self._connection() as conn:
                    conn.executemany('''UPDATE reconcile_match_cache SET payload=?, byte_size=?
                        WHERE cache_key=? AND checksum=? AND typeof(payload)='text' ''', updates)
                self.counts['storage_compacted'] += len(updates)

    @contextmanager
    def _connection(self):
        # Scan-local, used on its owning worker thread only. A completed SELECT
        # holds no transaction; every write commits before matching or other DB
        # work resumes. Avoid reopening SQLite and its pragmas for every candidate.
        if self.conn is None:
            self.conn = self.db._get_connection()
        try:
            with self.conn:
                yield self.conn
        except Exception:
            self.conn.close()
            self.conn = None
            raise

    def _remember(self, key, raw, cost):
        size = len(raw.encode())
        if size > MAX_RESULT:
            return
        if key in self.memory:
            self.memory_bytes -= len(self.memory.pop(key)[0].encode())
        self.memory[key] = (raw, cost)
        self.memory_bytes += size
        while self.memory_bytes > MAX_MEMORY and self.memory:
            _, (old, _) = self.memory.popitem(last=False)
            self.memory_bytes -= len(old.encode())

    def _read(self, key):
        if key in self.memory:
            self.memory.move_to_end(key)
            self.counts['memory_hits'] += 1
            return self.memory[key]
        if self.store:
            with self._connection() as conn:
                row = conn.execute('SELECT payload,checksum,compute_seconds,last_access,policy FROM reconcile_match_cache WHERE cache_key=?', (key,)).fetchone()
            if row and row[3] >= self.started - RETENTION and row[4] == self.policy:
                if not math.isfinite(row[2]) or row[2] < 0:
                    raise ValueError('Invalid matching cache record')
                raw = restored_payload(row[0])
                if digest(raw) != row[1]:
                    raise ValueError('Matching checksum mismatch')
                self.counts['persistent_hits'] += 1
                self.touches.add(key)
                self._remember(key, raw, row[2])
                return raw, row[2]
        return None

    def _write(self, key, raw, cost):
        size = len(raw.encode())
        if size > MAX_RESULT:
            self.counts['oversized'] += 1
            return
        self._remember(key, raw, cost)
        if self.store:
            packed = stored_payload(raw)
            size = len(packed) if isinstance(packed, bytes) else len(packed.encode())
            now = time.time()
            with self._connection() as conn:
                conn.execute('INSERT OR REPLACE INTO reconcile_match_cache VALUES (?,?,?,?,?,?,?,?)',
                             (key, self.policy, packed, digest(raw), size, now, now, cost))
                self.writes += 1
                if self.writes % 64 == 0:
                    evict(conn)

    def match(self, files, slots, forced_pairs, identity, compute):
        self.counts['requests'] += 1
        kind = 'manual' if forced_pairs else 'automatic'
        def fresh():
            start = time.perf_counter()
            with stage('candidate_matching'), stage('matching_' + kind):
                value = compute(files, slots, forced_pairs=forced_pairs) if forced_pairs else compute(files, slots)
            self.counts['computations'] += 1
            return value, time.perf_counter() - start
        session = current.get()
        eligible = (self.mode != 'off' and not self.disabled and not (session and session.album_failed)
                    and all((f.get('acoustid') or {}).get('status') in ('ok', 'no_match') for f in files))
        if not eligible:
            self.counts['bypassed'] += 1
            return fresh()[0]
        try:
            with stage('matching_signature'):
                key = input_key(self.policy, files, slots, forced_pairs, identity)
        except Exception:
            self.counts['signature_errors'] += 1
            return fresh()[0]
        try:
            with stage('matching_cache_read'):
                cached = self._read(key)
                value = decode(cached[0], len(files), len(slots), forced_pairs) if cached else None
        except Exception:
            self.counts['cache_errors'] += 1
            cached, value = None, None
            if key in self.memory:
                self.memory_bytes -= len(self.memory.pop(key)[0].encode())
        if cached and self.mode == 'reuse':
            self.counts['reused'] += 1
            self.counts['avoided_compute_ms_estimate'] += round(cached[1] * 1000)
            return value
        result, cost = fresh()
        if cached:
            self.counts['shadow_compared'] += 1
            if value != result:
                self.counts['shadow_mismatches'] += 1
                log.error('Matching shadow mismatch for cache key %s; reuse disabled for this scan', key)
                self.disabled = True
                self.memory.clear()
                self.memory_bytes = 0
                # A discrepancy is a release blocker; use fresh results for the
                # rest of this run, including all downstream plan projection.
                return result
        else:
            self.counts['misses'] += 1
        try:
            with stage('matching_cache_write'):
                raw = encode(result)
                if decode(raw, len(files), len(slots), forced_pairs) != result:
                    raise ValueError('Matching codec changed the result')
                if not cached:
                    self._write(key, raw, cost)
        except Exception:
            self.counts['cache_errors'] += 1
        return result

    def finish(self):
        storage = {}
        if self.store:
            try:
                with self._connection() as conn:
                    conn.executemany('UPDATE reconcile_match_cache SET last_access=? WHERE cache_key=? AND last_access<?',
                                     [(self.started, k, self.started - 3600) for k in self.touches])
                    evict(conn)
                    rows, size = conn.execute('SELECT COUNT(*),COALESCE(SUM(byte_size),0) FROM reconcile_match_cache').fetchone()
                    storage = {'results': rows, 'bytes': size}
            except Exception:
                self.counts['cache_errors'] += 1
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        return {'mode': self.mode, 'disabled': self.disabled, 'counts': dict(self.counts),
                'storage': storage, 'memory_bytes': self.memory_bytes, 'policy': self.policy}


def cached_match(files, slots, *, source='', release_id='', forced_pairs=None, compute=None):
    from core.library.reconciliation_plan import reconcile_folder
    compute = compute or reconcile_folder
    session = current.get()
    cache = getattr(session, 'matching', None)
    if cache is None:
        return compute(files, slots, forced_pairs=forced_pairs) if forced_pairs else compute(files, slots)
    profiles = sorted({str((f.get('db') or {}).get('owner_profile_id') or 1) for f in files})
    return cache.match(files, slots, forced_pairs, [profiles, source, release_id], compute)
