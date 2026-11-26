"""#2253: what a new sqlite connection costs, and how many are opened at once."""

from __future__ import annotations

import sqlite3
import threading
import time

import database.music_database as mdb
from database import connection_pool as cp
from database.music_database import MusicDatabase


def test_sql_functions_are_resolved_once_and_registered(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    first = mdb._sql_functions()
    assert mdb._sql_functions() is first
    conn = db._open_connection()
    try:
        row = conn.execute(
            "SELECT unidecode_lower('Ãbc'), name_key('Beyoncé'), norm_text('Hello!'), norm_key('AC/DC')"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "abc"
    assert row[1] and row[2] and row[3]
    assert mdb._sql_functions() is first


def test_reused_connection_is_not_set_up_again(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    cp.discard(db.database_path)
    opened = []
    real = db._open_connection
    monkeypatch.setattr(db, "_open_connection", lambda same=True: opened.append(1) or real(same))
    for _ in range(20):
        db._get_connection().close()
    assert len(opened) == 1


def _opener(path, calls, delay=0.0):
    def open_(same):
        calls.append(threading.current_thread().name)
        time.sleep(delay)
        return sqlite3.connect(path, check_same_thread=same)
    return open_


def test_only_a_few_connections_are_opened_at_once(tmp_path, monkeypatch):
    path = str(tmp_path / "g.db")
    sqlite3.connect(path).close()
    gate = threading.BoundedSemaphore(2)
    monkeypatch.setattr(cp, "_open_gate", gate)
    monkeypatch.setattr(cp, "OPEN_GATE_WAIT", 5.0)
    active = [0]
    peak = [0]
    lock = threading.Lock()

    def open_(same):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return sqlite3.connect(path, check_same_thread=same)

    held = []
    hold_lock = threading.Lock()

    def worker():
        c = cp.acquire(path, open_)
        with hold_lock:
            held.append(c)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert len(held) == 8
        assert peak[0] <= 2
    finally:
        for c in held:
            c.close()
        cp.discard(path)


def test_a_thread_that_waited_takes_a_returned_connection(tmp_path, monkeypatch):
    path = str(tmp_path / "r.db")
    sqlite3.connect(path).close()
    cp.discard(path)
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(cp, "_open_gate", gate)
    monkeypatch.setattr(cp, "OPEN_GATE_WAIT", 5.0)
    first = cp.acquire(path, _opener(path, []))
    gate.acquire()  # someone else is opening
    calls = []
    got = []
    t = threading.Thread(target=lambda: got.append(cp.acquire(path, _opener(path, calls))))
    t.start()
    time.sleep(0.05)
    raw = first._slot.raw
    first.close()  # given back while the other thread waits
    gate.release()
    t.join()
    try:
        assert calls == []
        assert got[0]._slot.raw is raw
    finally:
        got[0].close()
        cp.discard(path)


def test_a_stuck_opener_does_not_stall_everyone(tmp_path, monkeypatch):
    path = str(tmp_path / "s.db")
    sqlite3.connect(path).close()
    cp.discard(path)
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(cp, "_open_gate", gate)
    monkeypatch.setattr(cp, "OPEN_GATE_WAIT", 0.05)
    gate.acquire()  # held by an opener that never finishes
    try:
        started = time.monotonic()
        c = cp.acquire(path, _opener(path, []))
        assert time.monotonic() - started < 2
        c.close()
    finally:
        gate.release()
        cp.discard(path)
