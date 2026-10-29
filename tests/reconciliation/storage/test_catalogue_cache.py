import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from core.reconciliation import catalogue as catalogue


class DB:
    def __init__(self, path):
        self.path = str(path)

    def _get_connection(self):
        return sqlite3.connect(self.path)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / 'cache.db')


def fetcher(calls, payload=None, status=200):
    def fetch(path, *, params):
        calls.append((path, params))
        response = catalogue._response(json.dumps(payload or {'id': 'release', 'media': []}), status, 'http://mirror' + path)
        response.raise_for_status()
        return response
    return fetch


def test_shared_rich_release_snapshot_and_restart(db):
    calls = []
    session = catalogue.CatalogueSession(db)
    for includes in ('recordings+artist-credits', 'labels+release-groups', 'release-groups'):
        response = session.request('http://mirror', '/release/release', {'inc': includes}, fetcher(calls))
        response.json()['media'].append('local mutation')
    assert len(calls) == 1
    assert set(calls[0][1]['inc'].split('+')) == catalogue.RICH_RELEASE
    session.finish()
    restarted = catalogue.CatalogueSession(db)
    assert restarted.request('http://mirror', '/release/release', {}, fetcher(calls)).json()['media'] == []
    assert len(calls) == 1
    assert restarted.counts['persistent_hits'] == 1


@pytest.mark.parametrize('endpoint,path,params', [
    ('http://other', '/release/release', {}),
    ('http://mirror', '/release-group/release', {}),
    ('http://mirror', '/release/release', {'inc': 'url-rels'}),
    ('http://mirror', '/release/release', {'market': 'US'}),
])
def test_request_identity_isolated(db, endpoint, path, params):
    calls = []
    session = catalogue.CatalogueSession(db)
    session.request('http://mirror', '/release/release', {}, fetcher(calls))
    session.request(endpoint, path, params, fetcher(calls))
    assert len(calls) == 2


def test_ttl_expiry_and_force_refresh_are_distinct(db, monkeypatch):
    calls = []
    monkeypatch.setattr(catalogue.time, 'time', lambda: 1000)
    first = catalogue.CatalogueSession(db)
    first.request('http://mirror', '/release/release', {}, fetcher(calls))
    monkeypatch.setattr(catalogue.time, 'time', lambda: 1000 + 7 * 86400)
    # Existing analysis keeps its original immutable snapshot.
    first.request('http://mirror', '/release/release', {}, fetcher(calls))
    assert len(calls) == 1
    expired = catalogue.CatalogueSession(db)
    expired.request('http://mirror', '/release/release', {}, fetcher(calls))
    assert expired.counts['expired'] == 1 and len(calls) == 2
    catalogue.CatalogueSession(db, force=True).request('http://mirror', '/release/release', {}, fetcher(calls))
    assert len(calls) == 3


def test_confirmed_404_cached_but_not_provider_errors(db):
    calls = []
    for _ in range(2):
        session = catalogue.CatalogueSession(db)
        with pytest.raises(requests.HTTPError):
            session.request('http://mirror', '/release/missing', {}, fetcher(calls, status=404))
        assert not session.failed
    assert len(calls) == 1
    for status in (429, 500, 503):
        session = catalogue.CatalogueSession(db)
        with pytest.raises(requests.HTTPError):
            session.request('http://mirror', '/release/transient', {}, fetcher(calls, status=status))
        assert session.failed
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM reconcile_catalogue_cache').fetchone()[0] == 1


def test_failed_refresh_retains_successful_response(db):
    calls = []
    catalogue.CatalogueSession(db).request('http://mirror', '/release/release', {}, fetcher(calls))
    refresh = catalogue.CatalogueSession(db, force=True)
    with pytest.raises(requests.Timeout):
        refresh.request('http://mirror', '/release/release', {}, lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout()))
    catalogue.CatalogueSession(db).request('http://mirror', '/release/release', {}, fetcher(calls))
    assert len(calls) == 1


def test_invalid_and_corrupt_payloads_are_not_evidence(db):
    session = catalogue.CatalogueSession(db)
    with pytest.raises(ValueError):
        session.request('http://mirror', '/release/release', {}, fetcher([], {'error': 'busy'}))
    assert session.failed
    calls = []
    catalogue.CatalogueSession(db).request('http://mirror', '/release/release', {}, fetcher(calls))
    with db._get_connection() as conn:
        conn.execute("UPDATE reconcile_catalogue_cache SET payload='invalid json'")
    catalogue.CatalogueSession(db).request('http://mirror', '/release/release', {}, fetcher(calls))
    assert len(calls) == 2


