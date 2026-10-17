"""Backfilling ``lib2_artists.soul_id_path`` (L2-014).

The column is additive, so every artist that already had a soul_id when it
landed starts NULL — and nothing ever fills it: the worker only looks at
artists with NO soul_id, and the id-algorithm migration returns early on an
install that is already on the current version. MetaSync/Hydrabase then cannot
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

from core.soulid_worker import SoulIDWorker, generate_soul_id


class _DB:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        return conn


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "soulid.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE lib2_artists(id TEXT PRIMARY KEY, name TEXT, soul_id TEXT,
                                  soul_id_path TEXT, updated_at TIMESTAMP);
        CREATE TABLE lib2_albums(id TEXT PRIMARY KEY, primary_artist_id TEXT,
                                 title TEXT);
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.commit()
    conn.close()
    return _DB(path)


def _worker(db):
    worker = SoulIDWorker.__new__(SoulIDWorker)
    worker.db = db
    return worker


def _artist(db, artist_id, name, soul_id, albums=()):
    conn = db._get_connection()
    conn.execute("INSERT INTO lib2_artists(id, name, soul_id) VALUES(?,?,?)",
                 (artist_id, name, soul_id))
    for i, title in enumerate(albums):
        conn.execute(
            "INSERT INTO lib2_albums(id, primary_artist_id, title) VALUES(?,?,?)",
            (f"{artist_id}-al{i}", artist_id, title))
    conn.commit()
    conn.close()


def _paths(db):
    conn = db._get_connection()
    try:
        return dict(conn.execute("SELECT id, soul_id_path FROM lib2_artists").fetchall())
    finally:
        conn.close()


def test_a_name_only_id_is_proven_and_labelled(db):
    _artist(db, "a1", "Rone", generate_soul_id("Rone"))

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {"a1": "name"}


def test_an_album_derived_id_is_proven_and_labelled(db):
    _artist(db, "a1", "Rone", generate_soul_id("Rone", "Tohu Bohu"),
            albums=("Tohu Bohu",))

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {"a1": "album"}


def test_an_album_derived_id_survives_the_library_gaining_releases(db):
    """The id was minted from the alphabetically-first album at the time. A
    release added since would be first today, so only checking today's first
    album would misread this as canonical and quietly upgrade its trust."""
    _artist(db, "a1", "Rone", generate_soul_id("Rone", "Tohu Bohu"),
            albums=("Aleph", "Tohu Bohu"))

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {"a1": "album"}


def test_an_id_that_reproduces_neither_is_recorded_as_unproven(db):
    """A canonical (provider-id derived) key looks exactly like an id whose
    source album has since been deleted, so this is where the migration stops
    guessing."""
    _artist(db, "a1", "Rone", generate_soul_id("Rone", "1234567"),
            albums=("Tohu Bohu",))

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {"a1": "unknown"}


def test_an_existing_path_is_never_overwritten(db):
    _artist(db, "a1", "Rone", generate_soul_id("Rone"))
    conn = db._get_connection()
    conn.execute("UPDATE lib2_artists SET soul_id_path='canonical'")
    conn.commit()
    conn.close()

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {"a1": "canonical"}


def test_unnamed_placeholder_ids_are_left_alone(db):
    _artist(db, "a1", "Rone", "soul_unnamed_a1")

    _worker(db)._migrate_artist_soul_id_paths()

    assert _paths(db) == {"a1": None}


def test_it_runs_once_and_stamps_its_own_version(db):
    _artist(db, "a1", "Rone", generate_soul_id("Rone"))
    worker = _worker(db)

    worker._migrate_artist_soul_id_paths()

    conn = db._get_connection()
    try:
        assert conn.execute(
            "SELECT value FROM metadata WHERE key=?",
            (SoulIDWorker.PATH_MIGRATION_KEY,)).fetchone()[0] == \
            SoulIDWorker.PATH_MIGRATION_VERSION
        # A row that turns up NULL afterwards is not re-scanned.
        conn.execute("UPDATE lib2_artists SET soul_id_path=NULL")
        conn.commit()
    finally:
        conn.close()

    worker._migrate_artist_soul_id_paths()

    assert _paths(db) == {"a1": None}


def test_a_database_without_the_column_is_a_no_op(db):
    conn = db._get_connection()
    conn.executescript("""
        DROP TABLE lib2_artists;
        CREATE TABLE lib2_artists(id TEXT PRIMARY KEY, name TEXT, soul_id TEXT);
    """)
    conn.commit()
    conn.close()

    _worker(db)._migrate_artist_soul_id_paths()  # must not raise
