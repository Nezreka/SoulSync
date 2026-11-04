"""An older build booting on an upgraded database must not cost the catalogue.

A pre-v2 build recreates ``artists``/``albums``/``tracks`` empty beside the
retired ``legacy_*`` copies. The next v2 boot read those stubs as a changed,
empty library (a wishlist made it importable), re-ran the import, and the
snapshot pass removed every file and most tracks. It happened on a real
310k-track library during an 11 second checkout between two branches.
"""

from __future__ import annotations

from contextlib import closing

from core.library2 import bootstrap as lib2_bootstrap
from core.library2.importer import import_legacy_library


def _count(db, sql):
    with closing(db._get_connection()) as conn:
        return conn.execute(sql).fetchone()[0]


def _tables(db):
    with closing(db._get_connection()) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _older_build_boots(db):
    with closing(db._get_connection()) as conn:
        for table in ("artists", "albums", "tracks"):
            conn.execute(f"CREATE TABLE {table} AS SELECT * FROM legacy_{table} WHERE 0")
        conn.commit()


def _upgraded(db):
    assert lib2_bootstrap.run_bootstrap_if_needed(db, lambda *_: True)["success"] is True


def test_a_finished_upgrade_ignores_an_older_builds_empty_tables(legacy_db_factory, monkeypatch):
    db = legacy_db_factory(150)
    _upgraded(db)
    files = _count(db, "SELECT COUNT(*) FROM lib2_track_files")
    _older_build_boots(db)
    # the wishlist is what made an empty catalogue look importable
    monkeypatch.setattr(lib2_bootstrap, "legacy_user_state_waiting", lambda _db: True)

    assert lib2_bootstrap.run_bootstrap_if_needed(db, lambda *_: True) == {"skipped": "already_done"}
    assert _count(db, "SELECT COUNT(*) FROM lib2_track_files") == files == 150
    assert not {"artists", "albums", "tracks"} & _tables(db)
    assert {"legacy_artists", "legacy_albums", "legacy_tracks"} <= _tables(db)


def test_stub_tables_with_rows_are_kept(legacy_db_factory):
    # a downgraded build that really scanned wrote a library; that's data
    db = legacy_db_factory(3)
    _upgraded(db)
    _older_build_boots(db)
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO artists SELECT * FROM legacy_artists")
        conn.commit()

    lib2_bootstrap.run_bootstrap_if_needed(db, lambda *_: True)

    assert "artists" in _tables(db)


def test_the_import_keeps_the_catalogue_when_the_source_comes_back_empty(legacy_db_factory):
    db = legacy_db_factory(150)
    _upgraded(db)
    _older_build_boots(db)

    stats = import_legacy_library(db)

    assert stats["reconcile_skipped"] == 150
    assert _count(db, "SELECT COUNT(*) FROM lib2_track_files") == 150
    assert _count(db, "SELECT COUNT(*) FROM lib2_tracks WHERE legacy_track_id IS NOT NULL") == 150


def test_a_source_that_lost_a_few_tracks_still_reconciles(legacy_db_factory):
    db = legacy_db_factory(150)
    _upgraded(db)
    with closing(db._get_connection()) as conn:
        conn.execute("DELETE FROM legacy_tracks WHERE id IN (100, 101)")
        conn.commit()

    stats = import_legacy_library(db)

    assert "reconcile_skipped" not in stats
    assert stats["reconciled_files"] == 2
    assert _count(db, "SELECT COUNT(*) FROM lib2_track_files") == 148
