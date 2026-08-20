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


def _settled(conn):
    """The ledger's delete trigger is only installed once the migration is done."""
    conn.execute("UPDATE lib2_bootstrap_state SET status='done' WHERE id=1")


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    _settled(c)
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


def _owned_artist(conn, name):
    """An artist the enrichment queue will offer: one with a live file behind it."""
    artist = _artist(conn, name)
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,?)",
        (artist, f"{name} LP")).lastrowid
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title) VALUES(?,?)", (album, name)).lastrowid
    conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
        "VALUES(?,?,1,'active')", (track, f"/music/{track}.flac"))
    return artist


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

    stale = _owned_artist(conn, "Stale")
    fresh = _owned_artist(conn, "Fresh")
    record_attempt(conn, entity_type="artist", entity_id=stale,
                   service="spotify", status="not_found",
                   attempted_at="2020-01-01 00:00:00")

    # Unattempted before an expired retry, across every entity type — the
    # legacy priority 1-3 then 4-6, which per-type phasing had inverted.
    assert next_pending(conn, "spotify", entity_types=("artist",))["id"] == fresh
    assert pending_count(conn, "spotify", entity_types=("artist",)) == 2

    record_attempt(conn, entity_type="artist", entity_id=fresh,
                   service="spotify", status="matched")

    assert next_pending(conn, "spotify", entity_types=("artist",))["id"] == stale
    assert pending_count(conn, "spotify", entity_types=("artist",)) == 1


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
        _settled(c)
        ensure_provider_attempt_schema(c.cursor())
        # Owned, because progress_breakdown counts the universe the queue
        # actually works on -- a bare artist row with no files behind it is
        # a discography entry no worker will ever enrich.
        _owned_artist(c, "Alive")
        c.execute("DROP TRIGGER trg_lib2_artists_delete_provider_attempts")
        for orphan in (900, 901):
            record_attempt(c, entity_type="artist", entity_id=orphan,
                           service="spotify", status="matched")

        # The backlog is real rows pointing at artists that no longer exist.
        # (It used to be visible through progress_breakdown as a 100% bar for a
        # library with one unenriched artist. That symptom is gone on its own:
        # the breakdown now joins attempts through the entity table and scopes
        # both halves to the owned library, so an orphan cannot be counted as
        # progress. The rows themselves still have to be purged, which is what
        # this test is actually about.)
        assert c.execute(
            "SELECT COUNT(*) FROM lib2_provider_attempts").fetchone()[0] == 2
        assert progress_breakdown(c, "spotify", entity_types=("artist",))["artists"] == {
            "matched": 0, "total": 1, "percent": 0,
        }

        ensure_provider_attempt_schema(c.cursor())

        assert c.execute(
            "SELECT COUNT(*) FROM lib2_provider_attempts").fetchone()[0] == 0
        assert progress_breakdown(c, "spotify", entity_types=("artist",))["artists"]["percent"] == 0
    finally:
        c.close()


def test_the_orphan_purge_waits_for_the_migration_to_finish(tmp_path):
    """Mid-migration, "no entity row" does not yet mean "no entity".

    The bootstrap import seeds the ledger from the legacy columns precisely so a
    fresh v2 install does not re-ask every provider about the whole library.
    Purging against a half-imported catalogue would throw that away.
    """
    c = sqlite3.connect(str(tmp_path / "lib2.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        ensure_library_v2_schema(c)
        c.execute("UPDATE lib2_bootstrap_state SET status='running' WHERE id=1")
        ensure_provider_attempt_schema(c.cursor())
        # Seeded history for an artist this run has not imported yet.
        record_attempt(c, entity_type="artist", entity_id=900,
                       service="spotify", status="matched")

        ensure_provider_attempt_schema(c.cursor())
        assert c.execute(
            "SELECT COUNT(*) FROM lib2_provider_attempts").fetchone()[0] == 1
        assert c.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
            " AND name LIKE '%delete_provider_attempts'").fetchone()[0] == 0

        _settled(c)
        ensure_provider_attempt_schema(c.cursor())
        assert c.execute(
            "SELECT COUNT(*) FROM lib2_provider_attempts").fetchone()[0] == 0
    finally:
        c.close()


# ── Second round (issues.md §39) ───────────────────────────────────────────

@pytest.fixture
def music_db(tmp_path):
    from database.music_database import MusicDatabase

    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _server_track(db, *, server_id, title="Song", source="plex"):
    conn = db._get_connection()
    try:
        artist = conn.execute(
            "INSERT INTO lib2_artists(name,name_key) VALUES('Muse','muse')").lastrowid
        album = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title) VALUES(?,'Absolution')",
            (artist,)).lastrowid
        track = conn.execute(
            "INSERT INTO lib2_tracks(album_id,title,server_source,server_id)"
            " VALUES(?,?,?,?)", (album, title, source, server_id)).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state)"
            " VALUES(?,?,1,'active')", (track, f"/music/{track}.flac"))
        conn.commit()
        return track
    finally:
        conn.close()


