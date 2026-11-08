"""The folded-text index answers exactly what unidecode_lower(col) LIKE did.

A sync spent most of its time folding all 72k album titles (and 29k artist
names, 77k per-track credits) on every library lookup. The index folds each
row once; these pin that it returns the same rows as the SQL it replaces and
keeps up with rows added, removed and renamed.
"""

from __future__ import annotations

import sqlite3

import pytest

from database import fold_index


@pytest.fixture(autouse=True)
def _fresh():
    fold_index.clear()
    yield
    fold_index.clear()


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.create_function("unidecode_lower", 1, fold_index.fold)
    conn.execute("CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, title TEXT)")
    conn.executemany("INSERT INTO lib2_albums (id, title) VALUES (?, ?)", [
        (1, "Obsesión"), (2, "Tina Tuesday"), (3, "TINA"), (4, None),
        (5, "Café del Mar"), (6, "100%_Pure"), (7, "Björk Live"),
    ])
    return conn


def _sql(conn, needle):
    return [r[0] for r in conn.execute(
        "SELECT id FROM lib2_albums WHERE unidecode_lower(title) LIKE ? ORDER BY id",
        (f"%{needle}%",))]


@pytest.mark.parametrize("needle", ["obsesion", "tina", "cafe", "bjork", "zzz", "100", "%_p", "e_d"])
def test_same_rows_as_the_sql_it_replaces(needle):
    conn = _conn()
    got = fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, needle)
    assert sorted(got) == _sql(conn, needle)


def test_added_rows_are_found_without_a_refold(monkeypatch):
    conn = _conn()
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2, 3]
    folded = []
    real = fold_index.fold
    monkeypatch.setattr(fold_index, "fold", lambda v: folded.append(v) or real(v))
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (8, 'Tina Turner Hits')")
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2, 3, 8]
    assert folded == ["Tina Turner Hits"]


def test_a_deleted_row_is_gone():
    conn = _conn()
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2, 3]
    conn.execute("DELETE FROM lib2_albums WHERE id = 3")
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2]


def test_a_delete_and_an_add_together_still_refold():
    conn = _conn()
    fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina")
    conn.execute("DELETE FROM lib2_albums WHERE id = 3")
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (9, 'Other')")
    # same count, higher max id: not a pure append
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2]


def test_a_renamed_title_is_seen_after_the_refresh(monkeypatch):
    conn = _conn()
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "renamed") == []
    conn.execute("UPDATE lib2_albums SET title = 'Renamed' WHERE id = 2")
    monkeypatch.setattr(fold_index, "REFRESH_SECONDS", 0.0)
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "renamed") == [2]


def test_each_database_has_its_own_index():
    a, b = _conn(), _conn()
    b.execute("DELETE FROM lib2_albums WHERE id = 2")
    assert fold_index.matching_ids(a, "a", fold_index.ALBUM_TITLES, "tina") == [2, 3]
    assert fold_index.matching_ids(b, "b", fold_index.ALBUM_TITLES, "tina") == [3]


def test_limit_stops_early():
    conn = _conn()
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "", limit=2) == [1, 2]


def test_a_title_repeating_the_needle_counts_once_and_rows_never_join():
    conn = _conn()
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (10, 'tina tina tina')")
    # 'ytina' would only exist across the end of one title and the next
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (11, 'Hey')")
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (12, 'Tina')")
    got = fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina")
    assert got == _sql(conn, "tina") == [2, 3, 10, 12]
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "eytina") == []


def test_a_change_waits_for_the_check_interval(monkeypatch):
    monkeypatch.setattr(fold_index, "CHECK_SECONDS", 3600.0)
    conn = _conn()
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2, 3]
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (8, 'Tina Turner Hits')")
    # inside the interval the table isn't asked again
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2, 3]
    monkeypatch.setattr(fold_index, "CHECK_SECONDS", 0.0)
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2, 3, 8]


def test_artist_keys_answer_what_norm_key_equality_did():
    from core.text.normalize import normalize_key
    conn = sqlite3.connect(":memory:")
    conn.create_function("norm_key", 1, lambda x: normalize_key(x) if x else "")
    conn.execute("CREATE TABLE lib2_artists (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO lib2_artists (id, name) VALUES (?, ?)", [
        (1, "AC/DC"), (2, "ACDC"), (3, "Björk"), (4, "Bjork"), (5, "Beyoncé"), (6, None)])
    for form in ("AC-DC", "bjork", "Beyonce", "Nobody"):
        key = normalize_key(form)
        want = sorted(r[0] for r in conn.execute(
            "SELECT id FROM lib2_artists WHERE norm_key(name) = ?", (key,)))
        assert sorted(fold_index.ids_with_key(conn, "db", fold_index.ARTIST_KEYS, key)) == want
    assert fold_index.ids_with_key(conn, "db", fold_index.ARTIST_KEYS, "") == []
