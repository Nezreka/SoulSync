"""the Songs tab's artist / album / year / recent sorts walk an index.

3.6.1 read these sorts off an index. on library v2 each page read every song
and sorted them all: ~25 s a page on a 310k-track library. with the sort
indexes the same order is read one page at a time. the order must be exactly
the one the sorted query gives, page for page, including the corners: an
album whose artist row is gone (it sorts first), albums without a year and
songs without an added date (last), duplicate titles, several discs.
"""

from __future__ import annotations

import random

import pytest

from core.library2 import queries as Q
from core.library2.schema import ensure_library_v2_schema
from tests import lib2_seed


def _library(path, seed=11):
    rng = random.Random(seed)
    c = lib2_seed.row_conn(str(path))
    ensure_library_v2_schema(c)
    names = ["Abba", "abba", "Zed", "Ölund", "the Band", "Mono", "Alpha", "beta"]
    n = 0
    for a in range(24):
        artist = f"{rng.choice(names)} {a % 7}"
        for b in range(rng.randint(1, 3)):
            title = rng.choice(["Hits", "hits", "Zero", "Blue", "Ärger", "Live"]) + f" {b % 2}"
            year = None if rng.random() < 0.2 else rng.choice([1999, 2005, 2005, 2021])
            lib2_seed.album(c, artist, title, year=year)
            for t in range(rng.randint(1, 5)):
                n += 1
                lib2_seed.track(c, artist, title, rng.choice(["Song", "song", "Intro"]) + f" {t}",
                                path=f"/m/{n}.flac", track_number=rng.choice([1, 2, 2, 3]),
                                duration=200000)
    c.execute("UPDATE lib2_tracks SET disc_number = 1 + (id % 3 = 0)")
    c.execute("UPDATE lib2_tracks SET added_at = CASE WHEN id % 9 = 0 THEN NULL"
              " ELSE datetime('2026-01-01', '+' || (id % 5) || ' days') END")
    c.execute("UPDATE lib2_artists SET sort_name = CASE WHEN id % 4 = 0 THEN NULL"
              " WHEN id % 4 = 1 THEN '' ELSE 'The ' || name END")
    # two albums whose artist row is gone: they sort first by artist
    c.execute("PRAGMA foreign_keys = OFF")
    gone = [r[0] for r in c.execute("SELECT id FROM lib2_albums ORDER BY id DESC LIMIT 2")]
    c.execute("UPDATE lib2_albums SET primary_artist_id = 900000 + id WHERE id IN (?, ?)", gone)
    c.execute("UPDATE lib2_track_files SET file_state = 'deleted' WHERE id % 11 = 0")
    c.commit()
    return c


def _pages(conn, sort, **kw):
    out, page = [], 1
    while True:
        result = Q.list_songs(conn, sort=sort, page=page, limit=7, **kw)
        out.append([s["track_id"] for s in result["songs"]])
        if not result["pagination"]["has_next"]:
            return out
        page += 1


@pytest.mark.parametrize("sort", ["artist", "album", "year", "recent", "title"])
@pytest.mark.parametrize("filters", [{}, {"letter": "s"}, {"dupes": True}, {"quality": "lossless"}])
def test_the_index_walk_gives_the_sorted_querys_pages(tmp_path, sort, filters):
    sorted_db = _library(tmp_path / "a.db")
    walked_db = _library(tmp_path / "b.db")
    assert Q.ensure_song_sort_indexes(walked_db) == len(Q.SONG_SORT_INDEXES)
    assert not Q.song_sort_indexes_ready(sorted_db)

    expected = _pages(sorted_db, sort, **filters)
    assert sum(len(p) for p in expected) > 0
    assert _pages(walked_db, sort, **filters) == expected


def test_an_album_without_its_artist_row_still_sorts_first_by_artist(tmp_path):
    conn = _library(tmp_path / "c.db")
    Q.ensure_song_sort_indexes(conn)
    first = Q.list_songs(conn, sort="artist", limit=200)["songs"]
    orphans = {r[0] for r in conn.execute(
        "SELECT id FROM lib2_albums WHERE primary_artist_id > 900000")}
    leading = [s["album_id"] for s in first[:sum(1 for s in first if s["album_id"] in orphans)]]
    assert leading and set(leading) <= orphans


def _real_library_stats(conn):
    """plan as a 313k-track library does: a tiny db plans forgivingly, so the
    statistics are the ones a real library's ANALYZE wrote (Nov 2026)"""
    conn.execute("ANALYZE")
    rows = {"lib2_tracks": 313147, "lib2_albums": 72200, "lib2_artists": 29507,
            "lib2_track_files": 318000}
    conn.execute("DELETE FROM sqlite_stat1")
    for name, table in conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND tbl_name IN"
            " ('lib2_tracks','lib2_albums','lib2_artists','lib2_track_files')").fetchall():
        cols = conn.execute(f"SELECT COUNT(*) FROM pragma_index_info('{name}')").fetchone()[0] or 1
        # a lookup on the leading column finds ~3 rows (the real album/track index)
        stat = " ".join([str(rows[table])] + ["3"] + ["1"] * (cols - 1))
        conn.execute("INSERT INTO sqlite_stat1(tbl, idx, stat) VALUES(?,?,?)", (table, name, stat))
    for table, n in rows.items():
        conn.execute("INSERT INTO sqlite_stat1(tbl, idx, stat) VALUES(?, NULL, ?)", (table, str(n)))
    conn.commit()
    conn.execute("ANALYZE sqlite_schema")  # reload the statistics


@pytest.mark.parametrize("sort", ["artist", "album", "year", "recent"])
def test_the_walk_never_sorts_the_whole_library(tmp_path, sort):
    """a full sort ("USE TEMP B-TREE FOR ORDER BY") is the 25 s page"""
    conn = _library(tmp_path / "d.db")
    Q.ensure_song_sort_indexes(conn)
    _real_library_stats(conn)
    frm, order = Q._INDEXED_SONG_SORTS[sort]
    plan = " | ".join(r[3] for r in conn.execute(
        f"EXPLAIN QUERY PLAN SELECT t.id {frm} WHERE 1 ORDER BY {order} LIMIT 75"))
    assert "TEMP B-TREE FOR ORDER BY" not in plan, plan


def test_building_the_indexes_is_idempotent_and_never_raises(tmp_path):
    conn = _library(tmp_path / "e.db")
    assert Q.ensure_song_sort_indexes(conn) == len(Q.SONG_SORT_INDEXES)
    assert Q.ensure_song_sort_indexes(conn) == 0
    assert Q.song_sort_indexes_ready(conn)

    class Broken:
        def execute(self, *a):
            raise RuntimeError("locked")
    assert Q.ensure_song_sort_indexes(Broken()) == 0


def test_the_deferred_convergence_builds_them():
    import inspect
    from core.library2 import bootstrap
    assert "ensure_song_sort_indexes(conn)" in inspect.getsource(bootstrap.run_deferred_backfills)
