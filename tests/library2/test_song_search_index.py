"""The Songs tab's search index answers exactly what the catalogue scan did.

Upstream #1906 kept the Songs tab on indexes; the catalogue port scanned every
track per request (7.7s to open, 10-11s per search on a 310k library). The
index must not change a single result, before or after the library changes.
"""

import pytest

from core.library2 import queries as Q
from core.library2 import song_search
from core.library2.schema import ensure_library_v2_schema
from tests import lib2_seed

QUERIES = ("sour", "sour times", "sand", "sánd", "SÁND", "bjork", "BJÖRK", "björk",
           "gorillaz", "various", "til", "tomorrow", "ac/dc", "back in", "xx", "o",
           "zzz nothing", "100%", "under_score", "feel good")


class _Database:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return lib2_seed.row_conn(self.path)


@pytest.fixture()
def library(tmp_path):
    path = str(tmp_path / "songs.db")
    conn = lib2_seed.row_conn(path)
    ensure_library_v2_schema(conn)
    rows = [
        ("Portishead", "Dummy", "Sour Times", None),
        ("Portishead", "Dummy", "Glory Box", None),
        ("Morgan Wallen", "Dangerous", "Sánd in My Boots", None),
        ("Björk", "Post", "Hyperballad", None),
        ("Björk", "Post", "Army of Me", None),
        ("Various Artists", "Summer Hits", "Feel Good Inc", "Gorillaz"),
        ("Various Artists", "Summer Hits", "Clint Eastwood", "Gorillaz"),
        ("Chris Young", "Famous Friends", "'Til Tomorrow", None),
        ("AC/DC", "Back in Black", "Back In Black", None),
        ("Nobody", "Odd Titles", "100% Pure", None),
        ("Nobody", "Odd Titles", "under_score song", None),
    ]
    ids = {}
    for artist, album, title, track_artist in rows:
        cols = {"track_artist": track_artist} if track_artist else {}
        ids[title] = lib2_seed.track(conn, artist, album, title, **cols)
    conn.commit()
    yield conn, _Database(path), ids
    conn.close()


def _built(conn, value):
    conn.execute("UPDATE lib2_song_search_state SET built=? WHERE id=1", (value,))
    conn.commit()


def _search(conn, query):
    result = Q.list_songs(conn, search=query, limit=500)
    return [s["id"] for s in result["songs"]], result["pagination"]["total_count"]


#: the queries the index serves; the rest must fall back, never half-serve
_SERVED = [q for q in QUERIES if len(q) >= 3 and "%" not in q and "_" not in q]


def _assert_same_as_the_scan(conn):
    for query in QUERIES:
        _built(conn, 1)
        from core.text.normalize import normalize_for_comparison
        folded = normalize_for_comparison(query) or query.lower()
        # never a vacuous pass: the index really answered every query it serves
        assert (song_search.matching_ids(conn, folded) is not None) == (query in _SERVED), query
        indexed = _search(conn, query)
        _built(conn, 0)
        scanned = _search(conn, query)
        _built(conn, 1)
        assert indexed == scanned, query


def test_the_index_answers_exactly_what_the_scan_did(library):
    conn, db, _ = library
    assert song_search.rebuild(db)
    assert song_search.matching_ids(conn, "sour") is not None
    _assert_same_as_the_scan(conn)


def test_changes_are_exact_before_and_after_the_catch_up(library):
    conn, db, ids = library
    assert song_search.rebuild(db)
    conn.execute("UPDATE lib2_tracks SET title='Sourdough' WHERE id=?", (ids["Glory Box"],))
    conn.execute("UPDATE lib2_artists SET name='Bjork Gudmundsdottir' WHERE name='Björk'")
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (ids["Sour Times"],))
    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (ids["Sour Times"],))
    lib2_seed.track(conn, "Gorillaz", "Demon Days", "Feel Good Inc (Live)")
    conn.commit()

    # dirty tracks are answered live: exact before the index catches up
    _assert_same_as_the_scan(conn)
    assert song_search.flush(db) > 0
    assert conn.execute("SELECT COUNT(*) FROM lib2_song_search_dirty").fetchone()[0] == 0
    _assert_same_as_the_scan(conn)


def test_an_index_that_was_never_built_leaves_the_scan_in_charge(library):
    conn, _, _ = library
    assert song_search.matching_ids(conn, "sour") is None
    assert _search(conn, "sour times")[1] == 1


def test_the_song_count_follows_every_file_change(library):
    conn, _, ids = library
    assert Q.list_songs(conn)["pagination"]["total_count"] == 11
    lib2_seed.track(conn, "Portishead", "Third", "Machine Gun")
    conn.commit()
    assert Q.list_songs(conn)["pagination"]["total_count"] == 12
    conn.execute("UPDATE lib2_track_files SET file_state='missing' WHERE track_id=?", (ids["Glory Box"],))
    conn.commit()
    assert Q.list_songs(conn)["pagination"]["total_count"] == 11


def test_the_a_to_z_page_walks_its_index(library):
    conn, _, _ = library
    from core.library2.queries import _SONG_FROM, _SONG_SORTS, _alpha_key, _song_files_sql
    order = _SONG_SORTS["title"].replace("{title}", _alpha_key("t.title"))
    plan = " ".join(r[3] for r in conn.execute(
        f"EXPLAIN QUERY PLAN SELECT t.id {_SONG_FROM} WHERE EXISTS (SELECT 1 {_song_files_sql('t')})"
        f" ORDER BY {order} LIMIT 75"))
    assert "idx_lib2_tracks_alpha" in plan
    assert "TEMP B-TREE" not in plan


def test_an_index_table_of_another_shape_is_rebuilt(tmp_path):
    """derived data: an older layout is dropped and rebuilt, never a build
    that fails on every start."""
    path = str(tmp_path / "old.db")
    conn = lib2_seed.row_conn(path)
    conn.execute("CREATE VIRTUAL TABLE lib2_song_search USING fts5(title, artist, tokenize='trigram')")
    ensure_library_v2_schema(conn)
    lib2_seed.track(conn, "Portishead", "Dummy", "Sour Times")
    conn.commit()

    assert [r[1] for r in conn.execute("PRAGMA table_info(lib2_song_search)")] == [
        "title", "track_artist", "artist"]
    assert song_search.rebuild(_Database(path))
    assert song_search.matching_ids(conn, "sour") is not None
    conn.close()
