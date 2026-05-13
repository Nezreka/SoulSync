"""Pin Tidal `get_album_tracks` — fetches every track on an album
with full artist + name + duration metadata hydrated.

Discord report: clicking 'Download All' on the Your Albums section
showed "Queuing..." but never actually queued any Tidal-only albums.
Root cause: `/api/discover/album/<source>/<album_id>` had no `tidal`
branch and tidal_client had no `get_album_tracks` method — the
frontend's trySources fell back to spotify/deezer which returned
None for Tidal-only IDs.

This test suite covers the new tidal_client method:
  - Cursor-paginated walk of `/v2/albums/{id}/relationships/items`
  - Track meta (trackNumber + volumeNumber for multi-disc)
  - Batch hydration via `_get_tracks_batch` for artist/album names
  - Sort by (disc_number, track_number) so the modal renders in
    album order across multi-disc releases
  - Empty / error paths return [] without raising
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.tidal_client import Track, TidalClient


def _make_client():
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
# Single-page album (12 tracks, single disc)
# ---------------------------------------------------------------------------


_SINGLE_PAGE = {
    'data': [
        {'id': '1001', 'type': 'tracks', 'meta': {'volumeNumber': 1, 'trackNumber': 1}},
        {'id': '1002', 'type': 'tracks', 'meta': {'volumeNumber': 1, 'trackNumber': 2}},
        {'id': '1003', 'type': 'tracks', 'meta': {'volumeNumber': 1, 'trackNumber': 3}},
    ],
    'links': {},  # no `next` — single-page album
}


class TestSinglePageAlbum:
    def test_walks_page_and_hydrates(self):
        """Happy path: 3-track album, single page, single disc.
        IDs enumerated → batch hydrated → returned in album order."""
        client = _make_client()
        client.session.get = MagicMock(return_value=_FakeResp(200, _SINGLE_PAGE))
        client._get_tracks_batch = MagicMock(return_value=[
            Track(id='1001', name='Track One', artists=['Artist'], duration_ms=180000),
            Track(id='1002', name='Track Two', artists=['Artist'], duration_ms=200000),
            Track(id='1003', name='Track Three', artists=['Artist'], duration_ms=220000),
        ])

        with patch('core.tidal_client.time.sleep'):
            tracks = client.get_album_tracks('album-1')

        assert [t.id for t in tracks] == ['1001', '1002', '1003']
        assert [t.track_number for t in tracks] == [1, 2, 3]
        # Single disc → all volumeNumber=1
        assert all(t.disc_number == 1 for t in tracks)

    def test_no_token_returns_empty_without_request(self):
        """Auth precheck failure short-circuits."""
        client = _make_client()
        client.session.get = MagicMock()
        with patch.object(client, '_ensure_valid_token', return_value=False):
            assert client.get_album_tracks('album-1') == []
        client.session.get.assert_not_called()

    def test_http_error_returns_empty(self):
        client = _make_client()
        client.session.get = MagicMock(return_value=_FakeResp(404, text='not found'))
        with patch('core.tidal_client.time.sleep'):
            assert client.get_album_tracks('album-1') == []

    def test_429_raises_for_rate_limit_decorator(self):
        """The `rate_limited` decorator looks for '429' in the exception
        message to trigger retry/backoff. Don't swallow rate-limit
        responses — propagate so the decorator can handle them."""
        client = _make_client()
        client.session.get = MagicMock(return_value=_FakeResp(429, text='rate limited'))
        with patch('core.tidal_client.time.sleep'):
            with pytest.raises(Exception, match='429'):
                client.get_album_tracks('album-1')

    def test_skips_non_track_data_entries(self):
        """Forward-compat: schema additions might surface non-track
        types alongside tracks — only collect entries with type='tracks'."""
        client = _make_client()
        client.session.get = MagicMock(return_value=_FakeResp(200, {
            'data': [
                {'id': '1', 'type': 'tracks', 'meta': {'trackNumber': 1, 'volumeNumber': 1}},
                {'id': '99', 'type': 'videos', 'meta': {'trackNumber': 99}},
            ],
            'links': {},
        }))
        client._get_tracks_batch = MagicMock(return_value=[
            Track(id='1', name='Track', artists=['A'], duration_ms=100),
        ])
        with patch('core.tidal_client.time.sleep'):
            tracks = client.get_album_tracks('album-1')
        assert len(tracks) == 1
        assert tracks[0].id == '1'


# ---------------------------------------------------------------------------
# Multi-disc album — sort order matters
# ---------------------------------------------------------------------------


class TestMultiDiscAlbum:
    def test_sorts_by_disc_then_track(self):
        """Reporter's albums could be multi-disc compilations. After
        batch hydration the tracks may not be in album order
        (filter[id] endpoint doesn't guarantee preservation). Verify
        the final list is sorted by (disc, track) so the download
        modal renders disc 1 → 2 in track order each."""
        client = _make_client()
        # Page returns IDs in scrambled order intentionally
        client.session.get = MagicMock(return_value=_FakeResp(200, {
            'data': [
                {'id': 'd1t2', 'type': 'tracks', 'meta': {'volumeNumber': 1, 'trackNumber': 2}},
                {'id': 'd2t1', 'type': 'tracks', 'meta': {'volumeNumber': 2, 'trackNumber': 1}},
                {'id': 'd1t1', 'type': 'tracks', 'meta': {'volumeNumber': 1, 'trackNumber': 1}},
                {'id': 'd2t2', 'type': 'tracks', 'meta': {'volumeNumber': 2, 'trackNumber': 2}},
            ],
            'links': {},
        }))
        client._get_tracks_batch = MagicMock(return_value=[
            # Batch endpoint may not preserve order — return scrambled too
            Track(id='d2t1', name='D2T1', artists=['A'], duration_ms=100),
            Track(id='d1t1', name='D1T1', artists=['A'], duration_ms=100),
            Track(id='d2t2', name='D2T2', artists=['A'], duration_ms=100),
            Track(id='d1t2', name='D1T2', artists=['A'], duration_ms=100),
        ])

        with patch('core.tidal_client.time.sleep'):
            tracks = client.get_album_tracks('album-1')

        # Expect: disc 1 first (tracks 1,2), then disc 2 (tracks 1,2)
        assert [t.id for t in tracks] == ['d1t1', 'd1t2', 'd2t1', 'd2t2']
        assert [(t.disc_number, t.track_number) for t in tracks] == [
            (1, 1), (1, 2), (2, 1), (2, 2),
        ]


# ---------------------------------------------------------------------------
# Multi-page album — cursor walk
# ---------------------------------------------------------------------------


class TestMultiPageAlbum:
    def test_follows_cursor_chain(self):
        """Big album (>20 tracks) — cursor chain must be walked.
        First page returns links.next, second page returns no next."""
        client = _make_client()
        page1 = {
            'data': [
                {'id': '1', 'type': 'tracks', 'meta': {'trackNumber': 1, 'volumeNumber': 1}},
                {'id': '2', 'type': 'tracks', 'meta': {'trackNumber': 2, 'volumeNumber': 1}},
            ],
            'links': {'next': '/albums/x/relationships/items?cursor=ABC'},
        }
        page2 = {
            'data': [
                {'id': '3', 'type': 'tracks', 'meta': {'trackNumber': 3, 'volumeNumber': 1}},
            ],
            'links': {},
        }
        responses = iter([_FakeResp(200, page1), _FakeResp(200, page2)])
        client.session.get = MagicMock(side_effect=lambda *a, **kw: next(responses))
        client._get_tracks_batch = MagicMock(return_value=[
            Track(id='1', name='T1', artists=['A'], duration_ms=100),
            Track(id='2', name='T2', artists=['A'], duration_ms=100),
            Track(id='3', name='T3', artists=['A'], duration_ms=100),
        ])

        with patch('core.tidal_client.time.sleep'):
            tracks = client.get_album_tracks('album-1')

        assert [t.id for t in tracks] == ['1', '2', '3']
        # Two page requests must have happened
        assert client.session.get.call_count == 2

    def test_limit_short_circuits_at_page_boundary(self):
        """`limit` arg caps the walk early — useful for callers that
        only want a preview, not the full tracklist."""
        client = _make_client()
        page1 = {
            'data': [
                {'id': '1', 'type': 'tracks', 'meta': {'trackNumber': 1, 'volumeNumber': 1}},
                {'id': '2', 'type': 'tracks', 'meta': {'trackNumber': 2, 'volumeNumber': 1}},
            ],
            'links': {'next': '/albums/x/relationships/items?cursor=ABC'},
        }
        client.session.get = MagicMock(return_value=_FakeResp(200, page1))
        client._get_tracks_batch = MagicMock(return_value=[
            Track(id='1', name='T1', artists=['A'], duration_ms=100),
        ])

        with patch('core.tidal_client.time.sleep'):
            tracks = client.get_album_tracks('album-1', limit=1)

        # Only one page fetched even though links.next was set
        assert client.session.get.call_count == 1
        assert len(tracks) == 1


# ---------------------------------------------------------------------------
# Batch hydration robustness
# ---------------------------------------------------------------------------


class TestHydrationRobustness:
    def test_hydration_exception_returns_partial_results(self):
        """If one batch fails to hydrate, other batches still return.
        Defensive against transient Tidal errors mid-walk on big albums."""
        client = _make_client()
        # Big single-page album → 21 IDs split into two batches (20 + 1)
        big_page = {
            'data': [
                {'id': str(i), 'type': 'tracks', 'meta': {'trackNumber': i, 'volumeNumber': 1}}
                for i in range(1, 22)
            ],
            'links': {},
        }
        client.session.get = MagicMock(return_value=_FakeResp(200, big_page))

        # First batch succeeds, second raises
        def batch_side_effect(batch_ids):
            if len(batch_ids) == 1:  # The trailing batch
                raise RuntimeError("transient")
            return [
                Track(id=tid, name=f'T{tid}', artists=['A'], duration_ms=100)
                for tid in batch_ids
            ]
        client._get_tracks_batch = MagicMock(side_effect=batch_side_effect)

        with patch('core.tidal_client.time.sleep'):
            tracks = client.get_album_tracks('album-1')

        # 20 from the first batch — second batch failed but didn't crash
        assert len(tracks) == 20

    def test_no_track_ids_returns_empty_without_hydrating(self):
        """Empty album → no batch call (no point hydrating zero IDs)."""
        client = _make_client()
        client.session.get = MagicMock(return_value=_FakeResp(200, {'data': [], 'links': {}}))
        client._get_tracks_batch = MagicMock()
        with patch('core.tidal_client.time.sleep'):
            tracks = client.get_album_tracks('album-1')
        assert tracks == []
        client._get_tracks_batch.assert_not_called()
