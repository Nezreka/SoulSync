"""last.fm api keys need a pro subscription from 1 december 2026.

a refused key is remembered (no hammering), cleared when it works again or
the key changes, and every live last.fm read falls back to free sources
(listenbrainz, deezer) with a cache under all of it. no network here: the
last.fm session, the deezer reads and the listenbrainz reads are all fakes.
"""

from __future__ import annotations

import sqlite3

import pytest

from core import lastfm_client as lfc
from core import lastfm_fallback as fb
from core import listenbrainz_public as lbp
from core import provider_cache


# ── fakes ──

class _Resp:
    def __init__(self, payload=None, status=200, json_error=False):
        self._payload = payload
        self.status_code = status
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} for url ?api_key=SECRET")


class _Session:
    """answers each last.fm method from a table; counts every request."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        answer = self.answers.get(params.get('method')) if params else None
        if callable(answer):
            return answer(params)
        return answer if answer is not None else _Resp({'error': 6, 'message': 'not found'})

    def post(self, url, data=None, timeout=None):
        self.calls.append(dict(data or {}))
        return self.answers['__post__']


REJECTED = _Resp({'error': 10, 'message': 'Invalid API key - You must be granted a valid key by last.fm'},
                 status=403)
SIMILAR = _Resp({'similarartists': {'artist': [{'name': 'Portishead', 'match': '1'}]}})


def _client(answers, key='KEY'):
    client = lfc.LastFMClient(api_key=key, api_secret='s', session_key='sk')
    client.session = _Session(answers)
    return client


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(lfc, 'MIN_API_INTERVAL', 0)


# ── key state ──

def test_error_10_marks_the_key_refused_and_later_calls_skip_the_network():
    client = _client({'artist.getsimilar': REJECTED})
    assert client.get_similar_artists('Massive Attack') == []
    assert lfc.key_rejection('KEY') is not None
    asked = len(client.session.calls)
    assert client.get_similar_artists('Massive Attack') == []
    assert client.get_artist_top_tracks('Massive Attack') == []
    assert len(client.session.calls) == asked
    assert not lfc.lastfm_usable('KEY')


def test_the_client_exposes_key_rejected_for_the_enrichment_worker():
    client = _client({'artist.getsimilar': REJECTED})
    assert client.key_rejected is False
    client.get_similar_artists('Low')
    assert client.key_rejected is True
    # process-wide: a fresh client on the same key knows too
    assert lfc.LastFMClient(api_key='KEY').key_rejected is True
    assert lfc.LastFMClient(api_key='OTHER').key_rejected is False
    assert lfc.LastFMClient(api_key='').key_rejected is False


@pytest.mark.parametrize('response', [
    _Resp({'error': 26, 'message': 'Suspended API key'}, status=403),
    _Resp({'error': 4, 'message': 'Authentication Failed'}, status=200),
    _Resp(None, status=403, json_error=True),
    _Resp(None, status=401, json_error=True),
])
def test_other_refusals_count_too(response):
    client = _client({'artist.getsimilar': response})
    assert client.get_similar_artists('Low') == []
    assert lfc.key_rejection('KEY') is not None


@pytest.mark.parametrize('response', [
    _Resp({'error': 6, 'message': 'not found'}),
    _Resp({'error': 11, 'message': 'Service Offline'}),
    _Resp({'error': 29, 'message': 'Rate limit'}),
    _Resp(None, status=500, json_error=True),
])
def test_not_found_and_outages_are_not_a_refused_key(response):
    client = _client({'artist.getsimilar': response})
    client.get_similar_artists('Low')
    assert lfc.key_rejection('KEY') is None


def test_a_refused_key_raises_for_callers_that_tell_errors_from_not_found():
    client = _client({'artist.getinfo': REJECTED})
    with pytest.raises(lfc.LastFMKeyRejected) as err:
        client.get_artist_info('Low')
    assert 'Pro subscription' in str(err.value)
    # remembered: raises again, with no request
    asked = len(client.session.calls)
    with pytest.raises(lfc.LastFMKeyRejected):
        client.get_artist_info('Low')
    assert len(client.session.calls) == asked


def test_the_memory_runs_out_and_the_key_is_tried_again(monkeypatch):
    client = _client({'artist.getsimilar': REJECTED})
    client.get_similar_artists('Low')
    assert lfc.key_rejection('KEY') is not None
    real = lfc.time.time
    monkeypatch.setattr(lfc.time, 'time', lambda: real() + lfc.KEY_REJECT_TTL + 1)
    assert lfc.key_rejection('KEY') is None
    # a subscriber: it works now, and stays clear
    client.session.answers['artist.getsimilar'] = SIMILAR
    assert [a['name'] for a in client.get_similar_artists('Low')] == ['Portishead']
    assert lfc.lastfm_usable('KEY')


def test_a_new_key_starts_clean(monkeypatch):
    client = _client({'artist.getsimilar': REJECTED}, key='OLD')
    client.get_similar_artists('Low')
    assert not lfc.lastfm_usable('OLD')
    assert lfc.lastfm_usable('NEW')

    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: 'OLD' if key == 'lastfm.api_key' else default)
    assert not lfc.lastfm_usable()
    assert lfc.lastfm_key_status()['key_rejected'] is True
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: 'NEW' if key == 'lastfm.api_key' else default)
    assert lfc.lastfm_usable()
    assert lfc.lastfm_key_status() == {'configured': True, 'usable': True, 'key_rejected': False,
                                       'code': None, 'message': '', 'retry_at': None}


def test_the_settings_test_asks_past_the_memory_and_says_pro():
    client = _client({'chart.gettopartists': REJECTED})
    ok, message = client.check_api_key()
    assert ok is False
    assert message == lfc.LASTFM_PRO_MESSAGE
    # remembered, but the test button still asks: a new subscriber sees it work
    client.session.answers['chart.gettopartists'] = _Resp({'artists': {'artist': []}})
    ok, message = client.check_api_key()
    assert ok is True
    assert lfc.key_rejection('KEY') is None


def test_the_settings_test_never_shows_the_key():
    client = _client({'chart.gettopartists': _Resp(None, status=500, json_error=True)})
    ok, message = client.check_api_key()
    assert ok is False
    assert 'SECRET' not in message


def test_connection_test_returns_the_pro_message(monkeypatch):
    from core import connection_test

    class _Refused(lfc.LastFMClient):
        def __init__(self, api_key='', api_secret='', session_key=''):
            super().__init__(api_key=api_key)
            self.session = _Session({'chart.gettopartists': REJECTED})

    monkeypatch.setattr(lfc, 'LastFMClient', _Refused)
    ok, message = connection_test.run_service_test('lastfm', {'api_key': 'KEY'})
    assert ok is False and message == lfc.LASTFM_PRO_MESSAGE


def test_a_refused_scrobble_is_remembered_and_the_next_one_posts_nothing():
    client = _client({'__post__': _Resp({'error': 10, 'message': 'Invalid API key'})})
    assert client.scrobble_tracks([{'artist': 'a', 'track': 't', 'timestamp': 1}]) is False
    assert lfc.key_rejection('KEY') is not None
    asked = len(client.session.calls)
    assert client.scrobble_tracks([{'artist': 'a', 'track': 't', 'timestamp': 1}]) is False
    assert len(client.session.calls) == asked


# ── cache ──

def test_a_cached_answer_is_served_without_asking_again():
    client = _client({'artist.getsimilar': SIMILAR})
    assert client.get_similar_artists('Low')[0]['name'] == 'Portishead'
    asked = len(client.session.calls)
    # same artist, other spelling: one row
    assert client.get_similar_artists('  LOW ')[0]['name'] == 'Portishead'
    assert len(client.session.calls) == asked


def _age_every_row(seconds):
    store = provider_cache._get_store()
    for key, entry in list(store.rows.items()):
        store.rows[key] = provider_cache.Entry(entry.payload, entry.fetched_at - seconds)


def test_a_refused_key_serves_the_stale_answer_instead_of_nothing():
    client = _client({'artist.getsimilar': SIMILAR})
    client.get_similar_artists('Low')
    _age_every_row(lfc.CATALOGUE_TTL + 60)
    client.session.answers['artist.getsimilar'] = REJECTED
    assert [a['name'] for a in client.get_similar_artists('Low')] == ['Portishead']
    assert lfc.key_rejection('KEY') is not None
    # and with the refusal remembered: stale again, no request
    asked = len(client.session.calls)
    assert [a['name'] for a in client.get_similar_artists('Low')] == ['Portishead']
    assert len(client.session.calls) == asked


def test_an_outage_serves_the_stale_answer():
    client = _client({'artist.getsimilar': SIMILAR})
    client.get_similar_artists('Low')
    _age_every_row(lfc.CATALOGUE_TTL + 60)
    client.session.answers['artist.getsimilar'] = _Resp(None, status=503, json_error=True)
    assert [a['name'] for a in client.get_similar_artists('Low')] == ['Portishead']
    assert lfc.key_rejection('KEY') is None


def test_a_users_charts_go_stale_after_hours_not_a_month():
    page = _Resp({'topartists': {'artist': [{'name': 'Low', 'playcount': '9', 'image': []}],
                                 '@attr': {'totalPages': '1'}}})
    client = _client({'user.getTopArtists': page})
    client.get_user_top_artists('me', limit=1)
    asked = len(client.session.calls)
    _age_every_row(lfc.USER_TTL + 60)
    client.get_user_top_artists('me', limit=1)
    assert len(client.session.calls) == asked + 1


def test_the_sqlite_store_round_trips(tmp_path, monkeypatch):
    path = tmp_path / 'cache.db'
    conn = sqlite3.connect(path)
    provider_cache.ensure_schema(conn.cursor())
    conn.commit()
    conn.close()
    store = provider_cache._SqliteStore()
    monkeypatch.setattr(store, '_connect', lambda: sqlite3.connect(path))
    provider_cache.use_store(store)
    provider_cache.store('lastfm', 'artist.getsimilar', {'artist': 'Low'}, {'a': [1, 2]})
    hit = provider_cache.lookup('lastfm', 'artist.getsimilar', {'artist': ' low'})
    assert hit is not None and hit.payload == {'a': [1, 2]} and hit.age < 5


def test_the_music_database_creates_the_cache_table(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                           "AND name='provider_response_cache'").fetchone()
    assert row is not None


# ── fallback order ──

class _Lastfm:
    """a last.fm client stand-in: rows when it works, [] when it does not."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.api_key = 'KEY'
        self.calls = []

    def get_artist_top_tracks(self, name, limit=10):
        self.calls.append('top')
        return self.rows

    def get_similar_tracks(self, artist, track, limit=20):
        self.calls.append('similar_tracks')
        return self.rows

    def get_similar_artists(self, name, limit=10):
        self.calls.append('similar_artists')
        return self.rows

    def get_tag_top_artists(self, tag, limit=30):
        self.calls.append('tag')
        return self.rows


