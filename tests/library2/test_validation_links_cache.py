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


def _builds(conn):
    """How many times the map is rebuilt (the query that parses every finding)."""
    seen = []
    conn.set_trace_callback(lambda sql: seen.append(1) if "details_json LIKE" in sql else None)
    return seen


def test_closing_findings_keeps_the_map_without_a_rebuild():
    """nov 13 2026, a bulk fix on 8k tag findings: each file's fix closed its
    finding, the pending count moved, and the next file parsed every pending
    finding again (14% of a core for the length of the job, under the write
    lock)."""
    validation._JSON_LINKS.update(key=None, at=0.0, links={})
    conn = _db()
    ids = [_insert(conn, n) for n in range(1, 21)]
    builds = _builds(conn)
    validation._json_file_links(conn)
    for finding_id in ids[:15]:
        conn.execute("UPDATE repair_findings SET status='resolved' WHERE id=?", (finding_id,))
        validation._json_file_links(conn)
    assert len(builds) == 1
    # the closed ones are still in the map and are not handed out
    conn.execute("CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, primary_artist_id INTEGER)")
    conn.execute("CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, album_id INTEGER)")
    conn.execute("CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT)")
    conn.execute("INSERT INTO lib2_albums VALUES (1, 1)")
    for n in range(1, 21):
        conn.execute("INSERT INTO lib2_tracks VALUES (?, 1)", (n,))
        conn.execute("INSERT INTO lib2_track_files VALUES (?, ?, ?)", (n, n, f"/music/{n}.flac"))
    conn.row_factory = sqlite3.Row
    rows = validation._pending_findings_for_files(conn, list(range(1, 21)))
    assert sorted(r["id"] for r in rows) == ids[15:]
    assert len(builds) == 1


def test_a_finding_put_back_rebuilds_the_map():
    validation._JSON_LINKS.update(key=None, at=0.0, links={})
    conn = _db()
    conn.execute("INSERT INTO repair_findings (job_id, finding_type, details_json, status) "
                 "VALUES ('library_retag', 'library_retag', ?, 'dismissed')",
                 (json.dumps({"validation": {"file_id": 7}}),))
    _insert(conn, 1)
    assert "7" not in validation._json_file_links(conn)
    conn.execute("UPDATE repair_findings SET status='pending' WHERE id=1")
    assert "7" in validation._json_file_links(conn)


def test_an_own_insert_after_closes_still_keeps_the_map():
    validation._JSON_LINKS.update(key=None, at=0.0, links={})
    conn = _db()
    first, second = _insert(conn, 1), _insert(conn, 2)
    builds = _builds(conn)
    validation._json_file_links(conn)
    conn.execute("UPDATE repair_findings SET status='resolved' WHERE id=?", (first,))
    third = _insert(conn, 3)
    validation._note_own_finding(conn, third, 3)
    links = validation._json_file_links(conn)
    assert len(builds) == 1
    assert links["3"] == {third} and links["2"] == {second}
