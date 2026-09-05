"""Tests for ``core/prowlarr_client.py``.

Pins the parse + dispatch behavior so a future Prowlarr API tweak
that drops a field doesn't silently lose data, and the search
endpoint keeps building the repeated-key query Prowlarr expects.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch, MagicMock

import pytest

from core.prowlarr_client import (
    DEFAULT_MUSIC_CATEGORIES,
    ProwlarrClient,
    ProwlarrIndexer,
    ProwlarrSearchResult,
)


def _run(coro):
    loop = asyncio.new_event_loop()

    async def _drain_with_heartbeat():
        task = loop.create_task(coro)
        while not task.done():
            await asyncio.sleep(0.01)
        return task.result()

    try:
        return loop.run_until_complete(_drain_with_heartbeat())
    finally:
        loop.close()


def _client_with_config(url="http://prowlarr:9696", api_key="secret"):
    """Build a client whose ``_load_config`` already ran with the
    given URL + key, sidestepping the real config_manager."""
    client = ProwlarrClient.__new__(ProwlarrClient)
    client._url = url.rstrip('/')
    client._api_key = api_key
    return client


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------


def test_parse_indexer_extracts_core_fields() -> None:
    client = _client_with_config()
    entry = {
        'id': 7,
        'name': 'Public Tracker',
        'protocol': 'torrent',
        'enable': True,
        'privacy': 'public',
        'capabilities': {
            'categories': [
                {'id': 3000, 'name': 'Audio'},
                {'id': 3040, 'name': 'Audio/Lossless'},
            ],
        },
    }
    indexer = client._parse_indexer(entry)
    assert indexer == ProwlarrIndexer(
        id=7,
        name='Public Tracker',
        protocol='torrent',
        enable=True,
        privacy='public',
        categories=[3000, 3040],
        capabilities=entry['capabilities'],
    )


def test_parse_indexer_tolerates_missing_capabilities() -> None:
    """Some indexers (the ones in error state) come back with no
    ``capabilities`` block — must not crash."""
    client = _client_with_config()
    indexer = client._parse_indexer({'id': 1, 'name': 'X', 'protocol': 'usenet'})
    assert indexer.id == 1
    assert indexer.protocol == 'usenet'
    assert indexer.categories == []


def test_parse_result_extracts_torrent_fields() -> None:
    client = _client_with_config()
    entry = {
        'guid': 'guid-1',
        'title': 'Some Album FLAC',
        'indexerId': 3,
        'indexer': 'Tracker',
        'protocol': 'torrent',
        'downloadUrl': 'https://example.com/x.torrent',
        'magnetUrl': 'magnet:?xt=urn:btih:abc',
        'infoUrl': 'https://example.com/details/1',
        'size': 524288000,
        'seeders': 12,
        'leechers': 3,
        'grabs': 100,
        'publishDate': '2026-05-10T00:00:00Z',
        'categories': [{'id': 3040, 'name': 'Audio/Lossless'}],
    }
    result = client._parse_result(entry)
    assert result.title == 'Some Album FLAC'
    assert result.indexer_id == 3
    assert result.download_url == 'https://example.com/x.torrent'
    assert result.magnet_uri == 'magnet:?xt=urn:btih:abc'
    assert result.size == 524288000
    assert result.seeders == 12
    assert result.categories == [3040]


def test_parse_result_accepts_int_categories() -> None:
    """Some indexers return categories as bare ints instead of
    ``{id, name}`` dicts. Both forms must work."""
    client = _client_with_config()
    result = client._parse_result({'title': 'X', 'categories': [3000, 3010]})
    assert result.categories == [3000, 3010]


def test_parse_result_skips_garbage_category_entries() -> None:
    client = _client_with_config()
    result = client._parse_result({'title': 'X', 'categories': [{'name': 'no-id'}, 'string', None]})
    assert result.categories == []


# ---------------------------------------------------------------------------
# Configured-state predicates
# ---------------------------------------------------------------------------


def test_is_configured_requires_both_fields() -> None:
    assert _client_with_config('http://x', '').is_configured() is False
    assert _client_with_config('', 'key').is_configured() is False
    assert _client_with_config('http://x', 'key').is_configured() is True


def test_check_connection_returns_false_when_not_configured() -> None:
    client = _client_with_config('', '')
    assert _run(client.check_connection()) is False


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, json_body):
    resp = MagicMock()
    resp.ok = 200 <= status_code < 400
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


def test_search_passes_repeated_categories_and_indexer_ids() -> None:
    """Prowlarr's search endpoint expects repeated query keys —
    ``categories=3000&categories=3010&indexerIds=1``. ``requests``
    serializes a list of tuples into that exact form, so we assert
    the params are passed as a list-of-tuples (not a dict)."""
    client = _client_with_config()
    captured_params = {}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured_params['url'] = url
        captured_params['params'] = params
        return _mock_response(200, [])

    with patch('core.prowlarr_client.http_requests.get', side_effect=fake_get):
        _run(client.search('the query', categories=[3000, 3010], indexer_ids=[1, 5]))

    assert captured_params['url'] == 'http://prowlarr:9696/api/v1/search'
    params = captured_params['params']
    # Convert to a frozenset of pairs for order-independent comparison
    pair_set = set(params)
    assert ('query', 'the query') in pair_set
    assert ('type', 'search') in pair_set
    assert ('categories', 3000) in pair_set
    assert ('categories', 3010) in pair_set
    assert ('indexerIds', 1) in pair_set
    assert ('indexerIds', 5) in pair_set


def test_search_returns_empty_on_blank_query() -> None:
    client = _client_with_config()
    # No HTTP mock — call must short-circuit without touching the network.
    results = _run(client.search(''))
    assert results == []
    results = _run(client.search('   '))
    assert results == []


def test_search_parses_response_list() -> None:
    client = _client_with_config()
    with patch('core.prowlarr_client.http_requests.get',
               return_value=_mock_response(200, [
                   {'guid': 'a', 'title': 'Album A', 'protocol': 'torrent'},
                   {'guid': 'b', 'title': 'Album B', 'protocol': 'usenet'},
               ])):
        results = _run(client.search('q'))
    assert [r.title for r in results] == ['Album A', 'Album B']
    assert [r.protocol for r in results] == ['torrent', 'usenet']


def test_check_connection_hits_system_status() -> None:
    client = _client_with_config()
    with patch('core.prowlarr_client.http_requests.get',
               return_value=_mock_response(200, {'version': '1.13.0'})) as mock_get:
        ok = _run(client.check_connection())
    assert ok is True
    called_url = mock_get.call_args.args[0]
    assert called_url == 'http://prowlarr:9696/api/v1/system/status'
    assert mock_get.call_args.kwargs['headers']['X-Api-Key'] == 'secret'


def test_check_connection_returns_false_on_http_error() -> None:
    client = _client_with_config()
    with patch('core.prowlarr_client.http_requests.get',
               return_value=_mock_response(401, {'error': 'unauthorized'})):
        ok = _run(client.check_connection())
    assert ok is False


def test_default_music_categories_match_newznab_tree() -> None:
    """The Newznab Music category IDs are a stable convention across
    Prowlarr / Jackett / every indexer. Pin the defaults so a typo
    here doesn't silently broaden / narrow what SoulSync queries."""
    assert 3000 in DEFAULT_MUSIC_CATEGORIES   # Audio (parent)
    assert 3010 in DEFAULT_MUSIC_CATEGORIES   # MP3
    assert 3040 in DEFAULT_MUSIC_CATEGORIES   # Lossless