def test_single_flight_shares_success_and_releases_failure(db, monkeypatch):
    one, two = catalogue.CatalogueSession(db), catalogue.CatalogueSession(db)
    entered, release = threading.Event(), threading.Event()
    missed, resume = threading.Event(), threading.Event()
    calls = []

    read = two._read
    def delayed_miss(key, **kwargs):
        result = read(key, **kwargs)
        if result is None and not missed.is_set():
            missed.set()
            assert resume.wait(3)
        return result
    monkeypatch.setattr(two, '_read', delayed_miss)

    def fetch(path, *, params):
        entered.set()
        assert release.wait(3)
        return fetcher(calls)(path, params=params)

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(one.request, 'http://mirror', '/release/release', {}, fetch)
        assert entered.wait(2)
        b = pool.submit(two.request, 'http://mirror', '/release/release', {}, fetch)
        try:
            assert missed.wait(2)
            release.set()
            first = a.result().json()
        finally:
            release.set()
            resume.set()
        assert first == b.result().json()
    assert len(calls) == 1
    assert not catalogue._flights


def test_eviction_limits_bytes_and_rows_and_expiry(db):
    session = catalogue.CatalogueSession(db)
    for i in range(6):
        session.request('http://mirror', '/release/' + str(i), {}, fetcher([]))
    with db._get_connection() as conn:
        catalogue.evict(conn, max_rows=3, max_bytes=90)
        rows, size = conn.execute('SELECT COUNT(*),SUM(byte_size) FROM reconcile_catalogue_cache').fetchone()
        assert rows <= 3 and size <= 90
        assert catalogue.evict(conn, now=10**12) == rows


def test_cache_database_failure_does_not_block_provider(tmp_path):
    session = catalogue.CatalogueSession(DB(tmp_path / 'absent' / 'db'))
    calls = []
    assert session.request('http://mirror', '/release/release', {}, fetcher(calls)).json()['id'] == 'release'
    assert session.counts['cache_errors'] == 1
    assert not session.failed


def test_album_publication_discarded_after_late_provider_failure(db):
    session = catalogue.CatalogueSession(db)
    token = catalogue.current.set(session)
    published = []

    @catalogue.deferred('finding')
    def emit(value):
        published.append(value)
        return True

    try:
        adapter_cache = {'release': []}
        with session.album(1, caches=(adapter_cache,)):
            emit('old plan must survive')
            with pytest.raises(requests.HTTPError):
                session.request('http://mirror', '/release/failure', {}, fetcher([], status=503))
        assert published == []
        assert adapter_cache == {}
        with session.album(2):
            emit('independent album')
        assert published == ['independent album']
        assert session.created == 1
    finally:
        catalogue.current.reset(token)


def test_metrics_context_isolated_and_nested_timers_are_named(db, monkeypatch):
    session = catalogue.CatalogueSession(db)
    clock = iter([0, 1, 3, 5])
    monkeypatch.setattr(catalogue.time, 'perf_counter', lambda: next(clock))
    token = catalogue.current.set(session)
    try:
        with catalogue.stage('catalogue_inclusive'):
            with catalogue.stage('network'):
                pass
    finally:
        catalogue.current.reset(token)
    assert session.seconds == {'catalogue_inclusive': 5, 'network': 2}
    assert catalogue.current.get() is None


def test_search_empty_success_has_short_ttl_and_pagination_keys(db):
    calls = []
    session = catalogue.CatalogueSession(db)
    for offset in (0, 100):
        session.request('http://mirror', '/release', {'offset': offset},
                        fetcher(calls, {'releases': [], 'release-count': 0}))
    with db._get_connection() as conn:
        assert conn.execute('SELECT DISTINCT expires_at-fetched_at FROM reconcile_catalogue_cache').fetchall() == [(3600.0,)]
    assert len(calls) == 2 and session.counts['empty_success'] == 2


