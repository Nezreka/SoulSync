"""§56.2 provider match provenance, on the Library-v2 catalogue.

The table used to be filled by database triggers on the legacy
``<provider>_match_status`` columns. Those triggers wrote ``entity_type='artist'``
while every reader here asks for ``'lib2_artist'``, so the automatic half had
been writing rows nothing could read even before the legacy schema went away.

The manual half was broken by the same namespace split, but more quietly: the
table's CHECK listed only the three legacy spellings, so every Library-v2 write
was rejected by the constraint and swallowed by the ``except`` around it. These
tests pin the widened constraint and the rebuild that carries an existing
database over.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.match_status import set_library_v2_match
from database.music_database import MusicDatabase


def _row(conn, entity_type="lib2_artist", entity_id=1, service="spotify"):
    return conn.execute(
        """SELECT origin, external_id, actor
             FROM metadata_match_provenance
            WHERE entity_type=? AND entity_id=? AND service=?""",
        (entity_type, str(entity_id), service),
    ).fetchone()


@pytest.fixture
def conn(tmp_path):
    db = MusicDatabase(str(tmp_path / "matches.db"))
    connection = db._get_connection()
    connection.execute(
        "INSERT INTO lib2_artists(id, name, sort_name) VALUES(1, 'Drake', 'Drake')")
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def test_a_manual_match_is_recorded(conn):
    """The regression: this row could not be written at all. The constraint
    refused ``lib2_artist`` and ``set_library_v2_match`` logs and continues,
    so the failure was invisible from the outside — the chip simply never
    carried a ``last_attempted``."""
    set_library_v2_match(conn, "artist", 1, "spotify", "sp-manual", actor="admin")

    assert dict(_row(conn)) == {
        "origin": "manual",
        "external_id": "sp-manual",
        "actor": "admin",
    }


def test_choosing_again_overwrites_the_same_row(conn):
    set_library_v2_match(conn, "artist", 1, "spotify", "sp-first")
    set_library_v2_match(conn, "artist", 1, "spotify", "sp-second", actor="someone")

    assert dict(_row(conn)) == {
        "origin": "manual",
        "external_id": "sp-second",
        "actor": "someone",
    }
    assert conn.execute(
        "SELECT COUNT(*) FROM metadata_match_provenance").fetchone()[0] == 1


def test_clearing_the_match_removes_the_provenance(conn):
    set_library_v2_match(conn, "artist", 1, "spotify", "sp-1")
    assert _row(conn) is not None

    set_library_v2_match(conn, "artist", 1, "spotify", None)
    assert _row(conn) is None


def test_the_chip_reports_the_recorded_match(conn):
    """The reader is what the constraint was starving: ``_native_chips`` only
    fills ``last_attempted`` when a provenance row matches the stored id."""
    from core.library2.match_status import entity_match_status

    set_library_v2_match(conn, "artist", 1, "spotify", "sp-1")
    chips = {c["service"]: c for c in entity_match_status(conn, "artist", 1)}

    assert chips["spotify"]["status"] == "matched"
    assert chips["spotify"]["external_id"] == "sp-1"
    assert chips["spotify"]["last_attempted"] is not None


def test_an_upgraded_database_is_rebuilt_and_keeps_its_rows(tmp_path):
    """An install created before this fix has the narrow CHECK. The migration
    rebuilds the table once and carries the old rows across rather than
    dropping an audit trail."""
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.execute(
        """CREATE TABLE metadata_match_provenance (
               entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
               service TEXT NOT NULL, origin TEXT NOT NULL, external_id TEXT,
               matched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               actor TEXT,
               PRIMARY KEY (entity_type, entity_id, service),
               CHECK (entity_type IN ('artist', 'album', 'track')),
               CHECK (origin IN ('automatic', 'manual', 'legacy')))""")
    raw.execute(
        "INSERT INTO metadata_match_provenance(entity_type, entity_id, service, "
        "origin, external_id, actor) VALUES('artist', 7, 'spotify', 'automatic', "
        "'sp-old', 'system')")
    raw.commit()
    raw.close()

    conn = MusicDatabase(path)._get_connection()
    try:
        assert dict(_row(conn, "artist", 7)) == {
            "origin": "automatic", "external_id": "sp-old", "actor": "system"}
        conn.execute(
            "INSERT INTO metadata_match_provenance(entity_type, entity_id, "
            "service, origin, external_id) "
            "VALUES('lib2_track', 9, 'deezer', 'manual', 'dz-9')")
        conn.commit()
        assert _row(conn, "lib2_track", 9, "deezer") is not None
    finally:
        conn.close()


def test_the_rebuild_runs_only_once(tmp_path):
    """A second init must not churn the table again — the guard is the CHECK
    text, so a table that already names ``lib2_artist`` is left alone."""
    db = MusicDatabase(str(tmp_path / "twice.db"))
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO metadata_match_provenance(entity_type, entity_id, service, "
        "origin, external_id) VALUES('lib2_album', 3, 'qobuz', 'manual', 'q-3')")
    conn.commit()

    db._add_metadata_match_provenance(conn.cursor())
    conn.commit()

    assert _row(conn, "lib2_album", 3, "qobuz") is not None
    assert not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='metadata_match_provenance_new'"
    ).fetchone()
    conn.close()


# The triggers that used to fill this table are gone from the source, but an
# install that ever ran a build which created them still carries them in
# ``sqlite_master`` — nothing ever dropped them. They are not merely dead: the
# rebuild above drops the table they write into, and SQLite reparses every
# trigger in the schema on ``ALTER TABLE ... RENAME TO``. A stale trigger then
# fails that reparse, the whole init raises, and the container cannot boot.
_LEGACY_TRIGGER_TABLE = """
    CREATE TABLE artists (
        id INTEGER PRIMARY KEY, name TEXT, spotify_id TEXT,
        spotify_match_status TEXT, spotify_last_attempted TIMESTAMP)
