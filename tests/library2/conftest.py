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


@pytest.fixture(scope="session", autouse=True)
def _shutdown_library2_background_workers():
    """Do not let queued provider work delay pytest process shutdown."""
    yield
    from core.library2 import artwork
    from core.library2 import track_reconcile_trigger
    from core.library2 import unmapped_trigger

    track_reconcile_trigger.reset_for_tests()
    unmapped_trigger.reset_for_tests()
    artwork.shutdown_background_executor()


class LegacyDBShim:
    """Mimics the ``MusicDatabase._get_connection`` contract the importer needs."""

    def __init__(self, path: str):
        self.path = path

    @property
    def database_path(self) -> str:
        """Alias so lib2 helpers that key off ``database.database_path``
        (e.g. ``core.library2.artwork``) work against this shim too."""
        return self.path

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


# Columns a real installation grows through the ALTER TABLE migration chain in
# ``database/music_database.py`` (provider IDs, ISRC, disc number). The DDL above
# deliberately stays narrow — most lib2 tests INSERT positionally — so anything
# that has to behave like a *migrated* production DB asks for ``migrated_legacy_db``
# instead. Consumers that SELECT these columns directly (the repair-job scanners)
# only tell the truth when they run against this shape.
_MIGRATED_COLUMNS = {
    "artists": [
        "soul_id TEXT",
        "soul_id_path TEXT",
        "discogs_bio TEXT",
        "discogs_members TEXT",
        "discogs_urls TEXT",
        "genius_alt_names TEXT",
        "genius_description TEXT",
        "genius_url TEXT",
        "lastfm_bio TEXT",
        "lastfm_listeners INTEGER",
        "lastfm_playcount INTEGER",
        "lastfm_similar TEXT",
        "lastfm_tags TEXT",
        "lastfm_url TEXT",
        "server_source TEXT",
        "created_at TIMESTAMP",
    ],
    "albums": [
        "spotify_album_id TEXT",
        "itunes_album_id TEXT",
        "deezer_id TEXT",
        "discogs_id TEXT",
        "soul_id TEXT",
        "server_source TEXT",
        "created_at TIMESTAMP",
        "record_type TEXT",
        "bandcamp_id TEXT",
        "canonical_source TEXT",
        "canonical_album_id TEXT",
        "canonical_score REAL",
        "canonical_resolved_at TIMESTAMP",
        "bandcamp_label TEXT",
        "bandcamp_tags TEXT",
        "discogs_catno TEXT",
        "discogs_country TEXT",
        "discogs_genres TEXT",
        "discogs_label TEXT",
        "discogs_rating REAL",
        "discogs_rating_count INTEGER",
        "discogs_styles TEXT",
        "lastfm_listeners INTEGER",
        "lastfm_playcount INTEGER",
        "lastfm_tags TEXT",
        "lastfm_wiki TEXT",
        "canonical_locked INTEGER",
    ],
    "tracks": [
        "isrc TEXT",
        "musicbrainz_recording_id TEXT",
        "spotify_track_id TEXT",
        "itunes_track_id TEXT",
        "deezer_id TEXT",
        "soul_id TEXT",
        "album_soul_id TEXT",
        "disc_number INTEGER DEFAULT 1",
        "server_source TEXT",
        "created_at TIMESTAMP",
        "bandcamp_label TEXT",
        "bandcamp_tags TEXT",
        "genius_description TEXT",
        "genius_url TEXT",
        "lastfm_listeners INTEGER",
        "lastfm_playcount INTEGER",
        "lastfm_tags TEXT",
        "bandcamp_id TEXT",
    ],
}


def _migrate_legacy_schema(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        for table, columns in _MIGRATED_COLUMNS.items():
            for column in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        conn.commit()
    finally:
        conn.close()


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
def migrated_legacy_db(legacy_db):
    """``legacy_db`` widened to a fully migrated production schema.

    Note the columns are appended, so positional ``INSERT INTO <table> VALUES``
    no longer works on this fixture — name the columns.
    """
    _migrate_legacy_schema(legacy_db.path)
    return legacy_db


@pytest.fixture
def legacy_db_factory(tmp_path):
    """Factory for a legacy DB with N single-track albums under one artist —
    enough distinct work items to exercise bounded-concurrency precache pools
    (see tests/library2/test_artwork_precache_parallel.py, precache_tracklists
    concurrency tests in test_completeness.py)."""

    def _make(n_albums: int = 6, filename: str = "legacy_multi.db") -> LegacyDBShim:
        path = str(tmp_path / filename)
        conn = sqlite3.connect(path)
        conn.executescript(_LEGACY_DDL)
        conn.execute(
            "INSERT INTO artists VALUES(1,'Many','http://img',NULL,NULL,'sp1',NULL)"
        )
        for i in range(n_albums):
            album_id = 10 + i
            track_id = 100 + i
            conn.execute(
                "INSERT INTO albums VALUES(?,1,?,2020,NULL,NULL,1,NULL)",
                (album_id, f"Album {i}"),
            )
            conn.execute(
                "INSERT INTO tracks VALUES(?,?,1,?,1,200000,?,1000000,5000,NULL)",
                (track_id, album_id, f"Track {i}", f"/m/{i}.flac"),
            )
        conn.commit()
        conn.close()
        return LegacyDBShim(path)

    return _make


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


from lib2_ownership import own_every_track  # noqa: E402,F401
