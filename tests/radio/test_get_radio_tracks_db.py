"""End-to-end behavioral pin for MusicDatabase.get_radio_tracks.

Phase 0a extracted the radio SELECTION logic into core.radio.selection but the
DB method still owns the SQL. These tests drive the REAL get_radio_tracks
against an in-memory sqlite to prove the refactor preserved behavior — the
4-tier fallback (same-artist cap → genre → mood/style → random), dedup, and
exclude handling all still work through the extracted helpers.

Reuses the in-memory MusicDatabase harness pattern from
tests/test_reorganize_db_methods.py.
"""

import sqlite3
import sys
import types

import pytest


# ── stubs (same shape used elsewhere in the suite) ────────────────────────
if "spotipy" not in sys.modules:
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


class _InMemoryDB(MusicDatabase):
    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return _NonClosingConn(self._conn)


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


def _schema(db):
    cur = db._conn.cursor()
    cur.execute("""
        CREATE TABLE artists (
            id TEXT PRIMARY KEY, name TEXT,
            genres TEXT, mood TEXT, style TEXT, thumb_url TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY, artist_id TEXT, title TEXT,
            genres TEXT, mood TEXT, style TEXT, thumb_url TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT,
            title TEXT, track_number INTEGER, duration INTEGER,
            file_path TEXT, bitrate INTEGER
        )
    """)
    db._conn.commit()


def _add_artist(db, aid, name, genres="", mood="", style=""):
    db._conn.execute(
        "INSERT INTO artists (id, name, genres, mood, style, thumb_url) VALUES (?,?,?,?,?,?)",
        (aid, name, genres, mood, style, ""),
    )


def _add_album(db, alid, aid, title, genres="", mood="", style=""):
    db._conn.execute(
        "INSERT INTO albums (id, artist_id, title, genres, mood, style, thumb_url) VALUES (?,?,?,?,?,?,?)",
        (alid, aid, title, genres, mood, style, ""),
    )


def _add_track(db, tid, alid, aid, title, file_path="/m/x.flac"):
    db._conn.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, file_path, bitrate) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (tid, alid, aid, title, 1, 200, file_path, 1000),
    )


@pytest.fixture
def db():
    d = _InMemoryDB()
    _schema(d)
    return d


def test_missing_seed_track_returns_failure(db):
    res = db.get_radio_tracks("nope", limit=10)
    assert res["success"] is False


def test_tier1_same_artist_other_albums(db):
    _add_artist(db, "ar1", "Artist One")
    _add_album(db, "al1", "ar1", "Album A")
    _add_album(db, "al2", "ar1", "Album B")
    _add_track(db, "seed", "al1", "ar1", "Seed")
    _add_track(db, "t2", "al2", "ar1", "Other Album Track")
    db._conn.commit()

    res = db.get_radio_tracks("seed", limit=10)
    assert res["success"] is True
    ids = [t["id"] for t in res["tracks"]]
    assert "t2" in ids
    assert "seed" not in ids          # seed always excluded


def test_excludes_caller_supplied_ids(db):
    _add_artist(db, "ar1", "Artist One")
    _add_album(db, "al1", "ar1", "Album A")
    _add_album(db, "al2", "ar1", "Album B")
    _add_track(db, "seed", "al1", "ar1", "Seed")
    _add_track(db, "t2", "al2", "ar1", "T2")
    _add_track(db, "t3", "al2", "ar1", "T3")
    db._conn.commit()

    res = db.get_radio_tracks("seed", limit=10, exclude_ids=["t2"])
    ids = [t["id"] for t in res["tracks"]]
    assert "t2" not in ids
    assert "t3" in ids


def test_tier2_genre_match_other_artists(db):
    # No same-artist alternatives; falls to genre tier.
    _add_artist(db, "ar1", "Seed Artist", genres='["shoegaze"]')
    _add_artist(db, "ar2", "Other Artist", genres='["shoegaze"]')
    _add_album(db, "al1", "ar1", "Seed Album", genres='["shoegaze"]')
    _add_album(db, "al2", "ar2", "Other Album", genres='["shoegaze"]')
    _add_track(db, "seed", "al1", "ar1", "Seed")
    _add_track(db, "g1", "al2", "ar2", "Genre Match")
    db._conn.commit()

    res = db.get_radio_tracks("seed", limit=10)
    ids = [t["id"] for t in res["tracks"]]
    assert "g1" in ids


def test_tier4_random_fallback_fills_when_no_metadata_match(db):
    # Seed has no genre/mood/style and no same-artist alts → random tier.
    _add_artist(db, "ar1", "Seed Artist")
    _add_artist(db, "ar2", "Unrelated")
    _add_album(db, "al1", "ar1", "Seed Album")
    _add_album(db, "al2", "ar2", "Unrelated Album")
    _add_track(db, "seed", "al1", "ar1", "Seed")
    _add_track(db, "r1", "al2", "ar2", "Random One")
    db._conn.commit()

    res = db.get_radio_tracks("seed", limit=10)
    ids = [t["id"] for t in res["tracks"]]
    assert "r1" in ids                # filled from random tier


def test_only_returns_tracks_with_files(db):
    _add_artist(db, "ar1", "Artist One")
    _add_album(db, "al1", "ar1", "Album A")
    _add_album(db, "al2", "ar1", "Album B")
    _add_track(db, "seed", "al1", "ar1", "Seed")
    _add_track(db, "nofile", "al2", "ar1", "No File", file_path="")
    db._conn.commit()

    res = db.get_radio_tracks("seed", limit=10)
    ids = [t["id"] for t in res["tracks"]]
    assert "nofile" not in ids        # file_path filter still enforced


def test_no_duplicate_ids_across_tiers(db):
    # A track that qualifies for both same-artist AND genre must appear once.
    _add_artist(db, "ar1", "Artist One", genres='["pop"]')
    _add_album(db, "al1", "ar1", "Album A", genres='["pop"]')
    _add_album(db, "al2", "ar1", "Album B", genres='["pop"]')
    _add_track(db, "seed", "al1", "ar1", "Seed")
    _add_track(db, "dup", "al2", "ar1", "Could Match Twice")
    db._conn.commit()

    res = db.get_radio_tracks("seed", limit=10)
    ids = [t["id"] for t in res["tracks"]]
    assert ids.count("dup") == 1
