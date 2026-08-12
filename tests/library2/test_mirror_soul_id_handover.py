"""The soul id crosses to lib2 once, and only into a gap (docs §50.4.4.12).

``soul_id`` is not in ``match_status.SERVICES`` — Hydrabase is a consumer of the
id, not a provider that hands one out — so the mirror never carried it and lib2
had no column for it at all. Its worker now writes lib2 directly, which leaves
one thing that has to happen exactly once: the ids already generated on the
legacy side, at three seconds of API courtesy per artist, must not be thrown away
and recomputed.

Backfill is the only setting that gets both halves right, the same argument as
every migrated provider: overwrite would let the backlog sweep push the legacy
value back over a freshly generated native one, and dropping the field would
strand thousands of artist ids nothing would ever carry across.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.enrich import resync_entity_from_legacy
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    c.executescript(
        """
        CREATE TABLE artists(id INTEGER PRIMARY KEY, name TEXT, soul_id TEXT);
        CREATE TABLE albums(id INTEGER PRIMARY KEY, title TEXT, soul_id TEXT);
        CREATE TABLE tracks(
            id INTEGER PRIMARY KEY, title TEXT, soul_id TEXT, album_soul_id TEXT);
        """
    )
    c.commit()
    yield c
    c.close()


def _artist(conn, legacy_soul, lib2_soul=None):
    legacy_id = conn.execute(
        "INSERT INTO artists(name, soul_id) VALUES('A', ?)", (legacy_soul,)).lastrowid
    lib2_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, soul_id, legacy_artist_id) "
        "VALUES('A','A',?,?)", (lib2_soul, legacy_id)).lastrowid
    conn.commit()
    return legacy_id, lib2_id


def _resync(conn, entity_type, table, legacy_id, lib2_id):
    assert resync_entity_from_legacy(conn, entity_type, lib2_id, legacy_id)
    conn.commit()


class TestTheCarryOver:
    def test_an_empty_native_column_receives_the_legacy_id(self, conn):
        legacy_id, lib2_id = _artist(conn, "soul_legacy")
        _resync(conn, "artist", "artists", legacy_id, lib2_id)
        assert conn.execute(
            "SELECT soul_id FROM lib2_artists WHERE id=?", (lib2_id,)
        ).fetchone()[0] == "soul_legacy"

    def test_a_native_id_survives_the_drain(self, conn):
        """The worker writes lib2 now; the sweep must not undo that."""
        legacy_id, lib2_id = _artist(conn, "soul_legacy", lib2_soul="soul_native")
        _resync(conn, "artist", "artists", legacy_id, lib2_id)
        assert conn.execute(
            "SELECT soul_id FROM lib2_artists WHERE id=?", (lib2_id,)
        ).fetchone()[0] == "soul_native"

    def test_the_release_specific_track_id_crosses_too(self, conn):
        legacy_id = conn.execute(
            "INSERT INTO tracks(title, soul_id, album_soul_id) "
            "VALUES('T','soul_song','soul_release')").lastrowid
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES('A','A')").lastrowid
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Al')",
            (artist_id,)).lastrowid
        lib2_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, legacy_track_id) VALUES(?,'T',?)",
            (album_id, legacy_id)).lastrowid
        conn.commit()
        _resync(conn, "track", "tracks", legacy_id, lib2_id)
        row = conn.execute(
            "SELECT soul_id, album_soul_id FROM lib2_tracks WHERE id=?",
            (lib2_id,)).fetchone()
        assert (row["soul_id"], row["album_soul_id"]) == ("soul_song", "soul_release")


class TestTheImporter:
    """The other half of the carry-over: a first migration must not lose them.

    Waiting for the mirror's backlog sweep would work eventually — it enqueues
    500 divergent rows per tick — but a fresh import already has the legacy row
    open, and every artist id it drops costs three seconds of API courtesy to
    make again.
    """

    def test_a_first_import_brings_all_four_columns_across(self, migrated_legacy_db):
        from core.library2.importer import import_legacy_library

        conn = migrated_legacy_db._get_connection()
        try:
            conn.execute("UPDATE artists SET soul_id='soul_artist' WHERE id=1")
            conn.execute("UPDATE albums SET soul_id='soul_album' WHERE id=10")
            conn.execute(
                "UPDATE tracks SET soul_id='soul_song', album_soul_id='soul_release' "
                "WHERE id=100")
            conn.commit()
        finally:
            conn.close()

        import_legacy_library(migrated_legacy_db)

        conn = migrated_legacy_db._get_connection()
        try:
            assert conn.execute(
                "SELECT soul_id FROM lib2_artists WHERE legacy_artist_id=1"
            ).fetchone()[0] == "soul_artist"
            assert conn.execute(
                "SELECT soul_id FROM lib2_albums WHERE legacy_album_id=10"
            ).fetchone()[0] == "soul_album"
            row = conn.execute(
                "SELECT soul_id, album_soul_id FROM lib2_tracks WHERE legacy_track_id=100"
            ).fetchone()
            assert (row[0], row[1]) == ("soul_song", "soul_release")
        finally:
            conn.close()


class TestTheDeclaration:
    def test_the_metric_stops_reporting_a_row_the_worker_has_filled(self, conn):
        """A backfill-only field lib2 already holds is not divergence — reporting
        it would put a row into the metric no sweep could ever clear."""
        from core.library2.enrich import MIRROR_SPECS, mirror_divergence

        legacy_id, lib2_id = _artist(conn, "soul_legacy", lib2_soul="soul_native")
        legacy_row = conn.execute(
            "SELECT * FROM artists WHERE id=?", (legacy_id,)).fetchone()
        lib2_row = conn.execute(
            "SELECT * FROM lib2_artists WHERE id=?", (lib2_id,)).fetchone()
        assert "soul_id" not in mirror_divergence(
            MIRROR_SPECS["artist"], legacy_row, lib2_row)

    def test_an_unfilled_row_is_still_reported_so_the_sweep_picks_it_up(self, conn):
        from core.library2.enrich import MIRROR_SPECS, mirror_divergence

        legacy_id, lib2_id = _artist(conn, "soul_legacy")
        legacy_row = conn.execute(
            "SELECT * FROM artists WHERE id=?", (legacy_id,)).fetchone()
        lib2_row = conn.execute(
            "SELECT * FROM lib2_artists WHERE id=?", (lib2_id,)).fetchone()
        assert "soul_id" in mirror_divergence(
            MIRROR_SPECS["artist"], legacy_row, lib2_row)
