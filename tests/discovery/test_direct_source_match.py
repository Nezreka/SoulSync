"""#1566: a mirrored track is identified by its own id when the playlist and
the metadata source are the same service, with no search.

the search can miss a track or pick the wrong version (#1565); the id can't.
anything that doesn't pan out falls back to the normal search.
"""

from __future__ import annotations

import json

from core.deezer_client import DeezerClient
from core.discovery import playlist as dp
from core.discovery.direct_match import direct_source_match
from tests.discovery.test_discovery_playlist import _build_deps, _playlist, _track

DEEZER_RAW = {
    'id': 136340808,
    'title': "How Far I'll Go",
    'duration': 163,
    'artist': {'id': 1, 'name': "Auli'i Cravalho"},
    'album': {'id': 9, 'title': 'Moana (Original Motion Picture Soundtrack)',
              'cover_xl': 'https://img/xl.jpg', 'release_date': '2016-11-18'},
    'track_position': 3,
    'disk_number': 1,
}

SPOTIFY_RAW = {
    'id': '0flxRcIYyhr9teLBjouwQ8',
    'name': "How Far I'll Go",
    'duration_ms': 163000,
    'artists': [{'name': "Auli'i Cravalho"}],
    'album': {'name': 'Moana', 'images': [], 'release_date': '2016-11-18',
              'album_type': 'album', 'total_tracks': 40},
    'explicit': False,
}


class _Deezer(DeezerClient):
    def __init__(self, raw=DEEZER_RAW):
        self.raw = raw
        self.lookups = []
        self.search_calls = []

    def get_track_raw(self, track_id):
        self.lookups.append(track_id)
        return self.raw

    def search_tracks(self, query='', limit=20, **kw):
        self.search_calls.append(query)
        return []


class _Spotify:
    def __init__(self, details=None):
        self.details = details if details is not None else {'raw_data': SPOTIFY_RAW}
        self.lookups = []
        self.search_calls = []

    def is_spotify_authenticated(self):
        return True

    def get_track_details(self, track_id, allow_fallback=True):
        self.lookups.append((track_id, allow_fallback))
        return self.details

    def search_tracks(self, query, limit=10):
        self.search_calls.append(query)
        return []


# ── the helper ───────────────────────────────────────────────────────────────

def test_deezer_playlist_with_deezer_source_uses_the_id():
    client = _Deezer()
    match = direct_source_match('deezer', 'deezer', False, '136340808', fallback_client=client)
    assert match.id == '136340808'
    assert match.name == "How Far I'll Go"
    assert client.lookups == [136340808]


def test_spotify_playlist_with_spotify_source_uses_the_id():
    client = _Spotify()
    match = direct_source_match('spotify_public', 'spotify', True, '0flxRcIYyhr9teLBjouwQ8',
                                spotify_client=client)
    assert match.id == '0flxRcIYyhr9teLBjouwQ8'
    # a spotify id is never looked up in another catalogue
    assert client.lookups == [('0flxRcIYyhr9teLBjouwQ8', False)]


def test_different_services_never_use_the_id():
    deezer, spotify = _Deezer(), _Spotify()
    assert direct_source_match('spotify', 'deezer', False, '123', spotify, deezer) is None
    assert direct_source_match('deezer', 'spotify', True, '123', spotify, deezer) is None
    assert direct_source_match('tidal', 'deezer', False, '123', spotify, deezer) is None
    assert deezer.lookups == [] and spotify.lookups == []


def test_unusable_ids_and_failed_lookups_fall_back():
    assert direct_source_match('deezer', 'deezer', False, '', fallback_client=_Deezer()) is None
    assert direct_source_match('deezer', 'deezer', False, 'file:3d0e2993',
                               fallback_client=_Deezer()) is None
    assert direct_source_match('deezer', 'deezer', False, '1', fallback_client=_Deezer(raw=None)) is None
    assert direct_source_match('spotify', 'spotify', True, 'x',
                               spotify_client=_Spotify(details={})) is None

    class _Boom(_Deezer):
        def get_track_raw(self, track_id):
            raise RuntimeError('deezer down')

    assert direct_source_match('deezer', 'deezer', False, '1', fallback_client=_Boom()) is None


# ── through the real identification worker ───────────────────────────────────

def _written(deps):
    (_tid, extra), = deps._db.extra_data_writes
    return extra if isinstance(extra, dict) else json.loads(extra)


def test_identification_stores_the_deezer_track_without_searching():
    deezer = _Deezer()
    track = dict(_track(track_id=7, name="How Far I'll Go", artist="Auli'i Cravalho"),
                 source_track_id='136340808')
    deps = _build_deps(tracks_by_playlist={'p1': [track]}, spotify_auth=False,
                       discovery_source='deezer', fallback_source='deezer')
    deps.get_metadata_fallback_client = lambda: deezer

    dp.run_playlist_discovery_worker([_playlist('p1', source='deezer')], deps=deps)

    extra = _written(deps)
    assert extra['matched_data']['id'] == '136340808'
    assert extra['confidence'] == 1.0
    assert deezer.search_calls == []


def test_identification_stores_the_spotify_track_without_searching():
    spotify = _Spotify()
    track = dict(_track(track_id=7, name="How Far I'll Go", artist="Auli'i Cravalho"),
                 source_track_id='0flxRcIYyhr9teLBjouwQ8')
    deps = _build_deps(tracks_by_playlist={'p1': [track]}, discovery_source='spotify')
    deps.spotify_client = spotify

    dp.run_playlist_discovery_worker([_playlist('p1', source='spotify')], deps=deps)

    assert _written(deps)['matched_data']['id'] == '0flxRcIYyhr9teLBjouwQ8'
    assert spotify.search_calls == []


def test_a_failed_lookup_still_searches():
    deezer = _Deezer(raw=None)
    track = dict(_track(track_id=7), source_track_id='136340808')
    deps = _build_deps(tracks_by_playlist={'p1': [track]}, spotify_auth=False,
                       discovery_source='deezer', fallback_source='deezer')
    deps.get_metadata_fallback_client = lambda: deezer

    dp.run_playlist_discovery_worker([_playlist('p1', source='deezer')], deps=deps)

    assert deezer.search_calls   # the old path ran
