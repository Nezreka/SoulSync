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
