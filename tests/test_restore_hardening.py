"""a restore never makes things worse: a bad or newer backup is refused
before anything is touched, a half-staged file is never swapped in, and a
swap that fails puts the database back."""

from __future__ import annotations

import os
import shutil
import sqlite3

import pytest

import core.video.backup_restore as br
from core import db_integrity as dbi
from database.video_database import SCHEMA_VERSION, VideoDatabase


@pytest.fixture()
def env_db(tmp_path, monkeypatch):
    p = tmp_path / "video_library.db"
    monkeypatch.setenv("VIDEO_DATABASE_PATH", str(p))
    db = VideoDatabase(database_path=str(p))
    db.set_setting("marker", "original")
    return db, str(p)


def _marker(path):
    with sqlite3.connect(path) as c:
        return c.execute("SELECT value FROM video_settings WHERE key='marker'").fetchone()[0]


# ── video: staging ────────────────────────────────────────────────────────

def test_a_staged_restore_is_whole_or_absent(env_db, monkeypatch):
    db, path = env_db
    name = br.create_now()["name"]
    # the copy can't be written (a full or failing drive): nothing called .restore-pending is left
    pending = br._pending_path()
    original = sqlite3.connect

    def flaky(p, *a, **k):
        if str(p).endswith(".restore-pending.tmp"):
            raise sqlite3.OperationalError("unable to open database file")
        return original(p, *a, **k)

    monkeypatch.setattr(sqlite3, "connect", flaky)
    out = br.stage_restore(name)
    monkeypatch.setattr(sqlite3, "connect", original)
    assert out["ok"] is False
    assert not os.path.exists(pending) and not os.path.exists(pending + ".tmp")
    assert br.pending_restore() is False
    assert br.apply_pending_restore(path) is False and _marker(path) == "original"


def test_a_backup_from_a_newer_soulsync_is_not_staged(env_db):
    db, path = env_db
    name = br.create_now()["name"]
    backup = os.path.join(os.path.dirname(path), name)
    with sqlite3.connect(backup) as c:
        c.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION) + 5}")
    out = br.stage_restore(name)
    assert out["ok"] is False and "newer SoulSync" in out["error"]
    assert br.pending_restore() is False


def test_a_corrupt_backup_is_not_staged(env_db):
    db, path = env_db
    name = br.create_now()["name"]
    backup = os.path.join(os.path.dirname(path), name)
    size = os.path.getsize(backup)
    with open(backup, "r+b") as f:
        f.seek(size // 2)
        f.write(b"\x00garbage" * 400)
    with open(backup, "r+b") as f:
        f.write(b"this is not sqlite")
    out = br.stage_restore(name)
    assert out["ok"] is False and br.pending_restore() is False


# ── video: applying at startup ────────────────────────────────────────────

def test_a_staged_file_that_is_not_a_database_is_never_swapped_in(env_db):
    db, path = env_db
    with open(br._pending_path(path), "wb") as f:
        f.write(b"half a database" * 100)
    assert br.apply_pending_restore(path) is False
    assert _marker(path) == "original", "the live database is untouched"
    assert not os.path.exists(br._pending_path(path)), "and it won't be tried again at every start"
    assert os.path.exists(br._pending_path(path) + ".bad")


def test_a_swap_that_fails_puts_the_database_back(env_db, monkeypatch):
    db, path = env_db
    name = br.create_now()["name"]
    db.set_setting("marker", "changed since the backup")
    assert br.stage_restore(name)["ok"]
    pending = br._pending_path(path)
    real_move = shutil.move

    def move(src, dst, *a, **k):
        if str(src) == pending:
            raise OSError("permission denied")          # the staged file can't be moved into place
        return real_move(src, dst, *a, **k)

    monkeypatch.setattr(br.shutil, "move", move)
    assert br.apply_pending_restore(path) is False
    assert os.path.exists(path), "startup must not find the database gone and make an empty one"
    assert _marker(path) == "changed since the backup"
    assert os.path.exists(pending), "the staged restore is still there to try again"


def test_a_good_restore_still_goes_through(env_db):
    db, path = env_db
    name = br.create_now()["name"]
    db.set_setting("marker", "changed since the backup")
    assert br.stage_restore(name)["ok"] and br.pending_restore()
    assert not os.path.exists(br._pending_path(path) + ".tmp")
    assert br.apply_pending_restore(path) is True
    assert _marker(path) == "original"
    kept = [n for n in os.listdir(os.path.dirname(path)) if ".pre-restore-" in n and not n.endswith(("-wal", "-shm"))]
    assert len(kept) == 1, "what was there before is kept"


def test_video_keep_n_is_counted_in_backups(env_db, monkeypatch):
    db, path = env_db
    monkeypatch.setattr(br, "MAX_BACKUPS", 3)
    import itertools
    stamps = itertools.count(1)

    import datetime as real

    class Clock(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return real.datetime(2026, 11, 1, 1, 2, next(stamps))

    monkeypatch.setattr(br, "datetime", Clock)
    names = [br.create_now()["name"] for _ in range(5)]
    assert [b["name"] for b in br.list_backups()] == sorted(names[-3:], reverse=True)


# ── music: the pieces of the restore route ────────────────────────────────

def _db(path, version):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE t (x)")
        c.execute(f"PRAGMA user_version = {version}")


def test_music_restore_refuses_a_newer_schema_only(tmp_path):
    from api import database_admin as da
    live, older, newer = (str(tmp_path / n) for n in ("live.db", "older.db", "newer.db"))
    _db(live, 40)
    _db(older, 31)
    _db(newer, 44)
    assert da._restore_schema_problem(live, older, dbi) is None
    assert da._restore_schema_problem(live, live, dbi) is None
    problem = da._restore_schema_problem(live, newer, dbi)
    assert problem and "newer SoulSync" in problem and "44" in problem and "40" in problem
    assert da._restore_schema_problem(live, str(tmp_path / "missing.db"), dbi) is None


def test_music_pre_restore_copies_are_kept_outside_the_rotation_and_capped(tmp_path):
    from api import database_admin as da
    folder = str(tmp_path)
    live = os.path.join(folder, "music_library.db")
    _db(live, 1)
    copies = [os.path.join(folder, f"music_library.db.pre-restore_2026110{n}_010203") for n in range(1, 6)]
    for c in copies:
        open(c, "w").close()
    assert dbi.list_backups(live) == [], "a pre-restore copy is not a rolling backup: the next backup can't rotate it away"
    da._prune_pre_restore_copies(folder)
    left = sorted(n for n in os.listdir(folder) if ".pre-restore_" in n)
    assert left == [os.path.basename(c) for c in copies[-da.PRE_RESTORE_KEEP:]]


def test_after_a_restore_the_database_is_initialised_again(tmp_path):
    from api import database_admin as da
    from database import music_database as mdb
    path = str(tmp_path / "music_library.db")
    other = str(tmp_path / "other.db")
    mdb._database_initialized_paths.update({path, other})
    try:
        da._forget_database_initialized(path)
        assert path not in mdb._database_initialized_paths, "an older backup must get its migrations"
        assert other in mdb._database_initialized_paths
    finally:
        mdb._database_initialized_paths.discard(path)
        mdb._database_initialized_paths.discard(other)
