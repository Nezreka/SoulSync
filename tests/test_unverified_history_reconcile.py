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

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _InMemoryDB(MusicDatabase):
    """The catalogue tables this method reads, and the history table it heals.

    Hand-built rather than a full schema-ensure so the test stays a unit test:
    the verification verdict sits on the FILE row (ADR-03), the title on the
    track, which is exactly the join under test.
    """

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, album_id INTEGER, "
            "title TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "track_id INTEGER, path TEXT, verification_status TEXT)"
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

    def _add_track(self, tid, path, status, title="Song"):
        self._conn.execute(
            "INSERT INTO lib2_tracks (id, album_id, title) VALUES (?,1,?)",
            (tid, title))
        self._conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, verification_status) "
            "VALUES (?,?,?)", (tid, path, status))
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


def test_reconcile_basename_collision_does_not_false_heal():
    """Two different songs share the track-number filename '01 - Intro.flac'.
    Only one is a verified track; the OTHER's stale history row must NOT inherit
    that verified status just because the filename collides (title guard)."""
    db = _InMemoryDB()
    db._add_track(1, "/lib/AlbumA/01 - Intro.flac", "verified", title="Intro A")
    # A genuinely different, still-unverified song with the same filename.
    db._add_history("/transfer/AlbumB/01 - Intro.flac", "unverified", title="Intro B")
    healed = db.reconcile_unverified_history_from_tracks()
    assert healed == 0
    assert db._status_of(1) == "unverified"


def test_reconcile_basename_heals_when_titles_agree():
    """Same filename, same song (path drifted) — titles agree, so it heals."""
    db = _InMemoryDB()
    db._add_track(1, "/lib/AlbumA/01 - Intro.flac", "verified", title="Intro")
    db._add_history("/transfer/old/01 - Intro.flac", "unverified", title="Intro")
    healed = db.reconcile_unverified_history_from_tracks()
    assert healed == 1
    assert db._status_of(1) == "verified"


def test_reconcile_basename_heals_when_history_title_missing():
    """Legacy history rows may have no title — fall back to filename-only match
    (mirrors the scanner matcher's allowance) so they still heal."""
    db = _InMemoryDB()
    db._add_track(1, "/lib/A/01 - Song.flac", "verified", title="Whatever")
    db._add_history("/transfer/old/01 - Song.flac", "unverified", title="")
    healed = db.reconcile_unverified_history_from_tracks()
    assert healed == 1
    assert db._status_of(1) == "verified"


def test_reconcile_titleless_row_does_not_heal_on_basename_collision():
    """Follow-up hardening (#938 review): a title-less history row must NOT heal
    via filename when that basename collides across MORE THAN ONE verified track —
    we can't tell which song it is, so healing would risk marking a genuinely
    unverified import 'verified'. Unique-basename title-less heal still works
    (see test_reconcile_basename_heals_when_history_title_missing)."""
    db = _InMemoryDB()
    # Two DIFFERENT verified songs that happen to share the generic filename.
    db._add_track(1, "/lib/AlbumA/01 - Intro.flac", "verified", title="Intro A")
    db._add_track(2, "/lib/AlbumB/01 - Intro.flac", "verified", title="Intro B")
    # A stale, title-less history row with that same basename.
    db._add_history("/transfer/X/01 - Intro.flac", "unverified", title="")
    healed = db.reconcile_unverified_history_from_tracks()
    assert healed == 0
    assert db._status_of(1) == "unverified"


def test_reconcile_reads_only_the_verified_files_through_an_index():
    """review 5 R5-X2-3: every boot ran the heal's verified-file read as a scan
    of every file row (2.6-3.4 s on a 300k library) to find a few hundred"""
    from core.library2.schema import ensure_library_v2_schema

    db = _InMemoryDB.__new__(_InMemoryDB)
    db._conn = sqlite3.connect(":memory:")
    db._conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(db._conn, run_backfills=False)
    db._conn.execute(
        "CREATE TABLE library_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, title TEXT, "
        "file_path TEXT, verification_status TEXT)")
    db._conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'A')")
    db._conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title) VALUES(1, 1, 'B')")
    db._add_track(1, "/lib/A/01 - Song.flac", "verified")
    db._add_history("/lib/A/01 - Song.flac", "unverified")

    seen = []
    db._conn.set_trace_callback(seen.append)
    assert db.reconcile_unverified_history_from_tracks() == 1
    db._conn.set_trace_callback(None)

    read = next(q for q in seen if "FROM lib2_track_files" in q)
    plan = " ".join(r[3] for r in db._conn.execute("EXPLAIN QUERY PLAN " + read))
    assert "idx_lib2_track_files_verified" in plan, plan
