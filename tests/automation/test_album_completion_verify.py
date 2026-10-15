import sqlite3
from unittest.mock import patch
from core.automation.album_completion_verify import filter_still_missing


def test_disc_aware_stale_track_filter():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tracks (album_id INTEGER, disc_number INTEGER, track_number INTEGER, owner_profile_id INTEGER, file_path TEXT)")
    conn.execute("INSERT INTO tracks VALUES (42, 1, 2, NULL, ?)", ("/music/owned.flac",))
    class DB:
        def _get_connection(self):
            return conn
        def _owner_scope_sql(self, scope, column):
            return ("owner_profile_id IS NULL", [])
    missing = [{"disc_number": 1, "track_number": 2}, {"disc_number": 2, "track_number": 2}]
    with patch("core.automation.album_completion_verify.library_scope_for_profile", return_value="shared"):
        result = filter_still_missing(DB(), 42, missing, 1)
    assert result == [missing[1]]


def test_other_profile_track_is_not_owned():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tracks (album_id INTEGER, disc_number INTEGER, track_number INTEGER, owner_profile_id INTEGER, file_path TEXT)")
    conn.execute("INSERT INTO tracks VALUES (42, 1, 3, 2, ?)", ("/music/other.flac",))
    class DB:
        def _get_connection(self):
            return conn
        def _owner_scope_sql(self, scope, column):
            return ("owner_profile_id = ?", [scope])
    missing = [{"disc_number": 1, "track_number": 3}]
    with patch("core.automation.album_completion_verify.library_scope_for_profile", return_value=3):
        result = filter_still_missing(DB(), 42, missing, 3)
    assert result == missing
