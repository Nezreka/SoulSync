"""Library ownership: the owner column on a file, and the mapping rebuild.

Capacity only. Nothing reads any of it yet, so every test here is one assertion
in two halves: the shape a later implementation needs exists, AND an install
that has no own-library profile behaves exactly as it did before.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.media_mappings import (
    ensure_media_mapping_schema, resolve_mapping, upsert_mapping,
)
from core.library2.schema import LIB2_TRACK_FILES_DDL, ensure_library_v2_schema

_OLD_MAPPINGS_DDL = """
CREATE TABLE lib2_media_server_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('artist','album','track')),
    entity_id INTEGER NOT NULL,
    server_source TEXT NOT NULL,
    server_id TEXT NOT NULL,
    match_status TEXT NOT NULL DEFAULT 'recognized',
    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, server_source),
    UNIQUE(entity_type, server_source, server_id)
)
"""


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ddl_without_the_owner_column():
    """The lib2_track_files shape an install created before this change has.
    Derived from the DDL rather than copied, so it cannot drift away from it."""
    kept = [line for line in LIB2_TRACK_FILES_DDL.splitlines()
            if "owner_profile_id" not in line]
    return "\n".join(kept).replace(",\n)", "\n)")


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_library_v2_schema(connection)
    yield connection
    connection.close()


class TestTheOwnerColumn:
    def test_a_file_can_say_whose_library_it_is_in(self, conn):
        assert "owner_profile_id" in _columns(conn, "lib2_track_files")

    def test_and_says_nothing_by_default(self, conn):
        """Every row on every existing install. NULL has to stay meaningful as
        "the shared library", or the first implementation reads it as profile 0."""
        conn.execute("INSERT INTO lib2_track_files(path) VALUES('/music/a/b.flac')")
        row = conn.execute("SELECT owner_profile_id FROM lib2_track_files").fetchone()
        assert row["owner_profile_id"] is None

    def test_the_catalogue_rows_stay_ownerless(self, conn):
        """Deliberate: metadata is shared and deduplicated. Two people owning
        the same album must not produce two catalogue rows."""
        for table in ("lib2_artists", "lib2_albums", "lib2_tracks"):
            assert "owner_profile_id" not in _columns(conn, table)

    def test_an_install_that_predates_the_column_gains_it_and_keeps_its_rows(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(_ddl_without_the_owner_column())
        connection.execute("INSERT INTO lib2_track_files(path) VALUES('/music/old.flac')")
        assert "owner_profile_id" not in _columns(connection, "lib2_track_files")

        ensure_library_v2_schema(connection)

        assert "owner_profile_id" in _columns(connection, "lib2_track_files")
        row = connection.execute(
            "SELECT path, owner_profile_id FROM lib2_track_files").fetchone()
        assert row["path"] == "/music/old.flac"
        assert row["owner_profile_id"] is None
        connection.close()

    def test_the_index_only_covers_rows_that_have_an_owner(self, conn):
        """A full index would be one entry per file while nobody uses the
        feature. Partial keeps it empty until it earns its keep."""
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='idx_lib2_track_files_owner'"
        ).fetchone()
        assert sql and "WHERE owner_profile_id IS NOT NULL" in sql["sql"]


class TestTheMediaMappingRebuild:
    """The one constraint that actually blocked two libraries: an entity could
    hold one server id per server TYPE, so a second Jellyfin library had
    nowhere to go."""

    @pytest.fixture
    def old_install(self):
        # A whole lib2 schema, then its mapping table put back the way it was:
        # the migration runs against the entity tables and triggers it will
        # actually find in production, not against a table on its own.
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        ensure_library_v2_schema(connection)
        connection.execute("DROP TABLE lib2_media_server_mappings")
        connection.execute(_OLD_MAPPINGS_DDL)
        connection.execute(
            "INSERT INTO lib2_media_server_mappings"
            "(id, entity_type, entity_id, server_source, server_id) "
            "VALUES(7, 'album', 3, 'jellyfin', 'jf-3')")
        yield connection
        connection.close()

    def test_the_rows_survive_with_the_default_library(self, old_install):
        ensure_media_mapping_schema(old_install.cursor())
        row = old_install.execute("SELECT * FROM lib2_media_server_mappings").fetchone()
        assert row["id"] == 7
        assert row["server_id"] == "jf-3"
        assert row["server_library_id"] == ""

    def test_the_scratch_table_is_gone(self, old_install):
        ensure_media_mapping_schema(old_install.cursor())
        left = old_install.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%pre_library%'").fetchall()
        assert left == []

    def test_one_library_still_means_one_id_per_entity(self, old_install):
        ensure_media_mapping_schema(old_install.cursor())
        with pytest.raises(sqlite3.IntegrityError):
            old_install.execute(
                "INSERT INTO lib2_media_server_mappings"
                "(entity_type, entity_id, server_source, server_id) "
                "VALUES('album', 3, 'jellyfin', 'jf-other')")

    def test_one_library_still_means_one_entity_per_id(self, old_install):
        ensure_media_mapping_schema(old_install.cursor())
        with pytest.raises(sqlite3.IntegrityError):
            old_install.execute(
                "INSERT INTO lib2_media_server_mappings"
                "(entity_type, entity_id, server_source, server_id) "
                "VALUES('album', 99, 'jellyfin', 'jf-3')")

    def test_two_libraries_on_one_server_can_both_map_the_same_album(self, old_install):
        ensure_media_mapping_schema(old_install.cursor())
        old_install.execute(
            "INSERT INTO lib2_media_server_mappings"
            "(entity_type, entity_id, server_source, server_library_id, server_id) "
            "VALUES('album', 3, 'jellyfin', 'lib-b', 'jf-b-3')")
        ids = [r["server_id"] for r in old_install.execute(
            "SELECT server_id FROM lib2_media_server_mappings "
            "WHERE entity_type='album' AND entity_id=3 ORDER BY server_library_id")]
        assert ids == ["jf-3", "jf-b-3"]

    def test_running_it_twice_changes_nothing(self, old_install):
        cursor = old_install.cursor()
        ensure_media_mapping_schema(cursor)
        ensure_media_mapping_schema(cursor)
        assert old_install.execute(
            "SELECT COUNT(*) FROM lib2_media_server_mappings").fetchone()[0] == 1

    def test_the_delete_trigger_is_restored(self, conn):
        """It is dropped to get the rebuild's RENAME past it, so its return is
        the part worth pinning: without it a deleted artist leaves mappings."""
        conn.execute("INSERT INTO lib2_artists(id, name) VALUES(5, 'Gone')")
        upsert_mapping(conn.cursor(), "artist", 5, "plex", "px-5")
        assert resolve_mapping(conn.cursor(), "artist", "plex", "px-5") == 5

        conn.execute("DELETE FROM lib2_artists WHERE id=5")

        assert resolve_mapping(conn.cursor(), "artist", "plex", "px-5") is None

    def test_the_upsert_still_moves_a_rekeyed_id(self, conn):
        """The ON CONFLICT target had to name the widened constraint; if it
        named the old one every mapping write would raise instead."""
        cursor = conn.cursor()
        upsert_mapping(cursor, "track", 11, "navidrome", "nd-old")
        upsert_mapping(cursor, "track", 11, "navidrome", "nd-new")

        assert resolve_mapping(cursor, "track", "navidrome", "nd-new") == 11
        assert resolve_mapping(cursor, "track", "navidrome", "nd-old") is None
        assert conn.execute(
            "SELECT COUNT(*) FROM lib2_media_server_mappings").fetchone()[0] == 1


class TestTheUpgradePath:
    """Whose library a file is in has to survive the one-shot legacy import,
    or an install upgrading from upstream — where the owner sits on the legacy
    track row — silently hands every private library back to the shared one."""

    @staticmethod
    def _files(shim):
        connection = sqlite3.connect(shim.path)
        connection.row_factory = sqlite3.Row
        try:
            return {r["path"]: r["owner_profile_id"] for r in connection.execute(
                "SELECT path, owner_profile_id FROM lib2_track_files")}
        finally:
            connection.close()

    def test_a_legacy_owner_lands_on_the_file(self, legacy_db):
        from core.library2.importer import import_legacy_library

        connection = sqlite3.connect(legacy_db.path)
        connection.execute("ALTER TABLE tracks ADD COLUMN owner_profile_id INTEGER")
        owned = connection.execute(
            "SELECT id, file_path FROM tracks WHERE file_path IS NOT NULL "
            "AND TRIM(file_path) <> '' LIMIT 1").fetchone()
        assert owned, "the fixture needs at least one track with a file"
        connection.execute("UPDATE tracks SET owner_profile_id = 4 WHERE id = ?", (owned[0],))
        connection.commit()
        connection.close()

        import_legacy_library(legacy_db)

        assert self._files(legacy_db)[owned[1]] == 4

    def test_a_legacy_row_without_an_owner_is_the_shared_library(self, legacy_db):
        from core.library2.importer import import_legacy_library

        connection = sqlite3.connect(legacy_db.path)
        connection.execute("ALTER TABLE tracks ADD COLUMN owner_profile_id INTEGER")
        connection.commit()
        connection.close()

        import_legacy_library(legacy_db)

        assert set(self._files(legacy_db).values()) == {None}

    def test_an_install_that_never_had_own_libraries_imports_unchanged(self, legacy_db):
        """No such column at all — the overwhelmingly common upgrade. The
        importer must not care, and every file must read as shared."""
        from core.library2.importer import import_legacy_library

        stats = import_legacy_library(legacy_db)

        assert stats["files"] > 0
        assert set(self._files(legacy_db).values()) == {None}


class TestTheOwnershipPredicate:
    """`owned_sql` is the one definition of "the caller owns this". The scope
    rides on it so a query cannot accidentally be written without one."""

    @staticmethod
    def _owning_artist(conn, owner):
        from tests.support.catalogue_seed import seed_library_track
        track = seed_library_track(
            conn, artist=f'A{owner}', album=f'Al{owner}', title=f'T{owner}',
            artist_server_id=f'ar{owner}', album_server_id=f'al{owner}',
            track_server_id=f'tr{owner}', file_path=f'/m/{owner}.flac')
        conn.execute("UPDATE lib2_track_files SET owner_profile_id=? WHERE track_id=?",
                     (owner, track))
        return track

    def test_parked_means_no_filter_at_all(self):
        """The merge must not change a single query while the feature is off:
        'shared' would already hide an upgraded install's owned rows."""
        from core.library2.sql_util import owned_sql
        assert "owner_profile_id" not in owned_sql("track", "t")

    def test_an_explicit_scope_filters(self):
        from core.library2.sql_util import owned_sql
        assert "+owned_f.owner_profile_id IS NULL" in owned_sql("track", "t", scope="shared")
        assert "+owned_f.owner_profile_id = 7" in owned_sql("album", "al", scope=7)

    def test_any_owner_never_filters(self):
        """What the enrichment workers pass: metadata is shared, so a row is
        worth enriching whoever holds the file."""
        from core.library2.sql_util import ANY_OWNER, owned_sql
        assert "owner_profile_id" not in owned_sql("artist", "a", scope=ANY_OWNER)

    def test_the_planner_guard_survives(self):
        """The leading + keeps SQLite off the owner index. Losing it turned 2ms
        into 18s on a 300k-track library upstream, and nothing else would fail."""
        from core.library2.sql_util import owner_clause
        assert owner_clause("shared").lstrip().startswith("AND +")
        assert owner_clause(3).lstrip().startswith("AND +")

    def test_a_profiles_row_is_theirs_and_not_the_shared_librarys(self, conn):
        from core.library2.sql_util import ANY_OWNER, owned_sql

        self._owning_artist(conn, 7)

        def visible(scope):
            return conn.execute(
                "SELECT COUNT(*) FROM lib2_artists a WHERE "
                + owned_sql("artist", "a", scope=scope)).fetchone()[0]

        assert visible(7) == 1
        assert visible("shared") == 0
        assert visible(ANY_OWNER) == 1

    def test_a_row_with_no_owner_belongs_to_the_shared_library(self, conn):
        from core.library2.sql_util import owned_sql
        from tests.support.catalogue_seed import seed_library_track

        seed_library_track(conn, artist='Shared', album='Al', title='T',
                           file_path='/m/shared.flac')

        def visible(scope):
            return conn.execute(
                "SELECT COUNT(*) FROM lib2_artists a WHERE "
                + owned_sql("artist", "a", scope=scope)).fetchone()[0]

        assert visible("shared") == 1
        assert visible(7) == 0
