"""match_recording / match_artist / match_release must not negative-cache an outage.

Same production shape as `test_alias_lookup_resilience.py`'s "a lookup failure
was cached as an answer" defect, one level down: `search_recording` /
`search_artist` / `search_release` caught their own transport exceptions
(timeout, 429, 503, or MusicBrainz's 200-status "server busy" body) and
returned `[]` — the exact value the three `match_*` methods use to mean
"MusicBrainz knows no such artist/release/recording". Each one then wrote
that verdict into `musicbrainz_cache` with a 30-day TTL for a null
`musicbrainz_id`, so a single outage during a bulk scan could silence a
correct match for a month.

The fix mirrors `lookup_artist_aliases` / `_search_and_score_artists`, which
already pass `raise_on_error=True` so a transient failure lands in the
`except` branch instead of the "no results" branch — and the `except` branch
here, like there, never calls `_save_to_cache`.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from core.musicbrainz_service import MusicBrainzService


@pytest.fixture
def service():
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc._candidate_release_titles = MagicMock(return_value=[])
    return svc


def _transient(*_args, raise_on_error=False, **_kwargs):
    """Stand-in for the real client's contract: a transport failure is folded
    into `[]` unless the caller asked to be told about it. So a `match_*` that
    forgets `raise_on_error=True` sees a genuine-looking empty result here and
    negative-caches it — which is exactly the defect these tests pin."""
    if raise_on_error:
        raise TimeoutError("read timed out")
    return []


# --- match_recording ---------------------------------------------------------


def test_match_recording_transient_failure_is_not_cached(service):
    service.mb_client.search_recording.side_effect = _transient

    assert service.match_recording("Some Song", "Some Artist") is None
    service._save_to_cache.assert_not_called()


def test_match_recording_genuine_miss_is_still_cached(service):
    service.mb_client.search_recording.return_value = []
    # a strict miss now also tries the artist-pinned retry, which caches its
    # own artist_recording_pin row; only the recording row matters here
    service.mb_client.search_artist.return_value = []

    assert service.match_recording("Some Song", "Some Artist") is None
    recording_writes = [c for c in service._save_to_cache.call_args_list if c.args[0] == 'recording']
    assert recording_writes == [call('recording', "Some Song", "Some Artist", None, None, 0)]


def test_match_recording_passes_raise_on_error(service):
    service.mb_client.search_recording.return_value = []

    service.match_recording("Some Song", "Some Artist")

    service.mb_client.search_recording.assert_called_once_with(
        "Some Song", "Some Artist", limit=5, raise_on_error=True)


# --- match_artist -------------------------------------------------------------


def test_match_artist_transient_failure_is_not_cached(service):
    service.mb_client.search_artist.side_effect = _transient

    assert service.match_artist("Some Artist") is None
    service._save_to_cache.assert_not_called()


def test_match_artist_genuine_miss_is_still_cached(service):
    service.mb_client.search_artist.return_value = []

    assert service.match_artist("Some Artist") is None
    service._save_to_cache.assert_called_once_with(
        'artist', "Some Artist", None, None, None, 0)


def test_match_artist_passes_raise_on_error(service):
    # Non-empty (if low-confidence) result, so the strict search is the ONLY
    # call — the not-results fallback below would otherwise call the mock a
    # second time and break assert_called_once_with.
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-weak", "name": "Some Artist", "score": 10}]

    service.match_artist("Some Artist")

    service.mb_client.search_artist.assert_called_once_with(
        "Some Artist", limit=5, raise_on_error=True)


def test_match_artist_non_strict_fallback_also_passes_raise_on_error(service):
    # Strict comes back empty (a genuine, completed answer) so the fuzzy
    # fallback runs — it must keep the same raise_on_error contract.
    def _search(name, limit=5, strict=True, raise_on_error=False):
        assert raise_on_error is True
        return []

    service.mb_client.search_artist.side_effect = _search

    assert service.match_artist("Some Artist") is None
    service._save_to_cache.assert_called_once_with(
        'artist', "Some Artist", None, None, None, 0)


# --- match_release --------------------------------------------------------


def test_match_release_transient_failure_is_not_cached(service):
    service.mb_client.search_release.side_effect = _transient

    assert service.match_release("Some Album", "Some Artist") is None
    service._save_to_cache.assert_not_called()


def test_match_release_genuine_miss_is_still_cached(service):
    service.mb_client.search_release.return_value = []

    assert service.match_release("Some Album", "Some Artist") is None
    service._save_to_cache.assert_called_once_with(
        'release', "Some Album", "Some Artist", None, None, 0)


def test_match_release_passes_raise_on_error(service):
    service.mb_client.search_release.return_value = []

    service.match_release("Some Album", "Some Artist")

    service.mb_client.search_release.assert_called_once_with(
        "Some Album", "Some Artist", limit=5, raise_on_error=True)