# ---------------------------------------------------------------------------
# Indexer priority
# ---------------------------------------------------------------------------


def test_parse_indexer_reads_priority() -> None:
    client = _client_with_config()
    indexer = client._parse_indexer({'id': 3, 'name': 'X', 'protocol': 'torrent',
                                     'priority': 5})

    assert indexer.priority == 5


def test_parse_indexer_defaults_priority_to_prowlarrs_own_default() -> None:
    """1 (highest) to 50 (lowest), 25 in the middle — an absent value is 25."""
    client = _client_with_config()

    assert client._parse_indexer({'id': 3, 'name': 'X'}).priority == 25


def _priority_client(monkeypatch, listing):
    from core import prowlarr_client as pc
    monkeypatch.setattr(pc, '_INDEXER_PRIORITY_CACHE', None, raising=False)
    monkeypatch.setattr(pc, '_INDEXER_PRIORITY_CACHED_AT', 0.0, raising=False)
    client = _client_with_config()
    monkeypatch.setattr(client, '_get_indexers_sync', listing)
    return client


def test_the_search_wrapper_stamps_each_result_with_its_indexers_priority(monkeypatch) -> None:
    client = _priority_client(monkeypatch, lambda: [
        ProwlarrIndexer(id=1, name='Fast', protocol='torrent', enable=True,
                        privacy='public', priority=2),
        ProwlarrIndexer(id=2, name='Slow', protocol='torrent', enable=True,
                        privacy='public', priority=45),
    ])
    with patch.object(client, '_api_get', return_value=[
        {'guid': 'a', 'title': 'A', 'indexerId': 1, 'protocol': 'torrent'},
        {'guid': 'b', 'title': 'B', 'indexerId': 2, 'protocol': 'torrent'},
        {'guid': 'c', 'title': 'C', 'indexerId': 99, 'protocol': 'torrent'},
    ]):
        results = _run(client.search('q', throttle=False))

    # An indexer Prowlarr did not list keeps the neutral default rather than
    # being sorted to the bottom.
    assert [r.indexer_priority for r in results] == [2, 45, 25]


