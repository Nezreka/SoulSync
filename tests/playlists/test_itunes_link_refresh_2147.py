"""#2147: Apple Music (itunes_link) mirrors never refreshed.

the registry's itunes parser was a stub returning None, and the refresh passed
the url hash (source_playlist_id) instead of the link kept in description, so
every refresh failed with "no tracks returned from source".
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.playlists.itunes_link import fetch_itunes_link
from core.playlists.source_refs import (
    describe_mirrored_source_ref,
    normalize_mirrored_source_ref,
    require_refresh_url,
)
from core.playlists.sources.bootstrap import build_playlist_source_registry
from tests.automation.test_handlers_playlist import _build_deps, _StubDB

ALBUM_URL = 'https://music.apple.com/us/album/ark/1081180372'
ALBUM_HASH = hashlib.md5(b'itunes:album:1081180372').hexdigest()[:12]


class _Itunes:
    def __init__(self, album=None):
        self.album = album
        self.asked = []

    def get_album(self, album_id, include_tracks=True):
        self.asked.append(album_id)
        return self.album


def _album(*names):
    return {
        'id': '1081180372', 'name': 'Ark', 'artists': [{'name': 'Star Party'}],
        'images': [{'url': 'http://img'}],
        'tracks': {'items': [
            {'id': str(i + 1), 'name': name, 'artists': ['Star Party'], 'duration_ms': 1000}
            for i, name in enumerate(names)]},
    }


def test_album_link_parses_to_the_same_hash_the_mirror_stores():
    client = _Itunes(_album('Ark', 'Comet'))
    data = fetch_itunes_link(ALBUM_URL, lambda: client)
    assert client.asked == ['1081180372']
    assert data['url_hash'] == ALBUM_HASH
    assert data['url'] == ALBUM_URL
    assert [t['name'] for t in data['tracks']] == ['Ark', 'Comet']


def test_bad_link_and_missing_album_raise_with_a_reason():
    with pytest.raises(ValueError):
        fetch_itunes_link('https://example.com/nope', lambda: _Itunes())
    with pytest.raises(LookupError):
        fetch_itunes_link(ALBUM_URL, lambda: _Itunes(None))


def test_itunes_link_is_url_backed():
    assert require_refresh_url('itunes_link', ALBUM_URL) == ALBUM_URL
    with pytest.raises(ValueError):
        require_refresh_url('itunes_link', '', 'Ark')
    ref = normalize_mirrored_source_ref('itunes_link', ALBUM_URL)
    assert (ref.source_playlist_id, ref.description) == (ALBUM_HASH, ALBUM_URL)
    view = describe_mirrored_source_ref(
        {'source': 'itunes_link', 'source_playlist_id': ALBUM_HASH, 'description': ALBUM_URL})
    assert (view.source_ref, view.source_ref_kind) == (ALBUM_URL, 'url')


def test_refresh_hands_the_link_to_the_parser_not_the_hash():
    asked = []
    client = _Itunes(_album('Ark', 'Comet'))

    def parser(url):
        asked.append(url)
        return fetch_itunes_link(url, lambda: client)

    registry = build_playlist_source_registry(
        spotify_client_getter=lambda: None, tidal_client_getter=lambda: None,
        qobuz_client_getter=lambda: None, deezer_client_getter=lambda: None,
        itunes_link_parser=parser,
    )
    db = _StubDB(playlists=[{
        'id': 4, 'name': 'Ark', 'source': 'itunes_link', 'source_playlist_id': ALBUM_HASH,
        'description': ALBUM_URL, 'profile_id': 1,
    }])
    deps = _build_deps(get_database=lambda: db, playlist_source_registry=registry)

    result = auto_refresh_mirrored({'playlist_id': '4', 'skip_discovery': True}, deps)

    assert asked == [ALBUM_URL]
    assert result['refreshed'] == '1'
    assert result['errors'] == '0'
    call = db.mirror_calls[0]
    assert call['source_playlist_id'] == ALBUM_HASH
    assert [t['track_name'] for t in call['tracks']] == ['Ark', 'Comet']


def test_failed_refresh_hands_its_reason_to_the_pipeline():
    db = _StubDB(playlists=[{
        'id': 4, 'name': 'Ark', 'source': 'itunes_link', 'source_playlist_id': ALBUM_HASH,
        'description': ALBUM_URL, 'profile_id': 1,
    }])
    # the default registry has no itunes parser, so nothing comes back
    deps = _build_deps(get_database=lambda: db)
    result = auto_refresh_mirrored({'playlist_id': '4', 'skip_discovery': True}, deps)
    assert result['errors'] == '1'
    assert result['_error_messages'] == ['Ark: no tracks returned from source']


def test_the_live_registry_parser_is_not_a_stub():
    # web_server can't be imported here (it starts workers), so pin the seam
    # by its source: the registry parser runs the same parse as the route
    src = (Path(__file__).resolve().parents[2] / 'web_server.py').read_text(encoding='utf-8')
    body = src.split('def _itunes_link_parser_for_registry(url):', 1)[1].split('\n    def ', 1)[0]
    assert 'fetch_itunes_link(url, _get_itunes_client)' in body
    route = (Path(__file__).resolve().parents[2] / 'api/source_playlists.py').read_text(encoding='utf-8')
    assert 'fetch_itunes_link(' in route.split("def parse_itunes_link_endpoint", 1)[1].split('@bp.route', 1)[0]
