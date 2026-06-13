"""#867 regression: Tidal playlist track hydration must chunk to the API cap.

Tidal's ``/tracks?filter[id]=...`` endpoint returns at most ``_COLLECTION_BATCH_SIZE``
(20) tracks per request. ``get_playlist`` fetches a page of track-ID links and then
hydrates them via ``_get_tracks_batch``. If it sends a relationships page with more
than 20 IDs in a single hydration call, the surplus is silently dropped — a 59-track
playlist would surface as ~20. ``get_album_tracks`` already chunks; this pins the same
behavior for ``get_playlist`` so discovery sees every track.
"""

from __future__ import annotations

from core.tidal_client import TidalClient, Track


class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "data": {
                "id": "PL1",
                "attributes": {"name": "My Playlist", "accessType": "PRIVATE"},
                "relationships": {},
            }
        }


class _FakeSession:
    def get(self, *args, **kwargs):
        return _FakeResp()


def _make_client():
    """Build a TidalClient without running its network/config __init__."""
    client = object.__new__(TidalClient)
    client.base_url = "https://api.tidal.test"
    client.session = _FakeSession()
    return client


def test_get_playlist_chunks_oversized_relationships_page(monkeypatch):
    client = _make_client()
    cap = TidalClient._COLLECTION_BATCH_SIZE  # 20
    n = 59
    ids = [str(i) for i in range(n)]

    monkeypatch.setattr(client, "_ensure_valid_token", lambda: True)
    # One relationships page returns ALL 59 ID links at once, then no cursor.
    monkeypatch.setattr(
        client,
        "_get_playlist_tracks_page",
        lambda playlist_id, cursor=None: {
            "data": [{"type": "tracks", "id": i} for i in ids],
            "links": {"meta": {}},  # no nextCursor -> single page
        },
    )

    seen_chunk_sizes = []

    def fake_batch(chunk_ids):
        # Simulate the real filter[id] cap: never return more than the cap, so a
        # single oversized call would lose the surplus (the bug being guarded).
        seen_chunk_sizes.append(len(chunk_ids))
        capped = chunk_ids[:cap]
        return [Track(id=i, name=f"t{i}", artists=["a"]) for i in capped]

    monkeypatch.setattr(client, "_get_tracks_batch", fake_batch)

    playlist = client.get_playlist("PL1")

    assert playlist is not None
    # The whole point: all 59 hydrate, not just the first 20.
    assert len(playlist.tracks) == n
    # Every hydration call stayed within the API cap (so none truncated).
    assert seen_chunk_sizes and max(seen_chunk_sizes) <= cap


def test_get_playlist_small_page_single_call(monkeypatch):
    """A page at/under the cap still hydrates in one call (no behavior change)."""
    client = _make_client()
    ids = [str(i) for i in range(5)]

    monkeypatch.setattr(client, "_ensure_valid_token", lambda: True)
    monkeypatch.setattr(
        client,
        "_get_playlist_tracks_page",
        lambda playlist_id, cursor=None: {
            "data": [{"type": "tracks", "id": i} for i in ids],
            "links": {"meta": {}},
        },
    )

    calls = []

    def fake_batch(chunk_ids):
        calls.append(list(chunk_ids))
        return [Track(id=i, name=f"t{i}", artists=["a"]) for i in chunk_ids]

    monkeypatch.setattr(client, "_get_tracks_batch", fake_batch)

    playlist = client.get_playlist("PL1")
    assert len(playlist.tracks) == 5
    assert len(calls) == 1  # one chunk, one hydration call
