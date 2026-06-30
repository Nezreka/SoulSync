"""Fixtures for Library v2 tests.

These tests are intentionally self-contained: they build a synthetic *legacy*
library (``artists`` / ``albums`` / ``tracks``) in a throwaway SQLite file and run
the v2 importer/queries against it. No Flask, no full app — just sqlite3 and
``core.library2``.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest


class LegacyDBShim:
    """Mimics the ``MusicDatabase._get_connection`` contract the importer needs."""

    def __init__(self, path: str):
        self.path = path

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


_LEGACY_DDL = """
CREATE TABLE artists(
    id INTEGER PRIMARY KEY, name TEXT, thumb_url TEXT, genres TEXT, summary TEXT,
    spotify_artist_id TEXT, musicbrainz_id TEXT
);
CREATE TABLE albums(
    id INTEGER PRIMARY KEY, artist_id INTEGER, title TEXT, year INTEGER,
    thumb_url TEXT, genres TEXT, track_count INTEGER, release_date TEXT
);
CREATE TABLE tracks(
    id INTEGER PRIMARY KEY, album_id INTEGER, artist_id INTEGER, title TEXT,
    track_number INTEGER, duration INTEGER, file_path TEXT, bitrate INTEGER,
    file_size INTEGER, track_artist TEXT
);
"""

# A small but representative legacy library:
# - Drake/Views: a 2-track album; track 100 credits "Drake feat. Wizkid".
# - Drake/One Dance: a single-track album => type 'single', same song as track 100.
_LEGACY_SEED = """
INSERT INTO artists VALUES(1,'Drake','http://img','["rap"]','A rapper','sp1',NULL);
INSERT INTO albums  VALUES(10,1,'Views',2016,NULL,'["rap"]',2,'2016-04-29');
INSERT INTO albums  VALUES(11,1,'One Dance',2016,NULL,NULL,1,NULL);
INSERT INTO tracks  VALUES(100,10,1,'One Dance',1,200000,'/m/01.flac',1000000,5000,'Drake feat. Wizkid');
INSERT INTO tracks  VALUES(101,10,1,'Hotline Bling',2,180000,NULL,NULL,NULL,NULL);
INSERT INTO tracks  VALUES(102,11,1,'One Dance',1,200000,'/m/single.flac',900,5000,NULL);
"""


@pytest.fixture
def legacy_db(tmp_path):
    """A populated synthetic legacy DB; yields a LegacyDBShim."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_DDL)
    conn.executescript(_LEGACY_SEED)
    conn.commit()
    conn.close()
    yield LegacyDBShim(path)


@pytest.fixture
def imported_conn(legacy_db):
    """Run the importer, then yield an open connection to the resulting DB."""
    from core.library2.importer import import_legacy_library
    import_legacy_library(legacy_db)
    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()
