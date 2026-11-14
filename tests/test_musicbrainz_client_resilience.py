from __future__ import annotations

import pytest
import requests

from core.musicbrainz_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    MusicBrainzClient,
)


class _Response:
    def __init__(self, payload=None, status_code=200, headers=None):
        self._payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}
        self.url = None

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error", response=self)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, outcomes):
        self.headers = {}
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, *, params=None, timeout=None, allow_redirects=False):
        self.calls.append({'url': url, 'params': params, 'timeout': timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        outcome.url = url
        return outcome


def _client(session, *, retries=0):
    client = MusicBrainzClient.__new__(MusicBrainzClient)
    client.session = session
    client.connect_timeout = DEFAULT_CONNECT_TIMEOUT
    client.read_timeout = DEFAULT_READ_TIMEOUT
    client.max_retries = retries
    return client


def test_musicbrainz_requests_use_generous_read_timeout():
    session = _Session([_Response({'artists': []})])
    client = _client(session)

    response = client._get('/artist', params={'query': 'artist:\"Fast Pussycats\"'})

    assert response.json() == {'artists': []}
    assert session.calls[0]['timeout'] == (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)
    assert DEFAULT_READ_TIMEOUT >= 30


def test_musicbrainz_read_timeout_is_retried_with_global_pacing(monkeypatch):
    session = _Session([
        requests.exceptions.ReadTimeout('delayed by upstream'),
        _Response({'artists': [{'id': 'mbid', 'name': 'Fast Pussycats'}]}),
    ])
    client = _client(session, retries=1)
    waits = []
    sleeps = []
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: waits.append('slot'))
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda seconds: sleeps.append(seconds))

    response = client._get('/artist', params={'query': 'artist:\"Fast Pussycats\"'})

    assert response.json()['artists'][0]['name'] == 'Fast Pussycats'
    assert len(session.calls) == 2
    assert waits == ['slot', 'slot']
    assert sleeps == [2.0]


