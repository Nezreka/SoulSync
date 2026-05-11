"""Pin Tidal favorite albums + artists fetch via V2 user-collection
endpoints.

Discord report: Discover → Your Albums section showed nothing for
Tidal users regardless of how many albums they'd favorited. Audit
found `get_favorite_albums` (and `get_favorite_artists`) called the
deprecated `/v2/favorites?filter[type]=ALBUMS|ARTISTS` endpoint
which returns 404 for personal favorites — that endpoint is scoped
to collections the third-party app created itself, not the user's
app-level favorites. The V1 fallback (`/v1/users/<id>/favorites/...`)
returns 403 for modern OAuth tokens because they carry
`collection.read` instead of the legacy `r_usr` scope.

Fix: rewire to the same V2 user-collection cursor-paginated
endpoints we shipped for tracks (issue #502):
  - `/v2/userCollectionAlbums/me/relationships/items`
  - `/v2/userCollectionArtists/me/relationships/items`

Plus per-resource batch hydration via `/v2/{albums|artists}` with
extended-include semantics (`include=artists,coverArt` for albums,
`include=profileArt` for artists) so artist names + image URLs come
back in a single request per batch instead of N+1 lookups.

These tests pin:
  - Cursor walkers dispatch correct path + type to the generic
    `_iter_collection_resource_ids` helper
  - Batch hydrators parse JSON:API `data[]` + `included[]` into the
    legacy return shape that `database.upsert_liked_album` /
    `upsert_liked_artist` consume — preserves byte-identical wiring
    in `web_server.py`'s discover aggregator
  - Image URL resolution picks largest variant from artwork files[]
  - Artist-name resolution falls through to '' when relationships
    are missing (so the upsert path doesn't trip on None)
  - Empty-input + HTTP-error paths return [] without raising
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.tidal_client import TidalClient


def _make_client():
    """Bare TidalClient with auth state primed — no real connection.
    Mirrors the helper in test_tidal_collection_tracks.py."""
    client = TidalClient.__new__(TidalClient)
    client.access_token = "fake-token"
    client.token_expires_at = 9_999_999_999
    client.base_url = "https://openapi.tidal.com/v2"
    client.alt_base_url = "https://api.tidal.com/v1"
    client.session = MagicMock()
    return client


class _FakeResp:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._body = json_body if json_body is not None else {}
        self.text = text or str(self._body)

    def json(self):
        return self._body


# ---------------------------------------------------------------------------
# Cursor-walker dispatch
# ---------------------------------------------------------------------------


class TestCollectionWalkerDispatch:
    def test_album_iter_passes_album_path_and_type(self):
        """`_iter_collection_album_ids` must dispatch to the generic
        walker with the albums path + 'albums' expected_type. If the
        wrapper drifts (e.g. typoed path) the IDs come back empty."""
        client = _make_client()
        with patch.object(client, '_iter_collection_resource_ids',
                          return_value=['111', '222']) as mock_walk:
            ids = client._iter_collection_album_ids(max_ids=50)

        mock_walk.assert_called_once_with(
            'userCollectionAlbums/me/relationships/items', 'albums', 50,
        )
        assert ids == ['111', '222']

    def test_artist_iter_passes_artist_path_and_type(self):
        client = _make_client()
        with patch.object(client, '_iter_collection_resource_ids',
                          return_value=['17275']) as mock_walk:
            ids = client._iter_collection_artist_ids()

        mock_walk.assert_called_once_with(
            'userCollectionArtists/me/relationships/items', 'artists', None,
        )
        assert ids == ['17275']


# ---------------------------------------------------------------------------
# Helper: included map + relationship resolution
# ---------------------------------------------------------------------------


class TestIncludedMaps:
    def test_build_included_maps_groups_by_type(self):
        included = [
            {'id': 'a1', 'type': 'artists', 'attributes': {'name': 'Foo'}},
            {'id': 'art1', 'type': 'artworks', 'attributes': {'files': []}},
            {'id': 'a2', 'type': 'artists', 'attributes': {'name': 'Bar'}},
            {'id': 'unknown1', 'type': 'something_else'},
            {'type': 'artworks'},  # missing id — should be skipped
        ]
        artists, artworks = TidalClient._build_included_maps(included)
        assert set(artists.keys()) == {'a1', 'a2'}
        assert set(artworks.keys()) == {'art1'}
        assert artists['a1']['attributes']['name'] == 'Foo'

    def test_first_artist_name_resolves_from_map(self):
        artists_map = {'a1': {'attributes': {'name': 'Eminem'}}}
        rels = {'artists': {'data': [{'id': 'a1', 'type': 'artists'}]}}
        assert TidalClient._first_artist_name(rels, artists_map) == 'Eminem'

    def test_first_artist_name_empty_when_no_refs(self):
        """Defensive: relationships block missing or empty → '' so
        upsert path doesn't trip on None."""
        assert TidalClient._first_artist_name({}, {}) == ''
        assert TidalClient._first_artist_name(
            {'artists': {'data': []}}, {}
        ) == ''

    def test_first_artist_name_empty_when_unknown_id(self):
        """Artist ref points at an ID not in included map — fall
        through to '' rather than crash."""
        rels = {'artists': {'data': [{'id': 'missing'}]}}
        artists_map = {'other': {'attributes': {'name': 'X'}}}
        assert TidalClient._first_artist_name(rels, artists_map) == ''

    def test_first_artwork_url_picks_first_file(self):
        """Tidal returns artwork files largest-first. Picking files[0]
        gets the highest-resolution variant (typically 1280×1280)."""
        artworks_map = {
            'art1': {'attributes': {'files': [
                {'href': 'https://big.jpg', 'meta': {'width': 1280}},
                {'href': 'https://small.jpg', 'meta': {'width': 320}},
            ]}}
        }
        rel = {'data': [{'id': 'art1', 'type': 'artworks'}]}
        url = TidalClient._first_artwork_url(rel, artworks_map)
        assert url == 'https://big.jpg'

    def test_first_artwork_url_none_when_no_relationship(self):
        assert TidalClient._first_artwork_url({}, {}) is None
        assert TidalClient._first_artwork_url({'data': []}, {}) is None

    def test_first_artwork_url_none_when_no_files(self):
        """Defensive: artwork resource exists but has no files array.
        Return None rather than IndexError."""
        artworks_map = {'art1': {'attributes': {'files': []}}}
        rel = {'data': [{'id': 'art1'}]}
        assert TidalClient._first_artwork_url(rel, artworks_map) is None


