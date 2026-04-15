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

from core.repair_jobs import track_number_repair as tnr


class _FakeSearchClient:
    def __init__(self, search_results=None):
        self.search_results = search_results or []
        self.search_calls = []

    def search_albums(self, query, limit=10):
        self.search_calls.append((query, limit))
        return self.search_results


def _make_context():
    return SimpleNamespace(db=None, mb_client=None)


def test_resolve_album_tracklist_prefers_primary_source_album_id(monkeypatch):
    context = _make_context()
    file_track_data = [('/music/test.flac', '01 - Track.flac', None)]
    cache = {'album_tracks_cache': {}, 'title_similarity': 0.8}
    calls = []

    monkeypatch.setattr(tnr, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(
        tnr,
        '_lookup_album_ids_from_db',
        lambda *_args, **_kwargs: {'spotify': 'sp-album', 'itunes': 'it-album', 'deezer': 'dz-album'},
    )
    monkeypatch.setattr(tnr, '_read_album_id_from_file', lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(tnr, '_read_spotify_track_id_from_file', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tnr, '_read_musicbrainz_album_id_from_file', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tnr, '_read_album_artist_from_file', lambda *_args, **_kwargs: (None, None))

    def fake_get_album_tracks_for_source(source, album_id):
        calls.append((source, album_id))
        return {'items': [{'name': f'{source} track', 'track_number': 1, 'disc_number': 1}]}

    monkeypatch.setattr(tnr, 'get_album_tracks_for_source', fake_get_album_tracks_for_source)

    result = tnr.TrackNumberRepairJob()._resolve_album_tracklist(file_track_data, '/music', context, cache)

    assert result is not None
    assert result[0]['name'] == 'deezer track'
    assert calls == [('deezer', 'dz-album')]


def test_resolve_album_tracklist_uses_source_priority_for_search(monkeypatch):
    context = _make_context()
    file_track_data = [('/music/test.flac', 'Track.flac', None)]
    cache = {'album_tracks_cache': {}, 'title_similarity': 0.8}
    track_calls = []

    discogs_client = _FakeSearchClient([SimpleNamespace(id='dg-album')])
    spotify_client = _FakeSearchClient([SimpleNamespace(id='sp-album')])

    monkeypatch.setattr(tnr, 'get_primary_source', lambda: 'discogs')
    monkeypatch.setattr(tnr, '_lookup_album_ids_from_db', lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tnr, '_read_album_id_from_file', lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(tnr, '_read_spotify_track_id_from_file', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tnr, '_read_musicbrainz_album_id_from_file', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tnr, '_read_album_artist_from_file', lambda *_args, **_kwargs: ('Album Title', 'Artist Name'))
    monkeypatch.setattr(
        tnr,
        'get_client_for_source',
        lambda source: {'discogs': discogs_client, 'spotify': spotify_client}.get(source),
    )

    def fake_get_album_tracks_for_source(source, album_id):
        track_calls.append((source, album_id))
        return {'items': [{'name': f'{source} track', 'track_number': 1, 'disc_number': 1}]}

    monkeypatch.setattr(tnr, 'get_album_tracks_for_source', fake_get_album_tracks_for_source)

    result = tnr.TrackNumberRepairJob()._resolve_album_tracklist(file_track_data, '/music', context, cache)

    assert result is not None
    assert result[0]['name'] == 'discogs track'
    assert discogs_client.search_calls == [('Artist Name Album Title', 5)]
    assert spotify_client.search_calls == []
    assert track_calls == [('discogs', 'dg-album')]
