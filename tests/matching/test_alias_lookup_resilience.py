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
    svc._artist_row_mbid = MagicMock(return_value=None)
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
    service.resolve_artist_aliases = MagicMock(return_value=["澤野弘之"])

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
    service.resolve_artist_aliases = MagicMock(return_value=["澤野弘之"])

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]


def test_genuine_empty_cache_row_is_still_respected(service):
    # MusicBrainz answered and the artist truly has no aliases. The `resolved`
    # marker is what says so, and it must be honoured without re-querying.
    service._check_cache.return_value = {
        "musicbrainz_id": "mbid-x", "confidence": 95,
        "metadata": {"aliases": [], "resolved": True},
    }

    assert service.lookup_artist_aliases("Some Artist") == []
    service.mb_client.search_artist.assert_not_called()


def test_an_mbid_alone_never_makes_an_empty_row_a_verdict(service):
    """The production failure, as a test.

    A row carrying an MBID and an empty alias list used to count as "this
    artist has no aliases" for the full 90-day TTL. But `fetch_artist_aliases`
    returned `[]` for a rate-limited fetch exactly as readily as for a genuine
    absence, so one timeout during a download froze the romaji-kanji bridge —
    and every later scan of those files reported "cannot compare the names"
    while the download that wrote the row had passed them.
    """
    service._check_cache.return_value = {
        "musicbrainz_id": "mbid-sawano", "metadata": {"aliases": []},
        "confidence": 100,
    }
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100},
    ]
    service.resolve_artist_aliases = MagicMock(return_value=["澤野弘之"])

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]


def test_a_fetch_that_did_not_come_back_is_never_written_down(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100},
    ]
    service.resolve_artist_aliases = MagicMock(return_value=None)

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == []
    service._save_to_cache.assert_not_called()


# --- 2. a successful lookup is written back onto the catalogue -------------


def test_successful_lookup_is_persisted_to_the_catalogue(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100},
    ]
    service.resolve_artist_aliases = MagicMock(
        return_value=["澤野弘之", "Sawano, Hiroyuki"])

    aliases = service.lookup_artist_aliases("Sawano Hiroyuki")

    assert "澤野弘之" in aliases
    service._persist_artist_identity.assert_called_once_with(
        "Sawano Hiroyuki", "mbid-sawano", ["澤野弘之", "Sawano, Hiroyuki"])


def test_populated_aliases_short_circuit_before_any_write_back(service):
    # Tier 1 answered, so no search, no cache write and no write-back: an
    # alias list already on the artist is authoritative and left alone.
    service.get_artist_aliases.return_value = ["澤野弘之", "Sawano, Hiroyuki"]

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == [
        "澤野弘之", "Sawano, Hiroyuki"]
    service.mb_client.search_artist.assert_not_called()
    service._persist_artist_identity.assert_not_called()
    service._save_to_cache.assert_not_called()


def test_write_back_failure_never_costs_the_caller_its_aliases(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "Sawano Hiroyuki", "score": 100},
    ]
    service.resolve_artist_aliases = MagicMock(return_value=["澤野弘之"])
    service._persist_artist_identity.side_effect = RuntimeError("db is locked")

    assert service.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]


# --- the write-back must not put one artist's aliases on another ------------


