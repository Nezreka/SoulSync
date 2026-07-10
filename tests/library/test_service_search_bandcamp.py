"""core.library.service_search's 'bandcamp' dispatch branch — added
alongside the enhanced-library-view Bandcamp badge/chip/enrich-menu wiring.

Uses the raw multi-result client.search_albums/search_tracks (typed Album/
Track objects), NOT the search_album/search_track convenience methods —
those require both a confident title AND artist match (BandcampClient's
_best_match dual threshold, tuned for the unattended enrichment worker) and
silently returned zero candidates for a manual search typed without a
strong artist token (e.g. just the album/track title), which is exactly
what the modal's default query looks like. A human picking from a list
needs every candidate, not a pre-filtered "best" guess.

Bandcamp is URL-addressed (no numeric ID API), so the manual-match "id" this
branch returns must be the release URL — _SERVICE_ID_COLUMNS['bandcamp']
maps both album and track to the bandcamp_url column, not an *_id column
like every other service.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import core.library.service_search as ss
from core.metadata.types import Album, Track


def _wire_bandcamp(monkeypatch, *, search_albums=None, search_tracks=None):
    client = MagicMock()
    client.search_albums.return_value = search_albums or []
    client.search_tracks.return_value = search_tracks or []
    worker = SimpleNamespace(client=client)
    monkeypatch.setattr(ss, "bandcamp_worker", worker)
    return client


def test_album_search_returns_every_candidate_as_release_url_id(monkeypatch):
    client = _wire_bandcamp(
        monkeypatch,
        search_albums=[
            Album(
                id='3317386587', name='Full Body Recordings, Episode 1',
                artists=['Full Body Recordings'], release_date='', total_tracks=0,
                album_type='album', image_url='https://f4.bcbits.com/img/a1811014619_10.jpg',
                source='bandcamp',
                external_urls={'bandcamp': 'https://fullbodyrecordings.bandcamp.com/album/episode-1'},
            ),
            Album(
                id='9', name='Episode 2', artists=['Full Body Recordings'],
                release_date='', total_tracks=0, album_type='album', source='bandcamp',
                external_urls={'bandcamp': 'https://fullbodyrecordings.bandcamp.com/album/episode-2'},
            ),
        ],
    )

    results = ss._search_service('bandcamp', 'album', 'Episode 1')

    assert len(results) == 2
    assert results[0]['id'] == 'https://fullbodyrecordings.bandcamp.com/album/episode-1'
    assert results[0]['name'] == 'Full Body Recordings, Episode 1'
    assert results[0]['extra'] == 'Full Body Recordings'
    client.search_albums.assert_called_once_with('Episode 1', limit=8)


def test_track_search_does_not_require_an_artist_prefix(monkeypatch):
    """The exact bug: a bare track title (no 'Artist - ' prefix, as the
    per-track match chip sends) must still return results."""
    client = _wire_bandcamp(
        monkeypatch,
        search_tracks=[
            Track(
                id='3131312045', name='Drift', artists=['Early Fern'], album='',
                duration_ms=0, source='bandcamp',
                external_urls={'bandcamp': 'https://earlyfern.bandcamp.com/track/drift'},
            ),
        ],
    )

    results = ss._search_service('bandcamp', 'track', 'Drift')

    assert len(results) == 1
    assert results[0]['id'] == 'https://earlyfern.bandcamp.com/track/drift'
    client.search_tracks.assert_called_once_with('Drift', limit=8)


def test_candidates_without_a_bandcamp_url_are_skipped(monkeypatch):
    _wire_bandcamp(
        monkeypatch,
        search_albums=[
            Album(id='1', name='No URL', artists=['X'], release_date='', total_tracks=0,
                  album_type='album', source='bandcamp', external_urls={}),
        ],
    )

    assert ss._search_service('bandcamp', 'album', 'anything') == []


def test_no_results_returns_empty_list(monkeypatch):
    _wire_bandcamp(monkeypatch, search_albums=[])
    assert ss._search_service('bandcamp', 'album', 'Nonexistent Release') == []


def test_uninitialized_worker_raises():
    ss.bandcamp_worker = None

    try:
        ss._search_service('bandcamp', 'album', 'anything')
        raise AssertionError("Expected ValueError for uninitialized worker")
    except ValueError as exc:
        assert "not initialized" in str(exc)
