"""Tests for the SABnzbd and NZBGet usenet adapters.

Pins state-mapping behavior and the queue-vs-history merge logic so
get_all returns both active and completed jobs without losing
either bucket.
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import MagicMock, patch

import pytest

from core.usenet_clients import adapter_for_type
from core.usenet_clients.base import UsenetClientAdapter, UsenetStatus
from core.usenet_clients.nzbget import NZBGetAdapter, _map_state as nzbget_map
from core.usenet_clients.sabnzbd import SABnzbdAdapter, _map_state as sab_map


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _mock_response(status_code: int, json_body=None):
    resp = MagicMock()
    resp.ok = 200 <= status_code < 400
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


# ---------------------------------------------------------------------------
# Factory + protocol conformance
# ---------------------------------------------------------------------------


def test_adapter_for_type_returns_concrete_classes() -> None:
    assert isinstance(adapter_for_type('sabnzbd'), SABnzbdAdapter)
    assert isinstance(adapter_for_type('nzbget'), NZBGetAdapter)


def test_adapter_for_type_returns_none_for_unknown() -> None:
    assert adapter_for_type('unknown') is None


def test_adapters_conform_to_protocol() -> None:
    for adapter in (SABnzbdAdapter(), NZBGetAdapter()):
        assert isinstance(adapter, UsenetClientAdapter)


# ---------------------------------------------------------------------------
# SABnzbd
# ---------------------------------------------------------------------------


def _sab_with_config(url='http://sab:8080', api_key='k'):
    adapter = SABnzbdAdapter.__new__(SABnzbdAdapter)
    adapter._url = url.rstrip('/')
    adapter._api_key = api_key
    adapter._category = 'soulsync'
    return adapter


def test_sab_is_configured_requires_url_and_key() -> None:
    assert _sab_with_config('http://x', '').is_configured() is False
    assert _sab_with_config('', 'k').is_configured() is False
    assert _sab_with_config('http://x', 'k').is_configured() is True


def test_sab_state_mapping_covers_queue_states() -> None:
    assert sab_map('Downloading') == 'downloading'
    assert sab_map('Verifying') == 'verifying'
    assert sab_map('Repairing') == 'repairing'
    assert sab_map('Extracting') == 'extracting'
    assert sab_map('Paused') == 'paused'
    assert sab_map('Failed') == 'failed'
    # Case-insensitive — SAB sometimes returns lowercase.
    assert sab_map('downloading') == 'downloading'
    assert sab_map('') == 'error'


def test_sab_parse_timeleft_handles_hhmmss() -> None:
    # SABnzbd's timeleft is always HH:MM:SS (or H:MM:SS).
    assert SABnzbdAdapter._parse_timeleft('01:30:00') == 5400
    assert SABnzbdAdapter._parse_timeleft('00:05:30') == 330
    assert SABnzbdAdapter._parse_timeleft('00:30:00') == 1800
    # 2-part fallback covers MM:SS for robustness.
    assert SABnzbdAdapter._parse_timeleft('05:30') == 330
    assert SABnzbdAdapter._parse_timeleft('garbage') is None
    assert SABnzbdAdapter._parse_timeleft('') is None
    assert SABnzbdAdapter._parse_timeleft(None) is None


def test_sab_parse_queue_slot_converts_mb_to_bytes() -> None:
    adapter = _sab_with_config()
    status = adapter._parse_queue_slot({
        'nzo_id': 'SABnzbd_nzo_1',
        'filename': 'Album.nzb',
        'status': 'Downloading',
        'percentage': '42',
        'mb': '100',
        'mbleft': '58',
        'timeleft': '0:01:00',
        'cat': 'soulsync',
    })
    assert status.id == 'SABnzbd_nzo_1'
    assert status.state == 'downloading'
    assert status.progress == pytest.approx(0.42)
    assert status.size == 100 * 1024 * 1024
    assert status.downloaded == 42 * 1024 * 1024
    assert status.eta == 60


def test_sab_parse_history_slot_marks_failures() -> None:
    adapter = _sab_with_config()
    failed = adapter._parse_history_slot({
        'nzo_id': 'x', 'name': 'X', 'status': 'Failed',
        'bytes': 1024, 'fail_message': 'Damaged',
    })
    assert failed.state == 'failed'
    assert failed.progress == 0.0
    assert failed.error == 'Damaged'

    success = adapter._parse_history_slot({
        'nzo_id': 'y', 'name': 'Y', 'status': 'Completed',
        'bytes': 1024, 'storage': '/done',
    })
    assert success.state == 'completed'
    assert success.progress == 1.0
    assert success.save_path == '/done'


def test_sab_add_nzb_via_url_returns_first_nzo_id() -> None:
    adapter = _sab_with_config()
    with patch('core.usenet_clients.sabnzbd.http_requests.get',
               return_value=_mock_response(200, {'status': True, 'nzo_ids': ['SABnzbd_1']})) as mock_get:
        job_id = _run(adapter.add_nzb('https://example.com/x.nzb', category='cat'))
    assert job_id == 'SABnzbd_1'
    params = mock_get.call_args.kwargs['params']
    assert params['mode'] == 'addurl'
    assert params['apikey'] == 'k'
    assert params['name'] == 'https://example.com/x.nzb'
    assert params['cat'] == 'cat'


def test_sab_add_nzb_via_bytes_uses_addfile_multipart() -> None:
    adapter = _sab_with_config()
    with patch('core.usenet_clients.sabnzbd.http_requests.post',
               return_value=_mock_response(200, {'status': True, 'nzo_ids': ['SABnzbd_2']})) as mock_post:
        job_id = _run(adapter.add_nzb(b'<nzb/>', category='cat'))
    assert job_id == 'SABnzbd_2'
    assert mock_post.call_args.kwargs['params']['mode'] == 'addfile'
    files = mock_post.call_args.kwargs['files']
    assert 'name' in files
    assert files['name'][1] == b'<nzb/>'


def test_sab_get_all_merges_queue_and_history() -> None:
    """SAB's queue and history are separate endpoints; the adapter
    must hit both so completed jobs surface in the global list."""
    adapter = _sab_with_config()
    queue_resp = _mock_response(200, {'queue': {'slots': [
        {'nzo_id': 'q1', 'filename': 'A.nzb', 'status': 'Downloading',
         'percentage': '10', 'mb': '100', 'mbleft': '90', 'timeleft': '0:01:00'},
    ]}})
    history_resp = _mock_response(200, {'history': {'slots': [
        {'nzo_id': 'h1', 'name': 'B.nzb', 'status': 'Completed', 'bytes': 1024},
    ]}})
    with patch('core.usenet_clients.sabnzbd.http_requests.get',
               side_effect=[queue_resp, history_resp]):
        statuses = adapter._get_all_sync()
    assert [s.id for s in statuses] == ['q1', 'h1']
    assert [s.state for s in statuses] == ['downloading', 'completed']


# ---------------------------------------------------------------------------
# NZBGet
# ---------------------------------------------------------------------------


def _nzbget_with_config(url='http://nzbget:6789', username='u', password='p'):
    adapter = NZBGetAdapter.__new__(NZBGetAdapter)
    from itertools import count
    adapter._id_counter = count(1)
    adapter._url = url.rstrip('/')
    adapter._username = username
    adapter._password = password
    adapter._category = 'soulsync'
    return adapter


def test_nzbget_is_configured_requires_all_three() -> None:
    assert _nzbget_with_config('', 'u', 'p').is_configured() is False
    assert _nzbget_with_config('http://x', '', 'p').is_configured() is False
    assert _nzbget_with_config('http://x', 'u', '').is_configured() is False
    assert _nzbget_with_config('http://x', 'u', 'p').is_configured() is True


def test_nzbget_state_mapping_covers_post_process_phases() -> None:
    assert nzbget_map('DOWNLOADING') == 'downloading'
    assert nzbget_map('PAUSED') == 'paused'
    assert nzbget_map('LOADING_PARS') == 'verifying'
    assert nzbget_map('REPAIRING') == 'repairing'
    assert nzbget_map('UNPACKING') == 'extracting'
    assert nzbget_map('PP_FINISHED') == 'completed'
    assert nzbget_map('') == 'error'


def test_nzbget_mb_value_prefers_64bit_split() -> None:
    """NZBGet ships size as FileSizeHi << 32 | FileSizeLo for clients
    that need precision past 2 GB. Prefer that over the legacy MB
    field when both are present."""
    val = NZBGetAdapter._mb_value({'FileSizeLo': 1024 * 1024, 'FileSizeHi': 0, 'FileSizeMB': 999}, 'FileSize')
    assert val == 1.0


def test_nzbget_mb_value_falls_back_to_mb() -> None:
    val = NZBGetAdapter._mb_value({'FileSizeMB': 500}, 'FileSize')
    assert val == 500.0


def test_nzbget_add_nzb_url_passes_through_unchanged() -> None:
    adapter = _nzbget_with_config()
    captured = {}

    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        captured['payload'] = json
        return _mock_response(200, {'result': 42})

    with patch('core.usenet_clients.nzbget.http_requests.post', side_effect=fake_post):
        job_id = _run(adapter.add_nzb('https://x/x.nzb', category='cat'))

    assert job_id == '42'
    params = captured['payload']['params']
    assert params[0] == ''  # NZBFilename empty when content is a URL
    assert params[1] == 'https://x/x.nzb'
    assert params[2] == 'cat'


def test_nzbget_add_nzb_bytes_base64_encodes() -> None:
    adapter = _nzbget_with_config()
    captured = {}

    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        captured['payload'] = json
        return _mock_response(200, {'result': 7})

    with patch('core.usenet_clients.nzbget.http_requests.post', side_effect=fake_post):
        _run(adapter.add_nzb(b'<nzb/>', category='cat'))

    params = captured['payload']['params']
    assert params[0] == 'soulsync.nzb'
    assert params[1] == base64.b64encode(b'<nzb/>').decode('ascii')


def test_nzbget_remove_uses_groupfinal_when_deleting_files() -> None:
    """``GroupFinalDelete`` deletes downloaded data on disk;
    ``GroupDelete`` just removes the queue entry. The adapter must
    pick the right one based on the ``delete_files`` flag."""
    adapter = _nzbget_with_config()
    with patch.object(adapter, '_rpc_sync', return_value=True) as mock_rpc:
        adapter._remove_sync('42', delete_files=True)
        adapter._remove_sync('42', delete_files=False)
    cmds = [c.args[1][0] for c in mock_rpc.call_args_list]
    assert cmds == ['GroupFinalDelete', 'GroupDelete']


def test_nzbget_parse_group_computes_progress() -> None:
    adapter = _nzbget_with_config()
    status = adapter._parse_group({
        'NZBID': 99,
        'NZBName': 'Album.nzb',
        'Status': 'DOWNLOADING',
        'FileSizeLo': 200 * 1024 * 1024, 'FileSizeHi': 0,
        'RemainingSizeLo': 100 * 1024 * 1024, 'RemainingSizeHi': 0,
        'DownloadRate': 500_000,
        'DestDir': '/incomplete',
        'Category': 'soulsync',
    })
    assert status.id == '99'
    assert status.state == 'downloading'
    assert status.size == 200 * 1024 * 1024
    assert status.downloaded == 100 * 1024 * 1024
    assert status.progress == pytest.approx(0.5)
    assert status.download_speed == 500_000


def test_nzbget_remove_rejects_non_numeric_id() -> None:
    """NZBGet IDs are ints; passing a string id like 'abc' must
    fail fast instead of corrupting the editqueue call."""
    adapter = _nzbget_with_config()
    with patch.object(adapter, '_rpc_sync') as mock_rpc:
        assert adapter._remove_sync('not-a-number', delete_files=False) is False
    mock_rpc.assert_not_called()