# ---------------------------------------------------------------------------
# Batch hydration — albums
# ---------------------------------------------------------------------------


_ALBUM_BATCH_RESPONSE = {
    'data': [
        {
            'id': '141121273',
            'type': 'albums',
            'attributes': {
                'title': 'Mr. Morale & The Big Steppers',
                'releaseDate': '2022-05-13',
                'numberOfItems': 18,
            },
            'relationships': {
                'artists': {'data': [{'id': '5034248', 'type': 'artists'}]},
                'coverArt': {'data': [{'id': 'cover-uuid', 'type': 'artworks'}]},
            },
        },
        {
            'id': '999',
            'type': 'albums',
            'attributes': {'title': 'Album Without Artist or Cover'},
            'relationships': {},
        },
    ],
    'included': [
        {
            'id': '5034248', 'type': 'artists',
            'attributes': {'name': 'Kendrick Lamar'},
        },
        {
            'id': 'cover-uuid', 'type': 'artworks',
            'attributes': {'files': [
                {'href': 'https://resources.tidal.com/images/cover/1280x1280.jpg'},
            ]},
        },
    ],
}


class TestGetAlbumsBatch:
    def test_parses_full_album_response(self):
        client = _make_client()
        client.session.get = MagicMock(
            return_value=_FakeResp(200, _ALBUM_BATCH_RESPONSE)
        )
        results = client._get_albums_batch(['141121273', '999'])

        assert len(results) == 2
        # First album — full attributes resolved from included
        first = results[0]
        assert first['tidal_id'] == '141121273'
        assert first['album_name'] == 'Mr. Morale & The Big Steppers'
        assert first['artist_name'] == 'Kendrick Lamar'
        assert first['image_url'] == 'https://resources.tidal.com/images/cover/1280x1280.jpg'
        assert first['release_date'] == '2022-05-13'
        assert first['total_tracks'] == 18
        # Second album — missing relationships fall through to defaults
        second = results[1]
        assert second['album_name'] == 'Album Without Artist or Cover'
        assert second['artist_name'] == ''
        assert second['image_url'] is None
        assert second['release_date'] == ''
        assert second['total_tracks'] == 0

    def test_empty_input_returns_empty_without_request(self):
        client = _make_client()
        client.session.get = MagicMock()
        results = client._get_albums_batch([])
        assert results == []
        client.session.get.assert_not_called()

    def test_http_error_returns_empty(self):
        client = _make_client()
        client.session.get = MagicMock(
            return_value=_FakeResp(500, text='server error')
        )
        results = client._get_albums_batch(['111'])
        assert results == []

    def test_skips_data_entries_with_wrong_type(self):
        """Forward-compat: response shape might surface non-album
        resources alongside the request — only collect entries whose
        type is 'albums'."""
        client = _make_client()
        client.session.get = MagicMock(return_value=_FakeResp(200, {
            'data': [
                {'id': '1', 'type': 'albums', 'attributes': {'title': 'A'}, 'relationships': {}},
                {'id': '2', 'type': 'tracks', 'attributes': {'title': 'Skip Me'}},
            ],
            'included': [],
        }))
        results = client._get_albums_batch(['1', '2'])
        assert len(results) == 1
        assert results[0]['album_name'] == 'A'

    def test_filter_id_param_is_comma_joined(self):
        """The Tidal API expects `filter[id]=a,b,c` — verify our
        param construction. Drift here would break batching against
        production silently."""
        client = _make_client()
        captured_params = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured_params.update(params or {})
            return _FakeResp(200, {'data': [], 'included': []})

        client.session.get = MagicMock(side_effect=fake_get)
        client._get_albums_batch(['111', '222', '333'])
        assert captured_params['filter[id]'] == '111,222,333'
        assert captured_params['include'] == 'artists,coverArt'


