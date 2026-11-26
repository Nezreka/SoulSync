"""Exercise the actual SoulSync HTTP client against slskd's enqueue contract."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from core.soulseek_client import SoulseekClient
from core.video import slskd_download


FILENAME = r'collection\artist\track.flac'
TRANSFER = {'id': 'transfer-id', 'filename': FILENAME, 'username': 'peer'}


@pytest.fixture
def enqueue(monkeypatch):
    monkeypatch.setattr(SoulseekClient, '_setup_client', lambda self: None)

    def run(status, body, *, video=False, username='peer'):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((self.path, self.headers['X-API-Key'], payload))
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

            def log_message(self, *_args):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
            thread.start()
            try:
                base_url = f'http://127.0.0.1:{server.server_port}/slskd'
                if video:
                    monkeypatch.setattr(slskd_download, '_conn', lambda: (base_url, {'X-API-Key': 'test-key'}))
                    result = slskd_download.start_download(username, FILENAME, 123)
                else:
                    client = SoulseekClient()
                    client.base_url = base_url
                    client.api_key = 'test-key'
                    result = asyncio.run(client.download(username, FILENAME, 123))
            finally:
                server.shutdown()
                thread.join()
        return result, calls

    return run


def test_returns_matching_transfer_id_from_slskd_envelope(enqueue):
    result, calls = enqueue(201, {'enqueued': [{**TRANSFER, 'id': 'other', 'filename': 'other.flac'}, TRANSFER], 'failed': []})

    assert result == 'transfer-id'
    assert calls == [('/slskd/api/v0/transfers/downloads/peer', 'test-key', [{'filename': FILENAME, 'size': 123}])]


@pytest.mark.parametrize(('status', 'body'), [
    (201, {'enqueued': [], 'failed': [{'item1': FILENAME, 'item2': 'Already in progress'}]}),
    (201, {'enqueued': [{**TRANSFER, 'filename': 'other.flac'}], 'failed': []}),
    (201, {'enqueued': [{**TRANSFER, 'username': 'another-peer'}], 'failed': []}),
    (201, {'enqueued': [{**TRANSFER, 'id': ' '}], 'failed': []}),
    (201, {'enqueued': [TRANSFER, {**TRANSFER, 'id': 'another-id'}], 'failed': []}),
    (201, b'invalid json'),
    (500, {'error': 'Uncertain whether enqueue was applied'}),
])
def test_unconfirmed_enqueue_never_reports_success_or_posts_again(enqueue, status, body):
    result, calls = enqueue(status, body)

    assert result is None
    assert [path for path, _, _ in calls] == ['/slskd/api/v0/transfers/downloads/peer']


def test_an_empty_201_from_older_slskd_is_a_download(enqueue):
    # slskd 0.23 and older answer an accepted enqueue with a bare 201; the
    # filename stands in for the id, as it always did there
    result, calls = enqueue(201, b'')

    assert result == FILENAME
    assert len(calls) == 1


@pytest.mark.parametrize('body', [{'id': 'legacy-id'}, [{'id': 'legacy-id'}]])
def test_accepts_legacy_responses_with_explicit_ids(enqueue, body):
    result, calls = enqueue(201, body)

    assert result == 'legacy-id'
    assert len(calls) == 1


@pytest.mark.parametrize(('status', 'body', 'ok'), [
    (201, {'enqueued': [TRANSFER], 'failed': []}, True),
    (201, {'enqueued': [], 'failed': [{'item1': FILENAME, 'item2': 'Rejected'}]}, False),
    (201, {'enqueued': [{**TRANSFER, 'filename': 'other.flac'}], 'failed': []}, False),
    (201, b'', True),   # older slskd: a bare 201 is accepted
    (201, b'invalid json', False),
    (500, {'error': 'Internal error'}, False),
])
def test_video_only_reports_success_for_acknowledged_transfer(enqueue, status, body, ok):
    result, calls = enqueue(status, body, video=True)

    assert result['ok'] is ok
    assert set(result) == ({'ok'} if ok else {'ok', 'error'})
    assert calls == [('/slskd/api/v0/transfers/downloads/peer', 'test-key', [{'filename': FILENAME, 'size': 123}])]


@pytest.mark.parametrize('video', [False, True])
def test_conflicting_success_and_failure_is_not_acknowledged(enqueue, video):
    result, calls = enqueue(201, {
        'enqueued': [TRANSFER],
        'failed': [{'item1': FILENAME, 'item2': 'Rejected'}],
    }, video=video)

    if video:
        assert result['ok'] is False
    else:
        assert result is None
    assert len(calls) == 1


@pytest.mark.parametrize('video', [False, True])
def test_username_is_encoded_as_one_slskd_route_segment(enqueue, video):
    result, calls = enqueue(201, {'enqueued': [{**TRANSFER, 'username': 'a/b'}], 'failed': []}, video=video, username='a/b')

    assert result == ({'ok': True} if video else 'transfer-id')
    assert calls == [('/slskd/api/v0/transfers/downloads/a%2Fb', 'test-key', [{'filename': FILENAME, 'size': 123}])]
