"""Deterministic slow-I/O regressions: no live services or real library files."""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from core.image_cache import ImageCache
from core.downloads import status as st


def test_repeated_library_images_do_not_repeat_transactions(tmp_path, monkeypatch):
    cache = ImageCache(tmp_path)
    urls = [f'https://example.test/{i}.jpg' for i in range(75)]
    for url in urls:
        cache.cache_url_for(url)
    statements = []
    connect = cache._connect
    def traced():
        conn = connect()
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(cache, '_connect', traced)
    for url in urls:
        cache.cache_url_for(url)
    writes = [sql for sql in statements if sql.lstrip().upper().startswith('INSERT')]
    assert not writes, f'Repeated library page performed {len(writes)} write transactions'


def test_slow_recovery_does_not_hold_status_request_or_task_lock():
    entered, release = threading.Event(), threading.Event()
    def finder(*args):
        entered.set()
        assert release.wait(5)
        return '/found.flac', 'downloads'
    deps = st.StatusDeps(
        config_manager=SimpleNamespace(get=lambda key, default=None: default),
        docker_resolve_path=lambda p: p, find_completed_file=finder,
        make_context_key=lambda u, f: f'{u}::{f}',
        submit_post_processing=lambda *a: None, get_cached_transfer_data=lambda: {},
    )
    task = dict(track_index=0, status='downloading', track_info={},
                filename='song.flac', status_change_time=0)
    with st.tasks_lock:
        st.download_tasks['perf-task'] = task
        st.download_batches['perf-batch'] = dict(phase='downloading', queue=['perf-task'])
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(st.build_single_batch_status, 'perf-batch', deps)
        try:
            assert entered.wait(2)
            acquired = st.tasks_lock.acquire(timeout=0.2)
            if acquired:
                st.tasks_lock.release()
            assert acquired, 'Filesystem scan held the global download lock'
            assert future.result(timeout=0.5)[1] == 200
        finally:
            release.set()
            future.result(timeout=3)
            with st.tasks_lock:
                st.download_tasks.pop('perf-task', None)
                st.download_batches.pop('perf-batch', None)


def test_import_http_response_does_not_wait_for_processing(monkeypatch):
    from flask import Flask
    from api import import_routes as routes
    entered, release = threading.Event(), threading.Event()
    def process(runtime, data):
        entered.set()
        assert release.wait(5)
        return {'success': True, 'processed': 1, 'total': 1, 'errors': []}, 200
    monkeypatch.setattr(routes, '_build_import_route_runtime', lambda: None)
    monkeypatch.setattr(routes, '_import_album_process', process)
    app = Flask(__name__)
    def request():
        with app.test_request_context('/api/import/album/process', method='POST',
                                     json={'album': {}, 'matches': []},
                                     headers={'Prefer': 'respond-async', 'Idempotency-Key': 'proof-1245'}):
            response, status = routes.import_album_process()
            return response.get_json(), status
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(request)
        try:
            assert entered.wait(2)
            payload, status = future.result(timeout=0.3)
            assert status == 202
            assert payload['job_id']
        finally:
            release.set()
            future.result(timeout=3)


def test_cache_registration_survives_clear_and_refreshes_timestamp(tmp_path, monkeypatch):
    import core.image_cache as module
    cache = ImageCache(tmp_path)
    now = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: now[0])
    url = 'https://example.test/cover.jpg'
    key = cache.key_for_url(url)
    cache.cache_url_for(url)
    cache.clear()
    cache.cache_url_for(url)
    assert cache._get_row(key) is not None
    now[0] += 61
    cache.cache_url_for(url)
    assert cache._get_row(key)['last_accessed'] == now[0]


def test_recovery_never_overwrites_cancellation_or_retries(monkeypatch):
    for mutation in ({'status': 'cancelled'}, {'download_id': 'new-attempt'}, {'status': 'completed'}, {'cancel_requested': True}):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        submitted = []
        def finder(*args):
            entered.set()
            assert release.wait(3)
            return '/found.flac', 'downloads'
        class Pool:
            def submit(self, fn):
                def run():
                    try:
                        fn()
                    finally:
                        done.set()
                threading.Thread(target=run).start()
        monkeypatch.setattr(st, '_recovery_pool', Pool())
        deps = st.StatusDeps(SimpleNamespace(get=lambda k, d=None: d), lambda p: p,
                             finder, lambda u, f: f, lambda *a: submitted.append(a), lambda: {})
        task = dict(status='downloading', filename='song.flac', status_change_time=0)
        with st.tasks_lock:
            st.download_tasks['race-1245'] = task
            st._schedule_file_recovery('race-1245', 'batch', task, deps)
        try:
            assert entered.wait(2)
            with st.tasks_lock:
                task.update(mutation)
                st._schedule_file_recovery('race-1245', 'batch', task, deps)
            release.set()
            assert done.wait(2)
            assert not submitted
            for key, value in mutation.items():
                assert task[key] == value
        finally:
            release.set()
            done.wait(3)
            with st.tasks_lock:
                st.download_tasks.pop('race-1245', None)


