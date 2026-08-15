"""Batch selection for enrichment workers, from lib2 instead of legacy.

Every one of the sixteen workers picks its next item the same way: unattempted
artists, then albums, then tracks, then failures whose retry window has expired.
Legacy drove that from `<service>_match_status`; this drives it from the provider
attempt ledger, and returns the exact dict shape the workers already consume so
the change inside each worker stays small.

Written once here rather than sixteen times: the pinned-group override, the
artist→album→track priority and the retry ordering are the same rules for all of
them, and duplicating them per worker is how they would drift.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.provider_attempts import (
    ensure_provider_attempt_schema, record_attempt,
)
from core.library2.schema import ensure_library_v2_schema
from core.library2.worker_queue import next_pending, pending_count, progress_breakdown


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    ensure_provider_attempt_schema(c.cursor())
    artist = c.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES('Massive Attack','Massive Attack')"
    ).lastrowid
    album = c.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,album_type) "
        "VALUES(?,'Mezzanine','album')", (artist,)).lastrowid
    track = c.execute(
        "INSERT INTO lib2_tracks(album_id,title) VALUES(?,'Angel')", (album,)).lastrowid
    # The queue only offers what the user owns, and ownership is a live file row.
    c.execute("INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
              "VALUES(?,'/music/angel.flac',1,'active')", (track,))
    c.commit()
    yield c
    c.close()


class TestPriorityOrder:
    def test_an_unattempted_artist_comes_first(self, conn):
        item = next_pending(conn, "lastfm")

        assert item == {"type": "artist", "id": 1, "name": "Massive Attack"}

    def test_albums_follow_once_the_artists_are_done(self, conn):
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="matched")

        item = next_pending(conn, "lastfm")

        assert item["type"] == "album"
        assert item["name"] == "Mezzanine"
        assert item["artist"] == "Massive Attack", "the query needs the artist name"

    def test_tracks_come_last(self, conn):
        for entity in ("artist", "album"):
            record_attempt(conn, entity_type=entity, entity_id=1,
                           service="lastfm", status="matched")

        item = next_pending(conn, "lastfm")

        assert item["type"] == "track"
        assert item["name"] == "Angel"
        assert item["artist"] == "Massive Attack"

    def test_nothing_left_is_none(self, conn):
        for entity in ("artist", "album", "track"):
            record_attempt(conn, entity_type=entity, entity_id=1,
                           service="lastfm", status="matched")

        assert next_pending(conn, "lastfm") is None

    def test_a_failure_is_retried_only_after_its_window(self, conn):
        for entity in ("artist", "album", "track"):
            record_attempt(conn, entity_type=entity, entity_id=1,
                           service="lastfm", status="not_found")
        conn.commit()

        assert next_pending(conn, "lastfm", retry_after_days=30) is None

        conn.execute("UPDATE lib2_provider_attempts "
                     "SET last_attempted_at = datetime('now','-40 days')")
        conn.commit()

        assert next_pending(conn, "lastfm", retry_after_days=30)["type"] == "artist"

    def test_an_error_is_retried_after_the_window(self, conn):
        """Transient per-item errors must not remain a permanent black hole."""
        for entity in ("artist", "album", "track"):
            record_attempt(conn, entity_type=entity, entity_id=1,
                           service="lastfm", status="error")
        conn.execute("UPDATE lib2_provider_attempts "
                     "SET last_attempted_at = datetime('now','-400 days')")
        conn.commit()

        assert next_pending(conn, "lastfm", retry_after_days=30)["type"] == "artist"


class TestTheShapeWorkersConsume:
    def test_a_type_override_is_applied(self, conn):
        """Spotify and iTunes dispatch on 'album_individual'/'track_individual'."""
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="matched")

        item = next_pending(conn, "lastfm",
                            type_overrides={"album": "album_individual"})

        assert item["type"] == "album_individual"

    def test_a_pinned_entity_type_is_served_first(self, conn):
        """Manage Enrichment Workers can pin one entity type to the front."""
        item = next_pending(conn, "lastfm", pinned="track")

        assert item["type"] == "track"

    def test_an_exhausted_pin_falls_through_to_the_normal_order(self, conn):
        record_attempt(conn, entity_type="track", entity_id=1,
                       service="lastfm", status="matched")

        item = next_pending(conn, "lastfm", pinned="track")

        assert item["type"] == "artist"


class TestCounters:
    def test_pending_counts_every_entity_type(self, conn):
        assert pending_count(conn, "lastfm") == 3

        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="matched")

        assert pending_count(conn, "lastfm") == 2

    def test_the_breakdown_reports_per_entity_progress(self, conn):
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="matched")

        breakdown = progress_breakdown(conn, "lastfm")

        assert breakdown["artists"] == {"matched": 1, "total": 1, "percent": 100}
        assert breakdown["albums"] == {"matched": 0, "total": 1, "percent": 0}

    def test_an_attempt_counts_as_progress_even_when_it_found_nothing(self, conn):
        """Legacy counted any non-NULL match_status as processed. A 'not_found'
        is work done, and a progress bar that ignores it never reaches 100%."""
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="not_found")

        assert progress_breakdown(conn, "lastfm")["artists"]["matched"] == 1
