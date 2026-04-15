import sys
import types
from types import SimpleNamespace

# Stub optional Spotify dependency so metadata_service can import in tests.
if 'spotipy' not in sys.modules:
    spotipy = types.ModuleType('spotipy')
    oauth2 = types.ModuleType('spotipy.oauth2')

    class _DummySpotify:
        pass

    class _DummyOAuth:
        pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules['spotipy'] = spotipy
    sys.modules['spotipy.oauth2'] = oauth2

if 'config.settings' not in sys.modules:
    config_mod = types.ModuleType('config')
    settings_mod = types.ModuleType('config.settings')

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

    settings_mod.config_manager = _DummyConfigManager()
    config_mod.settings = settings_mod
    sys.modules['config'] = config_mod
    sys.modules['config.settings'] = settings_mod

from core.repair_jobs import library_reorganize as lr


class _FakeSearchClient:
    def __init__(self, source_name, year=None):
        self.source_name = source_name
        self.year = year
        self.calls = []

    def search_albums(self, query, limit=3):
        self.calls.append((query, limit))
        if self.year is None:
            return []
        return [SimpleNamespace(release_date=f"{self.year}-01-01")]


def test_lookup_years_prefers_primary_source(monkeypatch):
    deezer_client = _FakeSearchClient('deezer', '2022')
    spotify_client = _FakeSearchClient('spotify', '1999')

    monkeypatch.setattr(lr, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(lr, 'get_source_priority', lambda primary: [primary, 'itunes', 'spotify'])
    monkeypatch.setattr(
        lr,
        'get_client_for_source',
        lambda source: {'deezer': deezer_client, 'spotify': spotify_client}.get(source),
    )

    job = lr.LibraryReorganizeJob()
    result = job._lookup_years_from_api(SimpleNamespace(report_progress=None, check_stop=lambda: False, sleep_or_stop=lambda *_: False), {('Artist', 'Album')})

    assert result == {('artist', 'album'): '2022'}
    assert deezer_client.calls == [('Artist Album', 3)]
    assert spotify_client.calls == []


def test_lookup_years_falls_through_to_later_source(monkeypatch):
    deezer_client = _FakeSearchClient('deezer', None)
    spotify_client = _FakeSearchClient('spotify', '1999')

    monkeypatch.setattr(lr, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(lr, 'get_source_priority', lambda primary: [primary, 'itunes', 'spotify'])
    monkeypatch.setattr(
        lr,
        'get_client_for_source',
        lambda source: {'deezer': deezer_client, 'spotify': spotify_client}.get(source),
    )

    job = lr.LibraryReorganizeJob()
    result = job._lookup_years_from_api(
        SimpleNamespace(report_progress=None, check_stop=lambda: False, sleep_or_stop=lambda *_: False),
        {('Artist', 'Album')},
    )

    assert result == {('artist', 'album'): '1999'}
    assert deezer_client.calls == [('Artist Album', 3)]
    assert spotify_client.calls == [('Artist Album', 3)]


def test_lookup_years_rechecks_client_availability_per_album(monkeypatch):
    availability = {'spotify': True}

    class _SpotifyClient(_FakeSearchClient):
        def search_albums(self, query, limit=3):
            self.calls.append((query, limit))
            availability['spotify'] = False
            return []

    spotify_client = _SpotifyClient('spotify', None)
    itunes_client = _FakeSearchClient('itunes', '2002')
    helper_calls = []

    def fake_get_client_for_source(source):
        helper_calls.append(source)
        if source == 'spotify' and not availability['spotify']:
            return None
        return {'spotify': spotify_client, 'itunes': itunes_client}.get(source)

    monkeypatch.setattr(lr, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(lr, 'get_source_priority', lambda primary: [primary, 'itunes'])
    monkeypatch.setattr(lr, 'get_client_for_source', fake_get_client_for_source)

    job = lr.LibraryReorganizeJob()
    result = job._lookup_years_from_api(
        SimpleNamespace(report_progress=None, check_stop=lambda: False, sleep_or_stop=lambda *_: False),
        {('Artist A', 'Album A'), ('Artist B', 'Album B')},
    )

    assert result == {('artist a', 'album a'): '2002', ('artist b', 'album b'): '2002'}
    assert helper_calls.count('spotify') == 2
    assert helper_calls.count('itunes') == 2
    assert len(spotify_client.calls) == 1
    assert spotify_client.calls[0] in [('Artist A Album A', 3), ('Artist B Album B', 3)]
    assert set(itunes_client.calls) == {('Artist A Album A', 3), ('Artist B Album B', 3)}
