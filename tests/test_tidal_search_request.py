"""The one request behind all four Tidal searches.

Skowl reported artist matching failing with nothing but
"Tidal artist search failed: 400" in the log (Sept 22 2026). Two things were
wrong with that line: it could not say what Tidal objected to, and it was at
debug, so it never reached app.log at all. The search query is also a PATH
segment, which a slash in an artist name breaks before the search runs.
"""

import types

import pytest

from core.tidal_client import TidalClient, _search_query_segment


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


# ── the path segment ──

def test_a_slash_in_a_name_never_reaches_the_path():
    """"AC/DC" percent-encodes to AC%2FDC, which the gateway rejects with a
    400 inside a path segment — the search never runs."""
    assert _search_query_segment('AC/DC') == 'AC%20DC'
    assert '%2F' not in _search_query_segment('AC/DC')


def test_a_backslash_goes_the_same_way():
    assert '%5C' not in _search_query_segment(r'AC\DC')


def test_ordinary_names_are_still_fully_encoded():
    assert _search_query_segment('Nine Inch Nails') == 'Nine%20Inch%20Nails'
    assert _search_query_segment('Sigur Rós') == 'Sigur%20R%C3%B3s'


def test_collapsed_whitespace_does_not_leave_a_double_space():
    # a slash between words must not become two spaces
    assert _search_query_segment('AC / DC') == 'AC%20DC'
    assert _search_query_segment('  spaced   out  ') == 'spaced%20out'


@pytest.mark.parametrize('value', [None, '', '   ', '///'])
def test_an_empty_query_encodes_to_empty_rather_than_raising(value):
    assert _search_query_segment(value) == ''


# ── the shared request ──

def test_the_query_lands_in_the_path_and_the_rest_in_params():
    calls = []
    client = _client(_Response(payload={'ok': True}), calls)

    assert client._search_results('AC/DC', 'artists', 'search_artist') == {'ok': True}
    assert calls[0]['url'].endswith('/searchResults/AC%20DC')
    assert calls[0]['params'] == {'countryCode': 'US', 'include': 'artists'}


def test_limit_is_sent_only_when_given():
    calls = []
    client = _client(_Response(payload={}), calls)

    client._search_results('q', 'tracks', 'search_tracks', limit=10)
    assert calls[0]['params']['limit'] == 10

    client._search_results('q', 'artists', 'search_artist')
    assert 'limit' not in calls[1]['params']


def test_a_429_still_raises_for_the_rate_limit_decorator():
    client = _client(_Response(status_code=429), [])
    with pytest.raises(Exception, match='429'):
        client._search_results('q', 'artists', 'search_artist')


def test_a_failure_logs_the_body_tidal_sent_back(caplog):
    """The whole point: "failed: 400" alone could not be acted on."""
    client = _client(
        _Response(status_code=400, text='{"errors":[{"detail":"bad include"}]}'), []
    )

    with caplog.at_level('WARNING'):
        assert client._search_results('q', 'artists', 'search_artist') is None

    logged = ' '.join(r.getMessage() for r in caplog.records)
    assert '400' in logged
    assert 'bad include' in logged
    assert 'search_artist' in logged


def test_a_huge_error_body_is_truncated():
    client = _client(_Response(status_code=400, text='x' * 5000), [])
    messages = []
    import core.tidal_client as mod

    original = mod.logger.warning
    mod.logger.warning = lambda msg, *a, **k: messages.append(str(msg))
    try:
        client._search_results('q', 'artists', 'search_artist')
    finally:
        mod.logger.warning = original

    assert len(messages[0]) < 500


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

    result = client.search_artist('AC/DC')
    assert calls[0]['params']['include'] == 'artists'
    assert calls[0]['url'].endswith('/searchResults/AC%20DC')
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
    # artist + title are joined into the one path segment
    assert calls[0]['url'].endswith('/searchResults/Artist%20Album')
