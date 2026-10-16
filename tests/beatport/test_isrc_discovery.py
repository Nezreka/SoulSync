"""beatport tracks are identified by isrc before any search.

every beatport track carries its isrc. a title search for "Nirvana" by Armin
van Buuren ranks whatever version the provider likes; the isrc is the extended
mix itself. these run the real worker and the real lookup with the provider
clients faked at their http edge.
"""

import pytest

from core.deezer_client import DeezerClient
from core.discovery import beatport as worker
from core.discovery.direct_match import direct_source_match
from core.discovery.isrc_match import artists_agree, clean_isrc, isrc_match
from tests.discovery.test_discovery_beatport import _build_deps, _seed_state

_RAW = {
    'id': 3317371351, 'title': 'Nirvana (Extended Mix)', 'duration': 311,
    'artist': {'name': 'Armin van Buuren'},
    'contributors': [{'name': 'Armin van Buuren'}, {'name': 'Funk Tribu'}],
    'album': {'id': 1, 'title': 'Nirvana', 'cover_xl': 'http://x/cover.jpg'},
}


def _deezer(raw=_RAW):
    client = DeezerClient.__new__(DeezerClient)
    client.calls = []

    def api_get(endpoint, params=None, timeout=15, use_token=True, max_retries=2):
        client.calls.append((endpoint, use_token))
        return raw
    client._api_get = api_get
    return client


class _Spotify:
    def __init__(self, items, authed=True, free=False):
        self.items = items
        self.authed = authed
        self.free = free
        self.queries = []
        self.sp = self

    def search(self, q, type, limit):
        self.queries.append(q)
        return {'tracks': {'items': self.items}}

    def is_spotify_authenticated(self):
        return self.authed

    def _free_active(self):
        return self.free


_SPOTIFY_RAW = {
    'id': '4uLU6hMCjMI75M1A2tKUQC', 'name': 'Nirvana - Extended Mix', 'duration_ms': 311379,
    'popularity': 10, 'artists': [{'name': 'Armin van Buuren'}, {'name': 'Funk Tribu'}],
    'album': {'name': 'Nirvana', 'images': [], 'album_type': 'single', 'release_date': '2026-10-09'},
    'external_urls': {}, 'preview_url': None,
}


@pytest.fixture(autouse=True)
def _quiet_cache(monkeypatch):
    import core.metadata.cache as cache_mod

    class _C:
        def store_entity(self, *a):
            pass

        def get_entity(self, *a):
            return None
    monkeypatch.setattr(cache_mod, 'get_metadata_cache', lambda: _C())


def test_clean_isrc():
    assert clean_isrc('nlf712606025') == 'NLF712606025'
    assert clean_isrc('NL-F71-26-06025') == 'NLF712606025'
    assert clean_isrc('') == ''
    assert clean_isrc('beatport_123') == ''
    assert clean_isrc(None) == ''


def test_artists_agree():
    assert artists_agree(['Armin van Buuren'], ['Armin van Buuren', 'Funk Tribu'])
    assert artists_agree(['Armin van Buuren', 'Funk Tribu'], ['Armin van Buuren & Funk Tribu'])
    assert artists_agree(['Tiësto'], ['TIËSTO'])
    assert not artists_agree(['Armin van Buuren'], ['Some Karaoke Band'])
    assert artists_agree([], ['anyone'])


def test_deezer_lookup():
    client = _deezer()
    track = isrc_match('NLF712606025', 'deezer', False, fallback_client=client, artists=['Armin van Buuren'])
    assert track.name == 'Nirvana (Extended Mix)'
    assert client.calls == [('/track/isrc:NLF712606025', False)]


def test_spotify_lookup():
    client = _Spotify([_SPOTIFY_RAW])
    track = isrc_match('NLF712606025', 'spotify', True, spotify_client=client, artists=['Funk Tribu'])
    assert track.id == '4uLU6hMCjMI75M1A2tKUQC'
    assert client.queries == ['isrc:NLF712606025']


def test_spotify_without_the_real_api_does_not_try():
    assert isrc_match('NLF712606025', 'spotify', True, spotify_client=_Spotify([_SPOTIFY_RAW], free=True)) is None
    assert isrc_match('NLF712606025', 'spotify', True, spotify_client=_Spotify([_SPOTIFY_RAW], authed=False)) is None


def test_a_hit_by_someone_else_is_refused():
    client = _deezer(dict(_RAW, artist={'name': 'Karaoke Kings'}, contributors=[]))
    assert isrc_match('NLF712606025', 'deezer', False, fallback_client=client, artists=['Armin van Buuren']) is None


def test_no_lookup_on_sources_without_isrc_search():
    client = _deezer()
    assert isrc_match('NLF712606025', 'itunes', False, fallback_client=client) is None
    assert client.calls == []


