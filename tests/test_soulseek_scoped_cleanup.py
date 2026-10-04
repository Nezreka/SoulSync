"""slskd cleanup must not clobber other clients' state (#1499).

One slskd is often shared: Lidarr's slskd plugin, a second SoulSync, manual
downloads in slskd's own UI. clear_all_completed_downloads() used the bulk
/transfers/downloads/all/completed endpoint, which removes EVERY client's
transfers in any terminal state (Succeeded, Cancelled, Errored, Rejected,
TimedOut, Aborted). cancel_all_downloads() cancel-removed every client's
transfers including running ones, and the search purges operated on slskd's
single, unordered, shared search list.

With soulseek.cleanup_scope = 'own' (the default) every one of those paths
touches only the ids this client created; 'all' restores the old behavior
for single-client installs. On any config error the scope falls back to
'own': the destructive wide mode must never be reached by accident.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from core.soulseek_client import SoulseekClient


def _client():
    c = SoulseekClient.__new__(SoulseekClient)
    c.base_url = 'http://slskd.test'
    c._own_downloads = {}
    c._own_search_ids = set()
    return c


def _rig(client, transfers=None, searches=None):
    """Replace _make_request with a recorder; every DELETE succeeds."""
    calls = []

    async def fake(method, endpoint, **kwargs):
        calls.append((method, endpoint))
        if method == 'GET' and endpoint == 'transfers/downloads':
            return transfers if transfers is not None else []
        if method == 'GET' and endpoint == 'searches':
            return searches if searches is not None else []
        return {}

    client._make_request = fake
    return calls


def _transfers():
    return [
        {'username': 'peerA', 'directories': [{'files': [
            {'id': 'own-1', 'filename': 'a\\own1.flac', 'state': 'Completed, Succeeded'},
            {'id': 'own-2', 'filename': 'a\\own2.flac', 'state': 'Completed, Errored'},
            {'id': 'own-3', 'filename': 'a\\own3.flac', 'state': 'InProgress'},
            # same filename as own-1, different id: another client grabbed
            # the same file from the same peer (the #1501 review catch)
            {'id': 'other-1', 'filename': 'a\\own1.flac', 'state': 'Completed, Succeeded'},
        ]}]},
        {'username': 'peerB', 'directories': [{'files': [
            {'id': 'other-2', 'filename': 'b\\lidarr.flac', 'state': 'Completed, Succeeded'},
            {'id': 'other-3', 'filename': 'b\\running.flac', 'state': 'InProgress'},
        ]}]},
    ]


def _own_peer_a(client):
    client._own_downloads = {'peerA': {'own-1', 'own-2', 'own-3'}}


def _deletes(calls):
    return [e for m, e in calls if m == 'DELETE']


def _scope(value='own'):
    return patch('core.soulseek_client.config_manager.get', return_value=value)


def test_scoped_clear_removes_only_own_terminal_transfers():
    c = _client()
    _own_peer_a(c)
    calls = _rig(c, transfers=_transfers())
    with _scope('own'):
        assert asyncio.run(c.clear_all_completed_downloads()) is True
    deletes = _deletes(calls)
    assert deletes == [
        'transfers/downloads/peerA/own-1?remove=true',
        'transfers/downloads/peerA/own-2?remove=true',
    ], deletes
    # cleared ids leave the registry; the in-progress one stays
    assert c._own_downloads['peerA'] >= {'own-3'}
    assert 'own-1' not in c._own_downloads['peerA']


def test_scope_all_keeps_the_old_bulk_endpoint():
    c = _client()
    calls = _rig(c)
    with _scope('all'):
        assert asyncio.run(c.clear_all_completed_downloads()) is True
    assert calls == [('DELETE', 'transfers/downloads/all/completed')]


def test_config_error_falls_back_to_own_scope():
    c = _client()
    _own_peer_a(c)
    calls = _rig(c, transfers=_transfers())
    with patch('core.soulseek_client.config_manager.get', side_effect=RuntimeError('db locked')):
        assert asyncio.run(c.clear_all_completed_downloads()) is True
    assert ('DELETE', 'transfers/downloads/all/completed') not in calls
    assert 'transfers/downloads/peerA/own-1?remove=true' in _deletes(calls)


def test_scoped_cancel_skips_other_clients_running_transfers():
    c = _client()
    _own_peer_a(c)
    calls = _rig(c, transfers=_transfers())
    with _scope('own'):
        assert asyncio.run(c.cancel_all_downloads()) is True
    deletes = _deletes(calls)
    # own transfers cancelled regardless of state; nothing foreign touched
    assert deletes == [
        'transfers/downloads/peerA/own-1?remove=true',
        'transfers/downloads/peerA/own-2?remove=true',
        'transfers/downloads/peerA/own-3?remove=true',
    ], deletes


def test_cancel_scope_all_still_cancels_everything():
    c = _client()
    _own_peer_a(c)
    calls = _rig(c, transfers=_transfers())
    with _scope('all'):
        assert asyncio.run(c.cancel_all_downloads()) is True
    assert len(_deletes(calls)) == 6


def test_scoped_search_clear_leaves_foreign_searches():
    c = _client()
    c._own_search_ids = {'s-own-1', 's-own-2'}
    searches = [{'id': 's-own-1'}, {'id': 's-own-2'}, {'id': 's-for-1'}, {'id': 's-for-2'}]
    calls = _rig(c, searches=searches)
    with _scope('own'):
        assert asyncio.run(c.clear_all_searches()) is True
    assert sorted(_deletes(calls)) == ['searches/s-own-1', 'searches/s-own-2']
    assert c._own_search_ids == set()


def test_scoped_search_buffer_counts_and_sorts_only_own():
    c = _client()
    c._own_search_ids = {'s1', 's2', 's3', 's4', 's5'}
    searches = [
        # deliberately unordered, with foreign entries interleaved
        {'id': 's3', 'startedAt': '2026-10-03T03:00:00'},
        {'id': 'f1', 'startedAt': '2026-10-01T00:00:00'},
        {'id': 's1', 'startedAt': '2026-10-03T01:00:00'},
        {'id': 's5', 'startedAt': '2026-10-03T05:00:00'},
        {'id': 'f2', 'startedAt': '2026-10-01T00:30:00'},
        {'id': 's2', 'startedAt': '2026-10-03T02:00:00'},
        {'id': 's4', 'startedAt': '2026-10-03T04:00:00'},
    ]
    calls = _rig(c, searches=searches)
    with _scope('own'):
        assert asyncio.run(
            c.maintain_search_history_with_buffer(keep_searches=2, trigger_threshold=3)) is True
    # oldest three OWN searches by startedAt go; foreign never touched
    assert _deletes(calls) == ['searches/s1', 'searches/s2', 'searches/s3']
    assert c._own_search_ids == {'s4', 's5'}


def test_scoped_buffer_threshold_ignores_foreign_volume():
    c = _client()
    c._own_search_ids = {'s1'}
    searches = [{'id': f'f{i}', 'startedAt': '2026-10-01T00:00:00'} for i in range(300)]
    searches.append({'id': 's1', 'startedAt': '2026-10-03T01:00:00'})
    calls = _rig(c, searches=searches)
    with _scope('own'):
        assert asyncio.run(
            c.maintain_search_history_with_buffer(keep_searches=50, trigger_threshold=200)) is True
    # 301 searches on the server, but only ONE is ours: nothing to do
    assert _deletes(calls) == []


def test_download_records_ownership_by_id_only():
    c = _client()

    async def impl(username, filename, file_size=0):
        return 'id-9'

    c._download_impl = impl
    token = asyncio.run(c.download('peerA', 'a\\song.flac', 123))
    assert token == 'id-9'
    # id only: a filename record would match another client's transfer of
    # the same file from the same user
    assert c._own_downloads['peerA'] == {'id-9'}


def test_idless_enqueue_records_nothing():
    c = _client()

    async def impl(username, filename, file_size=0):
        return filename  # slskd response carried no id

    c._download_impl = impl
    token = asyncio.run(c.download('peerA', 'a\\song.flac', 123))
    assert token == 'a\\song.flac'
    # fail closed: nothing recorded, so scoped cleanup leaves it alone
    assert c._own_downloads.get('peerA', set()) == set() or 'peerA' not in c._own_downloads


# slskd 0.25.1 and 0.26.0 (TransfersController.EnqueueAsync) answer
# POST transfers/downloads/{user} with 201 {"enqueued": [Transfer], "failed": []}
# and no top-level id.
def _enqueue_reply(*pairs):
    return {'enqueued': [{'id': i, 'username': 'peerA', 'filename': f,
                          'state': 'Requested'} for i, f in pairs],
            'failed': []}


def test_extract_transfer_id_reads_real_enqueue_shape():
    reply = _enqueue_reply(('11111111-aaaa', 'a\\song.flac'))
    assert SoulseekClient._extract_transfer_id(reply, 'a\\song.flac') == '11111111-aaaa'


def test_extract_transfer_id_matches_filename_among_several():
    reply = _enqueue_reply(('id-1', 'a\\one.flac'), ('id-2', 'a\\two.flac'))
    assert SoulseekClient._extract_transfer_id(reply, 'a\\two.flac') == 'id-2'
    # no match and more than one entry: do not guess
    assert SoulseekClient._extract_transfer_id(reply, 'a\\three.flac') is None


def test_extract_transfer_id_single_entry_with_renamed_file():
    reply = _enqueue_reply(('id-1', 'a\\One.flac'))
    assert SoulseekClient._extract_transfer_id(reply, 'a\\one.flac') == 'id-1'


def test_extract_transfer_id_empty_or_failed_enqueue():
    assert SoulseekClient._extract_transfer_id(
        {'enqueued': [], 'failed': ['a\\song.flac']}, 'a\\song.flac') is None
    assert SoulseekClient._extract_transfer_id({}, 'x') is None


def test_extract_transfer_id_keeps_legacy_shapes():
    assert SoulseekClient._extract_transfer_id({'id': 'x1'}, 'f') == 'x1'
    assert SoulseekClient._extract_transfer_id([{'id': 'x2'}], 'f') == 'x2'


def test_download_with_real_reply_registers_id_and_scoped_clear_works():
    c = _client()
    calls = []

    async def fake(method, endpoint, **kwargs):
        calls.append((method, endpoint))
        if method == 'POST':
            return _enqueue_reply(('real-1', 'a\\song.flac'))
        if method == 'GET' and endpoint == 'transfers/downloads':
            return [{'username': 'peerA', 'directories': [{'files': [
                {'id': 'real-1', 'filename': 'a\\song.flac', 'state': 'Completed, Succeeded'},
                {'id': 'other-1', 'filename': 'a\\song.flac', 'state': 'Completed, Succeeded'},
            ]}]}]
        return {}

    c._make_request = fake
    c.download_path = '/tmp/dl'
    token = asyncio.run(c.download('peerA', 'a\\song.flac', 123))
    assert token == 'real-1'
    assert c._own_downloads['peerA'] == {'real-1'}

    assert asyncio.run(c.clear_all_completed_downloads()) is True
    deletes = [e for m, e in calls if m == 'DELETE']
    assert deletes == ['transfers/downloads/peerA/real-1?remove=true']
    assert c._own_downloads['peerA'] == set()


def test_cancel_all_forgets_the_ids_it_removed():
    c = _client()
    c._own_downloads = {'peerA': {'own-1', 'own-3'}}
    _rig(c, transfers=_transfers())
    assert asyncio.run(c.cancel_all_downloads()) is True
    assert c._own_downloads['peerA'] == set()
