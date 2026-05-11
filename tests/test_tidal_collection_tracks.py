"""Pin Tidal "Favorite Tracks" virtual-playlist behavior.

GitHub issue #502 (Yug1900): expose the user's favorited tracks
(My Collection) as a virtual playlist alongside their real playlists,
mirroring how Spotify's "Liked Songs" is treated. The endpoint Tidal
exposes is cursor-paginated (`GET /v2/userCollectionTracks/me/
relationships/items?include=items`) and the response only carries
track-level attributes — artist + album NAMES need a second pass via
the existing `_get_tracks_batch` hydration helper.

These tests pin:
- ID enumeration via the cursor chain (single page, multi-page,
  short-circuit on `max_ids`)
- Auth + permission failure paths (no token, 401/403 from
  `collection.read` scope missing)
- Hydration delegates to `_get_tracks_batch` (no duplication of
  the JSON:API artist/album parse)
- `get_playlist("tidal-favorites")` dispatches to the virtual
  path (so every existing playlist-by-id consumer — mirror refresh,
  discovery, detail endpoint — gets My Collection support for free)
- Count helper sums IDs across pages without hydrating
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.tidal_client import Track, Playlist, TidalClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResp:
    """Minimal `requests.Response` stand-in — only the fields the
    collection-fetch path reads."""

    def __init__(self, status_code: int = 200, json_body=None, text: str = ""):
        self.status_code = status_code
        self._body = json_body if json_body is not None else {}
        self.text = text or str(self._body)

    def json(self):
        return self._body


def _make_authed_client():
    """Build a minimal TidalClient with the auth-related state every
    collection method checks. Avoids touching disk / network in
    `__init__`."""
    client = TidalClient.__new__(TidalClient)
    client.access_token = "fake-token"
    client.token_expires_at = 9_999_999_999
    client.base_url = "https://openapi.tidal.com/v2"
    client.alt_base_url = "https://api.tidal.com/v1"
    return client


# Two-page collection response that exercises the cursor chain.
_PAGE_ONE = {
    'data': [
        {'id': '1001', 'type': 'tracks'},
        {'id': '1002', 'type': 'tracks'},
        {'id': '1003', 'type': 'tracks'},
    ],
    'links': {
        'next': '/userCollectionTracks/me/relationships/items?cursor=ABC',
    },
}
_PAGE_TWO = {
    'data': [
        {'id': '1004', 'type': 'tracks'},
        {'id': '1005', 'type': 'tracks'},
    ],
    'links': {},  # no `next` — end of cursor chain
}


# ---------------------------------------------------------------------------
# _iter_collection_track_ids
# ---------------------------------------------------------------------------


class TestIterCollectionTrackIds:
    def test_walks_full_cursor_chain(self):
        """Both pages enumerated, IDs preserved in cursor order."""
        client = _make_authed_client()

        responses = iter([_FakeResp(200, _PAGE_ONE), _FakeResp(200, _PAGE_TWO)])
        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', side_effect=lambda *a, **kw: next(responses)), \
             patch('core.tidal_client.time.sleep'):
            ids = client._iter_collection_track_ids()

        assert ids == ['1001', '1002', '1003', '1004', '1005']

    def test_max_ids_short_circuits_mid_page(self):
        """`max_ids` cap stops enumeration without fetching the next
        page — important for the count-with-cap callers we may add
        later. Cap of 2 returns only the first two IDs."""
        client = _make_authed_client()

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(200, _PAGE_ONE)), \
             patch('core.tidal_client.time.sleep'):
            ids = client._iter_collection_track_ids(max_ids=2)

        assert ids == ['1001', '1002']

    def test_max_ids_short_circuits_at_page_boundary(self):
        """Cap exactly equal to one page's worth — we should NOT make
        the second request even though the cursor chain says there is
        a next page."""
        client = _make_authed_client()
        call_count = {'n': 0}

        def fake_get(*args, **kwargs):
            call_count['n'] += 1
            return _FakeResp(200, _PAGE_ONE)

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', side_effect=fake_get), \
             patch('core.tidal_client.time.sleep'):
            ids = client._iter_collection_track_ids(max_ids=3)

        assert ids == ['1001', '1002', '1003']
        assert call_count['n'] == 1, "Should not have fetched the second cursor page"

    def test_no_token_returns_empty_without_request(self):
        """Auth precheck failure short-circuits before any HTTP."""
        client = _make_authed_client()

        with patch.object(client, '_ensure_valid_token', return_value=False), \
             patch('core.tidal_client.requests.get') as mock_get:
            ids = client._iter_collection_track_ids()

        assert ids == []
        assert not mock_get.called

    def test_401_response_breaks_loop(self):
        """Tokens predating the `collection.read` scope expansion will
        return 401. We log + bail rather than retry endlessly."""
        client = _make_authed_client()

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(401, text="unauthorized")), \
             patch('core.tidal_client.time.sleep'):
            ids = client._iter_collection_track_ids()

        assert ids == []

    def test_403_response_breaks_loop(self):
        """Same defensive bail for 403 (forbidden — scope or product
        tier issue)."""
        client = _make_authed_client()

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(403, text="forbidden")), \
             patch('core.tidal_client.time.sleep'):
            ids = client._iter_collection_track_ids()

        assert ids == []

    def test_401_sets_needs_reconnect_flag(self):
        """The single most common 'why is my collection empty' cause:
        existing token predates the `collection.read` scope. Listing
        endpoint reads `collection_needs_reconnect()` and surfaces a
        user-actionable hint instead of silently hiding the row."""
        client = _make_authed_client()

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(401)), \
             patch('core.tidal_client.time.sleep'):
            client._iter_collection_track_ids()

        assert client.collection_needs_reconnect() is True

    def test_403_sets_needs_reconnect_flag(self):
        """403 = scope/product-tier issue — same surface treatment as 401."""
        client = _make_authed_client()

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(403)), \
             patch('core.tidal_client.time.sleep'):
            client._iter_collection_track_ids()

        assert client.collection_needs_reconnect() is True

    def test_successful_walk_clears_stale_reconnect_flag(self):
        """User reconnects → next iter call MUST clear the prior
        flag. Otherwise the listing endpoint keeps showing the
        reconnect hint forever even after the scope is granted."""
        client = _make_authed_client()
        client._collection_needs_reconnect = True  # Simulate stale flag

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(200, _PAGE_TWO)), \
             patch('core.tidal_client.time.sleep'):
            client._iter_collection_track_ids()

        assert client.collection_needs_reconnect() is False

    def test_500_does_not_set_reconnect_flag(self):
        """Server-side errors (5xx, network timeout) are NOT a scope
        problem — must NOT poison the flag. User shouldn't be told
        to reconnect just because Tidal had a hiccup."""
        client = _make_authed_client()

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(500, text="server error")), \
             patch('core.tidal_client.time.sleep'):
            client._iter_collection_track_ids()

        assert client.collection_needs_reconnect() is False

    def test_skips_non_tracks_data_entries(self):
        """The endpoint may surface non-track relationship entries on
        future schema additions — we only collect `type == 'tracks'`
        IDs so a forward-compatible response shape doesn't poison the
        ID list with unrelated resources."""
        client = _make_authed_client()
        weird_page = {
            'data': [
                {'id': '999', 'type': 'tracks'},
                {'id': 'pl-1', 'type': 'playlists'},  # ignored
                {'id': '1000', 'type': 'tracks'},
            ],
            'links': {},
        }

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(200, weird_page)), \
             patch('core.tidal_client.time.sleep'):
            ids = client._iter_collection_track_ids()

        assert ids == ['999', '1000']

    def test_empty_data_on_first_page_returns_empty(self):
        """Empty collection — clean empty list, no errors."""
        client = _make_authed_client()
        empty = {'data': [], 'links': {}}

        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', return_value=_FakeResp(200, empty)), \
             patch('core.tidal_client.time.sleep'):
            ids = client._iter_collection_track_ids()

        assert ids == []


# ---------------------------------------------------------------------------
# get_collection_tracks_count
# ---------------------------------------------------------------------------


class TestGetCollectionTracksCount:
    def test_returns_total_across_pages(self):
        """Count = sum of IDs across the full cursor chain."""
        client = _make_authed_client()

        responses = iter([_FakeResp(200, _PAGE_ONE), _FakeResp(200, _PAGE_TWO)])
        with patch.object(client, '_ensure_valid_token', return_value=True), \
             patch('core.tidal_client.requests.get', side_effect=lambda *a, **kw: next(responses)), \
             patch('core.tidal_client.time.sleep'):
            assert client.get_collection_tracks_count() == 5

    def test_returns_zero_on_failure(self):
        """Wrapping handler swallows exceptions — caller treats any
        failure as 'no collection tracks' rather than propagating."""
        client = _make_authed_client()

        with patch.object(client, '_iter_collection_track_ids', side_effect=RuntimeError("boom")):
            assert client.get_collection_tracks_count() == 0

    def test_returns_zero_when_unauthenticated(self):
        client = _make_authed_client()
        with patch.object(client, '_ensure_valid_token', return_value=False):
            assert client.get_collection_tracks_count() == 0


# ---------------------------------------------------------------------------
# get_collection_tracks
# ---------------------------------------------------------------------------


class TestGetCollectionTracks:
    def test_hydrates_via_existing_batch_helper(self):
        """Hydration MUST delegate to `_get_tracks_batch` rather than
        reimplement the JSON:API artist/album parse — that's the
        existing battle-tested path. This test verifies the dispatch
        + that the hydrated tracks come back in the same order the
        IDs were enumerated."""
        client = _make_authed_client()

        ordered_ids = ['1001', '1002', '1003']
        fake_tracks = [
            Track(id='1001', name='Times Like These', artists=['Foo Fighters'], album='One by One'),
            Track(id='1002', name='Innerbloom', artists=['RÜFÜS DU SOL'], album='Bloom'),
            Track(id='1003', name='Set Fire to the Rain', artists=['Adele'], album='21'),
        ]

        captured_batches = []

        def fake_batch(ids):
            captured_batches.append(list(ids))
            id_to_track = {t.id: t for t in fake_tracks}
            return [id_to_track[i] for i in ids if i in id_to_track]

        with patch.object(client, '_iter_collection_track_ids', return_value=ordered_ids), \
             patch.object(client, '_get_tracks_batch', side_effect=fake_batch):
            result = client.get_collection_tracks()

        assert [t.id for t in result] == ['1001', '1002', '1003']
        assert [t.name for t in result] == ['Times Like These', 'Innerbloom', 'Set Fire to the Rain']
        # First (and only) batch should contain all three IDs since
        # default _COLLECTION_BATCH_SIZE is well above 3.
        assert captured_batches == [['1001', '1002', '1003']]

    def test_chunks_into_batch_size(self):
        """Pin the batching: 41 IDs at batch size 20 → three batches
        of 20 / 20 / 1. The Tidal `filter[id]` cap is 20 so we can't
        send everything in one request."""
        client = _make_authed_client()

        ids = [str(1000 + i) for i in range(41)]
        captured_batches = []

        def fake_batch(batch):
            captured_batches.append(list(batch))
            return [Track(id=tid, name=f'Track {tid}', artists=['A'], album='Alb') for tid in batch]

        with patch.object(client, '_iter_collection_track_ids', return_value=ids), \
             patch.object(client, '_get_tracks_batch', side_effect=fake_batch):
            result = client.get_collection_tracks()

        assert len(result) == 41
        assert [len(b) for b in captured_batches] == [20, 20, 1]

    def test_partial_batch_failure_continues(self):
        """One failed batch shouldn't abort the whole fetch — the rest
        of the collection should still come back. Defensive against
        transient Tidal errors mid-walk."""
        client = _make_authed_client()
        ids = ['1001', '1002', '1003']

        def fake_batch(batch):
            if batch == ['1002']:  # won't actually hit since batch_size > 1, but illustrate
                raise RuntimeError("transient")
            return [Track(id=tid, name=f'T{tid}', artists=['A'], album='Alb') for tid in batch]

        with patch.object(client, '_iter_collection_track_ids', return_value=ids), \
             patch.object(client, '_get_tracks_batch', side_effect=lambda b: (_ for _ in ()).throw(RuntimeError("transient")) if b == ids else []):
            result = client.get_collection_tracks()

        # All batches failed → empty result, but no exception bubbled
        assert result == []

    def test_no_ids_returns_empty_without_hydrating(self):
        """Empty collection short-circuits before any batch call."""
        client = _make_authed_client()

        with patch.object(client, '_iter_collection_track_ids', return_value=[]), \
             patch.object(client, '_get_tracks_batch') as mock_batch:
            result = client.get_collection_tracks()

        assert result == []
        assert not mock_batch.called

    def test_limit_passed_through_to_iter(self):
        """`limit` arg caps the ID walk so we don't hydrate everything
        when the caller only wants a slice."""
        client = _make_authed_client()
        captured_max = {'value': None}

        def fake_iter(max_ids=None):
            captured_max['value'] = max_ids
            return ['1001', '1002']

        with patch.object(client, '_iter_collection_track_ids', side_effect=fake_iter), \
             patch.object(client, '_get_tracks_batch', return_value=[]):
            client.get_collection_tracks(limit=50)

        assert captured_max['value'] == 50


# ---------------------------------------------------------------------------
# get_playlist virtual-id dispatch
# ---------------------------------------------------------------------------


class TestGetPlaylistVirtualId:
    def test_my_collection_id_returns_virtual_playlist(self):
        """Pin the dispatch — `get_playlist("tidal-favorites")`
        must NOT hit the real /playlists/<id> endpoint and must NOT
        require token validation (the collection methods do their own).
        It returns a synthetic Playlist with the hydrated collection
        tracks, so every existing call site (mirror refresh @ line
        1192, discovery start @ line 20835, detail endpoint @ line
        20725) gets My Collection support without per-site changes."""
        client = _make_authed_client()
        fake_collection = [
            Track(id='1001', name='Times Like These', artists=['Foo Fighters']),
            Track(id='1002', name='Innerbloom', artists=['RÜFÜS DU SOL']),
        ]

        with patch.object(client, 'get_collection_tracks', return_value=fake_collection), \
             patch.object(client, '_ensure_valid_token') as mock_token, \
             patch('core.tidal_client.requests.get') as mock_get:
            playlist = client.get_playlist("tidal-favorites")

        assert isinstance(playlist, Playlist)
        assert playlist.id == "tidal-favorites"
        assert playlist.name == "Favorite Tracks"
        assert playlist.description == "Your favorited tracks on Tidal"
        assert len(playlist.tracks) == 2
        assert playlist.tracks[0].id == '1001'
        # Virtual path should NOT touch the real /playlists/<id>
        # endpoint OR the auth precheck (get_collection_tracks
        # handles its own auth gate downstream).
        assert not mock_get.called
        assert not mock_token.called

    def test_real_playlist_id_falls_through_to_normal_path(self):
        """Sanity: a real playlist ID must NOT route to the virtual
        handler. Token check + HTTP request still happen."""
        client = _make_authed_client()
        client.session = SimpleNamespace(
            get=lambda *a, **kw: _FakeResp(404, text="not found"),
            headers={},
        )

        with patch.object(client, 'get_collection_tracks') as mock_collection, \
             patch.object(client, '_ensure_valid_token', return_value=True):
            # 404 from the fake session → returns None, but more
            # importantly the virtual-handler MUST NOT have been called.
            client.get_playlist("real-playlist-uuid")

        assert not mock_collection.called
