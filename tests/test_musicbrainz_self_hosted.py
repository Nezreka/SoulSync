"""Mirror configuration, shared pacing and real HTTP contract tests (no external API)."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

import pytest
import requests

import core.musicbrainz_client as mb


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get', lambda key, default=None: default)
    for key in ('BASE_URL', 'REQUEST_INTERVAL', 'MAX_RETRIES', 'READ_TIMEOUT', 'CONNECT_TIMEOUT'):
        monkeypatch.delenv('SOULSYNC_MUSICBRAINZ_' + key, raising=False)
    monkeypatch.setattr(mb, '_last_api_call_time', 0)


def test_defaults():
    client = mb.MusicBrainzClient()
    assert client.base_url == 'https://musicbrainz.org/ws/2'
    assert client.request_interval == 1.05


@pytest.mark.parametrize('url,expected', [
    ('http://mirror:5000', 'http://mirror:5000/ws/2'),
    ('http://mirror:5000/ws/2/', 'http://mirror:5000/ws/2'),
    ('https://mirror/mb/', 'https://mirror/mb/ws/2'),
    ('http://[::1]:5000', 'http://[::1]:5000/ws/2'),
])
def test_url_normalization(monkeypatch, url, expected):
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', url)
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0')
    client = mb.MusicBrainzClient()
    assert client.base_url == expected
    assert client.request_interval == 0


@pytest.mark.parametrize('url', ['mirror:5000', 'ftp://mirror', 'http://',
    'http://mirror:99999', 'http://mirror:bad', 'http://[bad',
    'http://user:secret@mirror', 'http://mirror?x=1', 'http://mirror/#x', 'http://bad host'])
def test_invalid_url_fails_explicitly(monkeypatch, url):
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', url)
    with pytest.raises(ValueError, match='BASE_URL'):
        mb.MusicBrainzClient()


@pytest.mark.parametrize('interval', ['-1', 'nan', 'inf', '-inf', 'invalid'])
def test_invalid_interval(monkeypatch, interval):
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', interval)
    with pytest.raises(ValueError, match='REQUEST_INTERVAL'):
        mb.MusicBrainzClient()


@pytest.mark.parametrize('url', ['https://musicbrainz.org', 'http://MUSICBRAINZ.ORG.:80',
                                 'https://beta.musicbrainz.org'])
def test_public_rate_floor(monkeypatch, url):
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', url)
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0.01')
    assert mb.MusicBrainzClient().request_interval == 1.05


def test_config_and_environment_precedence(monkeypatch):
    from core.settings import config_manager
    config = {'musicbrainz.base_url': 'http://config:5000', 'musicbrainz.request_interval': 0.2}
    monkeypatch.setattr(config_manager, 'get', lambda key, default=None: config.get(key, default))
    client = mb.MusicBrainzClient()
    assert client.base_url == 'http://config:5000/ws/2'
    assert client.request_interval == 0.2
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', 'http://environment:5001')
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0.05')
    client = mb.MusicBrainzClient()
    assert client.base_url == 'http://environment:5001/ws/2'
    assert client.request_interval == 0.05


def test_concurrent_clients_share_pacing(monkeypatch):
    clock = [100.0]
    slots = []
    monkeypatch.setattr(mb.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(mb.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', 'http://mirror')
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0.1')
    class Session:
        def get(self, *args, **kwargs):
            slots.append(clock[0])
            response = requests.Response()
            response.status_code = 200
            return response
    clients = [mb.MusicBrainzClient() for _ in range(10)]
    for client in clients:
        client.session = Session()
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda client: client._get('/artist'), clients))
    # Virtual time advances exactly once per additional request, across clients.
    assert clock[0] == pytest.approx(100.9)
    assert len(slots) == 10


@pytest.fixture
def server():
    calls = []
    outcomes = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, dict(self.headers)))
            status, payload = outcomes.pop(0)
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            if status == 302:
                self.send_header('Location', '/must-not-follow')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
        def log_message(self, *args):
            pass
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}/proxy', calls, outcomes
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_real_http_search_lookup_browse_and_retry(monkeypatch, server):
    url, calls, outcomes = server
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', url)
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0')
    monkeypatch.setattr(mb.time, 'sleep', lambda delay: None)
    outcomes.extend([(503, {}), (200, {'artists': [{'id': 'artist-id'}]}),
                     (200, {'id': 'release-id'}), (200, {'release-groups': [{'id': 'group-id'}]})])
    client = mb.MusicBrainzClient()
    client.session.trust_env = False
    assert client.search_artist('Björk')[0]['id'] == 'artist-id'
    assert client.get_release('release-id')['id'] == 'release-id'
    assert client.browse_artist_release_groups('artist-id')[0]['id'] == 'group-id'
    assert len(calls) == 4
    assert [urlsplit(path).path for path, _ in calls] == [
        '/proxy/ws/2/artist', '/proxy/ws/2/artist',
        '/proxy/ws/2/release/release-id', '/proxy/ws/2/release-group']
    assert parse_qs(urlsplit(calls[0][0]).query)['query'] == ['artist:"Björk"']
    for path, headers in calls:
        assert parse_qs(urlsplit(path).query)['fmt'] == ['json']
        assert 'SoulSync/' in headers['User-Agent']
        assert headers['Accept'] == 'application/json'


def test_redirect_is_not_followed(monkeypatch, server):
    url, calls, outcomes = server
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', url)
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0')
    outcomes.append((302, {}))
    client = mb.MusicBrainzClient()
    client.session.trust_env = False
    with pytest.raises(requests.HTTPError, match='final base URL'):
        client._get('/artist')
    assert len(calls) == 1


def test_worker_processes_items_without_extra_delay(monkeypatch):
    import core.musicbrainz_worker as worker_module
    worker = worker_module.MusicBrainzWorker.__new__(worker_module.MusicBrainzWorker)
    worker.should_stop = False
    worker.paused = False
    worker._stop_event = threading.Event()
    items = iter([{'id': 1}, {'id': 2}, None])
    worker._get_next_item = lambda: next(items)
    processed = []
    worker._process_item = lambda item: processed.append(item['id'])
    sleeps = []
    def sleep(event, seconds):
        sleeps.append(seconds)
        worker.should_stop = True
    monkeypatch.setattr(worker_module, 'interruptible_sleep', sleep)
    worker._run()
    assert processed == [1, 2]
    assert sleeps == [10]  # Idle backoff is retained after both items complete.



def _own_thread_clock(monkeypatch, clock, record=None):
    """Patch mb.time for THIS thread only.

    ``mb.time`` is the stdlib time module, so ``monkeypatch.setattr`` replaces
    ``time.sleep`` for the whole process. Any background thread that sleeps
    during the test then lands in the recorded list and shifts the fake clock.
    That is the flake behind ``assert [2.0, 3, 4.0] == [2.0, 4.0]``: something
    else in the suite slept 3 seconds mid-test. Other threads get the real
    sleep so they keep their own timing instead of spinning.
    """
    caller = threading.current_thread()
    real_sleep = mb.time.sleep

    def sleep(delay):
        if threading.current_thread() is not caller:
            real_sleep(delay)
            return
        if record is not None:
            record.append(delay)
        clock[0] += delay

    monkeypatch.setattr(mb.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(mb.time, 'sleep', sleep)

def test_sleep_recorder_ignores_other_threads(monkeypatch):
    """The retry-budget assertions must survive an unrelated sleeping thread.

    CI failed with ``assert [2.0, 3, 4.0] == [2.0, 4.0]``: mb.time is the
    stdlib time module, so patching sleep catches every thread in the process,
    and something elsewhere in the suite slept 3 seconds mid-test.
    """
    clock = [100.0]
    sleeps = []
    _own_thread_clock(monkeypatch, clock, sleeps)

    mb.time.sleep(2.0)                                    # the client's retry
    noise = threading.Thread(target=lambda: mb.time.sleep(3))
    noise.start()
    noise.join()                                          # somebody else's
    mb.time.sleep(4.0)                                    # the client's retry

    assert sleeps == [2.0, 4.0]
    assert clock[0] == 106.0

@pytest.mark.parametrize('status,expected_calls', [(429, 3), (503, 3), (504, 3), (404, 1)])
def test_http_failure_stays_on_mirror_and_respects_retry_budget(monkeypatch, server, status, expected_calls):
    url, calls, outcomes = server
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', url)
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0.2')
    clock = [100.0]
    sleeps = []
    _own_thread_clock(monkeypatch, clock, sleeps)
    outcomes.extend([(status, {})] * expected_calls)
    client = mb.MusicBrainzClient()
    client.session.trust_env = False
    with pytest.raises(requests.HTTPError):
        client.search_artist('Example', raise_on_error=True)
    assert len(calls) == expected_calls
    assert all(path.startswith('/proxy/ws/2/artist?') for path, _ in calls)
    assert sleeps == ([2.0, 4.0] if expected_calls == 3 else [])


def test_public_requests_are_paced_at_transport_boundary(monkeypatch):
    clock = [100.0]
    starts = []
    # Same reason as the retry-budget test: a stray thread's sleep would move
    # this clock and shift every asserted start time.
    _own_thread_clock(monkeypatch, clock)
    client = mb.MusicBrainzClient()
    def get(*args, **kwargs):
        starts.append(clock[0])
        response = requests.Response()
        response.status_code = 200
        return response
    monkeypatch.setattr(client.session, 'get', get)
    client._get('/artist')
    client._get('/release')
    client._get('/recording')
    assert starts == pytest.approx([100, 101.05, 102.1])


def test_existing_client_switches_server_after_settings_save(monkeypatch, server):
    from core.settings import config_manager
    mirror, calls, outcomes = server
    config = {'musicbrainz.base_url': mirror, 'musicbrainz.request_interval': 0}
    monkeypatch.setattr(config_manager, 'get', lambda key, default=None: config.get(key, default))
    client = mb.MusicBrainzClient()
    client.session.trust_env = False
    outcomes.extend([(200, {'artists': []}), (200, {'artists': []})])
    client.search_artist('Before')
    config['musicbrainz.base_url'] = mirror.replace('/proxy', '/new-prefix')
    client.search_artist('After')
    assert calls[0][0].startswith('/proxy/ws/2/artist?')
    assert calls[1][0].startswith('/new-prefix/ws/2/artist?')


@pytest.mark.parametrize('settings', [
    {'base_url': 'ftp://mirror'}, {'request_interval': -1}, 'invalid',
])
def test_settings_endpoint_rejects_invalid_values_before_saving(settings):
    import ast
    from pathlib import Path
    from flask import Flask, request, jsonify
    from types import SimpleNamespace
    tree = ast.parse(Path('web_server.py').read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'handle_settings')
    function.decorator_list = []
    saves = []
    namespace = {'request': request, 'jsonify': jsonify,
                 'config_manager': SimpleNamespace(get=lambda *args: None, set=lambda *args: saves.append(args))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'web_server.py', 'exec'), namespace)
    app = Flask(__name__)
    with app.test_request_context('/api/settings', method='POST', json={'musicbrainz': settings}):
        response, status = namespace['handle_settings']()
    assert status == 400
    assert response.json['success'] is False
    assert saves == []