"""
_LEGACY_TRIGGERS = [
    """CREATE TRIGGER metadata_match_artists_spotify_insert
       AFTER INSERT ON artists
       WHEN NEW.spotify_match_status='matched'
        AND COALESCE(CAST(NEW.spotify_id AS TEXT), '') <> ''
       BEGIN
           INSERT INTO metadata_match_provenance(
               entity_type, entity_id, service, origin, external_id,
               matched_at, actor)
           VALUES('artist', NEW.id, 'spotify', 'automatic',
                  CAST(NEW.spotify_id AS TEXT),
                  COALESCE(NEW.spotify_last_attempted, CURRENT_TIMESTAMP),
                  'system');
       END""",
    """CREATE TRIGGER metadata_match_artists_spotify_clear
       AFTER UPDATE OF spotify_id, spotify_match_status ON artists
       WHEN COALESCE(NEW.spotify_match_status, '') <> 'matched'
       BEGIN
           DELETE FROM metadata_match_provenance
            WHERE entity_type='artist' AND entity_id=NEW.id
              AND service='spotify';
       END""",
]
_OLD_PROVENANCE_TABLE = """
    CREATE TABLE metadata_match_provenance (
        entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL,
        service TEXT NOT NULL, origin TEXT NOT NULL, external_id TEXT,
        matched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actor TEXT,
        PRIMARY KEY (entity_type, entity_id, service),
        CHECK (entity_type IN ('artist', 'album', 'track')),
        CHECK (origin IN ('automatic', 'manual', 'legacy')))
"""


def _stale_trigger_database(path: str) -> None:
    raw = sqlite3.connect(path)
    raw.execute(_LEGACY_TRIGGER_TABLE)
    raw.execute(_OLD_PROVENANCE_TABLE)
    for statement in _LEGACY_TRIGGERS:
        raw.execute(statement)
    raw.execute(
        "INSERT INTO metadata_match_provenance(entity_type, entity_id, service, "
        "origin, external_id, actor) VALUES('artist', 7, 'spotify', 'automatic', "
        "'sp-old', 'system')")
    raw.commit()
    raw.close()


def _trigger_names(conn) -> set:
    return {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
    }


def test_a_stale_trigger_does_not_break_the_rebuild(tmp_path):
    """The boot failure: ``error in trigger metadata_match_artists_spotify_insert:
    no such table: main.metadata_match_provenance``, raised by the RENAME, from
    a trigger no running code creates any more."""
    path = str(tmp_path / "stale.db")
    _stale_trigger_database(path)

    conn = MusicDatabase(path)._get_connection()
    try:
        assert not [n for n in _trigger_names(conn)
                    if n.startswith("metadata_match_")]
        assert dict(_row(conn, "artist", 7)) == {
            "origin": "automatic", "external_id": "sp-old", "actor": "system"}
    finally:
        conn.close()


def test_a_stale_trigger_is_dropped_even_without_a_rebuild(tmp_path):
    """The trigger is a landmine independent of this table: it breaks the next
    schema reparse from any migration, and it writes the legacy ``artist``
    namespace that no Library-v2 reader asks for. A database whose CHECK is
    already current must still lose it."""
    path = str(tmp_path / "current.db")
    MusicDatabase(path)          # builds the current schema
    raw = sqlite3.connect(path)
    raw.execute(_LEGACY_TRIGGER_TABLE.replace("CREATE TABLE artists",
                                              "CREATE TABLE IF NOT EXISTS artists"))
    for statement in _LEGACY_TRIGGERS:
        raw.execute(statement)
    raw.commit()
    raw.close()

    db = MusicDatabase(path)
    conn = db._get_connection()
    try:
        db._add_metadata_match_provenance(conn.cursor())
        conn.commit()
        assert not [n for n in _trigger_names(conn)
                    if n.startswith("metadata_match_")]
    finally:
        conn.close()
