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


def test_sab_state_mapping_covers_full_sab_status_enum() -> None:
    """Every Status value SAB's sabnzbd/constants.py:Status emits must
    map to a known adapter state, NOT to the default 'error' fallback.
    Pre-fix: SAB's ``Deleted`` and ``Propagating`` were unmapped,
    fell through to the 'error' default, and the poll loop treated
    'error' as neither complete nor failed — it just spun until the
    6-hour timeout."""
    canonical = [
        'Idle', 'Queued', 'Grabbing', 'Propagating',
        'Fetching', 'Downloading', 'Paused',
        'Checking', 'QuickCheck', 'Verifying', 'Repairing',
        'Extracting', 'Moving', 'Running',
        'Completed', 'Failed', 'Deleted',
    ]
    for state in canonical:
        assert sab_map(state) != 'error', f'{state!r} fell through to error default'


def test_sab_state_mapping_propagating_routes_to_queued() -> None:
    """Propagating is SAB's pre-download delay state — semantically
    'we're waiting for the NZB to be available', map to queued so
    the poll doesn't treat it as downloading progress."""
    assert sab_map('Propagating') == 'queued'


def test_sab_state_mapping_deleted_routes_to_failed() -> None:
    """User removed the job mid-flight — terminal failure from
    SoulSync's perspective. Without this, the poll would keep
    spinning waiting for a job that's never coming back."""
    assert sab_map('Deleted') == 'failed'


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


def test_sab_history_save_path_falls_back_to_path_field() -> None:
    """Older SAB releases populated ``path`` instead of ``storage``.
    The adapter must fall through the field-name chain so we pick it
    up either way."""
    adapter = _sab_with_config()
    slot = adapter._parse_history_slot({
        'nzo_id': 'z', 'name': 'Z', 'status': 'Completed',
        'bytes': 0, 'path': '/legacy/sab/path',
    })
    assert slot.save_path == '/legacy/sab/path'


def test_sab_history_save_path_falls_back_to_download_path_field() -> None:
    """Some SAB forks expose ``download_path`` instead of the
    documented ``storage``. Same fallback chain catches it."""
    adapter = _sab_with_config()
    slot = adapter._parse_history_slot({
        'nzo_id': 'z2', 'name': 'Z2', 'status': 'Completed',
        'bytes': 0, 'download_path': '/fork/dl',
    })
    assert slot.save_path == '/fork/dl'


def test_sab_history_save_path_prefers_storage_when_multiple_present() -> None:
    """Field priority: ``storage`` wins over the fallbacks. The
    documented final-path key must be preferred so SAB upgrades
    don't subtly change the resolved path."""
    adapter = _sab_with_config()
    slot = adapter._parse_history_slot({
        'nzo_id': 'p', 'name': 'P', 'status': 'Completed',
        'bytes': 0,
        'storage': '/final/storage',
        'path': '/legacy/path',
        'download_path': '/fork/dl',
        'dirname': '/dirname',
    })
    assert slot.save_path == '/final/storage'


def test_sab_history_save_path_none_when_all_fields_empty() -> None:
    """Regression for #721: SAB's ``storage`` field lands a few
    seconds after the job flips to History. During that window
    EVERY known path field can be empty. The adapter must return
    ``save_path=None`` (not a stale string) so
    ``poll_album_download``'s retry loop can engage and wait for
    the next poll where ``storage`` lands."""
    adapter = _sab_with_config()
    slot = adapter._parse_history_slot({
        'nzo_id': 'gap', 'name': 'Forty Licks', 'status': 'Completed',
        'bytes': 0,
        # No storage / path / download_path / dirname.
    })
    assert slot.state == 'completed'
    assert slot.save_path is None


def test_sab_history_save_path_ignores_whitespace_only_values() -> None:
    """A field present but with whitespace-only content shouldn't
    fool the fallback chain — keep walking until a real path lands."""
    adapter = _sab_with_config()
    slot = adapter._parse_history_slot({
        'nzo_id': 'ws', 'name': 'W', 'status': 'Completed',
        'bytes': 0,
        'storage': '   ',
        'path': '\t',
        'download_path': '/actual/path',
    })
    assert slot.save_path == '/actual/path'


def test_sab_history_save_path_ignores_incomplete_path() -> None:
    """``incomplete_path`` is SAB's in-progress staging dir before
    post-process moves files to the final ``storage``. Using it as
    a save_path fallback would bypass ``poll_album_download``'s
    retry window AND point the bundle plugin at the wrong dir —
    the in-progress staging files might be gone by the time we
    walk it, or they might be partially-extracted. Safer to return
    ``None`` so the poll retries until ``storage`` lands. Pinned
    here so a future "let's add another fallback" change doesn't
    silently re-introduce the foot-gun."""
    adapter = _sab_with_config()
    slot = adapter._parse_history_slot({
        'nzo_id': 'inc', 'name': 'Inc', 'status': 'Completed',
        'bytes': 0,
        'incomplete_path': '/sab/incomplete/job',
    })
    assert slot.save_path is None


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


