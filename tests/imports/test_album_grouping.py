"""Seam tests for canonical album grouping (Sokhi: split album rows -> mixed
cover art). Drives find_existing_soulsync_album_id against a real catalogue
schema — no app singletons, no I/O.

The import mints a stable hash per album NAME and keeps it as the row's
``server_id`` under the ``soulsync`` server; the release's own provider id is
the better key, and in v2 it lives in a promoted column (Spotify, MusicBrainz)
or in ``external_ids`` (§50.4.4.29).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.imports.album_grouping import find_existing_soulsync_album_id
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO lib2_artists(id, name, name_key) VALUES(1,'Coldplay','coldplay')")
    conn.commit()
    yield cursor
    conn.close()


def _add(cur, *, server_id, title, artist_id=1, server_source="soulsync",
         spotify_id=None, musicbrainz_id=None, external_ids=None) -> int:
    return int(cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, server_source,"
        "                        server_id, spotify_id, musicbrainz_id, external_ids)"
        " VALUES(?,?, 'library', ?,?,?,?,?)",
        (artist_id, title, server_source, server_id, spotify_id, musicbrainz_id,
         json.dumps(external_ids or {}))).lastrowid)


def test_empty_db_returns_none(cur):
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Parachutes",
        album_source_id="SP1", source="spotify") is None


def test_exact_name_hash_id_wins_first(cur):
    row = _add(cur, server_id="nk", title="Parachutes")
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Parachutes") == row


def test_canonical_source_id_unifies_differently_named_imports(cur):
    # Existing row for release SP1 named "Parachutes". A second import of the
    # SAME release id but a drifted name must JOIN it, not split.
    row = _add(cur, server_id="existing", title="Parachutes", spotify_id="SP1")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="different_hash", artist_id=1,
        album_name="Parachutes (Deluxe Edition)",
        album_source_id="SP1", source="spotify")
    assert got == row


def test_a_long_tail_provider_id_unifies_too(cur):
    """Only Spotify and MusicBrainz have columns; Deezer and friends live in
    `external_ids`, and grouping must reach them there."""
    row = _add(cur, server_id="existing", title="Parachutes",
               external_ids={"deezer": "DZ7"})
    got = find_existing_soulsync_album_id(
        cur, name_key_id="different_hash", artist_id=1,
        album_name="Parachutes (Deluxe)", album_source_id="DZ7", source="deezer")
    assert got == row


def test_different_release_id_stays_separate(cur):
    # The single-vs-album case: a genuinely different release id must NOT merge
    # (documents the known limit — single->album resolution is a separate step).
    _add(cur, server_id="album_row", title="Parachutes", spotify_id="SP_ALBUM")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="single_hash", artist_id=1, album_name="Yellow",
        album_source_id="SP_SINGLE", source="spotify")
    assert got is None


def test_legacy_name_match_still_groups_without_a_source_id(cur):
    row = _add(cur, server_id="byname", title="Parachutes")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="other_hash", artist_id=1, album_name="parachutes",
        album_source_id=None)
    assert got == row  # case-insensitive title + artist


def test_another_artists_album_is_not_a_match(cur):
    cur.execute("INSERT INTO lib2_artists(id, name, name_key) VALUES(2,'Muse','muse')")
    _add(cur, server_id="other", title="Parachutes", artist_id=2)
    got = find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Parachutes")
    assert got is None


def test_empty_source_id_skips_canonical_match(cur):
    _add(cur, server_id="row", title="Parachutes", spotify_id="")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Other",
        album_source_id="", source="spotify")
    assert got is None


def test_an_unknown_provider_falls_through_not_raises(cur):
    """A provider nobody stores an id for must not break the import — it falls
    through to the name match."""
    row = _add(cur, server_id="byname", title="DZ Album")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="DZ Album",
        album_source_id="67890", source="somethingelse")
    assert got == row


def test_musicbrainz_release_id_grouping(cur):
    row = _add(cur, server_id="mbrow", title="Album", musicbrainz_id="mb-123")
    got = find_existing_soulsync_album_id(
        cur, name_key_id="nk2", artist_id=1, album_name="Album (Remaster)",
        album_source_id="mb-123", source="musicbrainz")
    assert got == row
