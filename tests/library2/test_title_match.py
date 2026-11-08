"""Plays link to their track whatever the case of a non-ascii letter.

review 5 R5-H-5 and R5-A-1: the play key was python's lower(), the catalogue
side sqlite's LOWER(), which folds only A-Z. 'WÖRLDENDER' became 'wörldender'
on one side and 'wÖrldender' on the other, so the play never linked.
"""
import sqlite3

import pytest

from core.library2.importer import normalize_name
from core.library2.schema import ensure_library_v2_schema
from core.library2.title_match import resolve_title_artist_pairs, sqlite_lower, title_fold


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    ensure_library_v2_schema(c)
    yield c
    c.close()


def _track(conn, artist, title):
    aid = conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES(?,?)",
                       (artist, normalize_name(artist))).lastrowid
    alid = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'LP')",
                        (aid,)).lastrowid
    return conn.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?,?)",
                        (alid, title)).lastrowid


def test_sqlite_lower_matches_sqlite():
    for text in ("WÖRLDENDER", "BIONICA MALÉFICA", "Plain"):
        assert sqlite_lower(text) == conn_lower(text)


def conn_lower(text):
    c = sqlite3.connect(":memory:")
    try:
        return c.execute("SELECT LOWER(?)", (text,)).fetchone()[0]
    finally:
        c.close()


@pytest.mark.parametrize("stored,played", [
    ("WÖRLDENDER", "WÖRLDENDER"),          # exactly the stored title
    ("WÖRLDENDER", "Wörldender"),          # another case of the same letters
    ("LO QUE LE PASÓ A HAWAii", "Lo Que Le Pasó a Hawaii"),
    ("Hyperballad", "HYPERBALLAD"),        # ascii control
])
def test_a_title_links_whatever_its_case(conn, stored, played):
    tid = _track(conn, "Artist", stored)
    key = normalize_name("Artist")
    found = resolve_title_artist_pairs(conn.cursor(), [(played, key)])
    assert found == {(title_fold(played), key): tid}


def test_another_artists_title_never_links(conn):
    _track(conn, "Someone Else", "WÖRLDENDER")
    found = resolve_title_artist_pairs(conn.cursor(), [("wörldender", "artist")])
    assert found == {}


def test_both_lookups_run_on_indexes(conn):
    _track(conn, "Artist", "WÖRLDENDER")
    seen = []
    conn.set_trace_callback(seen.append)
    resolve_title_artist_pairs(conn.cursor(), [("Wörldender", "artist")])
    conn.set_trace_callback(None)
    queries = [q for q in seen if "FROM lib2_" in q]
    assert len(queries) == 2   # the title index pass, then the artist fallback
    for sql in queries:
        plan = " | ".join(str(r[3]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
        assert "SCAN t" not in plan and "SCAN ar" not in plan, plan
