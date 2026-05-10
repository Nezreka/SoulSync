"""Pin Deezer search query construction.

Issue #534 — Deezer's free-text search returns karaoke / cover /
"originally performed by" variants ranked above the canonical
recording. Switching to Deezer's advanced search syntax
(`track:"X" artist:"Y"`) tightens the API's relevance ranking
dramatically by matching each term against the right field instead
of fuzzy-matching across title / lyrics / artist / album.

These tests pin the query construction at the client boundary so
the wire-shape contract is obvious from the tests alone (no need
to read the client source to know what query string the API
receives).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.deezer_client import DeezerClient


# ---------------------------------------------------------------------------
# _build_advanced_query — pure helper, no API calls
# ---------------------------------------------------------------------------


class TestBuildAdvancedQuery:
    def test_track_and_artist_quoted(self):
        q = DeezerClient._build_advanced_query(
            track='Dirty White Boy', artist='Foreigner',
        )
        assert q == 'track:"Dirty White Boy" artist:"Foreigner"'

    def test_track_only(self):
        q = DeezerClient._build_advanced_query(track='Dirty White Boy')
        assert q == 'track:"Dirty White Boy"'

    def test_artist_only(self):
        q = DeezerClient._build_advanced_query(artist='Foreigner')
        assert q == 'artist:"Foreigner"'

    def test_all_three_fields(self):
        q = DeezerClient._build_advanced_query(
            track='Head Games', artist='Foreigner', album='Head Games',
        )
        assert q == 'track:"Head Games" artist:"Foreigner" album:"Head Games"'

    def test_empty_inputs_produce_empty_query(self):
        assert DeezerClient._build_advanced_query() == ''

    def test_embedded_quotes_stripped(self):
        """Deezer's syntax has no escape mechanism for embedded
        double-quotes. Strip them to keep the query well-formed.
        Rare in practice but a search for `O"Hara` would otherwise
        produce a malformed `track:"O"Hara"` that breaks parsing."""
        q = DeezerClient._build_advanced_query(track='O"Hara')
        assert q == 'track:"OHara"'


# ---------------------------------------------------------------------------
# search_tracks — verify the right query string reaches the API
# ---------------------------------------------------------------------------


class TestSearchTracksQueryWiring:
    def _client(self):
        c = DeezerClient.__new__(DeezerClient)
        # Stub state needed by _api_get's downstream methods
        c._api_get = MagicMock(return_value={'data': []})
        return c

    def _stub_cache(self, monkeypatch):
        """Stub the metadata cache so it doesn't return stale data
        from a prior test run AND so we can verify the cache key
        the search uses."""
        cache = MagicMock()
        cache.get_search_results.return_value = None
        monkeypatch.setattr('core.deezer_client.get_metadata_cache', lambda: cache)
        return cache

    def test_field_scoped_kwargs_use_advanced_syntax(self, monkeypatch):
        """Headline assertion of issue #534's fix. When callers pass
        track + artist as kwargs, the actual API call must use
        Deezer's advanced syntax — NOT the joined free-text form."""
        self._stub_cache(monkeypatch)
        c = self._client()

        c.search_tracks(track='Dirty White Boy', artist='Foreigner')

        c._api_get.assert_called_once()
        params = c._api_get.call_args.args[1]
        assert params['q'] == 'track:"Dirty White Boy" artist:"Foreigner"', (
            f"Expected advanced-syntax query string, got {params['q']!r}"
        )

    def test_free_text_query_path_unchanged(self, monkeypatch):
        """Backward compat: legacy callers passing a single free-text
        query string still work, no advanced syntax applied."""
        self._stub_cache(monkeypatch)
        c = self._client()

        c.search_tracks('Foreigner Dirty White Boy')

        params = c._api_get.call_args.args[1]
        assert params['q'] == 'Foreigner Dirty White Boy', (
            "Free-text caller must pass through unchanged"
        )

    def test_field_kwargs_take_precedence_over_query_param(self, monkeypatch):
        """When BOTH query and field kwargs are provided, field
        kwargs win (they're authoritative). Avoids ambiguity at the
        endpoint layer where someone might forget to drop the legacy
        query when adding field params."""
        self._stub_cache(monkeypatch)
        c = self._client()

        c.search_tracks(query='ignored free text',
                        track='Dirty White Boy', artist='Foreigner')

        params = c._api_get.call_args.args[1]
        assert 'track:' in params['q']
        assert 'ignored' not in params['q']

    def test_no_query_or_kwargs_returns_empty_without_api_call(self, monkeypatch):
        """Defensive: empty input shouldn't fire a wasted API call.
        Returns empty list immediately."""
        self._stub_cache(monkeypatch)
        c = self._client()

        result = c.search_tracks()
        assert result == []
        c._api_get.assert_not_called()

    def test_album_only_kwarg_works(self, monkeypatch):
        """album-only field-scoped search — useful for callers who
        know the album exactly but not the track or artist."""
        self._stub_cache(monkeypatch)
        c = self._client()

        c.search_tracks(album='Head Games')

        params = c._api_get.call_args.args[1]
        assert params['q'] == 'album:"Head Games"'

    def test_limit_parameter_passed_through(self, monkeypatch):
        self._stub_cache(monkeypatch)
        c = self._client()

        c.search_tracks(track='X', artist='Y', limit=50)

        params = c._api_get.call_args.args[1]
        assert params['limit'] == 50

    def test_limit_clamped_to_100(self, monkeypatch):
        """Deezer's max page size is 100. Higher requests must get
        clamped on our side rather than forwarded as-is (which would
        either error or get silently truncated by the API)."""
        self._stub_cache(monkeypatch)
        c = self._client()

        c.search_tracks(track='X', limit=500)

        params = c._api_get.call_args.args[1]
        assert params['limit'] == 100


# ---------------------------------------------------------------------------
# Cache key consistency — both call modes share the cache via the
# constructed query string
# ---------------------------------------------------------------------------


class TestSearchTracksCacheKey:
    def test_field_scoped_call_uses_advanced_query_as_cache_key(self, monkeypatch):
        """Cache key is the constructed query string, NOT the raw
        kwargs. Means the same advanced query hits the cache no
        matter how it's reconstructed by future call sites."""
        cache = MagicMock()
        cache.get_search_results.return_value = None
        monkeypatch.setattr('core.deezer_client.get_metadata_cache', lambda: cache)

        c = DeezerClient.__new__(DeezerClient)
        c._api_get = MagicMock(return_value={'data': []})

        c.search_tracks(track='Dirty White Boy', artist='Foreigner', limit=20)

        cache.get_search_results.assert_called_once_with(
            'deezer', 'track',
            'track:"Dirty White Boy" artist:"Foreigner"',
            20,
        )