_ARTISTS_DDL = """
CREATE TABLE artists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    musicbrainz_id TEXT,
    musicbrainz_match_status TEXT,
    musicbrainz_last_attempted TIMESTAMP,
    aliases TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _artist_row_service(tmp_path, name, mbid=None, aliases=None):
    """A real `artists` table with one artist, so the write-back's guards run
    against actual rows rather than mocks."""
    import json
    import sqlite3

    db_path = tmp_path / "aliases.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(_ARTISTS_DDL)
    conn.execute(
        "INSERT INTO artists (id, name, musicbrainz_id, aliases) "
        "VALUES (1, ?, ?, ?)",
        (name, mbid, json.dumps(aliases) if aliases else None),
    )
    # A second artist that already owns the MBID our name search resolves to.
    conn.execute(
        "INSERT INTO artists (id, name, musicbrainz_id) "
        "VALUES (2, 'Someone Else', 'mbid-taken')")
    conn.commit()
    conn.close()

    class _DB:
        def _get_connection(self):
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.db = _DB()
    return svc, db_path


def _stored(db_path, artist_id):
    """``(musicbrainz_id, aliases)`` with the alias column decoded."""
    import json
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    mbid, aliases = conn.execute(
        "SELECT musicbrainz_id, aliases FROM artists WHERE id=?",
        (artist_id,)).fetchone()
    conn.close()
    return mbid, json.loads(aliases) if aliases else []


def test_write_back_stores_identity_on_an_unmatched_artist(tmp_path):
    svc, db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki")

    svc._persist_artist_identity("Sawano Hiroyuki", "mbid-sawano", ["澤野弘之"])

    mbid, aliases = _stored(db_path, 1)
    assert mbid == "mbid-sawano"
    assert aliases == ["澤野弘之"]


def test_a_conflicting_mbid_writes_neither_id_nor_its_aliases(tmp_path):
    # 'mbid-taken' belongs to another artist, so the name search landed on the
    # wrong entity — and its alias list is just as wrong as its id.
    svc, db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki")

    svc._persist_artist_identity("Sawano Hiroyuki", "mbid-taken", ["Someone Else"])

    assert _stored(db_path, 1) == (None, [])


def test_an_artist_matched_to_a_different_mbid_keeps_its_own_aliases(tmp_path):
    svc, db_path = _artist_row_service(
        tmp_path, "Sawano Hiroyuki", mbid="mbid-already-set",
        aliases=["澤野弘之"])

    svc._persist_artist_identity("Sawano Hiroyuki", "mbid-other", ["Wrong Person"])

    mbid, aliases = _stored(db_path, 1)
    assert mbid == "mbid-already-set"
    assert aliases == ["澤野弘之"]


def test_the_same_mbid_refreshes_the_alias_list(tmp_path):
    svc, db_path = _artist_row_service(
        tmp_path, "Sawano Hiroyuki", mbid="mbid-sawano", aliases=["澤野弘之"])

    svc._persist_artist_identity(
        "Sawano Hiroyuki", "mbid-sawano", ["澤野弘之", "さわの ひろゆき"])

    _mbid, aliases = _stored(db_path, 1)
    assert aliases == ["澤野弘之", "さわの ひろゆき"]


# --- the artist's own MBID beats guessing by name --------------------------


def test_a_known_mbid_is_used_instead_of_searching_by_name(tmp_path):
    """`Sawano Hiroyuki` is the case the trust gate was already documented as
    losing: MusicBrainz has several entities matching that string, so the name
    search either picks a decoy or refuses as ambiguous. None of that guessing
    is necessary once the catalogue row carries the artist's MBID — the aliases
    can be fetched from the identity we already know."""
    svc, db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki",
                                       mbid="mbid-sawano")
    svc.mb_client = MagicMock()
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc.resolve_artist_aliases = MagicMock(return_value=["澤野弘之"])

    assert svc.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]
    svc.resolve_artist_aliases.assert_called_once_with("mbid-sawano")
    svc.mb_client.search_artist.assert_not_called()


def test_the_mbid_fetch_persists_so_the_next_lookup_is_free(tmp_path):
    svc, db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki",
                                       mbid="mbid-sawano")
    svc.mb_client = MagicMock()
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc.resolve_artist_aliases = MagicMock(return_value=["澤野弘之"])

    svc.lookup_artist_aliases("Sawano Hiroyuki")

    _mbid, aliases = _stored(db_path, 1)
    assert aliases == ["澤野弘之"]


def test_a_fetch_for_the_row_mbid_that_failed_falls_through_to_the_name_search(tmp_path):
    svc, db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki",
                                       mbid="mbid-sawano")
    svc.mb_client = MagicMock()
    svc.mb_client.search_artist.return_value = [
        {"id": "mbid-other", "name": "Sawano Hiroyuki", "score": 100}]
    svc._calculate_similarity = _simple_sim
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc.resolve_artist_aliases = MagicMock(side_effect=[None, ["from-search"]])

    assert svc.lookup_artist_aliases("Sawano Hiroyuki") == ["from-search"]
    svc.mb_client.search_artist.assert_called()


def test_the_row_mbid_answering_none_is_an_answer_not_a_reason_to_guess(tmp_path):
    """MusicBrainz listing no alias for the artist's OWN identity settles it.

    Falling through to the name search here would be guessing about an artist
    we have already identified — which is how a decoy entity's aliases got
    attached to the wrong artist in the first place.
    """
    svc, _db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki",
                                        mbid="mbid-sawano")
    svc.mb_client = MagicMock()
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc.resolve_artist_aliases = MagicMock(return_value=[])

    assert svc.lookup_artist_aliases("Sawano Hiroyuki") == []
    svc.mb_client.search_artist.assert_not_called()


def test_a_cache_row_for_the_same_mbid_spares_the_fetch(tmp_path):
    """One rate-limited MusicBrainz call per scanned file, removed.

    Tier 1b sits in front of the cache, so without this an artist MusicBrainz
    lists no alias for would cost a blocking request on every single
    comparison — all of it queued behind the enrichment worker on the same
    one-per-second lock.
    """
    svc, _db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki",
                                        mbid="mbid-sawano")
    svc.mb_client = MagicMock()
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._save_to_cache = MagicMock()
    svc._check_cache = MagicMock(return_value={
        "musicbrainz_id": "mbid-sawano", "confidence": 100,
        "metadata": {"aliases": [], "resolved": True},
    })
    svc.resolve_artist_aliases = MagicMock()

    assert svc.lookup_artist_aliases("Sawano Hiroyuki") == []
    svc.resolve_artist_aliases.assert_not_called()
    svc.mb_client.search_artist.assert_not_called()


def test_a_cache_row_for_a_different_mbid_does_not_answer_for_this_one(tmp_path):
    svc, _db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki",
                                        mbid="mbid-sawano")
    svc.mb_client = MagicMock()
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._save_to_cache = MagicMock()
    svc._check_cache = MagicMock(return_value={
        "musicbrainz_id": "mbid-decoy", "confidence": 100,
        "metadata": {"aliases": [], "resolved": True},
    })
    svc.resolve_artist_aliases = MagicMock(return_value=["澤野弘之"])

    assert svc.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]
    svc.resolve_artist_aliases.assert_called_once_with("mbid-sawano")


def test_no_mbid_on_the_row_still_searches_by_name(tmp_path):
    svc, db_path = _artist_row_service(tmp_path, "Sawano Hiroyuki")
    svc.mb_client = MagicMock()
    svc.mb_client.search_artist.return_value = [
        {"id": "mbid-found", "name": "Sawano Hiroyuki", "score": 100}]
    svc._calculate_similarity = _simple_sim
    svc.get_artist_aliases = MagicMock(return_value=[])
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc.resolve_artist_aliases = MagicMock(return_value=["澤野弘之"])

    assert svc.lookup_artist_aliases("Sawano Hiroyuki") == ["澤野弘之"]
    svc.mb_client.search_artist.assert_called()
