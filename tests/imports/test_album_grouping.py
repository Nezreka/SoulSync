"""Seam tests for canonical album grouping (Sokhi: split album rows -> mixed
cover art). Drives find_existing_soulsync_album_id against a real in-memory
SQLite albums table — no app singletons, no I/O.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.imports.album_grouping import (
    find_existing_soulsync_album_id,
    ALLOWED_ALBUM_SOURCE_COLS,
)


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            artist_id TEXT,
            title TEXT,
            server_source TEXT,
            spotify_album_id TEXT,
            itunes_album_id TEXT,
            deezer_id TEXT,
            soul_id TEXT,
            discogs_id TEXT,
            musicbrainz_release_id TEXT
        )"""
    )
    yield conn.cursor()
    conn.close()


def _add(cur, *, id, title, artist_id="art1", server_source="soulsync", **source_ids):
    cols = ["id", "artist_id", "title", "server_source"] + list(source_ids)
    vals = [id, artist_id, title, server_source] + list(source_ids.values())
    cur.execute(
        f"INSERT INTO albums ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
        vals,
    )


def test_empty_db_returns_none(cur):
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id="art1", album_name="Parachutes",
        album_source_col="spotify_album_id", album_source_id="SP1") is None


def test_exact_name_hash_id_wins_first(cur):
    _add(cur, id="nk", title="Parachutes")
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id="art1", album_name="Parachutes") == "nk"


def test_canonical_source_id_unifies_differently_named_imports(cur):
    # Existing row for release SP1 named "Parachutes". A second import of the
    # SAME release id but a drifted name must JOIN it, not split.
    _add(cur, id="existing", title="Parachutes", spotify_album_id="SP1")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="different_hash", artist_id="art1",
        album_name="Parachutes (Deluxe Edition)",
        album_source_col="spotify_album_id", album_source_id="SP1")
    assert got == "existing"


def test_different_release_id_stays_separate(cur):
    # The single-vs-album case: a genuinely different release id must NOT merge
    # (documents the known limit — single->album resolution is a separate step).
    _add(cur, id="album_row", title="Parachutes", spotify_album_id="SP_ALBUM")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="single_hash", artist_id="art1", album_name="Yellow",
        album_source_col="spotify_album_id", album_source_id="SP_SINGLE")
    assert got is None


def test_legacy_name_match_still_groups_without_a_source_id(cur):
    _add(cur, id="byname", title="Parachutes")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="other_hash", artist_id="art1", album_name="parachutes",
        album_source_col=None, album_source_id=None)
    assert got == "byname"  # case-insensitive title + artist


def test_source_id_match_is_scoped_to_soulsync_rows(cur):
    _add(cur, id="plexrow", title="Parachutes", server_source="plex", spotify_album_id="SP1")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id="art1", album_name="X",
        album_source_col="spotify_album_id", album_source_id="SP1")
    assert got is None  # the matching row belongs to Plex, not soulsync


def test_non_allowlisted_column_is_ignored(cur):
    # A column not on the allowlist must never be spliced into SQL.
    assert "title" not in ALLOWED_ALBUM_SOURCE_COLS
    _add(cur, id="row", title="Parachutes")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id="art1", album_name="nope",
        album_source_col="title", album_source_id="Parachutes")
    assert got is None  # 'title' ignored as a source col; name 'nope' doesn't match


def test_empty_source_id_skips_canonical_match(cur):
    _add(cur, id="row", title="Parachutes", spotify_album_id="")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id="art1", album_name="Other",
        album_source_col="spotify_album_id", album_source_id="")
    assert got is None


def test_missing_album_column_falls_through_not_raises(cur):
    # Some sources (Deezer) don't have a dedicated album id column on the albums
    # table; an allow-listed-but-absent column must NOT raise (it broke the whole
    # import once) — it falls through to the name match.
    cur.execute("CREATE TABLE albums_min (id TEXT, artist_id TEXT, title TEXT, server_source TEXT)")
    cur.execute("INSERT INTO albums_min VALUES ('byname','art1','DZ Album','soulsync')")
    # Point the helper at a table missing deezer_id by aliasing via a fresh cursor.
    conn2 = sqlite3.connect(":memory:")
    conn2.execute("CREATE TABLE albums (id TEXT, artist_id TEXT, title TEXT, server_source TEXT)")
    conn2.execute("INSERT INTO albums VALUES ('byname','art1','DZ Album','soulsync')")
    c2 = conn2.cursor()
    got = find_existing_soulsync_album_id(
        c2, name_key_id="nk", artist_id="art1", album_name="DZ Album",
        album_source_col="deezer_id", album_source_id="67890")
    conn2.close()
    assert got == "byname"   # deezer_id column absent -> fell through to name match


def test_musicbrainz_release_id_grouping(cur):
    _add(cur, id="mbrow", title="Album", musicbrainz_release_id="mb-123")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="nk2", artist_id="art1", album_name="Album (Remaster)",
        album_source_col="musicbrainz_release_id", album_source_id="mb-123")
    assert got == "mbrow"