@pytest.fixture
def free(monkeypatch):
    """scripted deezer + listenbrainz answers; records what was asked."""
    asked = []
    deezer = {}
    lb = {}

    def deezer_get(endpoint, params=None):
        asked.append(('deezer', endpoint))
        return deezer.get(endpoint)

    def lb_get(url, params=None, timeout=15):
        asked.append(('listenbrainz', url))
        for key, value in lb.items():
            if key in url:
                return value
        return None

    monkeypatch.setattr(fb, '_deezer_get', deezer_get)
    monkeypatch.setattr(lbp, '_get', lb_get)
    monkeypatch.setattr(lbp, 'artist_mbid', lambda name: 'MBID-1' if lb else None)
    # a connected listenbrainz account: its top recordings need a token
    from core import listenbrainz_auth
    monkeypatch.setattr(listenbrainz_auth, '_candidates', lambda: ['lb-token'])
    return {'asked': asked, 'deezer': deezer, 'lb': lb}


def test_top_tracks_take_lastfm_first_when_it_answers(free):
    free['lb']['top-recordings-for-artist'] = [{'recording_name': 'LB song'}]
    rows, source = fb.artist_top_tracks('Low', lastfm=_Lastfm([{'name': 'Words', 'playcount': 5}]))
    assert source == 'lastfm' and rows[0]['name'] == 'Words'
    assert free['asked'] == []


