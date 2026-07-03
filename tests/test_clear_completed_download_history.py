"""'Clear Completed' on the Downloads page deletes ALL persisted completed-download
history (every event_type='download' row), including the unverified review-queue rows
— the user wants the list emptied, and those unverified rows ARE download-history rows.
It only removes HISTORY rows; the actual files / `tracks` entries are untouched, so the
library is never affected — only the 'needs verification' flags. (Clear-button restoration.)"""

import sqlite3
import sys
import types

if "spotipy" not in sys.modules:  # match the suite's lightweight stubs
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = object
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = object
    oauth2.SpotifyClientCredentials = object
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from database.music_database import MusicDatabase  # noqa: E402


class _NonClosingConn:
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def commit(self):
        return self._real.commit()

    def close(self):
        pass


class _InMemoryDB(MusicDatabase):
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE library_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, "
            "title TEXT, file_path TEXT, verification_status TEXT)")

    def _get_connection(self):
        return _NonClosingConn(self._conn)

    def _add(self, event_type, status, title="Song"):
        self._conn.execute(
            "INSERT INTO library_history (event_type, title, verification_status) "
            "VALUES (?, ?, ?)", (event_type, title, status))
        self._conn.commit()

    def _statuses(self):
        return [r[0] for r in self._conn.execute(
            "SELECT verification_status FROM library_history ORDER BY id").fetchall()]


def test_clears_all_completed_download_rows_including_unverified():
    db = _InMemoryDB()
    db._add("download", "verified")
    db._add("download", "human_verified")
    db._add("download", None)            # legacy / unscored completed
    db._add("download", "unverified")    # review queue — also cleared (user chose clear-all)
    db._add("download", "force_imported")
    removed = db.clear_completed_download_history()
    assert removed == 5
    assert db._statuses() == []


def test_does_not_touch_non_download_history():
    """Only event_type='download' rows are the Downloads-page tail; imports etc. stay."""
    db = _InMemoryDB()
    db._add("download", "verified")
    db._add("import", "verified")
    removed = db.clear_completed_download_history()
    assert removed == 1
    # the import row survives
    rows = db._conn.execute("SELECT event_type FROM library_history").fetchall()
    assert [r[0] for r in rows] == ["import"]


def test_empty_history_returns_zero():
    db = _InMemoryDB()
    assert db.clear_completed_download_history() == 0
