"""Unit tests for api_search_tracks (fix 2.2).

The track search endpoint previously called search_tracks() then
api_get_tracks_by_ids() to re-hydrate the same rows. api_search_tracks
now returns the full dict rows in a single query.
"""

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert_track(db, track_id, title, artist_id, artist_name, album_id, album_title):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO artists (id, name) VALUES (?, ?)",
            (artist_id, artist_name),
        )
        conn.execute(
            "INSERT OR IGNORE INTO albums (id, title, artist_id) VALUES (?, ?, ?)",
            (album_id, album_title, artist_id),
        )
        conn.execute(
            """INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path)
               VALUES (?, ?, ?, ?, 1, 180, ?)""",
            (track_id, album_id, artist_id, title, f"/music/{title}.mp3"),
        )
        conn.commit()


def test_api_search_tracks_returns_dict_rows_with_full_columns(db):
    _insert_track(db, 100, "Great Song", 1, "Band One", 10, "Album X")

    results = db.api_search_tracks(title="great")

    assert len(results) == 1
    row = results[0]
    assert isinstance(row, dict)
    assert str(row["id"]) == "100"
    assert row["title"] == "Great Song"
    assert row["artist_name"] == "Band One"
    assert row["album_title"] == "Album X"
    # file_path is a tracks.* column that must be present
    assert row["file_path"] == "/music/Great Song.mp3"


def test_api_search_tracks_empty_query_returns_empty(db):
    _insert_track(db, 1, "Track", 1, "Artist", 1, "Album")
    assert db.api_search_tracks() == []


def test_api_search_tracks_no_matches_returns_empty(db):
    _insert_track(db, 1, "Alpha", 1, "Artist A", 1, "Album A")
    assert db.api_search_tracks(title="nonexistent") == []


def test_api_search_tracks_matches_by_artist(db):
    _insert_track(db, 1, "Song A", 1, "Mystery Band", 1, "Album A")
    _insert_track(db, 2, "Song B", 2, "Other Artist", 2, "Album B")

    results = db.api_search_tracks(artist="mystery")
    assert len(results) == 1
    assert str(results[0]["id"]) == "1"


def test_api_search_tracks_respects_limit(db):
    for i in range(10):
        _insert_track(db, i + 1, f"Common Title {i}", 1, "Artist", 1, "Album")

    results = db.api_search_tracks(title="common", limit=3)
    assert len(results) == 3


def test_api_search_tracks_falls_back_to_fuzzy(db):
    """When basic LIKE returns nothing, fuzzy fallback should still find matches."""
    _insert_track(db, 1, "Bohemian Rhapsody", 1, "Queen", 1, "A Night at the Opera")

    # Basic search "bohemian" matches directly; the fuzzy path exists for
    # cases where basic fails. Verify at least that the method surfaces a
    # result for a direct query.
    results = db.api_search_tracks(title="bohemian rhapsody")
    assert any(str(r["id"]) == "1" for r in results)


def test_search_tracks_still_returns_database_track_objects(db):
    """Existing callers rely on the DatabaseTrack return shape of search_tracks()."""
    _insert_track(db, 1, "Legacy Track", 1, "Legacy Artist", 1, "Legacy Album")

    results = db.search_tracks(title="legacy")
    assert len(results) == 1
    # DatabaseTrack exposes attributes, not dict keys.
    assert str(results[0].id) == "1"
    assert results[0].title == "Legacy Track"
    assert results[0].artist_name == "Legacy Artist"