def test_disabled_cache_and_oversized_responses_do_not_persist(db, monkeypatch):
    calls = []
    disabled = catalogue.CatalogueSession(db, enabled=False)
    for _ in range(2):
        disabled.request('http://mirror', '/release/release', {}, fetcher(calls))
    assert len(calls) == 2
    monkeypatch.setattr(catalogue, 'MAX_RESPONSE', 10)
    enabled = catalogue.CatalogueSession(db)
    enabled.request('http://mirror', '/release/release', {}, fetcher(calls))
    with db._get_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM reconcile_catalogue_cache').fetchone()[0] == 0


def test_history_exposes_persisted_run_metrics(tmp_path):
    from core.repair_jobs.base import JobResult
    from core.repair_worker import RepairWorker
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'music.db'))
    worker = RepairWorker(database=db)
    run_id = worker._record_job_start('library_reconciliation')
    metrics = {'counts': {'memory_hits': 12}, 'seconds': {'network': .5}}
    worker._record_job_finish(run_id, 'library_reconciliation', JobResult(metrics=metrics), 1.0)
    assert worker.get_history('library_reconciliation', 1)[0]['metrics'] == metrics


@pytest.mark.parametrize('payload', [
    {'id': 'release'},
    {'id': 'release', 'media': None},
    {'id': 'release', 'media': [{'track-count': 2}]},
])
def test_partial_rich_release_cannot_replace_complete_evidence(db, payload):
    calls = []
    catalogue.CatalogueSession(db).request('http://mirror', '/release/release', {}, fetcher(calls))
    refresh = catalogue.CatalogueSession(db, force=True)
    with pytest.raises(ValueError):
        refresh.request('http://mirror', '/release/release', {}, fetcher([], payload))
    assert refresh.failed
    cached = catalogue.CatalogueSession(db).request('http://mirror', '/release/release', {}, fetcher(calls))
    assert cached.json()['media'] == []
    assert len(calls) == 1


def test_single_flight_failure_reaches_waiters_and_can_retry(db):
    import time
    one, two = catalogue.CatalogueSession(db), catalogue.CatalogueSession(db)
    entered, release = threading.Event(), threading.Event()

    def fail(path, *, params):
        entered.set()
        assert release.wait(3)
        raise requests.Timeout('temporary outage')

    with ThreadPoolExecutor(2) as pool:
        owner = pool.submit(one.request, 'http://mirror', '/release/release', {}, fail)
        assert entered.wait(2)
        waiter = pool.submit(two.request, 'http://mirror', '/release/release', {}, fail)
        deadline = time.monotonic() + 2
        while not two.counts['shared_inflight'] and time.monotonic() < deadline:
            time.sleep(.005)
        assert two.counts['shared_inflight'] == 1
        release.set()
        for future in (owner, waiter):
            with pytest.raises(requests.Timeout):
                future.result(timeout=3)
    assert one.failed and two.failed
    assert not catalogue._flights
    calls = []
    catalogue.CatalogueSession(db).request('http://mirror', '/release/release', {}, fetcher(calls))
    assert len(calls) == 1


def test_partial_scan_metrics_survive_exception_and_reset_context(db):
    from types import SimpleNamespace
    context = SimpleNamespace(db=db, report_progress=None)

    class Job:
        def _get_settings(self, context):
            return {}

        @catalogue.catalogue_scan
        def scan(self, context):
            catalogue.current.get().counts['albums_started'] += 1
            raise RuntimeError('scan interrupted')

    with pytest.raises(RuntimeError, match='scan interrupted'):
        Job().scan(context)
    assert context.reconciliation_metrics['counts']['albums_started'] == 1
    assert context.reconciliation_metrics['seconds']['total_wall'] >= 0
    assert catalogue.current.get() is None


@pytest.mark.parametrize('media', [
    [{'track-count': 0, 'format': 'Vinyl'}],
    [{'track-count': 1, 'tracks': [{'title': 'Song'}]},
     {'track-count': 0, 'format': 'DVD-Video'}],
])
def test_explicit_zero_track_media_are_valid_catalogue_evidence(db, media):
    calls = []
    payload = {'id': 'release', 'media': media}
    for _ in range(2):
        session = catalogue.CatalogueSession(db)
        assert session.request('http://mirror', '/release/release', {}, fetcher(calls, payload)).json() == payload
        assert not session.failed
    assert len(calls) == 1
