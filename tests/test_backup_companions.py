"""the player and audiobook databases are backed up with the music one."""

import os
import sqlite3

from core import backup_companions as bc
from core import db_integrity as dbi


def _db(path):
    with sqlite3.connect(str(path)) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE t (x)")
        c.execute("INSERT INTO t VALUES (1)")


def test_each_companion_gets_a_verified_backup_and_its_own_rotation(tmp_path, monkeypatch):
    player, books = tmp_path / "player.db", tmp_path / "books" / "audiobooks.db"
    books.parent.mkdir()
    _db(player)
    _db(books)
    monkeypatch.setenv("PLAYER_DATABASE_PATH", str(player))
    monkeypatch.setenv("AUDIOBOOK_DATABASE_PATH", str(books))
    assert sorted(n for n, _ in bc.companion_databases()) == ["audiobooks", "player"]
    out = []
    for n in range(1, 5):
        out = bc.backup_companions(f"202611{n:02d}_010203", 2)
    assert [r["ok"] for r in out] == [True, True]
    assert {r["name"]: r["file"] for r in out} == {
        "player": "player.db.backup_20261104_010203", "audiobooks": "audiobooks.db.backup_20261104_010203"}
    for db in (player, books):
        kept = dbi.list_backups(str(db))
        assert len(kept) == 2 and all(dbi.is_healthy(k) for k in kept)
        assert not any(os.path.exists(k + "-wal") for k in kept)


def test_a_missing_companion_is_skipped_and_a_broken_one_is_said_not_raised(tmp_path, monkeypatch):
    player = tmp_path / "player.db"
    player.write_bytes(b"this is not a database" * 40)
    monkeypatch.setenv("PLAYER_DATABASE_PATH", str(player))
    monkeypatch.setenv("AUDIOBOOK_DATABASE_PATH", str(tmp_path / "nope" / "audiobooks.db"))
    assert [n for n, _ in bc.companion_databases()] == ["player"]
    out = bc.backup_companions("20261101_010203", 5)
    assert len(out) == 1 and out[0]["ok"] is False and out[0]["error"]
    assert dbi.list_backups(str(player)) == [], "a corrupt database never becomes a backup"


def test_the_default_audiobook_database_only_counts_beside_the_music_one(tmp_path, monkeypatch):
    """its default path is under the working folder: a music database that is
    somewhere else (a test's) must not back up whatever sits there"""
    monkeypatch.delenv("AUDIOBOOK_DATABASE_PATH", raising=False)
    monkeypatch.delenv("PLAYER_DATABASE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "database").mkdir()
    _db(tmp_path / "database" / "audiobooks.db")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "elsewhere" / "music_library.db"))
    assert bc.companion_databases() == []
    monkeypatch.setenv("DATABASE_PATH", os.path.join("database", "music_library.db"))
    assert [n for n, _ in bc.companion_databases()] == ["audiobooks"]