# ---------------------------------------------------------------------------
# Batch hydration — artists
# ---------------------------------------------------------------------------


_ARTIST_BATCH_RESPONSE = {
    'data': [
        {
            'id': '17275',
            'type': 'artists',
            'attributes': {'name': 'Eminem'},
            'relationships': {
                'profileArt': {'data': [{'id': 'profile-uuid', 'type': 'artworks'}]},
            },
        },
    ],
    'included': [
        {
            'id': 'profile-uuid', 'type': 'artworks',
            'attributes': {'files': [
                {'href': 'https://resources.tidal.com/images/profile/750x750.jpg'},
            ]},
        },
    ],
}


class TestGetArtistsBatch:
    def test_parses_full_artist_response(self):
        client = _make_client()
        client.session.get = MagicMock(
            return_value=_FakeResp(200, _ARTIST_BATCH_RESPONSE)
        )
        results = client._get_artists_batch(['17275'])

        assert len(results) == 1
        assert results[0]['tidal_id'] == '17275'
        assert results[0]['name'] == 'Eminem'
        assert results[0]['image_url'] == 'https://resources.tidal.com/images/profile/750x750.jpg'

    def test_empty_input_returns_empty_without_request(self):
        client = _make_client()
        client.session.get = MagicMock()
        assert client._get_artists_batch([]) == []
        client.session.get.assert_not_called()

    def test_http_error_returns_empty(self):
        client = _make_client()
        client.session.get = MagicMock(
            return_value=_FakeResp(404, text='not found')
        )
        assert client._get_artists_batch(['17275']) == []

    def test_filter_id_and_include_params(self):
        client = _make_client()
        captured = {}

        def fake_get(url, params=None, headers=None, timeout=None):
            captured.update(params or {})
            return _FakeResp(200, {'data': [], 'included': []})

        client.session.get = MagicMock(side_effect=fake_get)
        client._get_artists_batch(['17275', '721'])
        assert captured['filter[id]'] == '17275,721'
        assert captured['include'] == 'profileArt'


