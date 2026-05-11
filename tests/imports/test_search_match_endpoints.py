"""End-to-end tests for the import-modal search endpoints.

Issue #534 — these endpoints back the "Search for Match" dialog
that lets users find a track when auto-match failed. They were
returning karaoke / cover variants ahead of canonical recordings
because:

1. Deezer endpoint joined `track + artist` into a single free-text
   string, losing field-scoping.
2. None of the endpoints applied any local relevance rerank, so
   junk results stayed wherever the source's API put them.

These tests pin the post-fix wiring:
- Deezer endpoint passes `track=` + `artist=` kwargs to the client
  (which now builds advanced-syntax `track:"X" artist:"Y"`).
- Deezer + iTunes + Spotify endpoints all run the response through
  ``rerank_tracks`` so karaoke / cover patterns drop to the bottom.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app_test_client():
    """Spin up a Flask test client backed by web_server.app."""
    import web_server
    web_server.app.config['TESTING'] = True
    with web_server.app.test_client() as client:
        yield client


@pytest.fixture
def fake_track():
    """Factory for a Track-like object the endpoints can serialise."""
    from core.metadata.types import Track

    def _make(name, artist, album='Album', track_id='t', album_type='album'):
        return Track(
            id=track_id, name=name, artists=[artist],
            album=album, duration_ms=200000, album_type=album_type,
        )
    return _make


# ---------------------------------------------------------------------------
# /api/deezer/search_tracks — field-scoped + rerank
# ---------------------------------------------------------------------------


class TestDeezerSearchTracksEndpoint:
    def test_joins_track_and_artist_into_free_text_query(self, app_test_client, fake_track):
        """Endpoint sends the joined `track artist` string as Deezer's
        free-text `q`. Field-scoped advanced-syntax queries were
        initially considered, but live-API testing showed Deezer's
        advanced-query ranking misses canonical recordings on some
        searches. Free-text + local rerank is the more reliable
        combination at this endpoint. Client-level kwarg support
        remains for future opt-in callers."""
        fake_client = MagicMock()
        fake_client.search_tracks.return_value = [
            fake_track('Dirty White Boy', 'Foreigner'),
        ]
        with patch('web_server._get_deezer_client', return_value=fake_client):
            resp = app_test_client.get(
                '/api/deezer/search_tracks?track=Dirty+White+Boy&artist=Foreigner&limit=20'
            )
        assert resp.status_code == 200
        call = fake_client.search_tracks.call_args
        # First positional arg is the joined free-text query
        assert call.args[0] == 'Dirty White Boy Foreigner'
        assert call.kwargs.get('limit') == 20

    def test_reranks_results_burying_karaoke(self, app_test_client, fake_track):
        """Endpoint runs results through rerank_tracks. Real Foreigner
        cut must end up first, karaoke variant last — even though the
        client returned them in the broken Deezer-API order."""
        fake_client = MagicMock()
        fake_client.search_tracks.return_value = [
            fake_track('Dirty White Boy (Karaoke Version Originally Performed By Foreigner)',
                       'Pop Music Workshop', album='Backing Tracks',
                       album_type='compilation', track_id='karaoke-1'),
            fake_track('Dirty White Boy', 'Foreigner', album='Head Games',
                       album_type='album', track_id='real-1'),
        ]
        with patch('web_server._get_deezer_client', return_value=fake_client):
            resp = app_test_client.get(
                '/api/deezer/search_tracks?track=Dirty+White+Boy&artist=Foreigner'
            )
        assert resp.status_code == 200
        body = resp.get_json()
        ids = [t['id'] for t in body['tracks']]
        assert ids[0] == 'real-1', (
            f"Real cut should be first after rerank; got order {ids}"
        )
        assert ids[-1] == 'karaoke-1', (
            f"Karaoke variant should be last; got order {ids}"
        )

    def test_legacy_query_param_still_works(self, app_test_client, fake_track):
        """Backward compat: callers passing the legacy `query=` param
        get free-text search, no rerank (no signal to rank against)."""
        fake_client = MagicMock()
        fake_client.search_tracks.return_value = [
            fake_track('Anything', 'Whatever', track_id='only'),
        ]
        with patch('web_server._get_deezer_client', return_value=fake_client):
            resp = app_test_client.get(
                '/api/deezer/search_tracks?query=anything+whatever'
            )
        assert resp.status_code == 200
        # Legacy path passes positional query, no track/artist kwargs
        call = fake_client.search_tracks.call_args
        assert call.args[0] == 'anything whatever' or call.kwargs.get('query') == 'anything whatever'

    def test_missing_query_returns_400(self, app_test_client):
        """Empty input → 400. Don't waste an API call."""
        with patch('web_server._get_deezer_client', return_value=MagicMock()):
            resp = app_test_client.get('/api/deezer/search_tracks')
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/itunes/search_tracks — rerank applied even though iTunes has no
# advanced-syntax search
# ---------------------------------------------------------------------------