def test_import_jobs_deduplicate_and_isolate_profiles():
    from core.imports.jobs import ImportJobs
    from core.profile_context import get_current_profile_id
    jobs = ImportJobs(workers=1, capacity=1)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def operation():
        calls.append(get_current_profile_id())
        entered.set()
        assert release.wait(3)
        return {'success': True, 'processed': 1}, 200
    try:
        accepted, status = jobs.submit(7, 'key', 'album', {'a': 1}, operation)
        assert status == 202 and entered.wait(2)
        duplicate, _ = jobs.submit(7, 'key', 'album', {'a': 1}, operation)
        assert duplicate == accepted
        assert jobs.submit(7, 'key', 'album', {'a': 2}, operation)[1] == 409
        assert jobs.submit(7, 'other', 'album', {}, operation)[1] == 429
        assert jobs.get(8, accepted['job_id'])[1] == 404
        release.set()
        jobs.pool.shutdown(wait=True)
        result, status = jobs.get(7, accepted['job_id'])
        assert status == 200 and result['state'] == 'complete'
        assert result['result']['processed'] == 1
        assert calls == [7]
    finally:
        release.set()
        jobs.pool.shutdown(wait=True)


def test_recovery_submission_failure_does_not_strand_processing(monkeypatch):
    monkeypatch.setattr(st, '_recovery_pool', SimpleNamespace(submit=lambda fn: fn()))
    def rejected(*args):
        raise RuntimeError('executor unavailable')
    deps = st.StatusDeps(SimpleNamespace(get=lambda k, d=None: d), lambda p: p,
                         lambda *a: ('/found.flac', 'downloads'), lambda u, f: f,
                         rejected, lambda: {})
    task = dict(status='downloading', filename='song.flac', status_change_time=0)
    st.download_tasks['submit-1245'] = task
    try:
        st._schedule_file_recovery('submit-1245', 'batch', task, deps)
        assert task['status'] == 'downloading'
        assert 'submit-1245' not in st._recovery_pending
    finally:
        st.download_tasks.pop('submit-1245', None)


def test_import_job_errors_are_reported_without_reexecuting():
    from core.imports.jobs import ImportJobs
    jobs = ImportJobs(workers=1)
    def fail():
        raise RuntimeError('provider failed')
    accepted, _ = jobs.submit(1, 'fail', 'single', {}, fail)
    jobs.pool.shutdown(wait=True)
    payload, _ = jobs.get(1, accepted['job_id'])
    assert payload['state'] == 'complete'
    assert payload['result']['success'] is False
    assert payload['status'] == 500
    assert jobs.submit(1, 'fail', 'single', {}, fail)[0] == accepted


def test_job_http_failure_preserves_disconnected_server_error(monkeypatch):
    from flask import Flask
    from api import import_routes as routes
    from core.imports import jobs as module
    jobs = module.ImportJobs(workers=1)
    monkeypatch.setattr(module, 'import_jobs', jobs)
    result = {'success': False, 'error': 'Server disconnected', 'error_code': 'media_server_not_connected'}
    accepted, _ = jobs.submit(1, 'offline', 'album', {}, lambda: (result, 503))
    jobs.pool.shutdown(wait=True)
    app = Flask(__name__)
    with app.test_request_context():
        response, status = routes.import_job_status(accepted['job_id'])
        assert status == 503
        assert response.get_json() == result


def test_synchronous_import_api_remains_compatible(monkeypatch):
    from flask import Flask
    from api import import_routes as routes
    monkeypatch.setattr(routes, '_build_import_route_runtime', lambda: None)
    result = {'success': True, 'processed': 1, 'total': 1, 'errors': []}
    monkeypatch.setattr(routes, '_import_album_process', lambda runtime, data: (result, 200))
    with Flask(__name__).test_request_context(method='POST', json={}):
        response, status = routes.import_album_process()
        assert status == 200
        assert response.get_json() == result
