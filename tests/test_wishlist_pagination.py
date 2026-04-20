"""Unit tests for SQL-level wishlist pagination and category filtering.

Fix 1.4: the API endpoint previously loaded the entire wishlist, filtered
by category in Python, then sliced for the requested page. Pagination and
category filtering are now pushed to SQL via LIMIT/OFFSET and json_extract.
"""

import json

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _insert(db, spotify_track_id, name, album_type, profile_id=1, date_added=None):
    spotify_data = {
        "id": spotify_track_id,
        "name": name,
        "artists": [{"name": "Test Artist"}],
        "album": {"name": f"Album for {name}", "album_type": album_type, "images": []},
    }
    date_added = date_added or f"2026-01-{len(spotify_track_id):02d}T00:00:00"
    with db._get_connection() as conn:
        conn.execute(
            """INSERT INTO wishlist_tracks
               (spotify_track_id, spotify_data, failure_reason, retry_count,
                date_added, source_type, source_info, profile_id)
               VALUES (?, ?, '', 0, ?, 'test', '{}', ?)""",
            (spotify_track_id, json.dumps(spotify_data), date_added, profile_id),
        )
        conn.commit()


def test_pagination_limit_offset(db):
    for i in range(5):
        _insert(db, f"t{i}", f"Track {i}", "album", date_added=f"2026-01-0{i+1}T00:00:00")

    page1 = db.get_wishlist_tracks(limit=2, offset=0)
    page2 = db.get_wishlist_tracks(limit=2, offset=2)
    page3 = db.get_wishlist_tracks(limit=2, offset=4)

    assert [t["spotify_track_id"] for t in page1] == ["t0", "t1"]
    assert [t["spotify_track_id"] for t in page2] == ["t2", "t3"]
    assert [t["spotify_track_id"] for t in page3] == ["t4"]


def test_category_filter_albums(db):
    _insert(db, "a1", "Album Track 1", "album", date_added="2026-01-01T00:00:00")
    _insert(db, "s1", "Single 1", "single", date_added="2026-01-02T00:00:00")
    _insert(db, "a2", "Album Track 2", "album", date_added="2026-01-03T00:00:00")
    _insert(db, "e1", "EP Track", "ep", date_added="2026-01-04T00:00:00")

    albums = db.get_wishlist_tracks(category="albums")
    assert sorted(t["spotify_track_id"] for t in albums) == ["a1", "a2"]


def test_category_filter_singles_includes_non_album_types(db):
    _insert(db, "a1", "Album", "album", date_added="2026-01-01T00:00:00")
    _insert(db, "s1", "Single", "single", date_added="2026-01-02T00:00:00")
    _insert(db, "e1", "EP", "ep", date_added="2026-01-03T00:00:00")
    _insert(db, "c1", "Compilation", "compilation", date_added="2026-01-04T00:00:00")

    singles = db.get_wishlist_tracks(category="singles")
    assert sorted(t["spotify_track_id"] for t in singles) == ["c1", "e1", "s1"]


def test_category_filter_singles_includes_missing_album_type(db):
    # Manually insert a row whose album has no album_type (malformed)
    with db._get_connection() as conn:
        conn.execute(
            """INSERT INTO wishlist_tracks
               (spotify_track_id, spotify_data, failure_reason, retry_count,
                date_added, source_type, source_info, profile_id)
               VALUES ('x1', ?, '', 0, '2026-01-01', 'test', '{}', 1)""",
            (json.dumps({"id": "x1", "name": "X", "album": {"name": "A"}}),),
        )
        conn.commit()

    singles = db.get_wishlist_tracks(category="singles")
    assert [t["spotify_track_id"] for t in singles] == ["x1"]


def test_get_wishlist_count_no_filter(db):
    for i in range(7):
        _insert(db, f"t{i}", f"Track {i}", "album", date_added=f"2026-01-0{i+1}T00:00:00")

    assert db.get_wishlist_count() == 7


def test_get_wishlist_count_with_category_filter(db):
    _insert(db, "a1", "A1", "album", date_added="2026-01-01T00:00:00")
    _insert(db, "a2", "A2", "album", date_added="2026-01-02T00:00:00")
    _insert(db, "s1", "S1", "single", date_added="2026-01-03T00:00:00")

    assert db.get_wishlist_count(category="albums") == 2
    assert db.get_wishlist_count(category="singles") == 1
    assert db.get_wishlist_count() == 3


def test_profile_isolation(db):
    _insert(db, "p1-a", "A", "album", profile_id=1, date_added="2026-01-01T00:00:00")
    _insert(db, "p1-b", "B", "album", profile_id=1, date_added="2026-01-02T00:00:00")
    _insert(db, "p2-a", "C", "album", profile_id=2, date_added="2026-01-03T00:00:00")

    assert db.get_wishlist_count(profile_id=1) == 2
    assert db.get_wishlist_count(profile_id=2) == 1

    p1 = db.get_wishlist_tracks(profile_id=1)
    assert sorted(t["spotify_track_id"] for t in p1) == ["p1-a", "p1-b"]


def test_backward_compat_no_args_returns_all(db):
    """Existing callers (wishlist_service) pass no limit/offset — must still work."""
    for i in range(3):
        _insert(db, f"t{i}", f"Track {i}", "album", date_added=f"2026-01-0{i+1}T00:00:00")

    rows = db.get_wishlist_tracks()
    assert len(rows) == 3


def test_ordering_by_date_added(db):
    _insert(db, "newer", "Newer", "album", date_added="2026-02-01T00:00:00")
    _insert(db, "older", "Older", "album", date_added="2026-01-01T00:00:00")

    rows = db.get_wishlist_tracks()
    assert [r["spotify_track_id"] for r in rows] == ["older", "newer"]