def test_top_tracks_fall_to_listenbrainz_then_deezer(free):
    free['lb']['top-recordings-for-artist'] = [
        {'recording_name': 'Lullaby', 'total_listen_count': 90, 'total_user_count': 9,
         'recording_mbid': 'r1'},
        {'recording_name': 'lullaby', 'total_listen_count': 40},
        {'recording_name': 'Words', 'total_listen_count': 30},
    ]
    rows, source = fb.artist_top_tracks('Low', lastfm=_Lastfm([]))
    assert source == 'listenbrainz'
    assert [r['name'] for r in rows] == ['Lullaby', 'Words']
    assert rows[0]['playcount'] == 90

    free['lb'].clear()
    free['deezer']['search/artist'] = {'data': [{'id': 7, 'name': 'Low'}]}
    free['deezer']['artist/7/top'] = {'data': [{'title': 'Sunflower'}]}
    rows, source = fb.artist_top_tracks('Low ', lastfm=None)
    assert source == 'deezer'
    assert rows == [{'name': 'Sunflower', 'playcount': 0, 'listeners': 0, 'url': ''}]


def test_top_tracks_with_nothing_anywhere_say_so(free):
    assert fb.artist_top_tracks('Nobody', lastfm=None) == ([], '')


def test_similar_tracks_fall_to_deezer_radio_neighbours_first(free):
    free['deezer']['search/artist'] = {'data': [{'id': 3, 'name': 'Low'}]}
    free['deezer']['artist/3/radio'] = {'data': [
        {'title': 'Words', 'artist': {'name': 'Low'}},       # the seed itself: dropped
        {'title': 'Sunflower', 'artist': {'name': 'Low'}},   # same artist: after neighbours
        {'title': 'Glory Box', 'artist': {'name': 'Portishead'}},
    ]}
    rows, source = fb.similar_tracks('Low', 'Words', lastfm=_Lastfm([]))
    assert source == 'deezer'
    assert [(r['artist'], r['name']) for r in rows] == [('Portishead', 'Glory Box'), ('Low', 'Sunflower')]
    assert rows[0]['match'] > rows[1]['match']

    rows, source = fb.similar_tracks('Low', 'Words', lastfm=_Lastfm([{'name': 'x', 'artist': 'y'}]))
    assert source == 'lastfm'


