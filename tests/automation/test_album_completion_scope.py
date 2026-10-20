"""Safety tests for automatic album-completion ownership checks."""

import sqlite3

import pytest

from core.automation.album_completion_scope import verify_shared_album_scope


class MemoryDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE albums (id TEXT PRIMARY KEY, owner_profile_id INTEGER)"
        )
        self.conn.execute(
            "CREATE TABLE profiles (id INTEGER PRIMARY KEY, "
            "library_mode TEXT, library_root TEXT)"
        )
        self.conn.executemany(
            "INSERT INTO albums VALUES (?, ?)",
            [("shared", None), ("personal", 2)],
        )
        self.conn.executemany(
            "INSERT INTO profiles VALUES (?, ?, ?)",
            [
                (2, "shared", None),
                (3, "own", "/personal/music"),
                (4, "own", None),
            ],
        )

    def _get_connection(self):
        return self.conn


@pytest.mark.parametrize(
    "album_id,profile_id,expected",
    [
        ("shared", 1, True),
        ("shared", 2, True),
        ("shared", 3, False),
        ("shared", 4, True),
        ("personal", 1, False),
        ("personal", 2, False),
        ("missing", 1, False),
        ("shared", 999, False),
        ("shared", 0, False),
        ("shared", None, False),
    ],
)
def test_album_scope(album_id, profile_id, expected):
    db = MemoryDatabase()
    try:
        assert verify_shared_album_scope(db, album_id, profile_id) is expected
    finally:
        db.conn.close()


def test_database_failure_is_rejected():
    class BrokenDatabase:
        def _get_connection(self):
            raise sqlite3.OperationalError("Database unavailable")

    assert verify_shared_album_scope(
        BrokenDatabase(), "shared", 2
    ) is False


def test_missing_ownership_column_is_rejected():
    class OldDatabase:
        def _get_connection(self):
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE TABLE albums (id TEXT PRIMARY KEY)")
            return conn

    assert verify_shared_album_scope(
        OldDatabase(), "shared", 1
    ) is False
