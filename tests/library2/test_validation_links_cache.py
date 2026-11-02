"""the tag precache doesn't re-parse every pending finding per file.

core/library2/validation keeps a {file_id: finding ids} map of pending
findings, rebuilt when the pending count or the newest id changes. the tag
precache inserts a Re-tag finding for every file with issues, so every insert
threw the map away and the next file parsed every pending finding again:
quadratic over a big upgrade, under the write lock. its own inserts now
update the map in place.
"""

from __future__ import annotations

import json
import sqlite3

from core.library2 import validation


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE repair_findings (id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, "
        "severity TEXT, entity_type TEXT, entity_id TEXT, file_path TEXT, title TEXT, "
        "description TEXT, details_json TEXT, status TEXT DEFAULT 'pending')")
    return conn


def _insert(conn, file_id):
    cur = conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, details_json) VALUES ('library_retag', 'library_retag', ?)",
        (json.dumps({"validation": {"file_id": file_id}}),))
    return cur.lastrowid


def test_an_own_insert_keeps_the_map_without_a_rebuild():
    validation._JSON_LINKS.update(key=None, at=0.0, links={})
    conn = _db()
    first = _insert(conn, 1)
    assert validation._json_file_links(conn) == {"1": {first}}
    built_at = validation._JSON_LINKS["at"]

    second = _insert(conn, 2)
    validation._note_own_finding(conn, second, 2)

    links = validation._json_file_links(conn)
    assert links == {"1": {first}, "2": {second}}
    assert validation._JSON_LINKS["at"] == built_at  # served from the map, not rebuilt


def test_a_map_that_was_already_stale_is_not_marked_current():
    validation._JSON_LINKS.update(key=None, at=0.0, links={})
    conn = _db()
    _insert(conn, 1)
    validation._json_file_links(conn)
    _insert(conn, 3)                 # someone else's insert, the map doesn't know it
    mine = _insert(conn, 2)
    validation._note_own_finding(conn, mine, 2)
    assert "3" in validation._json_file_links(conn)  # rebuilt, nothing lost
