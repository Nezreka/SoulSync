"""Phase A pinning tests for HiFiClient — UPDATED for Phase C5."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.download_engine import DownloadEngine
from core.hifi_client import HiFiClient


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def hifi_client_with_engine():
    client = HiFiClient.__new__(HiFiClient)
    client.download_path = Path('./test_hifi_downloads')
    client.shutdown_check = None
    client._engine = None
    engine = DownloadEngine()
    client.set_engine(engine)
    return client, engine


def test_download_returns_none_for_invalid_filename_format(hifi_client_with_engine):
    client, _ = hifi_client_with_engine
    result = _run_async(client.download('hifi', 'no-separator', 0))
    assert result is None


def test_download_returns_none_for_non_integer_track_id(hifi_client_with_engine):
    client, _ = hifi_client_with_engine
    result = _run_async(client.download('hifi', 'not-int||title', 0))
    assert result is None


def test_download_raises_when_engine_not_wired():
    """Defensive: client without engine reference must raise so the
    orchestrator's download_with_fallback surfaces the error and
    moves on to the next source. Returning None silently would drop
    the download with no user feedback (per JohnBaumb)."""
    import pytest
    client = HiFiClient.__new__(HiFiClient)
    client._engine = None
    with pytest.raises(RuntimeError, match="engine reference"):
        _run_async(client.download('hifi', 'v||t', 0))


def test_download_returns_uuid_for_valid_filename(hifi_client_with_engine):
    client, _ = hifi_client_with_engine
    with patch.object(client, '_download_sync', return_value='/tmp/x.flac'):
        result = _run_async(client.download('hifi', '12345||Some Song', 0))
    assert result is not None
    assert len(result) == 36


def test_download_populates_engine_record_with_initial_state(hifi_client_with_engine):
    client, engine = hifi_client_with_engine
    started = threading.Event()
    release = threading.Event()

    def slow_impl(*args, **kwargs):
        started.set()
        release.wait(timeout=1.0)
        return '/tmp/done.flac'

    with patch.object(client, '_download_sync', side_effect=slow_impl):
        download_id = _run_async(client.download('hifi', '999||My HiFi Song', 0))
        started.wait(timeout=1.0)
        record = engine.get_record('hifi', download_id)
        assert record['filename'] == '999||My HiFi Song'
        assert record['username'] == 'hifi'
        assert record['track_id'] == 999
        assert record['display_name'] == 'My HiFi Song'
        release.set()


def test_get_all_downloads_reads_engine_records(hifi_client_with_engine):
    client, engine = hifi_client_with_engine
    engine.add_record('hifi', 'dl-1', {
        'id': 'dl-1', 'filename': '111||A', 'username': 'hifi',
        'state': 'InProgress, Downloading', 'progress': 50.0,
    })
    result = _run_async(client.get_all_downloads())
    assert len(result) == 1
    assert result[0].id == 'dl-1'


def test_cancel_download_marks_cancelled(hifi_client_with_engine):
    client, engine = hifi_client_with_engine
    engine.add_record('hifi', 'dl-1', {'id': 'dl-1', 'state': 'InProgress, Downloading'})
    ok = _run_async(client.cancel_download('dl-1', None, remove=False))
    assert ok is True
    assert engine.get_record('hifi', 'dl-1')['state'] == 'Cancelled'


def test_instance_capability_probe_uses_track_manifests_not_legacy_track():
    class _Response:
        def __init__(self, *, ok=True, status_code=200, payload=None):
            self.ok = ok
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class _Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith('/search/'):
                return _Response(payload={'items': []})
            if url.endswith('/trackManifests/'):
                return _Response(payload={
                    'data': {
                        'data': {
                            'attributes': {
                                'uri': 'https://cdn.example/playlist.m3u8',
                            },
                        },
                    },
                })
            if url.endswith('/'):
                return _Response(payload={'version': 'test'})
            return _Response(ok=False, status_code=404)

    client = HiFiClient.__new__(HiFiClient)
    client.session = _Session()

    result = client.check_instance_capabilities('https://hifi.example')

    called_urls = [url for url, _ in client.session.calls]
    assert result['can_search'] is True
    assert result['can_download'] is True
    assert any(url.endswith('/trackManifests/') for url in called_urls)
    assert not any(url.endswith('/track') for url in called_urls)


def test_instance_capability_probe_reports_manifest_without_uri_as_limited():
    class _Response:
        ok = True
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class _Session:
        def get(self, url, **kwargs):
            if url.endswith('/search/'):
                return _Response({'items': []})
            if url.endswith('/trackManifests/'):
                return _Response({'data': {'data': {'attributes': {}}}})
            if url.endswith('/'):
                return _Response({'version': 'test'})
            return _Response({'data': {'data': {'attributes': {}}}})

    client = HiFiClient.__new__(HiFiClient)
    client.session = _Session()

    result = client.check_instance_capabilities('https://hifi.example')

    assert result['can_search'] is True
    assert result['can_download'] is False
    assert result['download_error'] == 'No playable manifest URL'


def test_instance_capability_probe_accepts_legacy_track_manifest():
    import base64
    import json

    class _Response:
        def __init__(self, payload, *, ok=True, status_code=200):
            self._payload = payload
            self.ok = ok
            self.status_code = status_code

        def json(self):
            return self._payload

    class _Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if url.endswith('/search/'):
                return _Response({'items': []})
            if url.endswith('/trackManifests/'):
                return _Response({'data': {'data': {'attributes': {}}}})
            if url.endswith('/track/'):
                manifest = base64.b64encode(json.dumps({
                    'mimeType': 'audio/flac',
                    'codecs': 'flac',
                    'encryptionType': 'NONE',
                    'urls': ['https://cdn.example/track.flac'],
                }).encode()).decode()
                return _Response({'data': {'manifest': manifest}})
            if url.endswith('/'):
                return _Response({'version': 'test'})
            return _Response({}, ok=False, status_code=404)

    client = HiFiClient.__new__(HiFiClient)
    client.session = _Session()

    result = client.check_instance_capabilities('https://hifi.example')

    assert result['can_search'] is True
    assert result['can_download'] is True
    assert result['download_probe'] == 'track'
    assert any(url.endswith('/track/') for url, _ in client.session.calls)


def test_get_hls_manifest_falls_back_to_legacy_track_endpoint():
    import base64
    import json

    client = HiFiClient.__new__(HiFiClient)
    calls = []

    def _fake_api_get(path, params=None, timeout=15):
        calls.append((path, params))
        if path == '/trackManifests/':
            return None
        manifest = base64.b64encode(json.dumps({
            'mimeType': 'audio/flac',
            'codecs': 'flac',
            'encryptionType': 'NONE',
            'urls': ['https://cdn.example/track.flac'],
        }).encode()).decode()
        return {'data': {'manifest': manifest}}

    client._api_get = _fake_api_get

    result = client._get_hls_manifest(123, quality='lossless')

    assert result['direct_urls'] == ['https://cdn.example/track.flac']
    assert result['extension'] == 'flac'
    assert calls[0][0] == '/trackManifests/'
    assert calls[1][0] == '/track/'
