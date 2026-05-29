"""Tests for the one-time genres CSV->JSON normalization migration.

artists.genres / albums.genres historically stored either a JSON array (new
writes) or a legacy comma-separated string (old writes). _normalize_genres_to_json
rewrites legacy rows to canonical JSON, mirroring the readers' exact parse so the
genre VALUES are unchanged — only the storage format. These tests drive the real
method on a temp database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from database.music_database import MusicDatabase


def _fresh_db(tmp_path: Path) -> MusicDatabase:
    # Init creates the schema and (harmlessly) runs the normalization once on the
    # empty DB, setting the marker. Tests clear the marker + seed, then call the
    # method directly so they exercise the real normalization logic.
    return MusicDatabase(str(tmp_path / "library.db"))


def _seed_and_normalize(db: MusicDatabase, artists, albums=()):
    """Insert (id, name, genres) artists and (id, artist_id, title, genres) albums
    with the marker cleared, then run the real migration. Returns nothing."""
    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM metadata WHERE key = 'genres_json_normalized'")
        for aid, name, genres in artists:
            cur.execute(
                "INSERT INTO artists (id, name, genres) VALUES (?, ?, ?)",
                (aid, name, genres),
            )
        for alid, artist_id, title, genres in albums:
            cur.execute(
                "INSERT INTO albums (id, artist_id, title, genres) VALUES (?, ?, ?, ?)",
                (alid, artist_id, title, genres),
            )
        conn.commit()
        db._normalize_genres_to_json(cur)
        conn.commit()


def _get_genres(db: MusicDatabase, table: str, rid: str):
    with db._get_connection() as conn:
        row = conn.execute(f"SELECT genres FROM {table} WHERE id = ?", (rid,)).fetchone()
    return row[0]


def test_csv_genres_normalized_to_json(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    _seed_and_normalize(db, [("a1", "Artist One", "Rock, Pop, Jazz")])
    stored = _get_genres(db, "artists", "a1")
    assert json.loads(stored) == ["Rock", "Pop", "Jazz"]


def test_existing_json_genres_left_unchanged(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    canonical = json.dumps(["Hip-Hop", "Soul"])
    _seed_and_normalize(db, [("a1", "Artist One", canonical)])
    # Byte-for-byte identical — no needless churn on already-canonical rows.
    assert _get_genres(db, "artists", "a1") == canonical


def test_single_genre_without_comma(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    _seed_and_normalize(db, [("a1", "Artist One", "Electronic")])
    assert json.loads(_get_genres(db, "artists", "a1")) == ["Electronic"]


def test_csv_whitespace_and_empties_dropped(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    _seed_and_normalize(db, [("a1", "Artist One", " Rock ,, Pop , ")])
    assert json.loads(_get_genres(db, "artists", "a1")) == ["Rock", "Pop"]


def test_albums_table_also_normalized(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    _seed_and_normalize(
        db,
        artists=[("a1", "Artist One", "Rock")],
        albums=[("al1", "a1", "Album One", "Soul, Funk")],
    )
    assert json.loads(_get_genres(db, "albums", "al1")) == ["Soul", "Funk"]


def test_values_match_legacy_reader_semantics(tmp_path: Path) -> None:
    """The normalized list must equal what the legacy CSV reader would produce,
    so downstream genre values are identical pre- and post-migration."""
    db = _fresh_db(tmp_path)
    raw = "Rock, Pop, Hip-Hop/Rap"
    _seed_and_normalize(db, [("a1", "Artist One", raw)])
    legacy = [g.strip() for g in raw.split(",") if g.strip()]
    assert json.loads(_get_genres(db, "artists", "a1")) == legacy


def test_idempotent_rerun(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    _seed_and_normalize(db, [("a1", "Artist One", "Rock, Pop")])
    first = _get_genres(db, "artists", "a1")
    # Marker is now set; a second run must be a no-op and leave the value identical.
    with db._get_connection() as conn:
        cur = conn.cursor()
        db._normalize_genres_to_json(cur)
        conn.commit()
    assert _get_genres(db, "artists", "a1") == first
    assert json.loads(first) == ["Rock", "Pop"]


def test_marker_set_after_fresh_init(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'genres_json_normalized'"
        ).fetchone()
    assert row is not None and row[0] == "true"
