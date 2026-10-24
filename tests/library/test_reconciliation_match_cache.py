import copy
from collections import Counter
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.library import reconciliation_match_cache as cache
from core.library.reconciliation_catalogue import current
from core.library.reconciliation_plan import reconcile_folder


class DB:
    def __init__(self, path):
        self.path = str(path)

    def _get_connection(self):
        return sqlite3.connect(self.path)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / 'matching.db')


@pytest.fixture
def evidence():
    return ([{'path': '/album/01 - Song.flac', 'size_bytes': 100, 'mtime_ns': 123,
              'tags': {'title': 'Song', 'artist': 'Artist', 'duration_ms': 200000, 'track_number': 1},
              'db': {'title': 'Song', 'owner_profile_id': 1},
              'acoustid': {'status': 'ok', 'recordings': [
                  {'title': 'Song', 'artist': 'Artist', 'mbid': 'rec', 'score': .99}]}}],
            [{'title': 'Song', 'artist': 'Artist', 'recording_mbid': 'rec',
              'track_number': 1, 'duration_ms': 200000}])


def run(session, evidence, forced=None, compute=reconcile_folder):
    return session.match(*evidence, forced, ['profile', 'provider', 'release'], compute)


def test_restart_shadow_reuse_and_mutation_isolation(db, evidence):
    fresh = reconcile_folder(*evidence)
    cold = cache.MatchingCache(db, 'shadow')
    assert run(cold, evidence) == fresh
    assert cold.finish()['counts']['misses'] == 1
    shadow = cache.MatchingCache(db, 'shadow')
    assert run(shadow, evidence) == fresh
    assert shadow.counts['shadow_compared'] == 1
    reuse = cache.MatchingCache(db, 'reuse')
    forbidden = Mock(side_effect=AssertionError('must reuse'))
    result = run(reuse, evidence, compute=forbidden)
    assert result == fresh
    result['pairs'][0]['reasons'].append('mutated by consumer')
    assert run(reuse, evidence, compute=forbidden) == fresh
    assert reuse.counts['persistent_hits'] == 1 and reuse.counts['memory_hits'] == 1


@pytest.mark.parametrize('path,value', [
    (('path',), '/moved/Song.flac'), (('size_bytes',), 101), (('mtime_ns',), 124),
    (('duration_ms',), 210000), (('tags', 'title'), 'Other'),
    (('tags', 'artist'), 'Other'), (('tags', 'track_number'), 2),
    (('db', 'title'), 'Song (live)'), (('db', 'recording_disambiguation'), 'live'),
    (('db', 'owner_profile_id'), 2), (('acoustid', 'status'), 'no_match'),
    (('acoustid', 'recordings', 0, 'score'), .50),
    (('acoustid', 'recordings', 0, 'title'), 'Other'),
    (('acoustid', 'recordings', 0, 'duration_ms'), 210000),
])
def test_evidence_changes_invalidate(evidence, path, value):
    files, slots = evidence
    before = cache.input_key('policy', files, slots, None, 'identity')
    node = files[0]
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    assert cache.input_key('policy', files, slots, None, 'identity') != before


def test_sequence_policy_release_and_manual_identity(evidence):
    files, slots = evidence
    files.append({**files[0], 'path': '/album/copy.flac'})
    slots.append({**slots[0], 'title': 'Other'})
    base = cache.input_key('v1', files, slots, None, 'release')
    for args in [('v2', files, slots, None, 'release'),
                 ('v1', files[::-1], slots, None, 'release'),
                 ('v1', files, slots[::-1], None, 'release'),
                 ('v1', files[:1], slots, None, 'release'),
                 ('v1', files, slots, {0: 1}, 'release'),
                 ('v1', files, slots, None, 'other')]:
        assert cache.input_key(*args) != base
    files[0]['db']['updated_at'] = 'unrelated'
    files[0]['db']['locked_fields'] = ['artist']
    slots[0]['preview_url'] = 'expiring'
    assert cache.input_key('v1', files, slots, None, 'release') == base


