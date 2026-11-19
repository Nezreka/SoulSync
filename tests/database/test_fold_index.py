"""The folded-text index answers exactly what unidecode_lower(col) LIKE did.

A sync spent most of its time folding all 72k album titles (and 29k artist
names, 77k per-track credits) on every library lookup. The index folds each
row once; these pin that it returns the same rows as the SQL it replaces and
keeps up with rows added, removed and renamed.
"""

from __future__ import annotations

import sqlite3
import threading
import time

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


# ── #2205: one look at a time, deletes without a refold, refresh off-thread ──


def _file_db(tmp_path, rows=None):
    path = str(tmp_path / "fold.db")
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.create_function("unidecode_lower", 1, fold_index.fold)
    conn.execute("CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, title TEXT)")
    conn.executemany("INSERT INTO lib2_albums (id, title) VALUES (?, ?)", rows or [
        (1, "Obsesión"), (2, "Tina Tuesday"), (3, "TINA"), (4, None),
        (5, "Café del Mar"), (6, "100%_Pure"), (7, "Björk Live"),
    ])
    conn.commit()
    return path, conn


def _count_full_reads(monkeypatch, delay=0.0):
    reads = []
    real = fold_index._rows

    def rows(conn, spec, after_id=None):
        if after_id is None:
            reads.append(spec)
            if delay:
                time.sleep(delay)
        return real(conn, spec, after_id=after_id)

    monkeypatch.setattr(fold_index, "_rows", rows)
    return reads


def test_twenty_threads_on_a_cold_index_read_the_table_once(tmp_path, monkeypatch):
    path, _ = _file_db(tmp_path)
    reads = _count_full_reads(monkeypatch, delay=0.2)
    results, barrier = [], threading.Barrier(20)

    def go():
        conn = sqlite3.connect(path)
        barrier.wait()
        results.append(fold_index.matching_ids(conn, path, fold_index.ALBUM_TITLES, "tina"))
        conn.close()

    threads = [threading.Thread(target=go) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reads) == 1
    assert results == [[2, 3]] * 20


def test_a_stale_index_answers_from_the_snapshot_while_another_thread_looks(tmp_path, monkeypatch):
    path, conn = _file_db(tmp_path)
    assert fold_index.matching_ids(conn, path, fold_index.ALBUM_TITLES, "tina") == [2, 3]
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (8, 'Tina Turner Hits')")
    conn.commit()
    asked = []
    monkeypatch.setattr(fold_index, "_signature", lambda *a: asked.append(a) or (0, 0))
    flight = fold_index._flight((path, fold_index.ALBUM_TITLES))
    held, done = threading.Event(), threading.Event()

    def someone_else_looking():
        with flight:
            held.set()
            done.wait(10)

    holder = threading.Thread(target=someone_else_looking)
    holder.start()
    held.wait(5)
    got = []
    asker = threading.Thread(target=lambda: got.append(
        fold_index.matching_ids(conn, path, fold_index.ALBUM_TITLES, "tina")))
    asker.start()
    asker.join(2)
    answered_while_held = not asker.is_alive()
    done.set()
    holder.join(5)
    asker.join(5)
    # answered at once from the snapshot, without asking the table
    assert answered_while_held
    assert got == [[2, 3]] and asked == []


def test_a_cold_index_waits_for_the_build_instead_of_answering_empty(tmp_path, monkeypatch):
    path, _ = _file_db(tmp_path)
    flight = fold_index._flight((path, fold_index.ALBUM_TITLES))
    got = []
    flight.acquire()
    t = threading.Thread(target=lambda: got.append(fold_index.matching_ids(
        sqlite3.connect(path), path, fold_index.ALBUM_TITLES, "tina")))
    t.start()
    time.sleep(0.2)
    assert got == []  # still waiting
    flight.release()
    t.join(5)
    assert got == [[2, 3]]


