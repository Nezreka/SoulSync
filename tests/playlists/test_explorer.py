"""Tests for core/playlists/explorer.py — playlist explorer build-tree route."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from core.playlists import explorer as ex


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def get_json(self):
        return self._payload


class _FakeResponse:
    """Captures the streaming generator + headers."""
    def __init__(self, generator, mimetype=None, headers=None):
        self.generator = generator
        self.mimetype = mimetype
        self.headers = headers or {}
        self.body_lines = list(generator)


def _fake_jsonify(payload):
    """Returns the payload dict as-is — the wrapper does the actual jsonify."""
    return payload


@dataclass
class _FakeAlbum:
    id: str
    name: str
    release_date: str = '2024-01-01'
    image_url: str = ''
    total_tracks: int = 10
    album_type: str = 'album'
    artist_ids: list = None


class _FakeArtistMeta:
    def __init__(self, name='Artist', id='a-1'):
        self.name = name
        self.id = id
        self.image_url = 'http://art-img'


class _FakeSpotify:
    def __init__(self, *, authenticated=True, search_results=None, albums=None):
        self._authenticated = authenticated
        self._search_results = search_results or []
        self._albums = albums or []
        self.sp = object()  # pretends to be spotify (skip_cache support)

    def is_spotify_authenticated(self):
        return self._authenticated

    def search_artists(self, name, limit=5):
        return self._search_results

    def get_artist_albums(self, artist_id, album_type='album,single', skip_cache=False):
        return self._albums

    def get_artist(self, artist_id):
        return None


class _FakeCursor:
    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeDB:
    def __init__(self, playlist=None, tracks=None):
        self._playlist = playlist
        self._tracks = tracks or []
        self.marked_explored = False

    def get_mirrored_playlist(self, pid):
        return self._playlist

    def get_mirrored_playlist_tracks(self, pid):
        return self._tracks

    def mark_mirrored_playlist_explored(self, pid):
        self.marked_explored = True

    def _get_connection(self):
        return _FakeConn()


class _FakeCache:
    def __init__(self):
        self.entries = {}

    def get_entity(self, source, kind, key):
        return self.entries.get((source, kind, key))

    def store_entity(self, source, kind, key, value):
        self.entries[(source, kind, key)] = value


def _build_deps(
    *,
    request_payload=None,
    spotify=None,
    db=None,
    discovery_source='spotify',
    fallback_source='itunes',
    cache=None,
):
    deps = ex.PlaylistExplorerDeps(
        request=_FakeRequest(request_payload or {}),
        flask_response=_FakeResponse,
        flask_jsonify=_fake_jsonify,
        spotify_client=spotify or _FakeSpotify(),
        get_database=lambda: db or _FakeDB(),
        get_active_discovery_source=lambda: discovery_source,
        get_metadata_fallback_client=lambda: spotify or _FakeSpotify(),
        get_metadata_fallback_source=lambda: fallback_source,
        get_metadata_cache=lambda: cache or _FakeCache(),
    )
    return deps


# ---------------------------------------------------------------------------
# Validation early exits
# ---------------------------------------------------------------------------

def test_no_data_returns_400():
    """Empty/None request body → 400."""
    deps = _build_deps(request_payload=None)
    deps.request = _FakeRequest(None)
    result = ex.playlist_explorer_build_tree(deps)
    payload, status = result
    assert status == 400
    assert payload == {"success": False, "error": "No data provided"}


def test_missing_playlist_id_returns_400():
    """Payload without playlist_id → 400."""
    deps = _build_deps(request_payload={'mode': 'albums'})
    payload, status = ex.playlist_explorer_build_tree(deps)
    assert status == 400
    assert 'playlist_id' in payload['error']


def test_invalid_mode_returns_400():
    """mode != 'albums'/'discographies' → 400."""
    deps = _build_deps(request_payload={'playlist_id': '1', 'mode': 'invalid'})
    payload, status = ex.playlist_explorer_build_tree(deps)
    assert status == 400
    assert "'albums' or 'discographies'" in payload['error']


def test_playlist_not_found_returns_404():
    """Database returns no playlist for the given ID → 404."""
    db = _FakeDB(playlist=None)
    deps = _build_deps(request_payload={'playlist_id': '99'}, db=db)
    payload, status = ex.playlist_explorer_build_tree(deps)
    assert status == 404


def test_playlist_with_no_tracks_returns_400():
    """Playlist found but has no tracks → 400."""
    db = _FakeDB(playlist={'name': 'P', 'image_url': ''}, tracks=[])
    deps = _build_deps(request_payload={'playlist_id': '1'}, db=db)
    payload, status = ex.playlist_explorer_build_tree(deps)
    assert status == 400


# ---------------------------------------------------------------------------
# Streaming response
# ---------------------------------------------------------------------------

def test_success_returns_streaming_response_with_meta_line():
    """Successful build → Response wrapper with NDJSON generator that starts with 'meta'."""
    db = _FakeDB(
        playlist={'name': 'My Playlist', 'image_url': 'http://img'},
        tracks=[{'track_name': 'T1', 'artist_name': 'Artist One', 'album_name': 'Album X', 'extra_data': None}],
    )
    spotify = _FakeSpotify(
        search_results=[_FakeArtistMeta(name='Artist One', id='a-1')],
        albums=[_FakeAlbum(id='alb-1', name='Album X', release_date='2024')],
    )
    deps = _build_deps(
        request_payload={'playlist_id': '1', 'mode': 'discographies'},
        spotify=spotify,
        db=db,
    )

    response = ex.playlist_explorer_build_tree(deps)

    # _FakeResponse exposes body_lines pre-collected from the generator
    assert response.mimetype == 'application/x-ndjson'
    lines = response.body_lines
    assert len(lines) >= 2

    # First line should be meta
    first = json.loads(lines[0])
    assert first['type'] == 'meta'
    assert first['playlist_name'] == 'My Playlist'
    assert first['source'] == 'spotify'

    # Last line should be 'complete'
    last = json.loads(lines[-1])
    assert last['type'] == 'complete'


def test_marks_playlist_explored_at_end_of_stream():
    """When the streaming generator runs to completion, mark_mirrored_playlist_explored fires."""
    db = _FakeDB(
        playlist={'name': 'P', 'image_url': ''},
        tracks=[{'track_name': 'T', 'artist_name': 'A', 'album_name': 'B', 'extra_data': None}],
    )
    spotify = _FakeSpotify(search_results=[_FakeArtistMeta(name='A')])
    deps = _build_deps(
        request_payload={'playlist_id': '1', 'mode': 'discographies'},
        spotify=spotify,
        db=db,
    )

    ex.playlist_explorer_build_tree(deps)

    assert db.marked_explored is True


# ---------------------------------------------------------------------------
# Discovered-track grouping
# ---------------------------------------------------------------------------

def test_discovered_artist_grouping_uses_matched_data():
    """Tracks with matching-source extra_data → use matched_data['artists'][0]."""
    db = _FakeDB(
        playlist={'name': 'P', 'image_url': ''},
        tracks=[
            {
                'track_name': 'T',
                'artist_name': 'Local Artist Name',  # raw
                'album_name': 'Local Album',
                'extra_data': json.dumps({
                    'discovered': True,
                    'provider': 'spotify',
                    'matched_data': {
                        'artists': [{'name': 'Discovered Artist', 'id': 'sp-aid'}],
                        'album': {'name': 'Discovered Album'},
                    },
                }),
            },
        ],
    )
    spotify = _FakeSpotify(
        search_results=[_FakeArtistMeta(name='Discovered Artist')],
        albums=[_FakeAlbum(id='alb-1', name='Discovered Album', release_date='2024')],
    )
    deps = _build_deps(
        request_payload={'playlist_id': '1', 'mode': 'discographies'},
        spotify=spotify, db=db,
    )

    response = ex.playlist_explorer_build_tree(deps)

    artist_lines = [json.loads(line) for line in response.body_lines if json.loads(line).get('type') == 'artist']
    assert len(artist_lines) == 1
    assert artist_lines[0]['name'] == 'Discovered Artist'
    assert artist_lines[0]['artist_id'] == 'sp-aid'


def test_provider_mismatch_falls_back_to_raw_track_name():
    """If discovered provider != active source, ignore matched_data, use raw artist_name."""
    db = _FakeDB(
        playlist={'name': 'P', 'image_url': ''},
        tracks=[
            {
                'track_name': 'T',
                'artist_name': 'Raw Artist',
                'album_name': 'Raw Album',
                'extra_data': json.dumps({
                    'discovered': True,
                    'provider': 'itunes',  # mismatch
                    'matched_data': {
                        'artists': [{'name': 'iTunes Artist'}],
                    },
                }),
            },
        ],
    )
    # Active source is spotify (default)
    spotify = _FakeSpotify(search_results=[_FakeArtistMeta(name='Raw Artist')])
    deps = _build_deps(
        request_payload={'playlist_id': '1', 'mode': 'discographies'},
        spotify=spotify, db=db,
    )

    response = ex.playlist_explorer_build_tree(deps)

    artist_lines = [json.loads(line) for line in response.body_lines if json.loads(line).get('type') == 'artist']
    assert artist_lines[0]['name'] == 'Raw Artist'  # NOT 'iTunes Artist'
