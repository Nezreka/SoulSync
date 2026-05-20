"""Tests for ``core/prowlarr_client.py``.

Pins the parse + dispatch behavior so a future Prowlarr API tweak
that drops a field doesn't silently lose data, and the search
endpoint keeps building the repeated-key query Prowlarr expects.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch, MagicMock

import pytest

from core.prowlarr_client import (
    DEFAULT_MUSIC_CATEGORIES,
    ProwlarrClient,
    ProwlarrIndexer,
    ProwlarrSearchResult,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


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