def test_a_blank_server_id_never_enters_the_stale_set(music_db):
    """One blank id would detach every blank-id row of the source.

    This used to return primary keys, where a stale entry meant one row. It now
    returns server ids and the deep scan diffs them against what the server
    reported — and a server never reports an empty id, so `''` was always stale.
    The 50% safety net cannot catch it: it counts distinct ids while the detach
    removes catalogue rows.
    """
    blank = _server_track(music_db, server_id="", title="Blank")
    real = _server_track(music_db, server_id="p-7", title="Real")

    assert music_db.get_all_track_ids_for_server("plex") == {"p-7"}

    # Even handed one explicitly, it must not become a wildcard.
    music_db.delete_stale_tracks({""}, "plex")
    conn = music_db._get_connection()
    try:
        alive = {int(r[0]) for r in conn.execute("SELECT id FROM lib2_tracks")}
    finally:
        conn.close()
    assert alive == {blank, real}


def test_the_mapping_beats_a_stale_snapshot_for_the_same_server_id(music_db):
    """`server_source`/`server_id` on the entity is the compatibility snapshot.

    A re-match moves the mapping row but leaves that pair behind, so both can
    answer for one id — and an OR returned whichever row the engine reached
    first, which for a UNION is the lower id: the stale one.
    """
    stale = _server_track(music_db, server_id="p-1", title="Old")
    current = _server_track(music_db, server_id="p-1", title="New")
    conn = music_db._get_connection()
    try:
        conn.execute(
            "INSERT INTO lib2_media_server_mappings"
            "(entity_type,entity_id,server_source,server_id,match_status)"
            " VALUES('track',?,'plex','p-1','recognized')", (current,))
        conn.commit()
    finally:
        conn.close()

    found = music_db.get_track_by_server_id("p-1", "plex")

    assert found is not None and found.title == "New", f"stale row {stale} won"


class TestTheQueueOffersOnlyTheOwnedLibrary:
    """Legacy's tables *were* the owned library; v2's are not."""

    def test_an_artist_without_a_file_is_not_offered(self, conn):
        from core.library2.worker_queue import next_pending, pending_count

        _artist(conn, "Merely Listed")

        assert next_pending(conn, "spotify") is None
        assert pending_count(conn, "spotify") == 0

    def test_a_discography_album_is_not_offered(self, conn):
        from core.library2.worker_queue import next_pending

        artist = _owned_artist(conn, "Watched")
        conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id,title,origin)"
            " VALUES(?,'Not Owned Yet','discography')", (artist,))

        offered = []
        while True:
            item = next_pending(conn, "spotify")
            if item is None:
                break
            offered.append((item["type"], item["name"]))
            record_attempt(conn, entity_type=item["type"], entity_id=item["id"],
                           service="spotify", status="matched")
        assert ("album", "Not Owned Yet") not in offered
        assert ("album", "Watched LP") in offered

    def test_an_unattempted_track_precedes_an_expired_artist_retry(self, conn):
        from core.library2.worker_queue import next_pending

        artist = _owned_artist(conn, "Rone")
        record_attempt(conn, entity_type="artist", entity_id=artist,
                       service="spotify", status="not_found",
                       attempted_at="2020-01-01 00:00:00")

        assert next_pending(conn, "spotify")["type"] == "album"


def test_the_provider_id_conflict_check_is_indexed(conn):
    """Both halves of the OR, or SQLite scans the whole artist table for it."""
    from core.library2.provider_ids import external_id_sql

    for service, column in (("spotify", "spotify_id"), ("deezer", None)):
        holds = f"{external_id_sql('external_ids', service)} = ?"
        params = [1, "x"]
        if column:
            holds = f"{column} = ? OR {holds}"
            params.insert(1, "x")
        plan = " ".join(str(row[3]) for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id, name, spotify_id, musicbrainz_id, "
            f"external_ids FROM lib2_artists WHERE id <> ? AND ({holds})",
            params).fetchall())
        assert "SCAN lib2_artists" not in plan, f"{service}: {plan}"
