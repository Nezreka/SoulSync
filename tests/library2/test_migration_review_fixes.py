"""Regressions the "everything onto lib2" rewrite introduced (issues.md §38).

Each of these worked before the port and stopped working after it, which is why
they are collected here rather than spread across the suites of the modules they
belong to: the shared property under test is "the new code still does what the
code it replaced did".
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.provider_attempts import (
    ensure_provider_attempt_schema, record_attempt,
)
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    ensure_provider_attempt_schema(c.cursor())
    yield c
    c.close()


def _artist(conn, name="Muse", **columns):
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES(?,?)", (name, name),
    ).lastrowid
    for column, value in columns.items():
        conn.execute(f"UPDATE lib2_artists SET {column}=? WHERE id=?",
                     (value, artist_id))
    return artist_id


# ── LV2-MIG-01 ─────────────────────────────────────────────────────────────

def test_the_mapping_backfill_terminates_on_a_blank_server_id(conn):
    """The batch predicate must select only what the write will accept.

    ``upsert_mapping`` refuses a blank ``server_id``; the SELECT only excluded
    NULL. The row came back on every pass, was never written, and the loop —
    whose sole exit condition is an empty batch — spun at 100% CPU from startup.
    """
    from core.library2.media_mappings import backfill_legacy_mappings

    _artist(conn, "Blank", server_source="plex", server_id="")
    _artist(conn, "Real", server_source="plex", server_id="p-7")

    passes = []

    def _on_batch():
        passes.append(1)
        if len(passes) > 4:
            raise AssertionError("backfill_legacy_mappings did not terminate")

    backfill_legacy_mappings(conn.cursor(), connection=conn, batch_size=10,
                             on_batch=_on_batch)

    mapped = conn.execute(
        "SELECT server_id FROM lib2_media_server_mappings "
        "WHERE entity_type='artist'").fetchall()
    assert [row["server_id"] for row in mapped] == ["p-7"]


# ── LV2-MIG-02 ─────────────────────────────────────────────────────────────

def test_an_import_does_not_stamp_disc_one_over_a_known_disc(conn):
    """Ownership may correct a row, never blank it.

    The importer never passes a disc, so the writer's own default was written
    outright over the real value — every import of a multi-disc release moved
    its tracks to disc 1. The mapping-only branch had always used COALESCE.
    """
    from core.library2.media_server_sync import upsert_track

    artist = _artist(conn)
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'Absolution')",
        (artist,)).lastrowid
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number,"
        "                        server_source,server_id)"
        " VALUES(?,'Time Is Running Out',7,2,'soulsync','ss-1')", (album,),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state)"
        " VALUES(?,'/music/d2-07.flac',1,'active')", (track,))

    assert upsert_track(
        conn.cursor(), server_source="soulsync", server_id="ss-1",
        album_id=album, artist_id=artist, title="Time Is Running Out",
        file_path="/music/d2-07.flac", allow_create=True,
    ) == track

    row = conn.execute(
        "SELECT track_number, disc_number FROM lib2_tracks WHERE id=?",
        (track,)).fetchone()
    assert (row["track_number"], row["disc_number"]) == (7, 2)


def test_a_new_track_still_defaults_to_disc_and_track_one(conn):
    """Dropping the parameter default must not leave fresh rows with NULLs."""
    from core.library2.media_server_sync import upsert_track

    artist = _artist(conn)
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'Absolution')",
        (artist,)).lastrowid

    track = upsert_track(
        conn.cursor(), server_source="soulsync", server_id="ss-new",
        album_id=album, artist_id=artist, title="Stockholm Syndrome",
        file_path="/music/new.flac", allow_create=True)

    row = conn.execute(
        "SELECT track_number, disc_number FROM lib2_tracks WHERE id=?",
        (track,)).fetchone()
    assert (row["track_number"], row["disc_number"]) == (1, 1)


# ── LV2-MIG-04 ─────────────────────────────────────────────────────────────

class TestTheConflictCheckNarrowsInSql:
    """The predicate moved into SQL; what counts as a conflict must not change."""

    def test_an_id_stored_only_in_external_ids_is_still_found(self, conn):
        from core.library2.worker_support import provider_id_conflict

        holder = _artist(conn, "Rone", external_ids=json.dumps({"deezer": "42"}))
        mine = _artist(conn, "Röyksopp")

        assert provider_id_conflict(conn, "deezer", "42", mine, "Röyksopp") == "Rone"
        assert provider_id_conflict(conn, "deezer", "42", holder, "Rone") is None

    def test_a_numeric_json_id_is_still_found(self, conn):
        """`_ids` stringifies; a JSON number must not slip past the SQL filter."""
        from core.library2.worker_support import provider_id_conflict

        _artist(conn, "Rone", external_ids=json.dumps({"deezer": 42}))
        mine = _artist(conn, "Röyksopp")

        assert provider_id_conflict(conn, "deezer", "42", mine, "Röyksopp") == "Rone"

    def test_the_promoted_column_is_still_searched(self, conn):
        from core.library2.worker_support import provider_id_conflict

        _artist(conn, "Rone", spotify_id="SP1")
        mine = _artist(conn, "Röyksopp")

        assert provider_id_conflict(conn, "spotify", "SP1", mine, "Röyksopp") == "Rone"

    def test_another_services_id_is_not_a_collision(self, conn):
        from core.library2.worker_support import provider_id_conflict

        _artist(conn, "Rone", external_ids=json.dumps({"discogs": "42"}))
        mine = _artist(conn, "Röyksopp")

        assert provider_id_conflict(conn, "deezer", "42", mine, "Röyksopp") is None


# ── LV2-MIG-05 ─────────────────────────────────────────────────────────────

def test_the_unattempted_half_of_the_queue_needs_no_temp_btree(conn):
    """The queue's hot path must stop early, not sort the whole table first.

    A worker asks for one row up to three times per tick. Ordering unattempted
    rows and expired retries in a single query forced a temp b-tree over every
    artist/album/track row before that one row came back.
    """
    from core.library2.worker_queue import _pending_sql

    for entity_type in ("artist", "album", "track"):
        plan = " ".join(
            str(row[3]) for row in conn.execute(
                "EXPLAIN QUERY PLAN "
                + _pending_sql(entity_type, service="spotify", phase="new")
                + " LIMIT 1",
                {"entity": entity_type, "service": "spotify", "window": "-30 days"},
            ).fetchall())
        assert "TEMP B-TREE" not in plan.upper(), f"{entity_type}: {plan}"


def test_the_queue_still_serves_unattempted_before_expired_retries(conn):
    """The split must not reorder the work."""
    from core.library2.worker_queue import next_pending, pending_count

    stale = _artist(conn, "Stale")
    fresh = _artist(conn, "Fresh")
    record_attempt(conn, entity_type="artist", entity_id=stale,
                   service="spotify", status="not_found",
                   attempted_at="2020-01-01 00:00:00")

    assert next_pending(conn, "spotify")["id"] == fresh
    assert pending_count(conn, "spotify") == 2

    record_attempt(conn, entity_type="artist", entity_id=fresh,
                   service="spotify", status="matched")

    assert next_pending(conn, "spotify")["id"] == stale
    assert pending_count(conn, "spotify") == 1


# ── LV2-MIG-10 ─────────────────────────────────────────────────────────────

def test_a_deleted_entity_takes_its_ledger_rows_with_it(conn):
    """Orphans are counted as processed, so they pin the progress bar at 100%."""
    artist = _artist(conn)
    record_attempt(conn, entity_type="artist", entity_id=artist,
                   service="spotify", status="matched")

    conn.execute("DELETE FROM lib2_artists WHERE id=?", (artist,))

    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_provider_attempts").fetchone()[0] == 0


def test_the_existing_orphan_backlog_is_cleared_once(tmp_path):
    """The ledger shipped without the trigger, so a backlog already exists."""
    from core.library2.worker_queue import progress_breakdown

    c = sqlite3.connect(str(tmp_path / "lib2.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        ensure_library_v2_schema(c)
        ensure_provider_attempt_schema(c.cursor())
        _artist(c, "Alive")
        c.execute("DROP TRIGGER trg_lib2_artists_delete_provider_attempts")
        for orphan in (900, 901):
            record_attempt(c, entity_type="artist", entity_id=orphan,
                           service="spotify", status="matched")

        assert progress_breakdown(c, "spotify", entity_types=("artist",))["artists"]["percent"] == 100

        ensure_provider_attempt_schema(c.cursor())

        assert c.execute(
            "SELECT COUNT(*) FROM lib2_provider_attempts").fetchone()[0] == 0
        assert progress_breakdown(c, "spotify", entity_types=("artist",))["artists"]["percent"] == 0
    finally:
        c.close()