def test_the_raw_search_never_pays_for_the_listing(monkeypatch) -> None:
    """`_search_sync` runs inside the fan-out's per-task deadline.

    A slow `/indexer` call there does not merely cost time, it can push an
    already-finished search past the budget, and those results are discarded.
    The video side also calls `_search_sync` directly per request and never
    reads the priority at all.
    """
    listing = MagicMock(return_value=[])
    client = _priority_client(monkeypatch, listing)
    with patch.object(client, '_api_get', return_value=[
        {'guid': 'a', 'title': 'A', 'indexerId': 1, 'protocol': 'torrent'},
    ]):
        results = client._search_sync('q', [3000], [], 100, throttle=False)

    assert listing.call_count == 0
    assert [r.indexer_priority for r in results] == [25]


def test_a_broken_indexer_listing_never_fails_a_search(monkeypatch) -> None:
    def _boom():
        raise RuntimeError('down')

    client = _priority_client(monkeypatch, _boom)
    with patch.object(client, '_api_get', return_value=[
        {'guid': 'a', 'title': 'A', 'indexerId': 1, 'protocol': 'torrent'},
    ]):
        results = _run(client.search('q', throttle=False))

    assert [r.indexer_priority for r in results] == [25]


def test_the_priority_map_is_shared_by_every_client(monkeypatch) -> None:
    """The video side builds a fresh client per search; a per-instance cache
    would never hit there, so every request paid for its own listing."""
    listing = MagicMock(return_value=[
        ProwlarrIndexer(id=1, name='Fast', protocol='torrent', enable=True,
                        privacy='public', priority=2),
    ])
    first = _priority_client(monkeypatch, listing)
    second = _client_with_config()
    monkeypatch.setattr(second, '_get_indexers_sync', listing)

    assert first.indexer_priorities() == {1: 2}
    assert second.indexer_priorities() == {1: 2}
    assert listing.call_count == 1


def test_a_cold_cache_under_concurrency_lists_once(monkeypatch) -> None:
    """Six searches starting together used to issue six listings."""
    import threading

    calls = []
    ready = threading.Barrier(6)

    def _slow_listing():
        calls.append(1)
        time.sleep(0.05)
        return [ProwlarrIndexer(id=1, name='X', protocol='torrent', enable=True,
                                privacy='public', priority=3)]

    client = _priority_client(monkeypatch, _slow_listing)

    def _worker():
        ready.wait()
        client.indexer_priorities()

    threads = [threading.Thread(target=_worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
