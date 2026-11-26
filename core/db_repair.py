"""repairing a damaged database ("database disk image is malformed").

a database file can be damaged by a crash or power cut in the middle of a
write. SoulSync notices (every backup checks the database first and refuses
to copy a damaged one), but the only way back used to be a backup, which
loses everything since it was made. this is the repair:

1. the damaged files are kept as they are (database, -wal, -shm), raw, in a
   folder of their own beside the database. nothing is lost whatever happens.
2. a working copy is made (with its -wal, so the newest writes come along)
   and every table's rows are copied into a new database, whose indexes are
   then made again. most damage is in an index, and reading a table never
   touches its indexes, so this fixes it without losing a row.
3. when that isn't enough, the sqlite3 tool's ``.recover`` reads every row
   it can still reach out of the damaged file into a new database.
4. the result must pass a full integrity check, and must still hold the
   tables the damaged file had, or it isn't used.

the caller swaps the repaired file in (api/database_admin.py, the same way a
restore does). when nothing works, it says so and points at the newest backup
that is healthy.

the health of the database is also remembered here (``last_health``), so the
dashboard and the Backups page can say "your database is damaged" without
each running a check of a large file.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("db_repair")

SIDES = ("", "-wal", "-shm")
RECOVER_TIMEOUT_S = 60 * 60          # a large damaged database takes a while
_health_lock = threading.Lock()
_health: Dict[str, Any] = {"healthy": None, "detail": "", "checked_at": None}


# ── health ────────────────────────────────────────────────

def check(db_path: str) -> Dict[str, Any]:
    """run the quick check now and remember the answer"""
    from core import db_integrity as dbi
    try:
        result = dbi.quick_check(db_path)
        healthy, detail = result == "ok", ("" if result == "ok" else str(result))
    except dbi.DBIntegrityError as e:
        healthy, detail = False, str(e)
    return remember(healthy, detail)


def remember(healthy: bool, detail: str = "") -> Dict[str, Any]:
    with _health_lock:
        _health.update(healthy=bool(healthy), detail=str(detail or "")[:500], checked_at=time.time())
        return dict(_health)


def last_health() -> Dict[str, Any]:
    with _health_lock:
        return dict(_health)


# ── keeping the damaged files ─────────────────────────────

def _size(path: str) -> int:
    return sum(os.path.getsize(path + s) for s in SIDES if os.path.exists(path + s))


def keep_damaged(db_path: str, *, stamp: Optional[str] = None) -> str:
    """copy the database and its -wal/-shm, byte for byte, into a folder of
    their own (``<dir>/damaged_<stamp>/``). raises when there's no room or a
    copy fails: no repair runs without this copy."""
    from core import db_integrity as dbi
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = os.path.join(os.path.dirname(os.path.abspath(db_path)), f"damaged_{stamp}")
    short = dbi.room_for(db_path, _size(db_path) * 3)       # this copy, the working copy, the result
    if short:
        raise OSError(short)
    os.makedirs(folder, exist_ok=False)
    for side in SIDES:
        if os.path.exists(db_path + side):
            shutil.copy2(db_path + side, os.path.join(folder, os.path.basename(db_path) + side))
    return folder


# ── rebuilding ────────────────────────────────────────────

def sqlite_cli() -> Optional[str]:
    return shutil.which("sqlite3")


def tables(path: str) -> List[str]:
    """the table names a file still lists (empty when it can't be read)"""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return sorted(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return []


def _integrity_ok(path: str) -> bool:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return conn.execute("PRAGMA integrity_check(1)").fetchone()[0] == "ok"
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def _working_copy(db_path: str, work: str) -> None:
    """the database and its -wal under one new name, so opening the copy
    brings in the newest writes; the -shm is left behind on purpose"""
    for side in ("", "-wal"):
        if os.path.exists(db_path + side):
            shutil.copy2(db_path + side, work + side)


def _settle(path: str) -> None:
    """fold the -wal into the file, so the result is one file"""
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    for side in ("-wal", "-shm"):
        with contextlib.suppress(OSError):
            os.remove(path + side)


# the tables an fts3/4/5 virtual table keeps its own storage in
_SHADOW_PARTS = ("data", "idx", "content", "docsize", "config", "segments", "segdir", "stat")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _copy_out(work: str, out: str) -> Dict[str, Any]:
    """every table's rows into a new database, then its indexes, views and
    triggers made again from their own CREATE statements. reading a table
    doesn't touch its indexes, so damage in an index (the usual kind) costs
    nothing. ``{ok, lost: [tables that couldn't be read]}``"""
    with contextlib.suppress(OSError):
        os.remove(out)
    src = sqlite3.connect(f"file:{work}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    lost: List[str] = []
    try:
        dst.execute("PRAGMA foreign_keys=OFF")
        dst.execute("PRAGMA journal_mode=OFF")
        dst.execute("PRAGMA synchronous=OFF")
        schema = src.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
        virtual = {n for t, n, _tbl, sql in schema if t == "table" and sql.upper().startswith("CREATE VIRTUAL")}
        # a virtual table's own storage tables are made by the virtual table
        shadow = {n for t, n, _tbl, _sql in schema
                  if t == "table" and any(n == f"{v}_{part}" for v in virtual for part in _SHADOW_PARTS)}
        plain = [(n, sql) for t, n, _tbl, sql in schema
                 if t == "table" and n not in virtual and n not in shadow and not n.startswith("sqlite_")]
        for _name, sql in plain:
            dst.execute(sql)
        for name, _sql in plain:
            try:
                cur = src.execute(f"SELECT * FROM {_q(name)}")
                width = len(cur.description or ())
                insert = f"INSERT INTO {_q(name)} VALUES ({','.join('?' * width)})"
                while True:
                    rows = cur.fetchmany(5000)
                    if not rows:
                        break
                    dst.executemany(insert, rows)
            except sqlite3.DatabaseError as e:
                logger.warning("repair: table %s couldn't be read: %s", name, e)
                lost.append(name)
        with contextlib.suppress(sqlite3.DatabaseError):
            seq = src.execute("SELECT name, seq FROM sqlite_sequence").fetchall()
            if seq:
                dst.executemany("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", seq)
        # derived search indexes (fts) come back empty and are rebuilt by the
        # app (core/library2/song_search.py marks itself unbuilt below)
        for t, name, _tbl, sql in schema:
            if t == "table" and name in virtual:
                dst.execute(sql)
        for kind in ("index", "view", "trigger"):
            for t, name, _tbl, sql in schema:
                if t == kind:
                    try:
                        dst.execute(sql)
                    except sqlite3.DatabaseError as e:
                        logger.warning("repair: %s %s couldn't be made again: %s", kind, name, e)
        if virtual:
            with contextlib.suppress(sqlite3.DatabaseError):
                dst.execute("UPDATE lib2_song_search_state SET built = 0")
        dst.commit()
    except sqlite3.DatabaseError as e:
        logger.warning("repair: copying out stopped: %s", e)
        return {"ok": False, "lost": lost or ["(schema)"]}
    finally:
        src.close()
        dst.close()
    if lost:
        return {"ok": False, "lost": lost}
    return {"ok": _integrity_ok(out), "lost": []}


def _recover(work: str, out: str, cli: str) -> bool:
    """sqlite3's .recover: every row it can still read, into a new file. two
    processes joined by a pipe, no shell"""
    with contextlib.suppress(OSError):
        os.remove(out)
    try:
        reader = subprocess.Popen([cli, work, ".recover"], stdout=subprocess.PIPE,  # noqa: S603 - our argv
                                  stderr=subprocess.DEVNULL)
        writer = subprocess.Popen([cli, out], stdin=reader.stdout,  # noqa: S603 - our argv
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if reader.stdout is not None:
            reader.stdout.close()              # the writer owns the pipe now
        _, err = writer.communicate(timeout=RECOVER_TIMEOUT_S)
        reader.wait(timeout=60)
        if writer.returncode != 0:
            logger.info("repair: recover's writer said %s", (err or b"")[-300:])
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("repair: recover didn't finish: %s", e)
        for p in ("reader", "writer"):
            proc = locals().get(p)
            if proc is not None:
                with contextlib.suppress(Exception):
                    proc.kill()
        return False
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        return False
    with contextlib.suppress(sqlite3.DatabaseError):
        _settle(out)
    return _integrity_ok(out)


def rebuild(db_path: str, out_path: str, *, cli: Optional[str] = "default") -> Dict[str, Any]:
    """a repaired copy of ``db_path`` at ``out_path``:
    ``{ok, method: 'copy'|'recover'|None, detail, missing_tables}``. the
    damaged file itself is never written to."""
    cli = sqlite_cli() if cli == "default" else cli
    before = set(tables(db_path))
    work = out_path + ".work"
    for side in SIDES:
        with contextlib.suppress(OSError):
            os.remove(work + side)
    try:
        _working_copy(db_path, work)
        method = None
        copied = _copy_out(work, out_path)
        if copied["ok"]:
            method = "copy"
        elif cli and _recover(work, out_path, cli):
            method = "recover"
        if method is None:
            with contextlib.suppress(OSError):
                os.remove(out_path)
            lost = copied.get("lost") or []
            reason = (f"{len(lost)} {'table' if len(lost) == 1 else 'tables'} couldn't be read"
                      + (", and the sqlite3 tool that can read around the damage isn't installed here."
                         if not cli else ", and reading around the damage didn't give a healthy file either."))
            return {"ok": False, "method": None, "detail": reason, "missing_tables": lost}
        # a recovered file that lost whole tables is not a repair
        missing = sorted(before - set(tables(out_path))) if before else []
        if missing:
            with contextlib.suppress(OSError):
                os.remove(out_path)
            return {"ok": False, "method": method, "missing_tables": missing,
                    "detail": f"The rebuilt file was missing {len(missing)} tables, so it wasn't used."}
        return {"ok": True, "method": method, "missing_tables": [],
                "detail": ("Copied every row into a new database and rebuilt its indexes." if method == "copy" else
                           "Read every row that could still be read into a new database.")}
    finally:
        for side in SIDES:
            with contextlib.suppress(OSError):
                os.remove(work + side)


def newest_healthy_backup(db_path: str) -> Optional[str]:
    """the newest rolling backup that passes its check, by file name"""
    from core import db_integrity as dbi
    for path in sorted(dbi.list_backups(db_path), key=lambda p: os.path.getmtime(p), reverse=True):
        if dbi.is_healthy(path):
            return os.path.basename(path)
    return None


__all__ = ["check", "remember", "last_health", "keep_damaged", "sqlite_cli", "tables", "rebuild",
           "newest_healthy_backup"]
