"""the Albums grid reads a page off a sort index and keeps its member list.

on a 71k-release library every page worked out library membership for every
release twice (the count and the page) and sorted them all: 1.4 s a page
(review 5 perf). the member ids are now kept until a table they come from
changes (core.library2.member_version) and the page is read off an index in
sort order. the pages must be exactly the ones the old query gave.
"""

from __future__ import annotations

import random

import pytest

from core.library2 import member_version
from core.library2 import queries as Q
from core.library2.schema import ensure_library_v2_schema
from tests import lib2_seed


def _library(path, seed=7):
    rng = random.Random(seed)
    c = lib2_seed.row_conn(str(path))
    ensure_library_v2_schema(c)
    n = 0
    for a in range(12):
        artist = f"{rng.choice(['Abba', 'the Band', 'Zed', 'Ölund'])} {a}"
        for b in range(rng.randint(1, 4)):
            title = rng.choice(['"Hits"', "hits", "Zero", "Blue", "Ärger", "...Live"]) + f" {b % 2}"
            year = None if rng.random() < 0.25 else rng.choice([0, 1999, 2005, 2021])
            lib2_seed.album(c, artist, title, year=year)
            for t in range(rng.randint(0, 3)):
                n += 1
                lib2_seed.track(c, artist, title, f"Song {t}", path=f"/m/{n}.flac")
    c.execute("UPDATE lib2_albums SET added_at = CASE WHEN id % 5 = 0 THEN NULL"
              " ELSE datetime('2026-01-01', '+' || (id % 4) || ' days') END")
    # some releases are not in the library: no file, no monitoring
    c.execute("UPDATE lib2_albums SET monitored = 0 WHERE id % 3 = 0")
    c.execute("UPDATE lib2_track_files SET file_state = 'deleted' WHERE id % 4 = 0")
    c.commit()
    return c


def _pages(conn, **kw):
    out, page = [], 1
    while True:
        albums, total = Q.list_albums(conn, page=page, limit=5, **kw)
        out.append(([a["id"] for a in albums], total, albums))
        if page * 5 >= total:
            return out
        page += 1


@pytest.mark.parametrize("sort", ["title", "year_desc", "year_asc", "added"])
@pytest.mark.parametrize("filters", [{}, {"search": "a"}, {"letter": "h"},
                                     {"monitored": "unmonitored"}])
def test_the_walk_gives_the_old_querys_pages(tmp_path, sort, filters):
    old = _library(tmp_path / "a.db")
    # no counter table: the membership-in-the-query path, as before
    old.execute("DROP TABLE lib2_member_state")
    walked = _library(tmp_path / "b.db")
    assert Q.ensure_album_sort_indexes(walked) == len(Q.ALBUM_SORT_INDEXES)
    member_version.clear()

    expected = _pages(old, sort=sort, **filters)
    assert expected[0][1] > 0
    assert _pages(walked, sort=sort, **filters) == expected


@pytest.mark.parametrize("sort", ["title", "year_desc", "year_asc", "added"])
def test_a_page_is_read_off_the_sort_index_without_membership_again(tmp_path, sort):
    conn = _library(tmp_path / "c.db")
    Q.ensure_album_sort_indexes(conn)
    member_version.clear()
    Q.list_albums(conn, sort=sort)
    seen: list = []
    conn.set_trace_callback(seen.append)
    Q.list_albums(conn, sort=sort, page=2, limit=3)
    conn.set_trace_callback(None)
    # the member list came from the cache: nothing walked every release
    assert not [s for s in seen if "owned_t" in s]
    walks = [s for s in seen if s.lstrip().startswith("SELECT al.id FROM lib2_albums al")
             and "ORDER BY" in s]
    assert walks
    for sql in walks:
        plan = " | ".join(r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
        assert "_order" in plan and "TEMP B-TREE FOR ORDER BY" not in plan, plan


def test_a_change_to_membership_shows_on_the_next_page(tmp_path):
    conn = _library(tmp_path / "d.db")
    Q.ensure_album_sort_indexes(conn)
    member_version.clear()
    _albums, total = Q.list_albums(conn)
    gone = conn.execute("SELECT id FROM lib2_albums WHERE monitored = 0 AND NOT EXISTS ("
                        "SELECT 1 FROM lib2_tracks t WHERE t.album_id = lib2_albums.id)"
                        " LIMIT 1").fetchone()[0]
    conn.execute("UPDATE lib2_albums SET monitored = 1 WHERE id = ?", (gone,))
    conn.commit()
    albums, after = Q.list_albums(conn, limit=500)
    assert after == total + 1
    assert gone in [a["id"] for a in albums]
