"""The mirror outbox under a backlog (Discord, Nov 3 2026: 98,260 pending,
pages stopped loading).

- every drain json-parsed every row newer than the oldest pending one to find
  superseded rows: with a backlog, the whole backlog, ~3 KB each, per call
- requests drained inline and always took the OLDEST 500 pending rows, so a
  click waited on hundreds of other wishlist writes, and the next request
  waited behind it on the drain lock
- the same intent was queued again on every reconcile, so the backlog grew
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2 import mirror_outbox as MO
from core.library2.schema import LIB2_MIRROR_OUTBOX_DDL, ensure_library_v2_schema
from tests.lib2_seed import row_conn


class _Wishlist:
    def __init__(self, path):
        self.path = path
        self.adds = []
        self.removes = []

    def _get_connection(self):
        return row_conn(self.path)

    def add_to_wishlist(self, payload, **kw):
        self.adds.append((payload.get("id"), kw.get("profile_id")))
        return True

    def remove_from_wishlist(self, track_id, profile_id=1, raise_on_error=False):
        self.removes.append((track_id, profile_id))
        return True

    def remove_release_from_wishlist(self, track_id, album_id=None, profile_id=1,
                                     raise_on_error=False):
        self.removes.append((track_id, profile_id))
        return True


@pytest.fixture
def outbox(tmp_path):
    path = str(tmp_path / "lib2.db")
    conn = row_conn(path)
    ensure_library_v2_schema(conn)
    conn.commit()
    yield _Wishlist(path), conn
    conn.close()


def _queue(conn, op, key, *, profile=1, track_id=None):
    track_id = track_id or key
    data = ({"payload": {"id": track_id}, "source_type": "album", "key": key}
            if op == "wishlist_add" else {"id": track_id, "key": key})
    cur = conn.execute(
        "INSERT INTO lib2_mirror_outbox(op, payload, profile_id) VALUES(?,?,?)",
        (op, json.dumps(data), profile))
    return cur.lastrowid


def _status(conn, row_id):
    return conn.execute("SELECT status FROM lib2_mirror_outbox WHERE id=?", (row_id,)).fetchone()[0]


def test_queuing_the_same_intent_again_keeps_one_pending_row(outbox):
    """every reconcile queued the same adds again; only the newest may stand"""
    _wl, conn = outbox
    first = _queue(conn, "wishlist_add", "rel-1")
    second = _queue(conn, "wishlist_add", "rel-1")
    third = _queue(conn, "wishlist_remove", "rel-1")
    other = _queue(conn, "wishlist_add", "rel-2")
    conn.commit()

    assert [_status(conn, i) for i in (first, second, third, other)] == [
        "superseded", "superseded", "pending", "pending"]
    assert conn.execute(
        "SELECT entity_key FROM lib2_mirror_outbox WHERE id=?", (third,)).fetchone()[0] == "wishlist|rel-1|1"


def test_another_profiles_row_for_the_same_entry_stands(outbox):
    _wl, conn = outbox
    admin = _queue(conn, "wishlist_add", "rel-1", profile=1)
    member = _queue(conn, "wishlist_remove", "rel-1", profile=3)
    conn.commit()
    assert (_status(conn, admin), _status(conn, member)) == ("pending", "pending")


def test_a_row_naming_no_entity_still_queues(outbox):
    _wl, conn = outbox
    cur = conn.execute(
        "INSERT INTO lib2_mirror_outbox(op, payload) VALUES('wishlist_add', 'not json')")
    unknown = conn.execute(
        "INSERT INTO lib2_mirror_outbox(op, payload) VALUES('mystery', '{}')").lastrowid
    conn.commit()
    for row_id in (cur.lastrowid, unknown):
        assert tuple(conn.execute("SELECT status, entity_key FROM lib2_mirror_outbox WHERE id=?",
                                  (row_id,)).fetchone()) == ("pending", None)


def test_a_backlog_from_an_older_build_is_keyed_and_collapsed_on_start(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(LIB2_MIRROR_OUTBOX_DDL)  # the table as it was: no entity_key
    for key, op in (("rel-1", "wishlist_add"), ("rel-1", "wishlist_add"),
                    ("rel-2", "wishlist_add"), ("rel-1", "wishlist_add")):
        data = {"payload": {"id": key}, "key": key} if op == "wishlist_add" else {"id": key}
        conn.execute("INSERT INTO lib2_mirror_outbox(op, payload) VALUES(?,?)", (op, json.dumps(data)))
    conn.execute("INSERT INTO lib2_mirror_outbox(op, payload, status) VALUES("
                 "'wishlist_add', ?, 'done')", (json.dumps({"payload": {"id": "rel-2"}, "key": "rel-2"}),))
    # a row an older build queued before the release key existed
    conn.execute("INSERT INTO lib2_mirror_outbox(op, payload) VALUES('wishlist_remove', ?)",
                 (json.dumps({"id": 77}),))
    conn.commit()

    ensure_library_v2_schema(conn)
    conn.commit()
    rows = conn.execute("SELECT id, status, entity_key FROM lib2_mirror_outbox ORDER BY id").fetchall()

    assert [r[1] for r in rows] == ["superseded", "superseded", "superseded", "pending", "done", "pending"]
    assert [r[2] for r in rows] == ["wishlist|rel-1|1", "wishlist|rel-1|1", "wishlist|rel-2|1",
                                    "wishlist|rel-1|1", "wishlist|rel-2|1", "wishlist|77|1"]
    conn.close()


def test_a_request_drains_only_the_rows_it_queued(outbox):
    """behind a backlog a click waited on the oldest 500 rows of other work"""
    wl, conn = outbox
    backlog = [_queue(conn, "wishlist_add", f"old-{n}") for n in range(5)]
    mine = _queue(conn, "wishlist_add", "mine")
    conn.commit()

    result = MO.drain(wl, ids=[mine])

    assert result["done"] == 1 and wl.adds == [("mine", 1)]
    assert {_status(conn, i) for i in backlog} == {"pending"}
    assert _status(conn, mine) == "done"
    assert MO.drain(wl, ids=[]) == {"done": 0, "failed": 0, "superseded": 0}


def test_supersession_is_an_index_lookup_not_a_parse_of_the_backlog(outbox, monkeypatch):
    """a keyed batch never json-parses another row to find what overrides it"""
    wl, conn = outbox
    for n in range(300):
        _queue(conn, "wishlist_add", f"old-{n}")
    conn.commit()
    parsed = []
    real_row_key = MO._row_key
    monkeypatch.setattr(MO, "_row_key", lambda row: parsed.append(row["id"]) or real_row_key(row))

    result = MO.drain(wl, limit=10)

    assert result["done"] == 10 and parsed == []


def test_a_stale_row_from_an_older_build_is_still_superseded_at_the_drain(outbox):
    """rows queued without a key (before the trigger) fall back to their payload"""
    wl, conn = outbox
    conn.execute("DROP TRIGGER trg_lib2_mirror_outbox_key")
    stale = conn.execute(
        "INSERT INTO lib2_mirror_outbox(op, payload) VALUES('wishlist_add', ?)",
        (json.dumps({"payload": {"id": "t"}, "key": "rel-1"}),)).lastrowid
    newer = conn.execute(
        "INSERT INTO lib2_mirror_outbox(op, payload, entity_key) VALUES('wishlist_remove', ?, ?)",
        (json.dumps({"id": "t", "key": "rel-1"}), "wishlist|rel-1|1")).lastrowid
    conn.commit()

    result = MO.drain(wl)

    assert result["superseded"] == 1 and wl.adds == []
    assert (_status(conn, stale), _status(conn, newer)) == ("superseded", "done")


def test_the_background_drainer_works_through_the_queue(outbox, monkeypatch):
    wl, conn = outbox
    rows = [_queue(conn, "wishlist_add", f"rel-{n}") for n in range(7)]
    conn.commit()
    monkeypatch.setattr(MO, "BACKGROUND_DRAIN", True)
    monkeypatch.setattr(MO, "BACKGROUND_BATCH", 3)
    monkeypatch.setattr(MO, "BACKGROUND_PAUSE", 0.0)

    assert MO.drain_soon(wl) is True
    MO._background["thread"].join(10)

    assert {_status(conn, i) for i in rows} == {"done"}
    assert len(wl.adds) == 7


def test_the_background_drainer_is_off_in_tests(outbox):
    wl, conn = outbox
    row = _queue(conn, "wishlist_add", "rel-1")
    conn.commit()
    assert MO.drain_soon(wl) is False
    assert _status(conn, row) == "pending"


def test_a_delete_drains_only_what_it_queued_after_its_mark(outbox):
    """the artist/album delete queues through code that doesn't hand ids back"""
    wl, conn = outbox
    backlog = [_queue(conn, "wishlist_add", f"old-{n}") for n in range(4)]
    conn.commit()
    mark = MO.queue_mark(conn)
    mine = [_queue(conn, "wishlist_remove", f"gone-{n}") for n in range(2)]
    conn.commit()

    result = MO.drain_since(wl, mark)

    assert result["done"] == 2
    assert {_status(conn, i) for i in backlog} == {"pending"}
    assert {_status(conn, i) for i in mine} == {"done"}
