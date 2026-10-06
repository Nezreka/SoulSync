"""the soulseek ownership registry survives a restart.

cleanup_scope='own' (the default) only removes slskd transfers this client
created. the registry of those ids used to be in memory only, so after every
restart all of SoulSync's older transfers looked foreign and piled up in
slskd's list forever. it is saved in the metadata table now and shared by
every client instance.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from core.soulseek_client import SoulseekClient


@pytest.fixture()
def store(monkeypatch):
    saved = {}
    monkeypatch.setattr(SoulseekClient, 'PERSIST_OWNERSHIP', True)
    monkeypatch.setattr(SoulseekClient, '_store_read',
                        staticmethod(lambda: json.loads(saved['v']) if 'v' in saved else {}))
    monkeypatch.setattr(SoulseekClient, '_store_write',
                        staticmethod(lambda data: saved.__setitem__('v', json.dumps(data))))
    return saved


def _client():
    c = SoulseekClient.__new__(SoulseekClient)
    c.base_url = 'http://slskd.test'
    return c


def _rig(client, transfers=None, searches=None):
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


def _deletes(calls):
    return [e for m, e in calls if m == 'DELETE']


def _own_scope():
    return patch('core.soulseek_client.config_manager.get', return_value='own')


def _listing(*files):
    return [{'username': 'peerA', 'directories': [{'files': list(files)}]}]


def test_completed_transfer_from_before_a_restart_is_still_cleared(store):
    before = _client()
    before._remember_own_download('peerA', 'own-1')

    after = _client()   # a fresh process: nothing in memory
    calls = _rig(after, transfers=_listing(
        {'id': 'own-1', 'state': 'Completed, Succeeded'},
        {'id': 'other-1', 'state': 'Completed, Succeeded'},
    ))
    with _own_scope():
        assert asyncio.run(after.clear_all_completed_downloads())
    assert _deletes(calls) == ['transfers/downloads/peerA/own-1?remove=true']
    # forgotten once removed, so the saved list doesn't grow
    assert json.loads(store['v'])['downloads'] == {}


def test_two_clients_do_not_overwrite_each_other(store):
    music, shared = _client(), _client()
    music._remember_own_download('peerA', 'm-1')
    shared._remember_own_download('peerB', 's-1')
    saved = json.loads(store['v'])['downloads']
    assert saved == {'peerA': ['m-1'], 'peerB': ['s-1']}


def test_ids_slskd_no_longer_lists_are_pruned(store):
    c = _client()
    c._remember_own_download('peerA', 'gone')      # removed by hand in slskd
    c._remember_own_download('peerA', 'running')
    _rig(c, transfers=_listing({'id': 'running', 'state': 'InProgress'}))
    with _own_scope():
        asyncio.run(c.clear_all_completed_downloads())
    assert json.loads(store['v'])['downloads'] == {'peerA': ['running']}


def test_a_download_enqueued_during_the_listing_is_not_pruned(store):
    c = _client()

    async def fake(method, endpoint, **kwargs):
        if method == 'GET' and endpoint == 'transfers/downloads':
            # enqueued while the listing was in flight: not in it yet
            c._remember_own_download('peerA', 'brand-new')
            return _listing({'id': 'other', 'state': 'InProgress'})
        return {}

    c._make_request = fake
    with _own_scope():
        asyncio.run(c.clear_all_completed_downloads())
    assert 'brand-new' in json.loads(store['v'])['downloads']['peerA']


def test_search_from_before_a_restart_is_still_cleared(store):
    before = _client()
    before._remember_own_search('search-1')

    after = _client()
    calls = _rig(after, searches=[{'id': 'search-1'}, {'id': 'lidarr-search'}])
    with _own_scope():
        asyncio.run(after.clear_all_searches())
    assert _deletes(calls) == ['searches/search-1']
    assert json.loads(store['v'])['searches'] == []


def test_store_failure_never_breaks_a_download(monkeypatch):
    monkeypatch.setattr(SoulseekClient, 'PERSIST_OWNERSHIP', True)

    def boom(*a):
        raise RuntimeError('db locked')

    monkeypatch.setattr(SoulseekClient, '_store_read', staticmethod(boom))
    monkeypatch.setattr(SoulseekClient, '_store_write', staticmethod(boom))
    c = _client()
    c._remember_own_download('peerA', 'own-1')   # must not raise
    assert c._owns_download('peerA', {'id': 'own-1'})


def test_real_metadata_store_round_trip(monkeypatch):
    # the real metadata table (the suite's temp db), not a fake
    monkeypatch.setattr(SoulseekClient, 'PERSIST_OWNERSHIP', True)
    try:
        SoulseekClient._store_write({})
        _client()._remember_own_download('peerA', 'own-1')
        assert SoulseekClient._store_read()['downloads'] == {'peerA': ['own-1']}
        assert _client()._owns_download('peerA', {'id': 'own-1'})
    finally:
        SoulseekClient._store_write({})