def test_manual_results_separate_and_invalid_assignments_still_fail(db, evidence):
    session = cache.MatchingCache(db, 'reuse')
    auto = run(session, evidence)
    manual = run(session, evidence, {0: 0})
    assert manual['pairs'][0]['confidence'] == 'manual'
    assert auto['pairs'][0]['confidence'] != 'manual'
    assert run(session, evidence, {0: 0}) == manual
    with pytest.raises(ValueError):
        run(session, evidence, {0: 99})


def test_competing_integer_keys_survive_restart(db, evidence):
    evidence[0].append(copy.deepcopy(evidence[0][0]))
    evidence[0][1]['path'] = '/album/copy.flac'
    fresh = reconcile_folder(*evidence)
    assert fresh['competing_files']
    run(cache.MatchingCache(db, 'reuse'), evidence)
    assert run(cache.MatchingCache(db, 'reuse'), evidence) == fresh
    assert all(type(k) is int for k in fresh['competing_files'])


def test_shadow_mismatch_uses_fresh_and_disables_run(db, evidence):
    run(cache.MatchingCache(db, 'reuse'), evidence)
    def changed(*args, **kwargs):
        result = reconcile_folder(*args, **kwargs)
        result['pairs'][0]['reasons'].append('new policy')
        return result
    shadow = cache.MatchingCache(db, 'shadow')
    assert run(shadow, evidence, compute=changed) == changed(*evidence)
    assert shadow.disabled and shadow.counts['shadow_mismatches'] == 1
    assert run(shadow, evidence) == reconcile_folder(*evidence)
    assert shadow.counts['bypassed'] == 1


@pytest.mark.parametrize('damage', ['checksum', 'schema'])
def test_corruption_falls_back_and_repairs(db, evidence, damage):
    run(cache.MatchingCache(db, 'reuse'), evidence)
    with db._get_connection() as conn:
        if damage == 'checksum':
            conn.execute("UPDATE reconcile_match_cache SET checksum='bad'")
        else:
            conn.execute('UPDATE reconcile_match_cache SET payload=?,checksum=?', ('{}', cache.digest('{}')))
    session = cache.MatchingCache(db, 'reuse')
    assert run(session, evidence) == reconcile_folder(*evidence)
    assert session.counts['cache_errors'] == 1
    restarted = cache.MatchingCache(db, 'reuse')
    run(restarted, evidence, compute=Mock(side_effect=AssertionError('must reuse repaired result')))


def test_database_failure_does_not_block_matching(evidence):
    session = cache.MatchingCache(SimpleNamespace(_get_connection=Mock(side_effect=OSError())), 'reuse')
    assert run(session, evidence) == reconcile_folder(*evidence)
    assert session.disabled


@pytest.mark.parametrize('status', ['error', 'timeout', None])
def test_transient_audio_is_not_cached(db, evidence, status):
    evidence[0][0]['acoustid']['status'] = status
    session = cache.MatchingCache(db, 'reuse')
    assert run(session, evidence) == reconcile_folder(*evidence)
    assert session.counts['bypassed'] == 1
    assert session.finish()['storage']['results'] == 0


def test_incomplete_catalogue_bypasses_matching(db, evidence):
    token = current.set(SimpleNamespace(album_failed=True, seconds=Counter()))
    try:
        session = cache.MatchingCache(db, 'reuse')
        assert run(session, evidence) == reconcile_folder(*evidence)
        assert session.counts['bypassed'] == 1
    finally:
        current.reset(token)


def test_expiry_and_limits(db, evidence, monkeypatch):
    run(cache.MatchingCache(db, 'reuse'), evidence)
    with db._get_connection() as conn:
        conn.execute('UPDATE reconcile_match_cache SET last_access=0')
    expired = cache.MatchingCache(db, 'reuse')
    run(expired, evidence)
    assert expired.counts['misses'] == 1
    with db._get_connection() as conn:
        assert cache.evict(conn, max_rows=0) == 1
    monkeypatch.setattr(cache, 'MAX_MEMORY', 1)
    session = cache.MatchingCache(db, 'reuse')
    run(session, evidence)
    assert not session.memory
    with db._get_connection() as conn:
        assert cache.evict(conn, max_bytes=1) == 1
    monkeypatch.setattr(cache, 'MAX_RESULT', 1)
    run(session, evidence)
    assert session.counts['oversized'] == 1


