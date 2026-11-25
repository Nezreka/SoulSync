"""repairing a damaged database: the damaged files are kept, the repair is
checked, and a result that lost tables is never used"""

import os
import sqlite3

import pytest

from core import db_integrity as dbi
from core import db_repair as rep


def _make(path, rows=500):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE plays (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("CREATE INDEX plays_title ON plays(title)")
    conn.execute("CREATE TABLE settings (k TEXT PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO plays (title) VALUES (?)", [(f"song {i:04d}",) for i in range(rows)])
    conn.execute("INSERT INTO settings VALUES ('theme', 'dark')")
    conn.commit()
    root = conn.execute("SELECT rootpage FROM sqlite_master WHERE name='plays_title'").fetchone()[0]
    page = conn.execute("PRAGMA page_size").fetchone()[0]
    conn.close()
    return root, page


def _break_index(path):
    """scribble over the index's root page: the table's rows are untouched"""
    root, page = _make(path)
    with open(path, "r+b") as f:
        f.seek((root - 1) * page + 8)
        f.write(b"\xff" * 64)


def test_a_broken_index_is_rebuilt_and_every_row_kept(tmp_path):
    db = str(tmp_path / "music_library.db")
    _break_index(db)
    assert not dbi.is_healthy(db)
    out = str(tmp_path / "repaired.db")
    result = rep.rebuild(db, out, cli=None)
    assert result["ok"] and result["method"] == "copy"
    assert dbi.is_healthy(out)
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0] == 500
    assert conn.execute("SELECT v FROM settings WHERE k='theme'").fetchone()[0] == "dark"
    conn.close()
    assert not os.path.exists(out + ".work"), "the working copy is cleaned up"


def test_the_damaged_files_are_kept_byte_for_byte(tmp_path):
    db = str(tmp_path / "music_library.db")
    _break_index(db)
    (tmp_path / "music_library.db-wal").write_bytes(b"wal bytes")
    folder = rep.keep_damaged(db, stamp="20261125_120000")
    assert os.path.basename(folder) == "damaged_20261125_120000"
    assert open(os.path.join(folder, "music_library.db"), "rb").read() == open(db, "rb").read()
    assert open(os.path.join(folder, "music_library.db-wal"), "rb").read() == b"wal bytes"


def test_without_the_sqlite_tool_a_deeper_break_is_reported_not_guessed(tmp_path):
    db = str(tmp_path / "music_library.db")
    _make(db)
    # break the first page's b-tree header: reindex can't get past that
    with open(db, "r+b") as f:
        f.seek(100)
        f.write(b"\x00" * 12)
    out = str(tmp_path / "repaired.db")
    result = rep.rebuild(db, out, cli=None)
    assert not result["ok"]
    assert "sqlite3 tool" in result["detail"] or "couldn't be read" in result["detail"]
    assert not os.path.exists(out)


def test_a_rebuild_that_lost_tables_is_not_used(tmp_path, monkeypatch):
    db = str(tmp_path / "music_library.db")
    _break_index(db)
    real = rep.tables
    calls = {"n": 0}

    def fewer(path):
        calls["n"] += 1
        names = real(path)
        return names if calls["n"] == 1 else [n for n in names if n != "settings"]

    monkeypatch.setattr(rep, "tables", fewer)
    out = str(tmp_path / "repaired.db")
    result = rep.rebuild(db, out, cli=None)
    assert not result["ok"] and result["missing_tables"] == ["settings"]
    assert not os.path.exists(out)


@pytest.mark.skipif(rep.sqlite_cli() is None, reason="no sqlite3 tool here")
def test_recover_reads_out_what_reindex_cannot(tmp_path):
    db = str(tmp_path / "music_library.db")
    _make(db)
    with open(db, "r+b") as f:
        f.seek(100)
        f.write(b"\x00" * 12)
    out = str(tmp_path / "repaired.db")
    result = rep.rebuild(db, out)
    # the tool may or may not get a healthy file out of this; either way the
    # answer is honest and a bad result is never left behind
    assert result["ok"] == os.path.exists(out)


def test_health_is_remembered(tmp_path):
    db = str(tmp_path / "music_library.db")
    _make(db)
    assert rep.check(db)["healthy"] is True
    _break_index(str(tmp_path / "other.db"))
    got = rep.check(str(tmp_path / "other.db"))
    assert got["healthy"] is False and got["detail"]
    assert rep.last_health()["healthy"] is False


def test_a_copy_keeps_autoincrement_triggers_views_and_rebuilds_fts(tmp_path):
    db = str(tmp_path / "music_library.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT);
        CREATE TABLE log (n INTEGER);
        CREATE TRIGGER notes_log AFTER INSERT ON notes BEGIN INSERT INTO log VALUES (NEW.id); END;
        CREATE VIEW note_bodies AS SELECT body FROM notes;
        CREATE TABLE lib2_song_search_state (id INTEGER PRIMARY KEY, built INTEGER);
        INSERT INTO lib2_song_search_state VALUES (1, 1);
        CREATE VIRTUAL TABLE lib2_song_search USING fts5(title);
        INSERT INTO lib2_song_search(title) VALUES ('kept out on purpose');
        INSERT INTO notes (body) VALUES ('a'), ('b');
    """)
    conn.close()
    out = str(tmp_path / "repaired.db")
    result = rep.rebuild(db, out, cli=None)
    assert result["ok"], result
    c = sqlite3.connect(out)
    assert c.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 2, "trigger didn't fire twice during the copy"
    c.execute("INSERT INTO notes (body) VALUES ('c')")
    assert c.execute("SELECT max(id) FROM notes").fetchone()[0] == 3
    assert c.execute("SELECT COUNT(*) FROM note_bodies").fetchone()[0] == 3
    assert c.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 3, "the trigger came back"
    assert c.execute("SELECT built FROM lib2_song_search_state").fetchone()[0] == 0
    c.close()
