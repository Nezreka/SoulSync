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


# ── the key file, and restoring a companion ───────────────────────────────

def _set(path, value):
    with sqlite3.connect(str(path)) as c:
        c.execute("DELETE FROM t")
        c.execute("INSERT INTO t VALUES (?)", (value,))


def _get(path):
    with sqlite3.connect(str(path)) as c:
        return c.execute("SELECT x FROM t").fetchone()[0]


def test_the_key_file_is_copied_with_a_backup_and_only_the_newest_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music_library.db"))
    monkeypatch.setenv("PLAYER_DATABASE_PATH", str(tmp_path / "none" / "player.db"))
    monkeypatch.delenv("AUDIOBOOK_DATABASE_PATH", raising=False)
    assert bc.backup_key_file("20261101_010203")["ok"] is False        # no key yet: said, not raised
    key = tmp_path / ".encryption_key"
    key.write_bytes(b"secret-key")
    for n in range(1, 9):
        out = bc.backup_companions(f"202611{n:02d}_010203", 5)
    assert out == [{"name": "key", "ok": True, "file": ".encryption_key.backup_20261108_010203", "size_bytes": 10}]
    copies = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".encryption_key.backup_"))
    assert len(copies) == bc.KEY_KEEP and copies[-1].endswith("20261108_010203")
    assert (tmp_path / copies[0]).read_bytes() == b"secret-key" and key.read_bytes() == b"secret-key"


def test_a_companion_is_restored_and_what_was_there_is_kept(tmp_path, monkeypatch):
    player = tmp_path / "player.db"
    _db(player)
    monkeypatch.setenv("PLAYER_DATABASE_PATH", str(player))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music_library.db"))
    monkeypatch.delenv("AUDIOBOOK_DATABASE_PATH", raising=False)
    _set(player, "at backup time")
    backup = bc.backup_companions("20261101_010203", 5)[0]["file"]
    _set(player, "changed since")
    seen = bc.companions_overview()
    assert seen[0]["name"] == "player" and seen[0]["backups"][0]["filename"] == backup
    out = bc.restore_companion("player", backup)
    assert _get(player) == "at backup time"
    assert _get(tmp_path / out["safety_copy"]) == "changed since", "the way back is kept"
    assert dbi.list_backups(str(player)) == [str(tmp_path / backup)], "and it is not a rolling backup"


def test_a_companion_restore_that_should_not_happen_changes_nothing(tmp_path, monkeypatch):
    import pytest
    player = tmp_path / "player.db"
    _db(player)
    monkeypatch.setenv("PLAYER_DATABASE_PATH", str(player))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music_library.db"))
    monkeypatch.delenv("AUDIOBOOK_DATABASE_PATH", raising=False)
    _set(player, "live")
    good = bc.backup_companions("20261101_010203", 5)[0]["file"]
    newer = bc.backup_companions("20261102_010203", 5)[0]["file"]
    with sqlite3.connect(str(tmp_path / newer)) as c:
        c.execute("PRAGMA user_version = 99")
    broken = tmp_path / "player.db.backup_20261103_010203"
    broken.write_bytes(b"not a database at all" * 30)
    for name, filename, status in (("nope", good, 404), ("player", "player.db.backup_20200101_000000", 404),
                                   ("player", "../" + good + "x", 404), ("player", broken.name, 409),
                                   ("player", newer, 409)):
        with pytest.raises(bc.CompanionRestoreError) as err:
            bc.restore_companion(name, filename)
        assert err.value.status == status, (name, filename)
    assert _get(player) == "live"
    assert not [p for p in tmp_path.iterdir() if ".pre-restore_" in p.name], "nothing was set aside for a restore that never ran"
