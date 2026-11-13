"""The Discover shelves work out "which pool tracks do I already own" once.

Oct 30 2026 the owned provider ids were moved into temp tables "kept per
pooled connection" and refilled after five minutes. The pool drops a
connection's temp tables when it is handed back, so nothing was kept: every
shelf of every Discover page load walked the whole catalogue again (74 rebuilds
of 7-60s in six hours of one log, Nov 13 2026). The answer is now kept once
per process, and each connection is handed it as a small temp table.

The database here opens a NEW connection per call, which is what the pool's
reset amounts to: no temp table survives from one call to the next."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager

import pytest

import core.personalized_playlists as pp
from core.library2.schema import ensure_library_v2_schema

POOL_DDL = """
CREATE TABLE discovery_pool (
    id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL DEFAULT 1, source TEXT NOT NULL,
    spotify_track_id TEXT, itunes_track_id TEXT, deezer_track_id TEXT, track_name TEXT,
    artist_name TEXT, album_name TEXT, album_cover_url TEXT, duration_ms INTEGER,
    popularity INTEGER, release_date TEXT, artist_genres TEXT, track_data_json TEXT);
CREATE TABLE blocklist (id INTEGER PRIMARY KEY, profile_id INTEGER NOT NULL DEFAULT 1,
    entity_type TEXT, name TEXT);
