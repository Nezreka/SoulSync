"""Regression for the watchlist_artists rebuild dropping amazon_artist_id.

`amazon_artist_id` is added to watchlist_artists via ALTER (music_database.py
~1732), but the table-rebuild migrations (the spotify_id-nullable fix and the
profile-scoped UNIQUE rebuild) recreated the table from a hardcoded column list
that omitted amazon_artist_id — so on upgrade the column AND any stored Amazon
artist IDs were silently dropped.

These tests drive the REAL migrations through MusicDatabase() against a fresh
temp database that starts in the pre-migration shape (no profile-scoped UNIQUE,
amazon_artist_id present with data), then assert the column and its data survive.

Proven differential: with database/music_database.py reverted to pre-fix,
test_amazon_artist_id_data_survives_rebuild FAILS (the column/data are dropped);
with the fix it passes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from database.music_database import MusicDatabase


# A pre-profile-migration watchlist_artists schema (no UNIQUE(profile_id, ...),
# i.e. exactly the state that triggers the rebuild path) that ALREADY carries
# amazon_artist_id + the other source-id columns — mirroring a real DB that ran
# the 1732 ALTER before the rebuild migrations existed.
_OLD_WATCHLIST_SCHEMA = """
    CREATE TABLE watchlist_artists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        spotify_artist_id TEXT UNIQUE,
        itunes_artist_id TEXT,
        deezer_artist_id TEXT,
        discogs_artist_id TEXT,
        musicbrainz_artist_id TEXT,
        amazon_artist_id TEXT,
        artist_name TEXT NOT NULL,
        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_scan_timestamp TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""


def _seed_old_db(db_path: Path) -> None:
    """Create a pre-migration watchlist_artists with an Amazon-tagged row."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_OLD_WATCHLIST_SCHEMA)
        conn.execute(
            "INSERT INTO watchlist_artists "
            "(spotify_artist_id, amazon_artist_id, artist_name) VALUES (?, ?, ?)",
            ("spfy_abc", "B0AMAZONXYZ", "Amazon Tagged Artist"),
        )
        conn.commit()
    finally:
        conn.close()


def _watchlist_columns(db_path: Path) -> list:
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(watchlist_artists)")]
    finally:
        conn.close()


def test_amazon_artist_id_column_survives_rebuild(tmp_path: Path) -> None:
    """After the real migrations run, watchlist_artists must still have the
    amazon_artist_id column (the rebuild must not drop it)."""
    db_path = tmp_path / "old_library.db"
    _seed_old_db(db_path)

    # Driving MusicDatabase against this path runs the real _initialize_database,
    # which fires the watchlist_artists rebuild(s).
    MusicDatabase(str(db_path))

    cols = _watchlist_columns(db_path)
    assert "amazon_artist_id" in cols, (
        "amazon_artist_id was dropped by the watchlist_artists rebuild; "
        f"columns are: {cols}"
    )
    # The rebuild's whole purpose: profile-scoped uniqueness must still apply.
    assert "profile_id" in cols


def test_amazon_artist_id_data_survives_rebuild(tmp_path: Path) -> None:
    """The stored Amazon artist ID must be carried across the rebuild, not lost.
    This is the test that FAILS on pre-fix code."""
    db_path = tmp_path / "old_library.db"
    _seed_old_db(db_path)

    MusicDatabase(str(db_path))

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT amazon_artist_id FROM watchlist_artists WHERE artist_name = ?",
            ("Amazon Tagged Artist",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "the watchlist row was lost entirely during rebuild"
    assert row[0] == "B0AMAZONXYZ", (
        f"amazon_artist_id data was not preserved across rebuild (got {row[0]!r})"
    )


def test_fresh_db_has_amazon_artist_id_column(tmp_path: Path) -> None:
    """A brand-new database (no pre-existing table) must also end up with the
    amazon_artist_id column, so fresh installs match upgraded ones."""
    db_path = tmp_path / "fresh_library.db"
    MusicDatabase(str(db_path))
    assert "amazon_artist_id" in _watchlist_columns(db_path)