def test_unsupported_inputs_bypass_and_off_does_not_write(db, evidence):
    evidence[0][0]['tags']['opaque'] = object()
    session = cache.MatchingCache(db, 'reuse')
    assert run(session, evidence) == reconcile_folder(*evidence)
    assert session.counts['signature_errors'] == 1
    off = cache.MatchingCache(db, 'off')
    assert run(off, evidence) == reconcile_folder(*evidence)
    assert not off.store and off.counts['bypassed'] == 1


def test_scan_connection_is_reused_without_holding_writer_lock(db, evidence):
    original = db._get_connection
    db._get_connection = Mock(side_effect=original)
    session = cache.MatchingCache(db, 'reuse')
    run(session, evidence)
    evidence[0][0]['mtime_ns'] += 1
    run(session, evidence)
    assert db._get_connection.call_count == 1
    # The persistent connection must not lock out current decision/file writes.
    other = original()
    try:
        other.execute('PRAGMA busy_timeout=1')
        with other:
            other.execute('CREATE TABLE decisions (choice TEXT)')
            other.execute("INSERT INTO decisions VALUES ('complete')")
    finally:
        other.close()
    held = session.conn
    session.finish()
    assert session.conn is None
    with pytest.raises(sqlite3.ProgrammingError):
        held.execute('SELECT 1')


def test_evidence_rich_results_fit_and_reuse_within_the_existing_disk_budget(db, evidence):
    fresh = reconcile_folder(*evidence)
    raw = cache.encode(fresh)
    cold = cache.MatchingCache(db, 'reuse')
    run(cold, evidence)
    cold.finish()
    with db._get_connection() as conn:
        payload, size = conn.execute('SELECT payload,byte_size FROM reconcile_match_cache').fetchone()
        assert size == len(payload) < len(raw.encode()) / 2
        assert cache.restored_payload(payload) == raw
        assert cache.evict(conn, max_bytes=len(raw.encode()) // 2) == 0
    assert run(cache.MatchingCache(db, 'reuse'), evidence,
               compute=Mock(side_effect=AssertionError('must reuse every check'))) == fresh


def test_legacy_cache_compacts_without_losing_hits_or_changing_evidence(db, evidence):
    fresh = run(cache.MatchingCache(db, 'reuse'), evidence)
    raw = cache.encode(fresh)
    with db._get_connection() as conn:
        conn.execute('UPDATE reconcile_match_cache SET payload=?,byte_size=?', (raw, len(raw.encode())))
    restarted = cache.MatchingCache(db, 'reuse')
    assert restarted.counts['storage_compacted'] == 1
    assert run(restarted, evidence, compute=Mock(side_effect=AssertionError('must preserve old hit'))) == fresh
    assert not cache.MatchingCache(db, 'reuse').counts['storage_compacted']


@pytest.mark.parametrize('damage', ['truncated', 'trailing', 'too_large'])
def test_invalid_compressed_results_are_bounded_and_recomputed(db, evidence, damage):
    import zlib
    run(cache.MatchingCache(db, 'reuse'), evidence)
    with db._get_connection() as conn:
        payload = conn.execute('SELECT payload FROM reconcile_match_cache').fetchone()[0]
        damaged = (payload[:-3] if damage == 'truncated' else payload + b'extra' if damage == 'trailing'
                   else b'Z1' + zlib.compress(b'x' * (cache.MAX_RESULT + 1)))
        conn.execute('UPDATE reconcile_match_cache SET payload=?', (damaged,))
    restarted = cache.MatchingCache(db, 'reuse')
    assert run(restarted, evidence) == reconcile_folder(*evidence)
    assert restarted.counts['cache_errors'] == 1
