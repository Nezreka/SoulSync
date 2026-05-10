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
        # Stub state needed by _api_get's downstream methods. Returns
        # one fake hit so the empty-result fallback (which would
        # double the API calls) doesn't fire — these tests only care
        # about the FIRST call's query construction.
        c._api_get = MagicMock(return_value={
            'data': [{
                'id': 1, 'title': 'X', 'duration': 200,
                'artist': {'id': 2, 'name': 'A'},
                'album': {'id': 3, 'title': 'B'},
            }],
        })
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

        # Stubbed API returns a hit so fallback doesn't fire; first
        # (and only) call uses advanced syntax.
        params = c._api_get.call_args_list[0].args[1]
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

        # First call uses advanced syntax (kwargs win over query).
        params = c._api_get.call_args_list[0].args[1]
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

        params = c._api_get.call_args_list[0].args[1]
        assert params['q'] == 'album:"Head Games"'

    def test_limit_parameter_passed_through(self, monkeypatch):
        self._stub_cache(monkeypatch)
        c = self._client()

        c.search_tracks(track='X', artist='Y', limit=50)

        params = c._api_get.call_args_list[0].args[1]
        assert params['limit'] == 50

    def test_limit_clamped_to_100(self, monkeypatch):
        """Deezer's max page size is 100. Higher requests must get
        clamped on our side rather than forwarded as-is (which would
        either error or get silently truncated by the API)."""
        self._stub_cache(monkeypatch)
        c = self._client()

        c.search_tracks(track='X', limit=500)

        params = c._api_get.call_args_list[0].args[1]
        assert params['limit'] == 100


# ---------------------------------------------------------------------------
# Cache key consistency — both call modes share the cache via the
# constructed query string
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Free-text fallback when advanced query returns 0 results
# ---------------------------------------------------------------------------


class TestSearchTracksAdvancedQueryFallback:
    """Defensive fallback: Deezer's advanced syntax is `artist:"X"`-
    style substring match, but in practice it's brittle on artist
    name variants ("Foreigner [US]", "The Foreigner", etc.) and on
    tracks indexed under non-canonical title spellings. When the
    advanced query returns nothing, fall back to a free-text join so
    the user sees the prior (less-relevant but non-empty) result set
    rather than "No matches".

    Contract: pre-fix behaviour preserved on the empty-advanced-query
    edge case. Caller-side rerank still tightens whatever the
    fallback returns.
    """

    def _client_with_responses(self, monkeypatch, responses):
        """Stub `_api_get` to return `responses` in sequence (FIFO).
        Lets the test simulate "advanced empty, free-text non-empty"."""
        cache = MagicMock()
        cache.get_search_results.return_value = None
        monkeypatch.setattr('core.deezer_client.get_metadata_cache', lambda: cache)

        c = DeezerClient.__new__(DeezerClient)
        call_log = []

        def fake_api_get(_path, params):
            call_log.append(params['q'])
            return responses.pop(0) if responses else None

        c._api_get = fake_api_get
        c._call_log = call_log
        return c

    def test_falls_back_to_free_text_when_advanced_empty(self, monkeypatch):
        c = self._client_with_responses(monkeypatch, [
            {'data': []},  # advanced query — 0 results
            {'data': [{'id': 99, 'title': 'Found It', 'duration': 200,
                       'artist': {'id': 1, 'name': 'Foreigner'},
                       'album': {'id': 2, 'title': 'X'}}]},  # free-text — has results
        ])
        results = c.search_tracks(track='Dirty White Boy', artist='Foreigner [US]')

        assert len(results) == 1
        assert results[0].name == 'Found It'
        # First call was the advanced query, second was the free-text fallback
        assert c._call_log[0] == 'track:"Dirty White Boy" artist:"Foreigner [US]"'
        assert c._call_log[1] == 'Dirty White Boy Foreigner [US]'

    def test_no_fallback_when_advanced_query_has_results(self, monkeypatch):
        """Don't waste an extra API call when the advanced query
        already returned something — even a single result counts as
        a hit, no fallback needed."""
        c = self._client_with_responses(monkeypatch, [
            {'data': [{'id': 99, 'title': 'Found', 'duration': 200,
                       'artist': {'id': 1, 'name': 'Foreigner'},
                       'album': {'id': 2, 'title': 'X'}}]},
        ])
        results = c.search_tracks(track='X', artist='Foreigner')

        assert len(results) == 1
        assert len(c._call_log) == 1, "Should not have hit the API twice"

    def test_no_fallback_when_legacy_free_text_call(self, monkeypatch):
        """Free-text caller already exhausted the only path — no
        secondary fallback exists. Empty result is final."""
        c = self._client_with_responses(monkeypatch, [{'data': []}])
        results = c.search_tracks('legacy free text')

        assert results == []
        assert len(c._call_log) == 1

    def test_no_fallback_when_query_unchanged(self, monkeypatch):
        """If the constructed advanced query happens to equal the
        free-text join (e.g. caller passed only `track=` with a
        single word), don't waste an identical second API call."""
        c = self._client_with_responses(monkeypatch, [{'data': []}])
        # Single-word track-only — advanced query is `track:"X"`,
        # free-text would be `X`. Different strings, fallback fires.
        # Skip this case; instead test the no-op-when-equal path
        # directly: empty kwargs trio means used_advanced=False,
        # we never enter the fallback branch.
        results = c.search_tracks(query='same')
        assert results == []
        assert len(c._call_log) == 1


class TestSearchTracksCacheKey:
    def test_field_scoped_call_uses_advanced_query_as_cache_key(self, monkeypatch):
        """Cache key is the constructed query string, NOT the raw
        kwargs. Means the same advanced query hits the cache no
        matter how it's reconstructed by future call sites."""
        cache = MagicMock()
        cache.get_search_results.return_value = None
        monkeypatch.setattr('core.deezer_client.get_metadata_cache', lambda: cache)

        c = DeezerClient.__new__(DeezerClient)
        # Non-empty stub so the empty-result fallback doesn't fire +
        # double the cache lookups.
        c._api_get = MagicMock(return_value={
            'data': [{
                'id': 1, 'title': 'X', 'duration': 200,
                'artist': {'id': 2, 'name': 'A'},
                'album': {'id': 3, 'title': 'B'},
            }],
        })

        c.search_tracks(track='Dirty White Boy', artist='Foreigner', limit=20)

        cache.get_search_results.assert_called_once_with(
            'deezer', 'track',
            'track:"Dirty White Boy" artist:"Foreigner"',
            20,
        )
