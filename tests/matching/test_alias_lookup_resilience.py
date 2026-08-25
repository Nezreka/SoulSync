"""Alias lookup: a failure is not an answer, and an answer must be kept.

Two defects behind the production "Wrong download" findings for
`Sawano Hiroyuki` / `澤野弘之`, both in `lookup_artist_aliases`:

1. **A lookup failure was cached as "this artist has no aliases".**
   `_search_and_score_artists` returns an empty list for *any* exception —
   timeout, rate limit, 503 — indistinguishable from a genuine empty result.
   The caller then wrote `{"aliases": []}` into `musicbrainz_cache` where it
   blocked every retry for its 30-day TTL. A bulk AcoustID scan is exactly
   the workload that trips MusicBrainz's rate limit, so one sweep could
   poison the only bridge between a romaji and a kanji spelling. The user's
   database contains such a row (`Hiroyuki Sawano`, confidence 0).

2. **A successful lookup was never written back to the catalogue.** The
   resolved MBID and alias list went into `musicbrainz_cache` keyed by the
   *name string* the caller happened to pass, and nowhere else — so the
   knowledge that let a download pass was not attached to the artist, did
   not survive the cache TTL, and was invisible to any later lookup that
   spelled the name differently.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.musicbrainz_service import MusicBrainzService


def _simple_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a.lower() == b.lower() else 0.0


@pytest.fixture
def service():
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._calculate_similarity = _simple_sim
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc._persist_artist_identity = MagicMock()
    return svc


# --- 1. a failed lookup must not be cached as an answer --------------------


def test_search_failure_is_not_cached_as_empty(service):
    service.mb_client.search_artist.side_effect = TimeoutError("read timed out")

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == []
    service._save_to_cache.assert_not_called()


def test_genuine_no_results_is_still_cached(service):
    # MusicBrainz answered and knows nobody by that name — a real verdict,
    # worth remembering so the next lookup doesn't re-ask.
    service.mb_client.search_artist.return_value = []

    assert service.lookup_artist_aliases("Not An Artist At All") == []
    service._save_to_cache.assert_called_once()


def test_strict_failure_still_uses_a_working_non_strict_result(service):
    def _search(name, limit=3, strict=True):
        if strict:
            raise TimeoutError("read timed out")
        return [{"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100}]

    service.mb_client.search_artist.side_effect = _search
    service.fetch_artist_aliases = MagicMock(return_value=["澤野弘之"])

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]


def test_poisoned_empty_cache_row_does_not_block_a_retry(service):
    # A cached empty result with NO mbid and zero confidence is the shape the
    # old failure path wrote. It is not a verdict, so it must not stand in for
    # one — fall through and ask again.
    service._check_cache.return_value = {
        "musicbrainz_id": None, "metadata": {"aliases": []}, "confidence": 0,
    }
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100},
    ]
    service.fetch_artist_aliases = MagicMock(return_value=["澤野弘之"])

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]


def test_genuine_empty_cache_row_is_still_respected(service):
    # MusicBrainz found the artist and it truly has no aliases: an mbid is
    # present, so this IS a verdict and must be honoured without re-querying.
    service._check_cache.return_value = {
        "musicbrainz_id": "mbid-x", "metadata": {"aliases": []}, "confidence": 95,
    }

    assert service.lookup_artist_aliases("Some Artist") == []
    service.mb_client.search_artist.assert_not_called()


# --- 2. a successful lookup is written back onto the catalogue -------------


def test_successful_lookup_is_persisted_to_the_catalogue(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100},
    ]
    service.fetch_artist_aliases = MagicMock(
        return_value=["澤野弘之", "Sawano, Hiroyuki"])

    aliases = service.lookup_artist_aliases("Sawano Hiroyuki")

    assert "澤野弘之" in aliases
    service._persist_artist_identity.assert_called_once_with(
        "Sawano Hiroyuki", "mbid-sawano", ["澤野弘之", "Sawano, Hiroyuki"])


def test_write_back_failure_never_costs_the_caller_its_aliases(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100},
    ]
    service.fetch_artist_aliases = MagicMock(return_value=["澤野弘之"])
    service._persist_artist_identity.side_effect = RuntimeError("db is locked")

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]
