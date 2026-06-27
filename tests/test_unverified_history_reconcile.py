"""Issue #934 — one-time reconcile that clears the existing backlog of
``library_history`` rows stuck at 'unverified' even though the file has since
been verified (by an AcoustID scan, or human-confirmed). Heals from the
``tracks`` truth, matching exact path AND basename (so a reorganized/moved file
heals too), upgrade-only. Never deletes anything."""

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

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _InMemoryDB(MusicDatabase):
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, file_path TEXT, "
            "verification_status TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE library_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, title TEXT, "
            "artist_name TEXT, album_name TEXT, file_path TEXT, "
            "download_source TEXT, verification_status TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )

    def _get_connection(self):
        return _NonClosingConn(self._conn)

    def _add_track(self, tid, path, status):
        self._conn.execute(
            "INSERT INTO tracks (id, file_path, verification_status) VALUES (?,?,?)",
            (tid, path, status))
        self._conn.commit()

    def _add_history(self, path, status, title="Song"):
        self._conn.execute(
            "INSERT INTO library_history (event_type, title, file_path, "
            "verification_status) VALUES ('download', ?, ?, ?)",
            (title, path, status))
        self._conn.commit()

    def _status_of(self, hid):
        return self._conn.execute(
            "SELECT verification_status FROM library_history WHERE id = ?", (hid,)
        ).fetchone()[0]


def test_reconcile_heals_exact_path_match():
    db = _InMemoryDB()
    db._add_track(1, "/lib/A/01 - Song.flac", "verified")
    db._add_history("/lib/A/01 - Song.flac", "unverified")
    healed = db.reconcile_unverified_history_from_tracks()
    assert healed == 1
    assert db._status_of(1) == "verified"


def test_reconcile_heals_by_basename_when_path_form_differs():
    db = _InMemoryDB()
    db._add_track(1, "/library/Artist/Album/01 - Song.flac", "verified")
    # History stored the transfer-folder path; basename still matches.
    db._add_history("/transfer/Artist - Album/01 - Song.flac", "unverified")
    healed = db.reconcile_unverified_history_from_tracks()
    assert healed == 1
    assert db._status_of(1) == "verified"


def test_reconcile_propagates_human_verified():
    db = _InMemoryDB()
    db._add_track(1, "/lib/01 - Song.flac", "human_verified")
    db._add_history("/lib/01 - Song.flac", "unverified")
    db.reconcile_unverified_history_from_tracks()
    assert db._status_of(1) == "human_verified"


def test_reconcile_leaves_genuinely_unverified_rows():
    db = _InMemoryDB()
    db._add_track(1, "/lib/01 - Song.flac", "unverified")  # track itself unconfirmed
    db._add_history("/lib/01 - Song.flac", "unverified")
    healed = db.reconcile_unverified_history_from_tracks()
    assert healed == 0
    assert db._status_of(1) == "unverified"


def test_reconcile_leaves_orphans_untouched():
    db = _InMemoryDB()
    # No track references this file at all (deleted / re-downloaded elsewhere).
    db._add_history("/lib/gone.flac", "unverified")
    healed = db.reconcile_unverified_history_from_tracks()
    assert healed == 0
    assert db._status_of(1) == "unverified"