# ---------------------------------------------------------------------------
# Public methods — orchestrator behavior
# ---------------------------------------------------------------------------


class TestGetFavoriteAlbums:
    def test_walks_then_batches_then_returns(self):
        """End-to-end: iter returns IDs, batch hydrates them, result
        is the concatenation. Backward-compatible shape preserved
        for `database.upsert_liked_album` callers."""
        client = _make_client()
        with patch.object(client, '_iter_collection_album_ids',
                          return_value=['1', '2', '3']) as mock_iter, \
             patch.object(client, '_get_albums_batch',
                          return_value=[
                              {'tidal_id': '1', 'album_name': 'A',
                               'artist_name': 'X', 'image_url': 'u',
                               'release_date': '2020', 'total_tracks': 10},
                              {'tidal_id': '2', 'album_name': 'B',
                               'artist_name': 'Y', 'image_url': None,
                               'release_date': '', 'total_tracks': 0},
                          ]) as mock_batch:
            results = client.get_favorite_albums(limit=100)

        mock_iter.assert_called_once_with(max_ids=100)
        # Single batch call since 3 IDs fit in one BATCH_SIZE chunk (20)
        assert mock_batch.call_count == 1
        assert len(results) == 2
        assert results[0]['tidal_id'] == '1'
        # Verify shape compatibility with upsert_liked_album kwargs
        expected_keys = {'tidal_id', 'album_name', 'artist_name',
                         'image_url', 'release_date', 'total_tracks'}
        assert set(results[0].keys()) == expected_keys

    def test_no_ids_returns_empty_without_batch(self):
        client = _make_client()
        with patch.object(client, '_iter_collection_album_ids', return_value=[]), \
             patch.object(client, '_get_albums_batch') as mock_batch:
            assert client.get_favorite_albums() == []
            mock_batch.assert_not_called()

    def test_chunks_into_batch_size(self):
        """41 IDs at BATCH_SIZE 20 → three batches of 20/20/1.
        Tidal's filter[id] cap is the per-request limit; orchestrator
        must respect it."""
        client = _make_client()
        ids = [str(i) for i in range(41)]
        captured_batches = []

        def fake_batch(batch):
            captured_batches.append(list(batch))
            return [{'tidal_id': b, 'album_name': f'A{b}', 'artist_name': '',
                     'image_url': None, 'release_date': '', 'total_tracks': 0}
                    for b in batch]

        with patch.object(client, '_iter_collection_album_ids', return_value=ids), \
             patch.object(client, '_get_albums_batch', side_effect=fake_batch):
            results = client.get_favorite_albums()

        assert len(results) == 41
        assert [len(b) for b in captured_batches] == [20, 20, 1]


class TestGetFavoriteArtists:
    def test_walks_then_batches(self):
        client = _make_client()
        with patch.object(client, '_iter_collection_artist_ids',
                          return_value=['17275']) as mock_iter, \
             patch.object(client, '_get_artists_batch',
                          return_value=[{'tidal_id': '17275', 'name': 'Eminem',
                                         'image_url': 'https://eminem.jpg'}]) as mock_batch:
            results = client.get_favorite_artists(limit=200)

        mock_iter.assert_called_once_with(max_ids=200)
        mock_batch.assert_called_once()
        assert len(results) == 1
        assert results[0]['name'] == 'Eminem'
        # Backward-compat shape — exactly the keys the prior
        # implementation returned
        assert set(results[0].keys()) == {'tidal_id', 'name', 'image_url'}

    def test_no_ids_returns_empty(self):
        client = _make_client()
        with patch.object(client, '_iter_collection_artist_ids', return_value=[]), \
             patch.object(client, '_get_artists_batch') as mock_batch:
            assert client.get_favorite_artists() == []
            mock_batch.assert_not_called()

    def test_swallows_iter_exception_returns_empty(self):
        """Defensive: if the cursor walker blows up mid-page, the
        public method should return [] (no partial corruption of the
        liked-artists table)."""
        client = _make_client()
        with patch.object(client, '_iter_collection_artist_ids',
                          side_effect=RuntimeError('boom')):
            assert client.get_favorite_artists() == []
