"""backup rotation counted in real backups, on real WAL databases.

the bug: every caller globbed ``<db>.backup_*``, which also matches a
backup's -wal / -shm sidecars and its .meta.json. a backup of a WAL database
is a WAL database, so every health check of it made sidecars, the rotation
counted them as backups, and "keep 5" kept one. the old tests used plain
(non-WAL) files, where there are no sidecars to count.
"""

import os
import sqlite3
import time

import pytest

from core import db_integrity as dbi


def _wal_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(200)])
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    return conn      # left open: the live database has a writer and a -wal


def _backup(db, n):
    dest = f"{db}.backup_202611{n:02d}_010203"
    dbi.safe_backup(str(db), dest)
    with open(dest + ".meta.json", "w") as f:
        f.write("{}")
    os.utime(dest, (time.time() + n, time.time() + n))
    return dest


def test_a_finished_backup_is_one_plain_file_however_often_it_is_read(tmp_path):
    db = tmp_path / "music.db"
    live = _wal_db(db)
    dest = _backup(db, 1)
    for _ in range(3):
        assert dbi.is_healthy(dest)
    assert not os.path.exists(dest + "-wal") and not os.path.exists(dest + "-shm")
    with sqlite3.connect(dest) as c:
        assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 200
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    # the live database is untouched: still WAL, still writable
    assert live.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    live.execute("INSERT INTO t VALUES (1)")
    live.commit()
    live.close()


def test_keep_five_keeps_five_real_backups(tmp_path):
    db = tmp_path / "music.db"
    live = _wal_db(db)
    made = []
    for n in range(1, 8):
        made.append(_backup(db, n))
        dbi.rotate_backups(str(db), 5)
    live.close()
    kept = dbi.list_backups(str(db))
    assert kept == sorted(made[-5:]), "the five newest, counted in backups, not in files"
    for gone in made[:2]:
        assert not os.path.exists(gone) and not os.path.exists(gone + ".meta.json")
    for stays in made[-5:]:
        assert os.path.exists(stays + ".meta.json"), "a kept backup keeps its version note"


def test_only_backups_are_listed(tmp_path):
    db = tmp_path / "music.db"
    _wal_db(db).close()
    real = _backup(db, 3)
    for extra in (real + "-wal", real + "-shm", str(db) + ".backup-before-something",
                  str(db) + ".backup_20261101_010203.partial", str(db) + ".backup_2026",
                  str(tmp_path / "other.db.backup_20261101_010203")):
        open(extra, "w").close()
    assert dbi.list_backups(str(db)) == [real]


def test_leftovers_of_deleted_backups_are_swept_and_nothing_else(tmp_path):
    db = tmp_path / "music.db"
    _wal_db(db).close()
    real = _backup(db, 3)
    orphan = str(db) + ".backup_20261001_010203"
    hand_made = str(db) + ".backup-pre-upgrade"
    for f in (orphan + "-wal", orphan + "-shm", orphan + ".meta.json", hand_made, hand_made + "-wal"):
        open(f, "w").close()
    gone = dbi.sweep_backup_leftovers(str(db))
    assert len(gone) == 3
    assert not os.path.exists(orphan + "-wal") and not os.path.exists(orphan + ".meta.json")
    assert os.path.exists(real) and os.path.exists(real + ".meta.json")
    assert os.path.exists(hand_made) and os.path.exists(hand_made + "-wal"), "a file someone named by hand is theirs"
    assert os.path.exists(str(db))


def test_the_newest_healthy_backup_is_never_rotated_out(tmp_path):
    db = tmp_path / "music.db"
    live = _wal_db(db)
    good = _backup(db, 1)
    bad = []
    for n in range(2, 6):
        dest = f"{db}.backup_202611{n:02d}_010203"
        with open(dest, "wb") as f:
            f.write(b"not a database at all" * 50)
        os.utime(dest, (time.time() + n, time.time() + n))
        bad.append(dest)
    dbi.rotate_backups(str(db), 2)
    live.close()
    assert os.path.exists(good), "the last good snapshot outlives newer broken ones"


def test_schema_version_and_room(tmp_path):
    db = tmp_path / "music.db"
    _wal_db(db).close()
    assert dbi.schema_version(str(db)) == 7
    assert dbi.schema_version(str(tmp_path / "missing.db")) is None
    assert dbi.room_for(str(db), 1024) is None
    short = dbi.room_for(str(db), 10 ** 18)
    assert short and "Not enough free space" in short


def test_remove_backup_takes_its_sidecars(tmp_path):
    db = tmp_path / "music.db"
    _wal_db(db).close()
    real = _backup(db, 3)
    open(real + "-wal", "w").close()
    dbi.remove_backup(real)
    assert not any(os.path.exists(real + s) for s in ("", "-wal", "-shm", ".meta.json"))
    dbi.remove_backup(real)      # twice is fine


@pytest.mark.parametrize("keep", [1, 3])
def test_rotation_reports_what_it_removed(tmp_path, keep):
    db = tmp_path / "music.db"
    live = _wal_db(db)
    made = [_backup(db, n) for n in range(1, 5)]
    removed = dbi.rotate_backups(str(db), keep)
    live.close()
    assert sorted(removed) == sorted(made[:4 - keep])