"""


class FileDatabase:
    def __init__(self, path):
        self.path = str(path)
        with self._get_connection() as conn:
            conn.executescript(POOL_DDL)
            ensure_library_v2_schema(conn)

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def pool(self, name, **ids):
        with self._get_connection() as conn:
            return conn.execute(
                "INSERT INTO discovery_pool(source, track_name, artist_name, album_name, "
                "spotify_track_id, itunes_track_id, deezer_track_id) VALUES('spotify',?,?,?,?,?,?)",
                (name, f"artist {name}", f"album {name}", ids.get("spotify"),
                 ids.get("itunes"), ids.get("deezer"))).lastrowid

    def own(self, spotify=None, itunes=None, deezer=None, with_file=True):
        external = {k: v for k, v in (("itunes", itunes), ("deezer", deezer)) if v}
        with self._get_connection() as conn:
            artist = conn.execute("INSERT INTO lib2_artists(name, name_key, sort_name) "
                                  "VALUES('A','a','A')").lastrowid
            album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) "
                                 "VALUES(?,'Al')", (artist,)).lastrowid
            track = conn.execute("INSERT INTO lib2_tracks(album_id, title, spotify_id, external_ids) "
                                 "VALUES(?, 'T', ?, ?)", (album, spotify, json.dumps(external))).lastrowid
            if with_file:
                conn.execute("INSERT INTO lib2_track_files(track_id,path) VALUES(?,?)",
                             (track, f"/music/{track}.flac"))


@pytest.fixture
def db(tmp_path):
    pp.clear_owned_pool_cache()
    database = FileDatabase(tmp_path / "music.db")
    yield database
    pp.clear_owned_pool_cache()


@pytest.fixture
def builds(monkeypatch):
    calls = []
    real = pp._build_owned_pool_ids
    monkeypatch.setattr(pp, "_build_owned_pool_ids", lambda conn: calls.append(1) or real(conn))
    return calls


def _names(service):
    rows = service._select_discovery_tracks(source="spotify", order_by="id", fetch_limit=100)
    return [row["track_name"] for row in rows]


def _seed(db):
    db.pool("by spotify", spotify="sp-owned")
    db.pool("by itunes", spotify="sp-x1", itunes="it-owned")
    db.pool("by deezer", deezer="dz-owned")
    db.pool("new 1", spotify="sp-new", itunes="it-new", deezer="dz-new")
    db.pool("new 2", deezer="dz-other")
    db.pool("known to the catalogue, no file", spotify="sp-wanted")
    db.pool("no ids at all")
    db.own(spotify="sp-owned")
    db.own(itunes="it-owned")
    db.own(deezer="dz-owned")
    db.own(spotify="sp-wanted", with_file=False)


def test_a_pool_row_owned_under_any_of_its_three_ids_is_left_out(db):
    _seed(db)
    service = pp.PersonalizedPlaylistsService(db)
    assert _names(service) == ["new 1", "new 2", "known to the catalogue, no file"]
    everything = service._select_discovery_tracks(
        source="spotify", order_by="id", fetch_limit=100, exclude_owned=False)
    assert len(everything) == 6


def test_every_shelf_of_a_page_load_shares_one_build(db, builds):
    _seed(db)
    service = pp.PersonalizedPlaylistsService(db)
    for _ in range(6):
        assert _names(service) == ["new 1", "new 2", "known to the catalogue, no file"]
        service._candidates(source="spotify", fetch_limit=10)
    assert builds == [1]


def test_shelves_that_ask_at_the_same_moment_build_once(db, builds, monkeypatch):
    _seed(db)
    started, release = threading.Event(), threading.Event()
    real = pp._build_owned_pool_ids

    def slow(conn):   # `real` is the counting wrapper from the fixture
        started.set()
        release.wait(5)
        return real(conn)

    monkeypatch.setattr(pp, "_build_owned_pool_ids", slow)
    service = pp.PersonalizedPlaylistsService(db)
    results = []
    threads = [threading.Thread(target=lambda: results.append(_names(service))) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert started.wait(5)
    threading.Event().wait(0.2)
    release.set()
    for thread in threads:
        thread.join(10)
    assert builds == [1]
    assert results == [["new 1", "new 2", "known to the catalogue, no file"]] * 5


def _age(db, seconds):
    stamp, built_at, ids = pp._owned_pool_cache[db.path]
    pp._owned_pool_cache[db.path] = (stamp, built_at - seconds, ids)


def test_a_new_file_is_seen_after_a_minute_not_on_every_download(db, builds):
    _seed(db)
    service = pp.PersonalizedPlaylistsService(db)
    _names(service)
    db.own(spotify="sp-new")
    assert "new 1" in _names(service)          # inside the minute: the kept answer
    assert builds == [1]
    _age(db, pp._OWNED_IDS_MIN_AGE + 1)
    assert "new 1" not in _names(service)
    assert builds == [1, 1]


def test_new_pool_rows_are_checked_too(db, builds):
    _seed(db)
    service = pp.PersonalizedPlaylistsService(db)
    _names(service)
    db.pool("owned, added later", spotify="sp-owned")
    _age(db, pp._OWNED_IDS_MIN_AGE + 1)
    assert "owned, added later" not in _names(service)
    assert builds == [1, 1]


def test_nothing_new_is_rebuilt_only_after_the_ttl(db, builds):
    _seed(db)
    service = pp.PersonalizedPlaylistsService(db)
    _names(service)
    _age(db, pp._OWNED_IDS_TTL - 5)
    _names(service)
    assert builds == [1]
    _age(db, 10)
    _names(service)
    assert builds == [1, 1]


def test_a_stale_answer_is_used_while_another_shelf_rebuilds(db, builds, monkeypatch):
    _seed(db)
    service = pp.PersonalizedPlaylistsService(db)
    _names(service)
    _age(db, pp._OWNED_IDS_TTL + 1)
    started, release = threading.Event(), threading.Event()
    real = pp._build_owned_pool_ids

    def slow(conn):   # `real` is the counting wrapper from the fixture
        started.set()
        release.wait(5)
        return real(conn)

    monkeypatch.setattr(pp, "_build_owned_pool_ids", slow)
    rebuilding = threading.Thread(target=lambda: _names(service))
    rebuilding.start()
    assert started.wait(5)
    assert _names(service) == ["new 1", "new 2", "known to the catalogue, no file"]  # did not wait
    release.set()
    rebuilding.join(10)
    assert builds == [1, 1]


def test_the_build_leaves_no_temp_tables_behind(db):
    _seed(db)
    with db._get_connection() as conn:
        pp._ensure_owned_ids(conn)
        left = {row[0] for row in conn.execute("SELECT name FROM sqlite_temp_master")}
    assert left == {"owned_pool", "owned_pool_version"}
