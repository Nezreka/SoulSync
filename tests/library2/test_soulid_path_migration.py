"""Backfilling ``lib2_artists.soul_id_path`` (L2-014).

The column is additive, so every artist that already had a soul_id when it
landed starts NULL — and nothing ever fills it: the worker only looks at
artists with NO soul_id, and the id-algorithm migration returns early on an
install already reading the current version. MetaSync/Hydrabase then cannot
tell a provider-canonical, reproducible artist key from a library-dependent
album/name fallback.

Regenerating the ids to find out is not an option: a soul_id is the shared
content key peers have already traded claims about. Each path is PROVEN locally
instead, and anything that cannot be proven is recorded as ``unknown`` rather
than assumed canonical.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.schema import ensure_library_v2_schema
from core.soulid_worker import SoulIDWorker, generate_soul_id


class _DB:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(path)
    ensure_library_v2_schema(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    return _DB(path)


def _worker(db):
    worker = SoulIDWorker.__new__(SoulIDWorker)
    worker.db = db
    return worker


def _artist(db, name, soul_id, albums=()):
    conn = db._get_connection()
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, soul_id) VALUES(?,?,?)",
        (name, name, soul_id)).lastrowid
    for title in albums:
        conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
            "VALUES(?,?,'album')", (artist_id, title))
    conn.commit()
    conn.close()
    return artist_id


def _paths(db):
    conn = db._get_connection()
    try:
        return {r["id"]: r["soul_id_path"] for r in
                conn.execute("SELECT id, soul_id_path FROM lib2_artists")}
    finally:
        conn.close()


def test_a_name_only_id_is_proven_and_labelled(db):
    artist = _artist(db, "Rone", generate_soul_id("Rone"))

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {artist: "name"}


def test_an_album_derived_id_is_proven_and_labelled(db):
    artist = _artist(db, "Rone", generate_soul_id("Rone", "Tohu Bohu"),
                     albums=("Tohu Bohu",))

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {artist: "album"}


def test_an_album_derived_id_survives_the_library_gaining_releases(db):
    """The id was minted from the alphabetically-first owned album at the time.
    A release added since would be first today, so only checking today's first
    album would misread this as canonical and quietly upgrade its trust."""
    artist = _artist(db, "Rone", generate_soul_id("Rone", "Tohu Bohu"),
                     albums=("Aleph", "Tohu Bohu"))

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {artist: "album"}


def test_an_id_that_reproduces_neither_is_recorded_as_unproven(db):
    """A canonical (provider-id derived) key looks exactly like an id whose
    source album has since been deleted, so this is where it stops guessing."""
    artist = _artist(db, "Rone", generate_soul_id("Rone", "1234567"),
                     albums=("Tohu Bohu",))

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {artist: "unknown"}


def test_an_existing_path_is_never_overwritten(db):
    artist = _artist(db, "Rone", generate_soul_id("Rone"))
    conn = db._get_connection()
    conn.execute("UPDATE lib2_artists SET soul_id_path='canonical'")
    conn.commit()
    conn.close()

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {artist: "canonical"}


def test_unnamed_placeholder_ids_are_left_alone(db):
    artist = _artist(db, "Rone", "soul_unnamed_7")

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {artist: None}


def test_it_runs_once_and_stamps_its_own_version(db):
    artist = _artist(db, "Rone", generate_soul_id("Rone"))
    worker = _worker(db)

    worker._migrate_artist_soul_id_paths()

    conn = db._get_connection()
    try:
        assert conn.execute(
            "SELECT value FROM metadata WHERE key=?",
            (SoulIDWorker.PATH_MIGRATION_KEY,)).fetchone()[0] == \
            SoulIDWorker.PATH_MIGRATION_VERSION
        conn.execute("UPDATE lib2_artists SET soul_id_path=NULL")
        conn.commit()
    finally:
        conn.close()

    worker._migrate_artist_soul_id_paths()

    assert _paths(db) == {artist: None}


def test_a_database_without_the_column_is_a_no_op(db):
    conn = db._get_connection()
    conn.execute("ALTER TABLE lib2_artists DROP COLUMN soul_id_path")
    conn.commit()
    conn.close()

    _worker(db)._migrate_artist_soul_id_paths()  # must not raise