def test_a_delete_folds_nothing_and_reads_no_text(monkeypatch):
    conn = _conn()
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2, 3]
    reads = _count_full_reads(monkeypatch)
    folded = []
    real = fold_index._FOLDS["fold"]
    monkeypatch.setitem(fold_index._FOLDS, "fold", lambda v: folded.append(v) or real(v))
    conn.execute("DELETE FROM lib2_albums WHERE id IN (3, 5)")
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2]
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "cafe") == []
    assert reads == [] and folded == []


def test_a_delete_and_an_add_fold_only_the_new_row(monkeypatch):
    conn = _conn()
    fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina")
    reads = _count_full_reads(monkeypatch)
    folded = []
    real = fold_index._FOLDS["fold"]
    monkeypatch.setitem(fold_index._FOLDS, "fold", lambda v: folded.append(v) or real(v))
    conn.execute("DELETE FROM lib2_albums WHERE id = 3")
    conn.execute("INSERT INTO lib2_albums (id, title) VALUES (9, 'Tina Again')")
    assert fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, "tina") == [2, 9]
    assert reads == [] and folded == ["Tina Again"]


def _wait_refolds():
    for t in threading.enumerate():
        if t.name.startswith("fold-refold-"):
            t.join(5)


def test_the_periodic_refold_runs_off_the_calling_thread(tmp_path, monkeypatch):
    path, conn = _file_db(tmp_path)
    assert fold_index.matching_ids(conn, path, fold_index.ALBUM_TITLES, "renamed") == []
    conn.execute("UPDATE lib2_albums SET title = 'Renamed' WHERE id = 2")
    conn.commit()
    reads = []
    real = fold_index._rows
    caller = threading.current_thread()

    def rows(c, spec, after_id=None):
        if after_id is None:
            reads.append(threading.current_thread() is caller)
        return real(c, spec, after_id=after_id)

    monkeypatch.setattr(fold_index, "_rows", rows)
    monkeypatch.setattr(fold_index, "REFRESH_SECONDS", 0.0)
    # the caller keeps the snapshot; the refold happens on its own thread
    assert fold_index.matching_ids(conn, path, fold_index.ALBUM_TITLES, "renamed") == []
    _wait_refolds()
    monkeypatch.setattr(fold_index, "REFRESH_SECONDS", 3600.0)
    assert fold_index.matching_ids(conn, path, fold_index.ALBUM_TITLES, "renamed") == [2]
    assert reads == [False]


def test_a_refold_started_before_clear_never_lands_after_it(tmp_path, monkeypatch):
    path, conn = _file_db(tmp_path)
    fold_index.matching_ids(conn, path, fold_index.ALBUM_TITLES, "tina")
    gate = threading.Event()
    real = fold_index._full

    def slow_full(*a, **k):
        gate.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(fold_index, "_full", slow_full)
    monkeypatch.setattr(fold_index, "REFRESH_SECONDS", 0.0)
    fold_index.matching_ids(conn, path, fold_index.ALBUM_TITLES, "tina")
    fold_index.clear()
    gate.set()
    _wait_refolds()
    assert fold_index._entries == {}


def test_random_writes_answer_what_the_sql_does(monkeypatch):
    import random
    rnd = random.Random(2205)
    conn = _conn()
    words = ["tina", "café", "björk", "obsesión", "mar", "live", "pure"]
    next_id = 20
    for _ in range(300):
        op = rnd.random()
        if op < 0.4:
            conn.execute("INSERT INTO lib2_albums (id, title) VALUES (?, ?)",
                         (next_id, " ".join(rnd.sample(words, 2))))
            next_id += 1
        elif op < 0.7:
            row = conn.execute("SELECT id FROM lib2_albums ORDER BY random() LIMIT 1").fetchone()
            if row:
                conn.execute("DELETE FROM lib2_albums WHERE id = ?", row)
        elif op < 0.8:
            # a low id coming back (ids aren't AUTOINCREMENT)
            conn.execute("INSERT OR IGNORE INTO lib2_albums (id, title) VALUES (?, ?)",
                         (rnd.randint(1, 19), rnd.choice(words)))
        needle = fold_index.fold(rnd.choice(words))
        assert sorted(fold_index.matching_ids(conn, "db", fold_index.ALBUM_TITLES, needle)) \
            == _sql(conn, needle)
