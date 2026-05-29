"""Tests for the canonical source-ID registry (core/source_ids.py)."""

from __future__ import annotations

import sqlite3

import pytest

from core import source_ids as sid


def test_canonical_columns_match_real_schema():
    # Spot-check the canonical names against the actual DB columns.
    assert sid.id_column("spotify", "artist") == "spotify_artist_id"
    assert sid.id_column("deezer", "artist") == "deezer_id"
    assert sid.id_column("musicbrainz", "artist") == "musicbrainz_id"
    assert sid.id_column("hydrabase", "artist") == "soul_id"
    assert sid.id_column("spotify", "album") == "spotify_album_id"
    assert sid.id_column("musicbrainz", "album") == "musicbrainz_release_id"
    assert sid.id_column("deezer", "album") == "deezer_id"
    assert sid.id_column("spotify", "track") == "spotify_track_id"
    assert sid.id_column("musicbrainz", "track") == "musicbrainz_recording_id"


def test_id_column_unknown_returns_none():
    assert sid.id_column("nonesuch", "artist") is None
    assert sid.id_column("spotify", "playlist") is None


def test_id_keys_canonical_first_then_aliases():
    keys = sid.id_keys("deezer", "artist")
    assert keys[0] == "deezer_id"  # canonical first
    assert "deezer_artist_id" in keys  # watchlist/pool alias
    assert "similar_artist_deezer_id" in keys


def test_get_id_reads_canonical_column():
    row = {"deezer_id": "525046", "name": "Artist"}
    assert sid.get_id(row, "deezer", "artist") == "525046"


def test_get_id_falls_back_to_alias():
    # A watchlist-shaped row uses deezer_artist_id, not deezer_id.
    row = {"deezer_artist_id": "999", "artist_name": "X"}
    assert sid.get_id(row, "deezer", "artist") == "999"


def test_get_id_prefers_canonical_over_alias():
    row = {"deezer_id": "canon", "deezer_artist_id": "alias"}
    assert sid.get_id(row, "deezer", "artist") == "canon"


def test_get_id_missing_and_empty_return_none():
    assert sid.get_id({"name": "X"}, "deezer", "artist") is None
    assert sid.get_id({"deezer_id": ""}, "deezer", "artist") is None
    assert sid.get_id({"deezer_id": None}, "deezer", "artist") is None


def test_get_id_works_with_sqlite_row():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (spotify_artist_id TEXT, name TEXT)")
    conn.execute("INSERT INTO t VALUES ('spfy123', 'Artist')")
    row = conn.execute("SELECT * FROM t").fetchone()
    conn.close()
    assert sid.get_id(row, "spotify", "artist") == "spfy123"
    # A column not present on the row must not raise — just None.
    assert sid.get_id(row, "deezer", "artist") is None


def test_source_id_map_builds_provider_dict():
    row = {
        "spotify_artist_id": "s1",
        "deezer_id": "d1",
        "itunes_artist_id": None,
    }
    result = sid.source_id_map(row, "artist", providers=["spotify", "deezer", "itunes"])
    assert result == {"spotify": "s1", "deezer": "d1", "itunes": None}


def test_source_id_map_default_covers_all_providers():
    result = sid.source_id_map({"deezer_id": "d1"}, "artist")
    assert result["deezer"] == "d1"
    assert "spotify" in result and result["spotify"] is None


def test_source_id_field_unchanged_after_registry_refactor():
    """artist_source_lookup.SOURCE_ID_FIELD must keep its exact prior mapping
    after being folded into the registry (no behavior change)."""
    from core.artist_source_lookup import SOURCE_ID_FIELD
    assert SOURCE_ID_FIELD == {
        "spotify": "spotify_artist_id",
        "itunes": "itunes_artist_id",
        "deezer": "deezer_id",
        "discogs": "discogs_id",
        "hydrabase": "soul_id",
        "musicbrainz": "musicbrainz_id",
        "amazon": "amazon_id",
    }
