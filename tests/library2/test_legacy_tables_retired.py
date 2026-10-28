"""A finished upgrade renames the legacy catalogue (#1062 review, point 3).

Anything still reading ``artists``/``albums``/``tracks`` after the cutover
would quietly get a frozen copy. Renamed to ``legacy_*`` it fails loudly,
while the importer -- the one reader left -- still rebuilds from it.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from core.library2 import bootstrap as lib2_bootstrap
from core.library2.importer import import_legacy_library
from core.library2.migration_gate import migration_required
from core.library2.source_snapshot import retire_legacy_tables


def _tables(db):
    with closing(db._get_connection()) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(db, table):
    with closing(db._get_connection()) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _upgraded(db):
    assert lib2_bootstrap.run_bootstrap_if_needed(db, lambda *_: True)["success"] is True


def test_the_upgrade_renames_the_legacy_catalogue(legacy_db):
    tracks = _count(legacy_db, "tracks")

    _upgraded(legacy_db)

    assert {"legacy_artists", "legacy_albums", "legacy_tracks"} <= _tables(legacy_db)
    assert not {"artists", "albums", "tracks"} & _tables(legacy_db)
    assert _count(legacy_db, "legacy_tracks") == tracks
    with pytest.raises(sqlite3.OperationalError, match="no such table: tracks"):
        _count(legacy_db, "tracks")


def test_the_rename_changes_nothing_the_upgrade_watches(legacy_db):
    _upgraded(legacy_db)

    assert lib2_bootstrap.run_bootstrap_if_needed(legacy_db, lambda *_: True) == {
        "skipped": "already_done"}
    assert migration_required(legacy_db) is False


def test_an_install_upgraded_earlier_is_renamed_at_the_next_start(legacy_db, monkeypatch):
    monkeypatch.setattr("core.library2.source_snapshot.retire_legacy_tables", lambda conn: [])
    _upgraded(legacy_db)
    assert "tracks" in _tables(legacy_db)
    monkeypatch.undo()

    assert lib2_bootstrap.run_bootstrap_if_needed(legacy_db, lambda *_: True) == {
        "skipped": "already_done"}
    assert "legacy_tracks" in _tables(legacy_db)


def test_a_rebuild_still_reads_the_renamed_catalogue(legacy_db):
    _upgraded(legacy_db)
    imported = _count(legacy_db, "lib2_tracks")

    stats = import_legacy_library(legacy_db, reset=True)

    assert stats["tracks"] == _count(legacy_db, "legacy_tracks")
    assert _count(legacy_db, "lib2_tracks") == imported
    # the views lived on the importer's connection only
    assert "tracks" not in _tables(legacy_db)


def test_nothing_is_renamed_before_the_upgrade_is_done(legacy_db):
    with closing(legacy_db._get_connection()) as conn:
        assert retire_legacy_tables(conn) == []
    assert "tracks" in _tables(legacy_db)


def test_a_taken_legacy_name_is_left_alone(legacy_db):
    _upgraded(legacy_db)
    with closing(legacy_db._get_connection()) as conn:
        # an upstream build ran on this database again and recreated it
        conn.execute("CREATE TABLE tracks(id TEXT PRIMARY KEY)")
        conn.commit()
        assert retire_legacy_tables(conn) == []
    assert {"tracks", "legacy_tracks"} <= _tables(legacy_db)