def test_musicbrainz_503_is_retried(monkeypatch):
    session = _Session([_Response(status_code=503), _Response({'releases': []})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    response = client._get('/release', params={'query': 'release:\"Album\"'})

    assert response.json() == {'releases': []}
    assert len(session.calls) == 2


def test_retry_after_overrides_backoff_and_applies_to_other_callers(monkeypatch):
    import core.musicbrainz_client as mb
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', 'http://mirror:5000')
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0.1')
    monkeypatch.setattr(mb, '_server_rate_states', {})
    monkeypatch.setattr(mb, '_last_api_call_time', 0)
    clock = [100.0]
    sleeps = []
    from types import SimpleNamespace
    monkeypatch.setattr(mb, 'time', SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda delay: (sleeps.append(delay), clock.__setitem__(0, clock[0] + delay))))
    session = _Session([_Response(status_code=503, headers={'Retry-After': '7'}), _Response({'artists': []})])
    client = _client(session, retries=1)
    client._get('/artist')
    assert 7 in sleeps
    assert mb._server_rate_states['http://mirror:5000/ws/2']['cooldown_until'] == 107


def test_retry_after_http_date_is_parsed():
    import core.musicbrainz_client as mb
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    future = datetime.now(timezone.utc) + timedelta(seconds=20)
    delay = mb._retry_after_seconds(_Response(headers={'Retry-After': format_datetime(future)}))
    assert 18 <= delay <= 20


def test_merged_recording_redirect_stays_on_mirror(monkeypatch):
    import core.musicbrainz_client as mb
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', 'http://mirror:5000')
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_REQUEST_INTERVAL', '0.1')
    monkeypatch.setattr(mb, '_wait_for_musicbrainz_slot', lambda *args: None)
    session = _Session([
        _Response(status_code=301, headers={'Location': '/ws/2/recording/canonical?fmt=json'}),
        _Response({'id': 'canonical', 'title': 'Song'}),
    ])
    client = _client(session)
    assert client.get_recording('merged', raise_on_error=True)['id'] == 'canonical'
    assert len(session.calls) == 2
    assert session.calls[1]['url'] == 'http://mirror:5000/ws/2/recording/canonical?fmt=json'


def test_cross_server_recording_redirect_is_rejected(monkeypatch):
    import core.musicbrainz_client as mb
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', 'http://mirror:5000')
    monkeypatch.setattr(mb, '_wait_for_musicbrainz_slot', lambda *args: None)
    session = _Session([_Response(status_code=301, headers={
        'Location': 'https://musicbrainz.org/ws/2/recording/canonical?fmt=json'})])
    client = _client(session)
    with pytest.raises(requests.HTTPError, match='final base URL'):
        client.get_recording('merged', raise_on_error=True)
    assert len(session.calls) == 1


def test_recording_lookup_raises_outage_but_returns_none_on_404(monkeypatch):
    import core.musicbrainz_client as mb
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', 'http://mirror:5000')
    monkeypatch.setattr(mb, '_wait_for_musicbrainz_slot', lambda *args: None)
    client = _client(_Session([_Response(status_code=404)]))
    assert client.get_recording('missing', raise_on_error=True) is None
    client.session = _Session([_Response(status_code=503)])
    with pytest.raises(requests.HTTPError):
        client.get_recording('unavailable', raise_on_error=True)


def test_adaptive_rate_grows_then_halves_on_overload(monkeypatch):
    import core.musicbrainz_client as mb
    monkeypatch.setattr(mb, '_server_rate_states', {})
    clock = [100.0]
    from types import SimpleNamespace
    monkeypatch.setattr(mb, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    server = 'http://mirror/ws/2'
    mb._rate_state(server)
    for n in range(80):
        clock[0] = 100 + n / 8
        mb._record_musicbrainz_result(server, 0, success=True)
    clock[0] = 110.0
    mb._record_musicbrainz_result(server, 0, success=True)
    assert mb._server_rate_states[server]['rate'] == pytest.approx(11.5)
    mb._record_musicbrainz_result(server, 0, retry_delay=4)
    assert mb._server_rate_states[server]['rate'] == pytest.approx(5.75)
    assert mb._server_rate_states[server]['cooldown_until'] == 114


def test_adaptive_rate_slows_when_latency_climbs(monkeypatch):
    import core.musicbrainz_client as mb
    from types import SimpleNamespace
    clock = [100.0]
    monkeypatch.setattr(mb, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(mb, '_server_rate_states', {})
    server = 'http://mirror/ws/2'
    state = mb._rate_state(server)
    state['best_p95'] = 0.05
    for n in range(20):
        clock[0] = 100 + n / 2
        mb._record_musicbrainz_result(server, 0, success=True, latency=0.4)
    clock[0] = 110.0
    mb._record_musicbrainz_result(server, 0, success=True, latency=0.4)
    assert state['rate'] == pytest.approx(7.0)


# --- p5-mb-busy-negcache: a 200 OK "server busy" body is a transient failure ---
#
# MusicBrainz sometimes answers overload with HTTP 200 and a body like
# {"error": "The MusicBrainz web server is currently busy. Please try again
# later."} instead of a 503. raise_for_status() never fires on a 200, so
# without detecting this shape every caller's `data.get('recordings', [])`
# quietly returns `[]` — identical to a genuine empty result, and (via
# search_recording/search_release/search_artist) that got written down as a
# 30-day negative cache entry for an outage.

_BUSY_BODY = {'error': 'The MusicBrainz web server is currently busy. Please try again later.'}


def test_busy_body_is_retried_like_a_503(monkeypatch):
    session = _Session([_Response(_BUSY_BODY), _Response({'recordings': [{'id': 'rec-1'}]})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    response = client._get('/recording', params={'query': 'recording:\"Song\"'})

    assert response.json() == {'recordings': [{'id': 'rec-1'}]}
    assert len(session.calls) == 2


def test_busy_body_propagates_like_a_503_after_retries_exhausted(monkeypatch):
    session = _Session([_Response(_BUSY_BODY), _Response(_BUSY_BODY)])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    import core.musicbrainz_client as mbc
    with pytest.raises(mbc.MusicBrainzBusyError):
        client._get('/recording', params={'query': 'recording:\"Song\"'})
    assert len(session.calls) == 2


def test_a_non_busy_200_error_body_is_not_retried(monkeypatch):
    # MusicBrainz also reports genuine, non-transient failures (e.g. a
    # malformed Lucene query) as an `error` key at HTTP 200. Retrying one
    # would just repeat the same 200 across the whole retry budget for a
    # request that was never going to succeed — only "busy"/"try again"
    # wording should be treated as transient.
    session = _Session([_Response({'error': 'Invalid search syntax near: foo:('})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)

    response = client._get('/recording', params={'query': 'recording:\"Song\"'})

    assert response.json() == {'error': 'Invalid search syntax near: foo:('}
    assert len(session.calls) == 1


def test_search_recording_returns_empty_for_a_non_busy_200_error_body(monkeypatch):
    session = _Session([_Response({'error': 'Invalid search syntax near: foo:('})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)

    assert client.search_recording('Song', 'Artist') == []
    assert len(session.calls) == 1


def test_legit_empty_result_is_not_mistaken_for_a_busy_body(monkeypatch):
    session = _Session([_Response({'recordings': []})])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)

    response = client._get('/recording', params={'query': 'recording:\"Nonexistent Song\"'})

    assert response.json() == {'recordings': []}
    assert len(session.calls) == 1


def test_search_recording_fails_soft_on_a_busy_body_by_default(monkeypatch):
    session = _Session([_Response(_BUSY_BODY), _Response(_BUSY_BODY)])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    assert client.search_recording('Song', 'Artist') == []
    assert len(session.calls) == 2


def test_search_recording_raises_on_a_busy_body_when_asked(monkeypatch):
    session = _Session([_Response(_BUSY_BODY), _Response(_BUSY_BODY)])
    client = _client(session, retries=1)
    monkeypatch.setattr('core.musicbrainz_client._wait_for_musicbrainz_slot', lambda *args: None)
    monkeypatch.setattr('core.musicbrainz_client.time.sleep', lambda _seconds: None)

    import core.musicbrainz_client as mbc
    with pytest.raises(mbc.MusicBrainzBusyError):
        client.search_recording('Song', 'Artist', raise_on_error=True)
    assert len(session.calls) == 2


def test_merged_release_group_redirect_is_followed(monkeypatch):
    """nov 13 2026: musicbrainz answers a merged release-group id with a 301
    to the group it was merged into (checked against the live api). only
    recordings were followed, so the album failed on every start with
    "configure its final base URL"."""
    import core.musicbrainz_client as mb
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', 'http://mirror:5000')
    monkeypatch.setattr(mb, '_wait_for_musicbrainz_slot', lambda *args: None)
    session = _Session([
        _Response(status_code=301, headers={
            'Location': 'http://mirror:5000/ws/2/release-group/canonical?fmt=json&inc=releases'}),
        _Response({'id': 'canonical', 'title': 'Album'}),
    ])
    client = _client(session)
    assert client.get_release_group('merged', includes=['releases'])['id'] == 'canonical'
    assert len(session.calls) == 2
    assert session.calls[1]['url'] == 'http://mirror:5000/ws/2/release-group/canonical?fmt=json&inc=releases'


@pytest.mark.parametrize('asked, target, allowed', [
    ('/ws/2/artist/a', '/ws/2/artist/b', True),
    ('/ws/2/release/a', '/ws/2/release/b', True),
    ('/ws/2/release-group/a', '/ws/2/release-group/b', True),
    ('/ws/2/recording/a', '/ws/2/recording/b', True),
    ('/ws/2/work/a', '/ws/2/work/b', True),
    # another kind of entity, another api root, a browse or search path
    ('/ws/2/release-group/a', '/ws/2/release/b', False),
    ('/ws/2/recording/a', '/ws/3/recording/b', False),
    ('/ws/2/recording/a', '/recording/b', False),
    ('/ws/2/recording', '/ws/2/recording/b', False),
    ('/ws/2/recording/a', '/ws/2/recording/b/extra', False),
    ('/ws/2/recording/a', '/ws/2/recording/', False),
    ('/ws/2/isrc/a', '/ws/2/isrc/b', False),
    ('/ws/2/login/a', '/ws/2/login/b', False),
])
def test_only_a_same_entity_lookup_redirect_is_followed(asked, target, allowed):
    import core.musicbrainz_client as mb
    assert mb._same_entity_redirect('/ws/2', asked, target) is allowed
    assert mb._same_entity_redirect('/ws/2/', asked, target) is allowed


def test_a_release_group_redirect_to_another_host_is_still_refused(monkeypatch):
    import core.musicbrainz_client as mb
    monkeypatch.setenv('SOULSYNC_MUSICBRAINZ_BASE_URL', 'http://mirror:5000')
    monkeypatch.setattr(mb, '_wait_for_musicbrainz_slot', lambda *args: None)
    session = _Session([_Response(status_code=301, headers={
        'Location': 'https://elsewhere.example/ws/2/release-group/canonical?fmt=json'})])
    client = _client(session)
    with pytest.raises(requests.HTTPError, match='final base URL'):
        client._get('/release-group/merged', params={'fmt': 'json'})
    assert len(session.calls) == 1
