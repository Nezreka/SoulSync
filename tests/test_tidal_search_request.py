"""The one request behind all four Tidal searches.

Skowl reported artist matching failing with nothing but
"Tidal artist search failed: 400" in the log (Sept 22 2026): the line could
not say what Tidal objected to, and it was at debug, so it never reached
app.log. #1290 found what it was objecting to: tidal retired
/searchResults/{query}, so every search 400'd INVALID_RESOURCE_ID and read as
"not found". the query is a filter[query] param on /searchResults now.
"""

import types

import pytest

import core.tidal_client as tidal_mod
from core.tidal_client import TidalClient, _clean_search_query


class _Response:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


def _client(response, calls):
    """A TidalClient with everything but the request stubbed out."""
    client = TidalClient.__new__(TidalClient)
    client.base_url = 'https://openapi.tidal.com/v2'
    client._ensure_valid_token = lambda: True

    def _get(url, params=None, timeout=None):
        calls.append({'url': url, 'params': params})
        return response

    client.session = types.SimpleNamespace(get=_get)
    return client


def _warnings(monkeypatch):
    messages = []
    monkeypatch.setattr(
        tidal_mod.logger, 'warning', lambda msg, *a, **k: messages.append(str(msg))
    )
    return messages


# ── the query ──

def test_ordinary_names_pass_through_untouched():
    # requests encodes the param; no path-segment mangling any more
    assert _clean_search_query('AC/DC') == 'AC/DC'
    assert _clean_search_query('Sigur Rós') == 'Sigur Rós'


def test_whitespace_collapses():
    assert _clean_search_query('  spaced   out  ') == 'spaced out'


def test_the_query_is_capped_at_what_tidal_accepts():
    assert len(_clean_search_query('x' * 1000)) == 256


@pytest.mark.parametrize('value', [None, '', '   '])
def test_an_empty_query_cleans_to_empty_rather_than_raising(value):
    assert _clean_search_query(value) == ''


# ── the shared request ──

def test_the_query_rides_filter_query_not_the_path():
    """#1290: /searchResults/{query} answers 400 INVALID_RESOURCE_ID now."""
    calls = []
    client = _client(_Response(payload={'ok': True}), calls)

    assert client._search_results('Crazy Lixx', 'artists', 'search_artist') == {'ok': True}
    assert calls[0]['url'] == 'https://openapi.tidal.com/v2/searchResults'
    assert calls[0]['params'] == {
        'filter[query]': 'Crazy Lixx', 'countryCode': 'US', 'include': 'artists',
    }


def test_a_slash_in_a_name_stays_in_the_query():
    calls = []
    _client(_Response(payload={}), calls)._search_results('AC/DC', 'artists', 'search_artist')
    assert calls[0]['params']['filter[query]'] == 'AC/DC'
    assert calls[0]['url'].endswith('/searchResults')


def test_no_limit_param_is_sent():
    """the collection endpoint has no limit; an unknown param is a 400 risk."""
    calls = []
    client = _client(_Response(payload={}), calls)
    client.search_tracks('q', limit=3)
    assert 'limit' not in calls[0]['params']


def test_an_empty_query_never_hits_the_network():
    calls = []
    assert _client(_Response(), calls)._search_results('   ', 'artists', 'x') is None
    assert calls == []


def test_a_429_still_raises_for_the_rate_limit_decorator():
    client = _client(_Response(status_code=429), [])
    with pytest.raises(Exception, match='429'):
        client._search_results('q', 'artists', 'search_artist')


def test_a_failure_logs_the_body_tidal_sent_back(monkeypatch):
    """The whole point: "failed: 400" alone could not be acted on."""
    messages = _warnings(monkeypatch)
    client = _client(
        _Response(status_code=400, text='{"errors":[{"code":"INVALID_RESOURCE_ID"}]}'), []
    )

    assert client._search_results('q', 'artists', 'search_artist') is None

    logged = ' '.join(messages)
    assert '400' in logged
    assert 'INVALID_RESOURCE_ID' in logged
    assert 'search_artist' in logged


def test_a_huge_error_body_is_truncated(monkeypatch):
    messages = _warnings(monkeypatch)
    _client(_Response(status_code=400, text='x' * 5000), [])._search_results(
        'q', 'artists', 'search_artist'
    )
    assert len(messages[0]) < 500


# ── ranking ──

def _ranked_payload():
    """the collection shape: ranked ids in data[0], resources in included."""
    return {
        'data': [{
            'id': 'q', 'type': 'searchResults',
            'relationships': {'tracks': {'data': [
                {'id': '3', 'type': 'tracks'}, {'id': '1', 'type': 'tracks'},
            ]}},
        }],
        'included': [
            {'type': 'tracks', 'id': '1', 'attributes': {'title': 'second'}},
            {'type': 'artists', 'id': '9', 'attributes': {'name': 'someone'}},
            {'type': 'tracks', 'id': '3', 'attributes': {'title': 'first'}},
        ],
    }


def test_included_follows_the_search_ranking():
    """json:api promises no order for included; the ranking lives in data."""
    data = _client(_Response(payload=_ranked_payload()), [])._search_results(
        'q', 'tracks', 'search_tracks'
    )
    assert [r['id'] for r in data['included'] if r['type'] == 'tracks'] == ['3', '1']


def test_search_tracks_limit_keeps_the_top_hits(monkeypatch):
    client = _client(_Response(payload=_ranked_payload()), [])
    monkeypatch.setattr(client, '_parse_track_data', lambda item: item['id'], raising=False)

    assert client.search_tracks('q', limit=1) == ['3']


def test_a_payload_without_a_ranking_is_left_alone():
    payload = {'included': [{'type': 'artists', 'id': '2'}, {'type': 'artists', 'id': '1'}]}
    data = _client(_Response(payload=payload), [])._search_results('q', 'artists', 'x')
    assert [r['id'] for r in data['included']] == ['2', '1']


# ── the four callers all go through it ──

def test_search_artist_asks_for_artists_and_picks_the_best_name():
    calls = []
    payload = {
        'included': [
            {'type': 'artists', 'id': '1', 'attributes': {'name': 'AC/DC Tribute'}},
            {'type': 'artists', 'id': '2', 'attributes': {'name': 'AC DC'}},
        ]
    }
    client = _client(_Response(payload=payload), calls)

    result = client.search_artist('AC DC')
    assert calls[0]['params']['include'] == 'artists'
    assert calls[0]['params']['filter[query]'] == 'AC DC'
    assert result['id'] == '2'


def test_search_artist_returns_none_on_a_400_instead_of_raising():
    client = _client(_Response(status_code=400, text='nope'), [])
    assert client.search_artist('Anyone') is None


def test_search_album_and_track_ask_for_their_own_include():
    calls = []
    client = _client(_Response(payload={}), calls)

    client.search_album('Artist', 'Album')
    client.search_track('Artist', 'Title')

    assert calls[0]['params']['include'] == 'albums'
    assert calls[1]['params']['include'] == 'tracks'
    # artist + title are joined into the one query
    assert calls[0]['params']['filter[query]'] == 'Artist Album'
    assert calls[1]['params']['filter[query]'] == 'Artist Title'
