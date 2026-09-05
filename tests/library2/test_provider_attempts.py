"""The bookkeeping every enrichment worker needs before it can leave legacy.

A worker does not simply "enrich everything". It picks a batch, and the pick is
driven by two legacy columns per provider: ``<service>_match_status`` and
``<service>_last_attempted``. Without an equivalent, a worker moved to lib2
would re-ask every provider about every entity on every cycle — it could not
tell "never tried" from "tried on Tuesday and Last.fm has never heard of them".

lib2 keeps it as a ledger rather than a column pair per provider per table.
That is the same reason lib2 exists: in the legacy shape, adding Bandcamp meant
three ``ALTER TABLE``s, and the fourteenth provider means three more.

Deliberately *not* part of the legacy→lib2 mirror: the trigger's own contract is
that ordinary bookkeeping traffic must not enqueue anything, and
``*_last_attempted`` is written on every single provider call. The existing state
is seeded once by a backfill instead.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.provider_attempts import (
    attempt_state, backfill_from_legacy, due_entities,
    ensure_provider_attempt_schema, record_attempt,
)
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    ensure_provider_attempt_schema(c.cursor())
    for name in ("A", "B", "C"):
        c.execute("INSERT INTO lib2_artists(name, sort_name) VALUES(?,?)", (name, name))
    c.commit()
    yield c
    c.close()


class TestRecording:
    def test_an_attempt_is_readable_back(self, conn):
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="not_found")

        state = attempt_state(conn, entity_type="artist", entity_id=1)

        assert state["lastfm"]["status"] == "not_found"
        assert state["lastfm"]["attempts"] == 1
        assert state["lastfm"]["last_attempted_at"]

    def test_a_second_attempt_updates_in_place_and_counts(self, conn):
        for _ in range(3):
            record_attempt(conn, entity_type="artist", entity_id=1,
                           service="lastfm", status="not_found")

        state = attempt_state(conn, entity_type="artist", entity_id=1)

        assert state["lastfm"]["attempts"] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_provider_attempts").fetchone()[0] == 1

    def test_a_success_after_failures_resets_the_counter(self, conn):
        """Attempt count exists to back off on repeated failure. Carrying it past
        a success would keep backing off a provider that just answered."""
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="error")
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="matched")

        state = attempt_state(conn, entity_type="artist", entity_id=1)

        assert state["lastfm"]["status"] == "matched"
        assert state["lastfm"]["attempts"] == 1

    def test_services_and_entity_types_do_not_collide(self, conn):
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="matched")
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="genius", status="not_found")
        record_attempt(conn, entity_type="track", entity_id=1,
                       service="lastfm", status="error")

        state = attempt_state(conn, entity_type="artist", entity_id=1)

        assert state["lastfm"]["status"] == "matched"
        assert state["genius"]["status"] == "not_found"

    def test_an_unknown_service_is_refused(self, conn):
        """A typo'd service name would create a ledger row no worker ever reads,
        so the entity looks permanently un-attempted."""
        with pytest.raises(ValueError):
            record_attempt(conn, entity_type="artist", entity_id=1,
                           service="lastfmm", status="matched")


class TestPickingTheNextBatch:
    def test_never_attempted_entities_come_first(self, conn):
        record_attempt(conn, entity_type="artist", entity_id=2,
                       service="lastfm", status="matched")

        due = due_entities(conn, entity_type="artist", service="lastfm", limit=10)

        assert due == [1, 3]

    def test_a_matched_entity_is_not_due_again(self, conn):
        for entity_id in (1, 2, 3):
            record_attempt(conn, entity_type="artist", entity_id=entity_id,
                           service="lastfm", status="matched")

        assert due_entities(conn, entity_type="artist", service="lastfm") == []

    def test_a_failed_entity_becomes_due_again_after_the_retry_window(self, conn):
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="not_found")
        conn.execute(
            "UPDATE lib2_provider_attempts "
            "SET last_attempted_at = datetime('now', '-40 days') WHERE entity_id=1")
        record_attempt(conn, entity_type="artist", entity_id=2,
                       service="lastfm", status="not_found")
        conn.commit()

        due = due_entities(conn, entity_type="artist", service="lastfm",
                           retry_after_days=30, limit=10)

        assert 1 in due, "a 40-day-old miss is worth retrying"
        assert 2 not in due, "a miss from today is not"

    def test_the_limit_is_honoured(self, conn):
        assert len(due_entities(conn, entity_type="artist", service="lastfm",
                                limit=2)) == 2

    def test_a_missing_ledger_table_still_yields_work(self, conn):
        """A worker must not stall because its bookkeeping has not been created
        yet — an install with no ledger has attempted nothing."""
        conn.execute("DROP TABLE lib2_provider_attempts")
        conn.commit()

        assert due_entities(conn, entity_type="artist", service="lastfm") == [1, 2, 3]


class TestSeedingFromLegacy:
    def test_legacy_status_and_timestamp_are_carried_over(self, conn):
        """Switching a worker to lib2 must not make it re-ask every provider
        about the whole library, so the existing history is seeded once."""
        conn.executescript(
            """
            CREATE TABLE artists(
                id INTEGER PRIMARY KEY, name TEXT,
                lastfm_match_status TEXT, lastfm_last_attempted TIMESTAMP,
                genius_match_status TEXT, genius_last_attempted TIMESTAMP
            );
            INSERT INTO artists(id, name, lastfm_match_status, lastfm_last_attempted,
                                genius_match_status, genius_last_attempted)
            VALUES(500, 'A', 'not_found', '2026-07-01 10:00:00', 'matched', NULL);
            """
        )
        conn.execute("UPDATE lib2_artists SET legacy_artist_id=500 WHERE id=1")
        conn.commit()

        stats = backfill_from_legacy(conn)

        assert stats["seeded"] == 2
        state = attempt_state(conn, entity_type="artist", entity_id=1)
        assert state["lastfm"]["status"] == "not_found"
        assert state["lastfm"]["last_attempted_at"].startswith("2026-07-01")
        assert state["genius"]["status"] == "matched"

    def test_the_backfill_is_idempotent_and_never_overwrites_newer_work(self, conn):
        conn.executescript(
            """
            CREATE TABLE artists(
                id INTEGER PRIMARY KEY, name TEXT,
                lastfm_match_status TEXT, lastfm_last_attempted TIMESTAMP
            );
            INSERT INTO artists(id, name, lastfm_match_status, lastfm_last_attempted)
            VALUES(500, 'A', 'not_found', '2026-07-01 10:00:00');
            """
        )
        conn.execute("UPDATE lib2_artists SET legacy_artist_id=500 WHERE id=1")
        conn.commit()
        backfill_from_legacy(conn)
        record_attempt(conn, entity_type="artist", entity_id=1,
                       service="lastfm", status="matched")

        second = backfill_from_legacy(conn)

        assert second["seeded"] == 0
        assert attempt_state(
            conn, entity_type="artist", entity_id=1)["lastfm"]["status"] == "matched"

    def test_a_row_with_no_legacy_twin_is_left_alone(self, conn):
        conn.execute(
            "CREATE TABLE artists(id INTEGER PRIMARY KEY, name TEXT, "
            "lastfm_match_status TEXT, lastfm_last_attempted TIMESTAMP)")
        conn.commit()

        assert backfill_from_legacy(conn)["seeded"] == 0

    def test_no_legacy_table_at_all_is_not_an_error(self, conn):
        """After the legacy tables are dropped (§32.3.1 stage 4) this must go
        quiet on its own rather than raise on every start."""
        assert backfill_from_legacy(conn) == {"seeded": 0, "scanned": 0}

    def test_all_pages_and_similar_artist_state_are_seeded(self, conn):
        conn.execute("CREATE TABLE artists(id INTEGER PRIMARY KEY, "
                     "similar_artists_match_status TEXT)")
        for entity_id in range(1, 4):
            conn.execute("INSERT INTO artists VALUES(?, 'matched')", (entity_id,))
            conn.execute("UPDATE lib2_artists SET legacy_artist_id=? WHERE id=?",
                         (entity_id, entity_id))

        assert backfill_from_legacy(conn, limit=1) == {"seeded": 3, "scanned": 3}
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_provider_attempts WHERE service='similar_artists'"
        ).fetchone()[0] == 3


def test_upgrade_import_itself_seeds_attempt_history(legacy_db):
    from core.library2.importer import import_legacy_library

    with legacy_db._get_connection() as source:
        source.execute("ALTER TABLE artists ADD COLUMN similar_artists_match_status TEXT")
        source.execute(
            "UPDATE artists SET similar_artists_match_status='matched' WHERE id=1")
        source.commit()

    import_legacy_library(legacy_db)

    with legacy_db._get_connection() as conn:
        assert conn.execute(
            "SELECT status FROM lib2_provider_attempts "
            "WHERE entity_type='artist' AND service='similar_artists'"
        ).fetchone()[0] == "matched"
