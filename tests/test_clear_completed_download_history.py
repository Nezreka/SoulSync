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

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

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
            "title TEXT, file_path TEXT, verification_status TEXT, "
            "origin TEXT, origin_context TEXT, cleared_at TEXT)")

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


def test_clear_finished_keeps_the_review_queue():
    """Clear finished (keep_review) takes the completed tail but leaves rows
    still waiting in the review queue -- they leave through Review. Clear
    everything (the default, above) still takes them."""
    db = _InMemoryDB()
    db._add("download", "verified")
    db._add("download", "human_verified")
    db._add("download", None)
    db._add("download", "unverified")
    db._add("download", "force_imported")
    removed = db.clear_completed_download_history(keep_review=True)
    assert removed == 3
    assert db._statuses() == ["unverified", "force_imported"]


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


# ── #1931: the download origin tab keeps what a clear hides ──────────────────

def _real(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        for title, origin, ctx in (("Manual", None, None),
                                   ("From Radar", "playlist", "Release Radar"),
                                   ("From Drake", "watchlist", "Drake")):
            conn.execute(
                "INSERT INTO library_history (event_type, title, artist_name, origin, "
                "origin_context) VALUES ('download', ?, 'A', ?, ?)", (title, origin, ctx))
        conn.commit()
    return db


def test_a_clear_empties_the_list_but_keeps_the_origins(tmp_path):
    db = _real(tmp_path)

    assert db.clear_completed_download_history() == 3

    entries, total = db.get_library_history(event_type="download")
    assert (entries, total) == ([], 0)
    assert db.get_library_history_stats()["downloads"] == 0
    playlist, _ = db.get_download_origin_entries("playlist")
    watchlist, _ = db.get_download_origin_entries("watchlist")
    assert [e["title"] for e in playlist] == ["From Radar"]
    assert [e["title"] for e in watchlist] == ["From Drake"]
    assert len(db.get_origin_cleanup_candidates()) == 2


def test_a_second_clear_counts_nothing_new(tmp_path):
    db = _real(tmp_path)
    db.clear_completed_download_history()

    assert db.clear_completed_download_history() == 0



def test_clear_finished_keeps_an_origin_row_in_review_visible(tmp_path):
    """A watchlist download still waiting in review is not hidden by Clear
    finished; one that is not waiting is, as #1931 does."""
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        for title, status in (("Waiting", "unverified"), ("Done", "verified")):
            conn.execute(
                "INSERT INTO library_history (event_type, title, artist_name, origin, "
                "origin_context, verification_status) VALUES ('download', ?, 'A', "
                "'watchlist', 'Drake', ?)", (title, status))
        conn.commit()

    assert db.clear_completed_download_history(keep_review=True) == 1

    assert [e["title"] for e in db.get_library_history_unverified()] == ["Waiting"]
