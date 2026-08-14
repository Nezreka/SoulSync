"""Two batch-selection shapes the first workers did not need.

The Similar Artists worker differs from the enrichment workers in two ways that
are deliberate, not accidental, so ``worker_queue`` has to carry both rather than
have that worker keep its own copy of the selection logic:

* it retries ``error`` as well as ``not_found``, because its errors are MusicMap
  timeouts and 5xx — transient by nature, and its own fetch already sorts a
  genuine 400/404 into ``not_found``. The enrichment workers must NOT retry
  ``error``, or a provider outage becomes an infinite loop;
* its universe is only artists already matched to a metadata source, because the
  similars it stores are keyed by that source id. An unmatched artist has nothing
  to key by, and offering it would mark it failed forever.

It also reports a status breakdown rather than a progress percentage, which the
attempt ledger can answer directly.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.provider_attempts import (
    ensure_provider_attempt_schema, record_attempt,
)
from core.library2.schema import ensure_library_v2_schema
from core.library2.worker_queue import next_pending, status_counts


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    ensure_provider_attempt_schema(c.cursor())
    yield c
    c.close()


def _artist(conn, name, *, spotify_id=None, external_ids=None):
    return conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, spotify_id, external_ids) "
        "VALUES(?,?,?,?)",
        (name, name, spotify_id, json.dumps(external_ids or {})),
    ).lastrowid


def _stale(conn, entity_id, status, service="similar_artists"):
    record_attempt(conn, entity_type="artist", entity_id=entity_id,
                   service=service, status=status)
    conn.execute(
        "UPDATE lib2_provider_attempts SET last_attempted_at=datetime('now','-90 days') "
        "WHERE entity_id=? AND service=?", (entity_id, service))


class TestWhichFailuresComeBack:
    def test_by_default_an_error_is_not_retried(self, conn):
        """A provider outage must not become an infinite retry loop."""
        artist = _artist(conn, "Rone", spotify_id="sp-1")
        _stale(conn, artist, "error")

        assert next_pending(conn, "similar_artists",
                            entity_types=("artist",)) is None

    def test_an_error_can_be_made_retryable(self, conn):
        artist = _artist(conn, "Rone", spotify_id="sp-1")
        _stale(conn, artist, "error")

        item = next_pending(conn, "similar_artists", entity_types=("artist",),
                            retry_statuses=("error", "not_found"))

        assert item is not None and item["id"] == artist

    def test_a_settled_match_never_comes_back(self, conn):
        artist = _artist(conn, "Rone", spotify_id="sp-1")
        _stale(conn, artist, "matched")

        assert next_pending(conn, "similar_artists", entity_types=("artist",),
                            retry_statuses=("error", "not_found")) is None

    def test_the_retry_window_still_applies(self, conn):
        artist = _artist(conn, "Rone", spotify_id="sp-1")
        record_attempt(conn, entity_type="artist", entity_id=artist,
                       service="similar_artists", status="error")

        assert next_pending(conn, "similar_artists", entity_types=("artist",),
                            retry_statuses=("error",)) is None


class TestTheProviderMatchedUniverse:
    def test_an_unmatched_artist_is_not_offered(self, conn):
        """Its similars would have no source id to be keyed by, so offering it
        would only mark it failed forever."""
        _artist(conn, "Unmatched")

        assert next_pending(conn, "similar_artists", entity_types=("artist",),
                            require_provider_id=True) is None

    def test_a_promoted_column_counts_as_matched(self, conn):
        artist = _artist(conn, "Rone", spotify_id="sp-1")

        item = next_pending(conn, "similar_artists", entity_types=("artist",),
                            require_provider_id=True)

        assert item is not None and item["id"] == artist

    def test_an_external_id_counts_as_matched(self, conn):
        artist = _artist(conn, "Rone", external_ids={"deezer": "dz-1"})

        item = next_pending(conn, "similar_artists", entity_types=("artist",),
                            require_provider_id=True)

        assert item is not None and item["id"] == artist

    def test_an_unsupported_external_id_does_not_enter_the_queue(self, conn):
        _artist(conn, "Rone", external_ids={"discogs": "dc-1"})

        assert next_pending(conn, "similar_artists", entity_types=("artist",),
                            require_provider_id=True) is None

    def test_without_the_restriction_everyone_is_offered(self, conn):
        artist = _artist(conn, "Unmatched")

        item = next_pending(conn, "similar_artists", entity_types=("artist",))

        assert item is not None and item["id"] == artist


class TestTheParentsProviderIdOnAChildItem:
    """Five workers verify a child match against the parent artist's own provider
    id — a track our library credits to one artist but which lives on another
    artist's album would otherwise stamp the wrong id onto our artist. Each of them
    had its own two-query dance for it; one option here replaces five copies."""

    def test_an_album_carries_its_artists_id(self, conn):
        artist = _artist(conn, "Rone", external_ids={"qobuz": "qb-artist"})
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(?,'Tohu Bohu','album')", (artist,)).lastrowid

        item = next_pending(conn, "qobuz", entity_types=("album",),
                            include_parent_id=True)

        assert item["id"] == album
        assert item["artist_qobuz_id"] == "qb-artist"

    def test_a_track_reaches_through_its_album(self, conn):
        """A track's artist is two joins away in lib2 — track → album → primary
        artist — where legacy carried tracks.artist_id on the row itself."""
        artist = _artist(conn, "Rone", external_ids={"qobuz": "qb-artist"})
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(?,'Tohu Bohu','album')", (artist,)).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Bora')",
            (album,)).lastrowid

        item = next_pending(conn, "qobuz", entity_types=("track",),
                            include_parent_id=True)

        assert item["id"] == track
        assert item["artist_qobuz_id"] == "qb-artist"

    def test_an_unmatched_parent_yields_none_for_the_key(self, conn):
        """Present but empty, so the caller's `if not parent_id: return True` guard
        reads the same as it did on legacy."""
        artist = _artist(conn, "Rone")
        conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(?,'Tohu Bohu','album')", (artist,))

        item = next_pending(conn, "qobuz", entity_types=("album",),
                            include_parent_id=True)

        assert item["artist_qobuz_id"] is None

    def test_a_promoted_column_counts(self, conn):
        artist = _artist(conn, "Rone", spotify_id="sp-artist")
        conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(?,'Tohu Bohu','album')", (artist,))

        item = next_pending(conn, "spotify", entity_types=("album",),
                            include_parent_id=True)

        assert item["artist_spotify_id"] == "sp-artist"

    def test_an_artist_item_gets_no_such_key(self, conn):
        _artist(conn, "Rone", external_ids={"qobuz": "qb-artist"})

        item = next_pending(conn, "qobuz", entity_types=("artist",),
                            include_parent_id=True)

        assert "artist_qobuz_id" not in item

    def test_it_is_off_by_default(self, conn):
        artist = _artist(conn, "Rone", external_ids={"qobuz": "qb-artist"})
        conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
            "VALUES(?,'Tohu Bohu','album')", (artist,))

        item = next_pending(conn, "qobuz", entity_types=("album",))

        assert "artist_qobuz_id" not in item


class TestTheStatusBreakdown:
    def test_every_outcome_is_tallied(self, conn):
        matched = _artist(conn, "A", spotify_id="sp-1")
        missing = _artist(conn, "B", spotify_id="sp-2")
        broken = _artist(conn, "C", spotify_id="sp-3")
        _artist(conn, "D", spotify_id="sp-4")  # never attempted
        record_attempt(conn, entity_type="artist", entity_id=matched,
                       service="similar_artists", status="matched")
        record_attempt(conn, entity_type="artist", entity_id=missing,
                       service="similar_artists", status="not_found")
        record_attempt(conn, entity_type="artist", entity_id=broken,
                       service="similar_artists", status="error")

        counts = status_counts(conn, "similar_artists", "artist")

        assert counts == {"matched": 1, "not_found": 1, "error": 1,
                          "pending": 1, "total": 4}

    def test_the_universe_narrows_the_totals_too(self, conn):
        """A tally over a different population than the queue picks from would
        show a percentage that never reaches 100."""
        _artist(conn, "Matched", spotify_id="sp-1")
        _artist(conn, "Unmatched")

        counts = status_counts(conn, "similar_artists", "artist",
                               require_provider_id=True)

        assert counts["total"] == 1
        assert counts["pending"] == 1

    def test_another_services_attempts_are_not_counted(self, conn):
        artist = _artist(conn, "Rone", spotify_id="sp-1")
        record_attempt(conn, entity_type="artist", entity_id=artist,
                       service="lastfm", status="matched")

        counts = status_counts(conn, "similar_artists", "artist")

        assert counts == {"matched": 0, "not_found": 0, "error": 0,
                          "pending": 1, "total": 1}
