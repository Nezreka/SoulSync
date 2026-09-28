"""S12: expired wishlist_ignore rows must not accumulate forever.

``get_wishlist_ignore`` already purges expired rows opportunistically, but
only when the ignore-list UI is opened. Users who never open it accumulate
dead rows unboundedly (worse now that S4 writes an ignore per cleared
track). The write path prunes the profile's expired rows on every insert.
"""
import pytest

from core.wishlist.ignore import REASON_REMOVED
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _ignore_ids(db, profile_id=1):
    with db._get_connection() as conn:
        return [r["track_id"] for r in conn.execute(
            "SELECT track_id FROM wishlist_ignore WHERE profile_id = ?",
            (profile_id,)).fetchall()]


def test_s12_write_prunes_expired_rows(db):
    assert db.add_to_wishlist_ignore("old", "Old Song", "Old Artist",
                                     REASON_REMOVED, profile_id=1) is True
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE wishlist_ignore SET created_at = '2020-01-01 00:00:00' "
            "WHERE track_id = 'old'")
        conn.commit()
    assert db.add_to_wishlist_ignore("new", "New Song", "New Artist",
                                     REASON_REMOVED, profile_id=1) is True
    assert _ignore_ids(db) == ["new"]


def test_s12_fresh_rows_survive_prune(db):
    assert db.add_to_wishlist_ignore("a", "Song A", "Art A", REASON_REMOVED) is True
    assert db.add_to_wishlist_ignore("b", "Song B", "Art B", REASON_REMOVED) is True
    assert sorted(_ignore_ids(db)) == ["a", "b"]
    assert db.is_track_ignored("a") is True
    assert db.is_track_ignored("b") is True
