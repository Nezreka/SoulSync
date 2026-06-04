"""Tests for the three torrent client adapters.

Pins state-mapping behavior (each client has a different native state
vocabulary that must collapse onto the adapter-uniform set) and basic
HTTP / RPC plumbing so a future protocol-spec drift fails CI instead
of silently breaking downloads.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from core.torrent_clients import adapter_for_type, get_active_adapter
from core.torrent_clients.base import TorrentClientAdapter, TorrentStatus
from core.torrent_clients.deluge import DelugeAdapter, _map_state as deluge_map
from core.torrent_clients.qbittorrent import QBittorrentAdapter, _map_state as qbit_map
from core.torrent_clients.transmission import TransmissionAdapter, _map_state as trans_map


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _mock_response(status_code: int, json_body=None, text=None, headers=None):
    resp = MagicMock()
    resp.ok = 200 <= status_code < 400
    resp.status_code = status_code
    resp.headers = headers or {}
    if json_body is not None:
        resp.json.return_value = json_body
    resp.text = text or ''
    return resp


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_adapter_for_type_returns_concrete_classes() -> None:
    assert isinstance(adapter_for_type('qbittorrent'), QBittorrentAdapter)
    assert isinstance(adapter_for_type('transmission'), TransmissionAdapter)
    assert isinstance(adapter_for_type('deluge'), DelugeAdapter)


def test_adapter_for_type_returns_none_for_unknown() -> None:
    assert adapter_for_type('utorrent') is None
    assert adapter_for_type('') is None


def test_adapters_conform_to_protocol() -> None:
    """``isinstance`` checks the runtime_checkable Protocol — catches
    adapters that lose a required method during refactors."""
    for adapter in (QBittorrentAdapter(), TransmissionAdapter(), DelugeAdapter()):
        assert isinstance(adapter, TorrentClientAdapter)


# ---------------------------------------------------------------------------
# State mapping
# ---------------------------------------------------------------------------


def test_qbittorrent_state_mapping() -> None:
    assert qbit_map('downloading') == 'downloading'
    assert qbit_map('forcedDL') == 'downloading'
    assert qbit_map('stalledDL') == 'stalled'
    assert qbit_map('uploading') == 'seeding'
    assert qbit_map('pausedUP') == 'completed'
    assert qbit_map('pausedDL') == 'paused'
    assert qbit_map('error') == 'error'
    assert qbit_map('missingFiles') == 'error'
    # Unknown native value → error rather than swallowing silently.
    assert qbit_map('not-a-real-state') == 'error'


def test_transmission_state_mapping() -> None:
    assert trans_map(4, 0.5) == 'downloading'
    assert trans_map(6, 1.0) == 'seeding'
    # Status 0 is the ambiguous one: paused vs completed-but-not-seeding.
    assert trans_map(0, 0.3) == 'paused'
    assert trans_map(0, 1.0) == 'completed'
    assert trans_map(2, 0.0) == 'queued'   # checking files
    # Unknown numeric code → error.
    assert trans_map(99, 0.0) == 'error'


def test_deluge_state_mapping() -> None:
    assert deluge_map('Downloading', 0.5) == 'downloading'
    assert deluge_map('Seeding', 1.0) == 'seeding'
    assert deluge_map('Paused', 0.4) == 'paused'
    # Deluge reports 'Paused' for completed-not-seeding too.
    assert deluge_map('Paused', 1.0) == 'completed'
    assert deluge_map('Error', 0.0) == 'error'
    assert deluge_map('', 0.0) == 'error'


# ---------------------------------------------------------------------------
# qBittorrent adapter
# ---------------------------------------------------------------------------


def _qbit_with_config(url='http://qbit:8080', username='admin', password='x'):
    adapter = QBittorrentAdapter.__new__(QBittorrentAdapter)
    import threading
    adapter._session = None
    adapter._session_lock = threading.Lock()
    adapter._url = url.rstrip('/')
    adapter._username = username
    adapter._password = password
    adapter._category = 'soulsync'
    adapter._save_path = ''
    return adapter


def test_qbit_is_configured_requires_only_url() -> None:
    # qBittorrent allows no-auth LAN setups — URL is enough.
    assert _qbit_with_config('http://x', '', '').is_configured() is True
    assert _qbit_with_config('', 'u', 'p').is_configured() is False


def test_qbit_login_sends_referer_for_csrf() -> None:
    """qBittorrent rejects login attempts without a Referer matching
    its host — pin the header to catch regressions."""
    adapter = _qbit_with_config()
    fake_session = MagicMock()
    fake_session.post.return_value = _mock_response(200, text='Ok.')
    fake_session.post.return_value.text = 'Ok.'
    with patch('core.torrent_clients.qbittorrent.http_requests.Session',
               return_value=fake_session):
        sess = adapter._ensure_session_sync()
    assert sess is not None
    args, kwargs = fake_session.post.call_args
    assert args[0].endswith('/api/v2/auth/login')
    assert kwargs['headers']['Referer'] == 'http://qbit:8080'
    assert kwargs['data'] == {'username': 'admin', 'password': 'x'}


def test_qbit_login_failure_returns_none() -> None:
    adapter = _qbit_with_config()
    fake_session = MagicMock()
    bad_resp = _mock_response(200, text='Fails.')
    bad_resp.text = 'Fails.'
    fake_session.post.return_value = bad_resp
    with patch('core.torrent_clients.qbittorrent.http_requests.Session',
               return_value=fake_session):
        sess = adapter._ensure_session_sync()
    assert sess is None


def test_qbit_login_accepts_204_no_content() -> None:
    """qBittorrent 5.2.0+ returns HTTP 204 with an empty body on a successful
    login (was HTTP 200 + 'Ok.'). The adapter must treat that as success even
    when no SID cookie is visible to us."""
    adapter = _qbit_with_config()
    fake_session = MagicMock()
    fake_session.cookies.get.return_value = None  # no SID surfaced
    resp = _mock_response(204, text='')
    resp.text = ''
    fake_session.post.return_value = resp
    with patch('core.torrent_clients.qbittorrent.http_requests.Session',
               return_value=fake_session):
        sess = adapter._ensure_session_sync()
    assert sess is not None


def test_qbit_login_accepts_sid_cookie_with_empty_body() -> None:
    """A SID auth cookie is the authoritative success signal regardless of body."""
    adapter = _qbit_with_config()
    fake_session = MagicMock()
    fake_session.cookies.get.return_value = 'SID-abc123'
    resp = _mock_response(200, text='')
    resp.text = ''
    fake_session.post.return_value = resp
    with patch('core.torrent_clients.qbittorrent.http_requests.Session',
               return_value=fake_session):
        sess = adapter._ensure_session_sync()
    assert sess is not None


def test_qbit_login_rejects_fails_even_with_stale_cookie() -> None:
    """Bad creds: qBittorrent returns HTTP 200 'Fails.' (not a 4xx). Must fail
    even if a stale SID cookie lingers on the session."""
    adapter = _qbit_with_config()
    fake_session = MagicMock()
    fake_session.cookies.get.return_value = 'SID-stale'
    resp = _mock_response(200, text='Fails.')
    resp.text = 'Fails.'
    fake_session.post.return_value = resp
    with patch('core.torrent_clients.qbittorrent.http_requests.Session',
               return_value=fake_session):
        sess = adapter._ensure_session_sync()
    assert sess is None


def test_qbit_parse_status_normalises_native_fields() -> None:
    adapter = _qbit_with_config()
    status = adapter._parse_status({
        'hash': 'abc123', 'name': 'Album',
        'state': 'downloading', 'progress': 0.5,
        'size': 1024, 'downloaded': 512,
        'dlspeed': 200, 'upspeed': 50,
        'num_seeds': 4, 'num_leechs': 1,
        'eta': 60, 'save_path': '/data/torrents',
    })
    assert status == TorrentStatus(
        id='abc123', name='Album', state='downloading',
        progress=0.5, size=1024, downloaded=512,
        download_speed=200, upload_speed=50, seeders=4, peers=1,
        eta=60, save_path='/data/torrents',
    )


def test_qbit_parse_status_zeros_eta_when_unknown() -> None:
    adapter = _qbit_with_config()
    # qBittorrent uses 8640000 for "unknown" but the adapter just
    # treats anything <= 0 as unknown; pin that 0 maps to None.
    status = adapter._parse_status({
        'hash': 'x', 'name': 'X', 'state': 'stalledDL',
        'progress': 0.0, 'size': 100, 'downloaded': 0,
        'dlspeed': 0, 'upspeed': 0, 'eta': 0,
    })
    assert status.eta is None


# ---------------------------------------------------------------------------
# Transmission adapter
# ---------------------------------------------------------------------------


def _trans_with_config(url='http://trans:9091/transmission/rpc'):
    adapter = TransmissionAdapter.__new__(TransmissionAdapter)
    import threading
    adapter._session_id = None
    adapter._session_id_lock = threading.Lock()
    adapter._url = url
    adapter._username = ''
    adapter._password = ''
    adapter._category = 'soulsync'
    adapter._save_path = ''
    return adapter


def test_transmission_normalises_bare_host_to_rpc_path() -> None:
    """Users sometimes paste ``http://host:9091``; the adapter must
    append ``/transmission/rpc`` so the request hits the right
    endpoint."""
    adapter = TransmissionAdapter.__new__(TransmissionAdapter)
    with patch('core.torrent_clients.transmission.config_manager') as cm:
        cm.get.side_effect = lambda key, default='': {
            'torrent_client.url': 'http://host:9091',
            'torrent_client.username': '',
            'torrent_client.password': '',
            'torrent_client.category': 'soulsync',
            'torrent_client.save_path': '',
        }.get(key, default)
        import threading
        adapter._session_id = None
        adapter._session_id_lock = threading.Lock()
        adapter._load_config()
    assert adapter._url == 'http://host:9091/transmission/rpc'


# ---------------------------------------------------------------------------
# URL scheme normalization (#790)
# ---------------------------------------------------------------------------


def test_normalize_client_url_prepends_http_when_scheme_missing() -> None:
    from core.torrent_clients.base import normalize_client_url
    # The exact shapes users type: bare IP:port, bare DNS name:port, bare host.
    assert normalize_client_url('192.168.1.5:8080') == 'http://192.168.1.5:8080'
    assert normalize_client_url('qbittorrent.lan:8080') == 'http://qbittorrent.lan:8080'
    assert normalize_client_url('myhost') == 'http://myhost'


def test_normalize_client_url_preserves_existing_scheme_and_trims() -> None:
    from core.torrent_clients.base import normalize_client_url
    assert normalize_client_url('http://host:8080') == 'http://host:8080'
    assert normalize_client_url('https://host') == 'https://host'
    assert normalize_client_url('  http://host:8080/  ') == 'http://host:8080'
    assert normalize_client_url('') == ''
    assert normalize_client_url(None) == ''


def test_qbit_load_config_defaults_scheme_for_bare_host() -> None:
    """Regression #790: a bare ``host:port`` config (no scheme) must become an
    http:// URL. Otherwise requests can't pick an adapter and raises
    'No connection adapters were found for ...', which surfaced to the user as
    a generic 'qbittorrent probe failed'."""
    adapter = QBittorrentAdapter.__new__(QBittorrentAdapter)
    import threading
    adapter._session = None
    adapter._session_lock = threading.Lock()
    with patch('core.torrent_clients.qbittorrent.config_manager') as cm:
        cm.get.side_effect = lambda key, default='': {
            'torrent_client.url': '192.168.1.5:8080',
        }.get(key, default)
        adapter._load_config()
    assert adapter._url == 'http://192.168.1.5:8080'


def test_deluge_load_config_defaults_scheme_for_bare_host() -> None:
    adapter = DelugeAdapter.__new__(DelugeAdapter)
    import threading
    adapter._session = None
    adapter._session_lock = threading.Lock()
    with patch('core.torrent_clients.deluge.config_manager') as cm:
        cm.get.side_effect = lambda key, default='': {
            'torrent_client.url': 'deluge.lan:8112',
        }.get(key, default)
        adapter._load_config()
    assert adapter._url == 'http://deluge.lan:8112'


def test_transmission_session_id_renegotiation() -> None:
    """Transmission rejects the first call with 409 and a fresh
    ``X-Transmission-Session-Id`` header; the adapter must store it
    and retry the same call exactly once."""
    adapter = _trans_with_config()
    first = _mock_response(409, headers={'X-Transmission-Session-Id': 'sid-2'})
    second = _mock_response(200, json_body={'result': 'success', 'arguments': {'session-id': 1}})
    with patch('core.torrent_clients.transmission.http_requests.post',
               side_effect=[first, second]) as mock_post:
        result = adapter._rpc('session-get', {})
    assert result == {'session-id': 1}
    assert mock_post.call_count == 2
    # Second call carried the new session id.
    second_call_kwargs = mock_post.call_args_list[1].kwargs
    assert second_call_kwargs['headers']['X-Transmission-Session-Id'] == 'sid-2'


def test_transmission_rpc_returns_none_on_failure_result() -> None:
    adapter = _trans_with_config()
    with patch('core.torrent_clients.transmission.http_requests.post',
               return_value=_mock_response(200, json_body={'result': 'unknown method'})):
        assert adapter._rpc('bogus', {}) is None


def test_transmission_add_torrent_handles_duplicate() -> None:
    """torrent-add returns either ``torrent-added`` (new) or
    ``torrent-duplicate`` (already-there) — both must surface the hash."""
    adapter = _trans_with_config()
    with patch.object(adapter, '_rpc', return_value={'torrent-duplicate': {'hashString': 'dup'}}):
        hash_id = adapter._add_torrent_sync('magnet:?xt=urn:btih:abc', 'cat', None)
    assert hash_id == 'dup'


def test_transmission_parse_status() -> None:
    adapter = _trans_with_config()
    status = adapter._parse_status({
        'hashString': 'h', 'name': 'X', 'status': 4, 'percentDone': 0.42,
        'totalSize': 100, 'downloadedEver': 42,
        'rateDownload': 10, 'rateUpload': 5,
        'peersSendingToUs': 2, 'peersGettingFromUs': 0,
        'eta': 300, 'downloadDir': '/dl', 'errorString': '',
    })
    assert status.id == 'h'
    assert status.state == 'downloading'
    assert status.progress == 0.42
    assert status.eta == 300


def test_transmission_parse_status_negative_eta_is_none() -> None:
    """Transmission reports -1 / -2 for 'unknown' ETA — must normalise to None."""
    adapter = _trans_with_config()
    status = adapter._parse_status({
        'hashString': 'h', 'name': 'X', 'status': 4, 'percentDone': 0.0,
        'totalSize': 100, 'downloadedEver': 0,
        'rateDownload': 0, 'rateUpload': 0,
        'peersSendingToUs': 0, 'peersGettingFromUs': 0,
        'eta': -1, 'downloadDir': '/dl',
    })
    assert status.eta is None


# ---------------------------------------------------------------------------
# Deluge adapter
# ---------------------------------------------------------------------------


def _deluge_with_config(url='http://deluge:8112', password='delugepass'):
    adapter = DelugeAdapter.__new__(DelugeAdapter)
    import threading
    from itertools import count
    adapter._session = None
    adapter._session_lock = threading.Lock()
    adapter._id_counter = count(1)
    adapter._url = url.rstrip('/')
    adapter._password = password
    adapter._category = 'soulsync'
    adapter._save_path = ''
    return adapter


def test_deluge_is_configured_requires_password() -> None:
    assert _deluge_with_config('http://x', '').is_configured() is False
    assert _deluge_with_config('http://x', 'pw').is_configured() is True


def test_deluge_add_torrent_uses_magnet_method() -> None:
    adapter = _deluge_with_config()
    with patch.object(adapter, '_ensure_session_sync', return_value=MagicMock()), \
         patch.object(adapter, '_rpc_sync', return_value='hash123') as mock_rpc:
        hash_id = adapter._add_torrent_sync('magnet:?xt=urn:btih:abc', 'cat', None)
    assert hash_id == 'hash123'
    # First call was core.add_torrent_magnet, not the URL variant.
    first_method = mock_rpc.call_args_list[0].args[0]
    assert first_method == 'core.add_torrent_magnet'


def test_deluge_add_torrent_uses_url_method_for_http() -> None:
    adapter = _deluge_with_config()
    with patch.object(adapter, '_ensure_session_sync', return_value=MagicMock()), \
         patch.object(adapter, '_rpc_sync', return_value='hash456') as mock_rpc:
        hash_id = adapter._add_torrent_sync('https://example.com/x.torrent', 'cat', None)
    assert hash_id == 'hash456'
    first_method = mock_rpc.call_args_list[0].args[0]
    assert first_method == 'core.add_torrent_url'


def test_deluge_parse_status_normalises_percent_progress() -> None:
    """Deluge reports progress as 0-100 (not 0-1) — adapter must
    normalise."""
    adapter = _deluge_with_config()
    status = adapter._parse_status({
        'hash': 'abc', 'name': 'X', 'state': 'Downloading',
        'progress': 42.0,
        'total_size': 1000, 'total_done': 420,
        'download_payload_rate': 100, 'upload_payload_rate': 0,
        'num_seeds': 1, 'num_peers': 0, 'eta': 0,
    })
    assert status.progress == pytest.approx(0.42)
    assert status.state == 'downloading'