def test_similar_artists_go_lastfm_listenbrainz_deezer(free):
    rows, source = fb.similar_artists('Low', lastfm=_Lastfm([{'name': 'Codeine'}]))
    assert (source, rows[0]['name']) == ('lastfm', 'Codeine')

    free['lb']['similar-artists'] = [{'name': 'Galaxie 500', 'artist_mbid': 'g', 'score': 50},
                                     {'name': 'Bedhead', 'artist_mbid': 'b', 'score': 25}]
    rows, source = fb.similar_artists('Low', lastfm=None)
    assert source == 'listenbrainz'
    assert [(r['name'], r['match']) for r in rows] == [('Galaxie 500', 1.0), ('Bedhead', 0.5)]

    free['lb'].clear()
    free['deezer']['search/artist'] = {'data': [{'id': 3, 'name': 'Low'}]}
    free['deezer']['artist/3/related'] = {'data': [{'name': 'Duster'}]}
    rows, source = fb.similar_artists('Low', lastfm=None)
    assert (source, rows[0]['name']) == ('deezer', 'Duster')


def test_tag_artists_fall_to_deezer_playlists_most_frequent_first(free):
    free['deezer']['search/playlist'] = {'data': [{'id': 1}, {'id': 2}]}
    free['deezer']['playlist/1/tracks'] = {'data': [{'artist': {'name': 'Slowdive'}},
                                                    {'artist': {'name': 'Ride'}},
                                                    {'artist': {'name': 'Slowdive'}}]}
    free['deezer']['playlist/2/tracks'] = {'data': [{'artist': {'name': 'Ride'}},
                                                    {'artist': {'name': 'slowdive'}}]}
    rows, source = fb.tag_top_artists('shoegaze', lastfm=_Lastfm([]))
    assert source == 'deezer'
    assert [r['name'] for r in rows] == ['Slowdive', 'Ride']


def test_fallback_answers_are_cached(free):
    free['deezer']['search/artist'] = {'data': [{'id': 7, 'name': 'Low'}]}
    free['deezer']['artist/7/top'] = {'data': [{'title': 'Sunflower'}]}
    fb.artist_top_tracks('Low', lastfm=None)
    asked = len(free['asked'])
    fb.artist_top_tracks('Low', lastfm=None)
    assert len(free['asked']) == asked


def test_search_tracks_fall_to_deezer(free):
    free['deezer']['search/track'] = {'data': [{'title': 'Words', 'artist': {'name': 'Low'},
                                                'album': {'cover_medium': 'http://c.jpg'}}]}
    client = _client({'track.search': REJECTED})
    rows, source = fb.search_tracks('words', lastfm=client)
    assert source == 'deezer'
    assert rows[0] == {'name': 'Words', 'artist': 'Low', 'mbid': '', 'listeners': 0,
                       'image_url': 'http://c.jpg'}


def test_listenbrainz_user_top_artists_parse(free):
    free['lb']['stats/user'] = {'payload': {'artists': [
        {'artist_name': 'Low', 'listen_count': 40, 'artist_mbid': 'm'}]}}
    assert lbp.user_top_artists('me') == [{'name': 'Low', 'playcount': 40, 'mbid': 'm'}]
