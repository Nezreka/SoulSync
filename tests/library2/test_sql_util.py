"""Chunk-safe id-set query helper (review Teil B reuse/999-var-limit)."""

from __future__ import annotations

import sqlite3

from core.library2.sql_util import select_existing_ids


def _conn_with_ids(ids):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO t(id) VALUES(?)", [(i,) for i in ids])
    return conn


def test_returns_only_existing_ids():
    conn = _conn_with_ids([1, 2, 3])
    assert select_existing_ids(conn, "t", [2, 3, 99]) == {2, 3}


def test_empty_input_is_empty_no_query():
    conn = _conn_with_ids([1])
    assert select_existing_ids(conn, "t", []) == set()


def test_survives_more_ids_than_sqlite_variable_limit():
    """The whole point: an IN list far larger than SQLite's 999-var limit
    must not raise — it's chunked. 5000 present + 5000 absent ids."""
    present = list(range(1, 5001))
    conn = _conn_with_ids(present)
    absent = list(range(100000, 105000))
    result = select_existing_ids(conn, "t", present + absent, chunk=900)
    assert result == set(present)


def test_custom_column():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, ref INTEGER)")
    conn.executemany("INSERT INTO t(id, ref) VALUES(?,?)",
                     [(1, 10), (2, 20)])
    assert select_existing_ids(conn, "t", [10, 99], column="ref") == {10}