def test_sab_get_status_uses_direct_nzo_ids_lookup_against_queue() -> None:
    """Targeted nzo_ids query against queue first — avoids paging
    the full 50-entry history bulk fetch on every poll."""
    adapter = _sab_with_config()
    queue_resp = _mock_response(200, {'queue': {'slots': [
        {'nzo_id': 'target', 'filename': 'Album.nzb', 'status': 'Downloading',
         'percentage': '50', 'mb': '100', 'mbleft': '50', 'timeleft': '0:01:00'},
    ]}})
    with patch('core.usenet_clients.sabnzbd.http_requests.get',
               return_value=queue_resp) as mock_get:
        status = adapter._get_status_sync('target')
    assert status is not None
    assert status.id == 'target'
    assert status.state == 'downloading'
    # First call must include the nzo_ids filter — that's the whole
    # point of the change.
    assert mock_get.call_args.kwargs['params']['mode'] == 'queue'
    assert mock_get.call_args.kwargs['params']['nzo_ids'] == 'target'


def test_sab_get_status_falls_through_to_history_when_queue_empty() -> None:
    """Job already moved out of queue → check history with the same
    nzo_ids filter. Direct lookup means SoulSync doesn't lose the
    job on a busy SAB where it's rolled past the bulk history limit."""
    adapter = _sab_with_config()
    empty_queue = _mock_response(200, {'queue': {'slots': []}})
    history_resp = _mock_response(200, {'history': {'slots': [
        {'nzo_id': 'target', 'name': 'Album.nzb', 'status': 'Completed',
         'bytes': 1024, 'storage': '/done/Album'},
    ]}})
    with patch('core.usenet_clients.sabnzbd.http_requests.get',
               side_effect=[empty_queue, history_resp]) as mock_get:
        status = adapter._get_status_sync('target')
    assert status is not None
    assert status.id == 'target'
    assert status.state == 'completed'
    assert status.save_path == '/done/Album'
    # Second call must hit the history endpoint, also filtered by id.
    second_params = mock_get.call_args_list[1].kwargs['params']
    assert second_params['mode'] == 'history'
    assert second_params['nzo_ids'] == 'target'


def test_sab_get_status_returns_none_when_neither_endpoint_finds_id() -> None:
    """Mid SAB queue→history transition window: the slot is gone
    from the queue but not yet in history. Direct lookup returns
    None — the poll layer treats this as a transient miss and
    retries, NOT as a terminal failure. Pre-fix this was the most
    likely trigger for the user's stuck-at-downloading bug (#706).

    Bulk fallback also returns nothing (both endpoints reported
    empty); ``_get_status_sync`` returns None rather than raising."""
    adapter = _sab_with_config()
    empty_queue = _mock_response(200, {'queue': {'slots': []}})
    empty_history = _mock_response(200, {'history': {'slots': []}})
    # Three calls: direct queue, direct history, bulk fallback queue
    # + bulk fallback history. Empty for all four.
    with patch('core.usenet_clients.sabnzbd.http_requests.get',
               side_effect=[empty_queue, empty_history, empty_queue, empty_history]):
        status = adapter._get_status_sync('target')
    assert status is None


def test_sab_get_status_empty_job_id_returns_none_without_hitting_api() -> None:
    """Defensive — an empty job_id from upstream shouldn't fire
    HTTP queries we know will be wrong."""
    adapter = _sab_with_config()
    with patch('core.usenet_clients.sabnzbd.http_requests.get') as mock_get:
        assert adapter._get_status_sync('') is None
        mock_get.assert_not_called()


def test_sab_poll_recovers_after_queue_to_history_handoff_gap() -> None:
    """Integration test: simulate the SAB queue→history transition
    window end-to-end through the adapter. Sequence: 3 polls where
    SAB has moved the slot out of queue but hasn't added it to
    history yet (both endpoints return empty), followed by the slot
    appearing in history as Completed with a save_path. Pre-fix,
    the first None read on the SAB side surfaced to the poll layer
    as 'disappeared' → terminal failure even though SAB was healthy
    and just mid-handoff. Post-fix the adapter still returns None
    during the gap, but the poll helper's TransientMissCounter
    absorbs the gap and recovers when the history entry appears."""
    from core.download_plugins.album_bundle import poll_album_download

    adapter = _sab_with_config()
    empty_queue = _mock_response(200, {'queue': {'slots': []}})
    empty_history = _mock_response(200, {'history': {'slots': []}})
    final_history = _mock_response(200, {'history': {'slots': [
        {'nzo_id': 'target', 'name': 'Album.nzb', 'status': 'Completed',
         'bytes': 1024, 'storage': '/done/Album'},
    ]}})

    # Each _get_status_sync call hits two endpoints (queue + history).
    # Three gap polls + one recovery poll = 4 * 2 = 8 HTTP calls.
    # On recovery the queue is still empty but the history finds the job.
    poll_results = [
        empty_queue, empty_history,   # gap poll 1
        empty_queue, empty_history,   # gap poll 2
        empty_queue, empty_history,   # gap poll 3
        empty_queue, final_history,   # recovery
    ]

    class _Clock:
        def __init__(self): self.now = 0.0
        def monotonic(self): return self.now
        def sleep(self, s): self.now += s

    clock = _Clock()
    emits: list = []
    with patch('core.usenet_clients.sabnzbd.http_requests.get',
               side_effect=poll_results):
        result = poll_album_download(
            get_status=lambda: adapter._get_status_sync('target'),
            title='Linkin Park - From Zero',
            emit=lambda state, **kw: emits.append((state, kw)),
            complete_states=frozenset(['completed']),
            failed_states=frozenset(['failed']),
            transient_miss_threshold=5,
            sleep=clock.sleep, monotonic=clock.monotonic,
            poll_interval=2.0, timeout=60.0,
        )

    assert result == '/done/Album'
    # Terminal failure must NOT have been emitted — the gap was transient.
    assert 'failed' not in [e[0] for e in emits]


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