def test_errors_and_misses_are_none():
    assert isrc_match('NLF712606025', 'deezer', False, fallback_client=_deezer({'error': {'code': 800}})) is None
    assert isrc_match('NLF712606025', 'deezer', False, fallback_client=_deezer(None)) is None
    assert isrc_match('junk', 'deezer', False, fallback_client=_deezer()) is None

    class _Boom(DeezerClient):
        def __init__(self):
            pass

        def _api_get(self, *a, **k):
            raise RuntimeError('down')
    assert isrc_match('NLF712606025', 'deezer', False, fallback_client=_Boom()) is None


# -- the worker ---------------------------------------------------------------

def _chart_track(**over):
    t = {'id': 'beatport_30700667', 'name': 'Nirvana (Extended Mix)',
         'artists': ['Armin van Buuren', 'Funk Tribu'], 'isrc': 'NLF712606025'}
    t.update(over)
    return t


def test_worker_takes_the_isrc_hit_without_searching():
    states = {}
    _seed_state('genre:trance-main-floor:7:top-10', states, tracks=[_chart_track()])
    deps = _build_deps(states=states, discovery_source='deezer', spotify_auth=False)
    client = _deezer()
    searched = []
    client.search_tracks = lambda q, limit=10: searched.append(q) or []
    deps.get_metadata_fallback_client = lambda: client
    worker.run_beatport_discovery_worker('genre:trance-main-floor:7:top-10', deps)
    assert searched == []
    result = states['genre:trance-main-floor:7:top-10']['discovery_results'][0]
    assert result['status_class'] == 'found'
    assert result['match_method'] == 'isrc'
    assert result['confidence'] == 1.0
    assert result['spotify_data']['name'] == 'Nirvana (Extended Mix)'
    assert result['spotify_data']['album']['name'] == 'Nirvana'
    assert result['source_track'] == {'id': 'beatport_30700667'}


def test_worker_searches_when_the_isrc_disagrees():
    states = {}
    _seed_state('h', states, tracks=[_chart_track()])
    deps = _build_deps(states=states, discovery_source='deezer', spotify_auth=False)
    client = _deezer(dict(_RAW, artist={'name': 'Karaoke Kings'}, contributors=[]))
    searched = []
    client.search_tracks = lambda q, limit=10: searched.append(q) or []
    deps.get_metadata_fallback_client = lambda: client
    worker.run_beatport_discovery_worker('h', deps)
    result = states['h']['discovery_results'][0]
    assert result['match_method'] == 'search'
    assert searched, 'a refused isrc hit must fall back to the search'
    assert result['status_class'] == 'wing-it'


def test_worker_without_isrc_is_unchanged():
    states = {}
    _seed_state('h', states, tracks=[_chart_track(isrc='')])
    deps = _build_deps(states=states, discovery_source='deezer', spotify_auth=False)
    client = _deezer()
    client.search_tracks = lambda q, limit=10: []
    deps.get_metadata_fallback_client = lambda: client
    worker.run_beatport_discovery_worker('h', deps)
    assert client.calls == []
    assert states['h']['discovery_results'][0]['match_method'] == 'search'


def test_worker_isrc_on_spotify_keeps_the_album_from_cache():
    states = {}
    _seed_state('h', states, tracks=[_chart_track()])
    deps = _build_deps(states=states, discovery_source='spotify', spotify_auth=True)
    client = _Spotify([_SPOTIFY_RAW])
    deps.spotify_client = client

    class _Cache:
        def get_entity(self, source, kind, entity_id):
            return _SPOTIFY_RAW if entity_id == _SPOTIFY_RAW['id'] else None
    deps.get_metadata_cache = lambda: _Cache()
    worker.run_beatport_discovery_worker('h', deps)
    result = states['h']['discovery_results'][0]
    assert result['match_method'] == 'isrc'
    assert result['spotify_data']['id'] == '4uLU6hMCjMI75M1A2tKUQC'
    assert result['spotify_data']['album']['name'] == 'Nirvana'


# -- mirrored beatport tracks -------------------------------------------------

def test_mirrored_beatport_track_resolves_through_its_remembered_isrc(monkeypatch):
    import core.beatport.fetch as bp_fetch
    remembered = {'isrc': 'NLF712606025', 'artist': 'Armin van Buuren, Funk Tribu',
                  'artists': [{'name': 'Armin van Buuren'}, {'name': 'Funk Tribu'}]}
    monkeypatch.setattr(bp_fetch, 'cached_track', lambda tid: remembered if tid == 'beatport_30700667' else None)
    client = _deezer()
    track = direct_source_match('beatport', 'deezer', False, 'beatport_30700667', fallback_client=client)
    assert track.name == 'Nirvana (Extended Mix)'
    assert direct_source_match('beatport', 'deezer', False, 'beatport_1', fallback_client=client) is None