class TestiTunesSearchTracksEndpoint:
    def test_reranks_results_burying_karaoke(self, app_test_client, fake_track, monkeypatch):
        """iTunes API doesn't expose field-scoped search, but rerank
        still applies — local relevance still penalises karaoke /
        cover patterns regardless of source."""
        fake_client = MagicMock()
        fake_client.search_tracks.return_value = [
            fake_track('Dirty White Boy (Karaoke Version)',
                       'Karaoke Co', track_id='karaoke-1',
                       album='Karaoke Hits', album_type='compilation'),
            fake_track('Dirty White Boy', 'Foreigner',
                       album='Head Games', album_type='album',
                       track_id='real-1'),
        ]
        # Endpoint dispatches via _get_metadata_fallback_client; stub it
        monkeypatch.setattr('web_server._get_metadata_fallback_client', lambda: fake_client)
        monkeypatch.setattr('web_server._get_metadata_fallback_source', lambda: 'itunes')
        monkeypatch.setattr('web_server._is_hydrabase_active', lambda: False)
        # Avoid hydrabase worker side-effect during test
        monkeypatch.setattr('web_server.hydrabase_worker', None, raising=False)
        monkeypatch.setattr('web_server.dev_mode_enabled', False, raising=False)

        resp = app_test_client.get(
            '/api/itunes/search_tracks?track=Dirty+White+Boy&artist=Foreigner'
        )
        assert resp.status_code == 200
        body = resp.get_json()
        ids = [t['id'] for t in body['tracks']]
        assert ids[0] == 'real-1', (
            f"Real cut should be first after rerank; got {ids}"
        )

    def test_legacy_query_param_skips_rerank(self, app_test_client, fake_track, monkeypatch):
        """Free-text query has no expected title/artist to rank
        against — rerank is a no-op (returns input order)."""
        # Order: A first, B second — must stay in that order.
        a = fake_track('Anything', 'X', track_id='first')
        b = fake_track('Whatever', 'Y', track_id='second')
        fake_client = MagicMock()
        fake_client.search_tracks.return_value = [a, b]
        monkeypatch.setattr('web_server._get_metadata_fallback_client', lambda: fake_client)
        monkeypatch.setattr('web_server._get_metadata_fallback_source', lambda: 'itunes')
        monkeypatch.setattr('web_server._is_hydrabase_active', lambda: False)
        monkeypatch.setattr('web_server.hydrabase_worker', None, raising=False)
        monkeypatch.setattr('web_server.dev_mode_enabled', False, raising=False)

        resp = app_test_client.get('/api/itunes/search_tracks?query=anything')
        body = resp.get_json()
        assert [t['id'] for t in body['tracks']] == ['first', 'second']


# ---------------------------------------------------------------------------
# /api/spotify/search_tracks — already builds field-scoped query;
# verify rerank also applies for consistency
# ---------------------------------------------------------------------------


class TestSpotifySearchTracksEndpoint:
    def test_reranks_results(self, app_test_client, fake_track, monkeypatch):
        """Spotify endpoint already builds `track:X artist:Y` query
        syntax. Rerank still applies as the safety net for any
        karaoke / cover that slips through."""
        fake_client = MagicMock()
        fake_client.is_authenticated.return_value = True
        fake_client.search_tracks.return_value = [
            fake_track('Track (Karaoke)', 'Karaoke Co', track_id='karaoke-1',
                       album='Karaoke Hits', album_type='compilation'),
            fake_track('Track', 'Real Artist', album='Album',
                       album_type='album', track_id='real-1'),
        ]
        monkeypatch.setattr('web_server.spotify_client', fake_client, raising=False)
        monkeypatch.setattr('web_server._is_hydrabase_active', lambda: False)
        monkeypatch.setattr('web_server.hydrabase_worker', None, raising=False)
        monkeypatch.setattr('web_server.dev_mode_enabled', False, raising=False)

        resp = app_test_client.get(
            '/api/spotify/search_tracks?track=Track&artist=Real+Artist'
        )
        assert resp.status_code == 200
        body = resp.get_json()
        ids = [t['id'] for t in body['tracks']]
        assert ids[0] == 'real-1'
