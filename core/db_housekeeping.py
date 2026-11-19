"""keeping a big library database in shape (#2205).

a 47 GB music_library.db with a 2.3 GB wal: the wal never shrank (nothing
capped it once a checkpoint had reset it), the full-text indexes only ever
merged when sqlite felt like it, and a few journals and caches had no end.

- ``checkpoint``: fold the wal back in, and cut the file down when it is
  bigger than the cap. a thread runs it every few minutes.
- ``prune``: the retention rules below, for tables that are caches or logs
  of their own work. never listening history, never library rows, never
  anything a person made.
- ``optimize``: merge each fts5 index into one segment and let sqlite
  refresh the planner stats it thinks are stale.
- ``table_sizes``: what is taking the room, for the maintenance panel.

the cache maintenance repair job runs prune + optimize; the settings page's
database maintenance section runs them by hand.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("db_housekeeping")


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


# how often the wal is checkpointed; 0 turns the thread off
CHECKPOINT_MINUTES = _env_int("SOULSYNC_DB_CHECKPOINT_MINUTES", 10)
# a TRUNCATE checkpoint waits for readers holding the writer lock; never long
TRUNCATE_WAIT_MS = 2000
# rows deleted per statement, so a prune never holds the write lock for long
PRUNE_BATCH = 5000


# (table, timestamp column, keep days, how the column stores time, extra where,
# the partial index the prune reads). only tables that are a cache or a log of soulsync's own work. each is
# skipped when the table or column isn't there.
#   'iso'   : text timestamps ('2026-11-19 08:00:00' or iso-8601)
#   'epoch' : unix seconds
RETENTION_RULES = (
    # musicbrainz lookups. the service treats a hit older than 90 days as
    # stale and asks again (core/musicbrainz_service._check_cache), so past
    # 180 these rows are never read: dead weight. 1.07 GB of a real 11 GB
    # library was recording/release rows. artist rows stay, the naming facts
    # read them at any age (core/imports/artist_facts)
    ("musicbrainz_cache", "last_updated", 180, "iso",
     "entity_type IN ('recording', 'release')", "idx_mb_cache_stale"),
    # repair findings someone or something resolved. a resolved row only
    # matters for the 7-day recurrence grace; dismissed rows are decisions
    # and stay. 164k resolved retag rows held 336 MB of details on that library
    ("repair_findings", "resolved_at", 90, "iso", "status = 'resolved'", "idx_rf_resolved_age"),
)


def _tables(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _cutoff(kind: str, days: float, now: float):
    at = now - days * 86400
    if kind == "epoch":
        return at
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(at))


def _indexes(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def select_sql(rule, indexes=()) -> str:
    """the rowids a rule deletes, one batch. named index when it exists: the
    planner otherwise picks an entity_type or status index and reads every
    matching row's date off the table (json and all)."""
    table, column, _days, _kind, where = rule[:5]
    index = rule[5] if len(rule) > 5 else None
    hint = f' INDEXED BY "{index}"' if index and index in indexes else ""
    extra = f" AND ({where})" if where else ""
    return f'SELECT rowid FROM "{table}"{hint} WHERE "{column}" < ?{extra} LIMIT {PRUNE_BATCH}'


def prune(conn, *, now: Optional[float] = None, rules=None,
          should_stop=None) -> Dict[str, int]:
    """apply the retention rules. returns {table: rows deleted}."""
    now = time.time() if now is None else now
    tables, indexes = _tables(conn), _indexes(conn)
    removed: Dict[str, int] = {}
    for rule in (RETENTION_RULES if rules is None else rules):
        table, column, days, kind = rule[:4]
        if table not in tables or column not in _columns(conn, table):
            continue
        cutoff = _cutoff(kind, days, now)
        batch = select_sql(rule, indexes)
        total = 0
        while True:
            if should_stop and should_stop():
                break
            cur = conn.execute(f'DELETE FROM "{table}" WHERE rowid IN ({batch})', (cutoff,))
            conn.commit()
            n = max(cur.rowcount or 0, 0)
            total += n
            if n < PRUNE_BATCH:
                break
        if total:
            removed[table] = total
            logger.info("db housekeeping: pruned %d rows from %s older than %s days", total, table, days)
    return removed


def fts_tables(conn) -> List[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND sql LIKE 'CREATE VIRTUAL TABLE%' AND lower(sql) LIKE '%using fts5%'")]


def optimize(conn) -> Dict[str, Any]:
    """merge every fts5 index's segments and refresh stale planner stats."""
    done: List[str] = []
    for table in fts_tables(conn):
        try:
            conn.execute(f'INSERT INTO "{table}"("{table}") VALUES (\'optimize\')')
            conn.commit()
            done.append(table)
        except sqlite3.Error as exc:
            logger.warning("fts optimize of %s failed: %s", table, exc)
    freed = _incremental_vacuum(conn)
    try:
        # analysis_limit keeps it a sample, not a read of every index
        conn.execute("PRAGMA analysis_limit = 1000")
        conn.execute("PRAGMA optimize")
        conn.commit()
    except sqlite3.Error as exc:
        logger.debug("PRAGMA optimize skipped: %s", exc)
    return {"fts_optimized": done, "pages_freed": freed}


# pages handed back per optimize when the database is in incremental
# auto-vacuum mode (~400 MB at 4 KB pages), a slice at a time
INCREMENTAL_VACUUM_PAGES = 100_000
_INCREMENTAL_STEP = 5_000


def _incremental_vacuum(conn) -> int:
    """the settings page's "enable incremental vacuum" switched the mode on,
    and nothing ever ran ``PRAGMA incremental_vacuum``, so no page was ever
    handed back. this does, in slices, so no one write holds the lock long."""
    try:
        if int(conn.execute("PRAGMA auto_vacuum").fetchone()[0]) != 2:
            return 0
        freed = 0
        while freed < INCREMENTAL_VACUUM_PAGES:
            free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            if free <= 0:
                break
            step = min(_INCREMENTAL_STEP, free, INCREMENTAL_VACUUM_PAGES - freed)
            conn.execute(f"PRAGMA incremental_vacuum({step})").fetchall()
            conn.commit()
            freed += step
        return freed
    except sqlite3.Error as exc:
        logger.debug("incremental vacuum skipped: %s", exc)
        return 0


def wal_path(db_path: str) -> str:
    return f"{db_path}-wal"


def wal_size(db_path: str) -> int:
    try:
        return os.path.getsize(wal_path(db_path))
    except OSError:
        return 0


def checkpoint(db_path: str, *, truncate_over: Optional[int] = None) -> Dict[str, Any]:
    """fold the wal into the database on a connection of its own. PASSIVE
    first (never waits on anyone); when the wal file is bigger than
    ``truncate_over`` bytes, a TRUNCATE that waits at most TRUNCATE_WAIT_MS
    for readers cuts the file back to nothing."""
    from database.music_database import DB_JOURNAL_SIZE_LIMIT_MB
    limit = DB_JOURNAL_SIZE_LIMIT_MB * 1024 * 1024 if truncate_over is None else truncate_over
    out: Dict[str, Any] = {"wal_before": wal_size(db_path), "mode": "PASSIVE"}
    conn = sqlite3.connect(db_path, timeout=TRUNCATE_WAIT_MS / 1000)
    try:
        conn.execute(f"PRAGMA busy_timeout = {TRUNCATE_WAIT_MS}")
        busy, log, done = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        if out["wal_before"] > limit:
            out["mode"] = "TRUNCATE"
            try:
                busy, log, done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            except sqlite3.OperationalError as exc:  # busy past the wait: next time
                logger.debug("wal truncate skipped: %s", exc)
                busy = 1
        out.update(busy=int(busy), log_pages=int(log), checkpointed=int(done))
    finally:
        conn.close()
    out["wal_after"] = wal_size(db_path)
    if out["wal_before"] > limit:
        logger.info("wal checkpoint: %.1f MB -> %.1f MB (%s%s)", out["wal_before"] / 1e6,
                    out["wal_after"] / 1e6, out["mode"], ", readers in the way" if out["busy"] else "")
    return out


def table_sizes(conn, *, limit: int = 30, use_dbstat: bool = True) -> Dict[str, Any]:
    """bytes per table (indexes counted with their table) from dbstat when
    sqlite has it, else row estimates from the planner stats. dbstat reads
    every page, so on a big database this takes a while: call it off the
    request path."""
    owners = {r[0]: r[1] for r in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type IN ('table', 'index')")}
    rows = None
    if use_dbstat:
        try:
            rows = conn.execute(
                "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name").fetchall()
        except sqlite3.Error:
            rows = None
    if rows is not None:
        sizes: Dict[str, int] = {}
        fts = fts_tables(conn)
        for name, size in rows:
            owner = owners.get(name, name)
            # fts5 shadow tables (x_data, x_idx, ...) belong to x
            for fts_name in fts:
                if owner.startswith(fts_name + "_"):
                    owner = fts_name
                    break
            sizes[owner] = sizes.get(owner, 0) + int(size or 0)
        top = sorted(sizes.items(), key=lambda kv: -kv[1])[:limit]
        return {"method": "dbstat", "tables": [{"name": n, "bytes": b} for n, b in top]}
    estimates: Dict[str, int] = {}
    try:
        for tbl, stat in conn.execute("SELECT tbl, stat FROM sqlite_stat1"):
            try:
                estimates[tbl] = max(estimates.get(tbl, 0), int(str(stat).split()[0]))
            except (ValueError, IndexError):
                continue
    except sqlite3.Error:
        pass
    top = sorted(estimates.items(), key=lambda kv: -kv[1])[:limit]
    return {"method": "row_estimates", "tables": [{"name": n, "rows": r} for n, r in top]}


# ── the measuring job and the checkpoint thread ──────────────────────────────

_measure_lock = threading.Lock()
_measure: Dict[str, Any] = {"state": "idle", "result": None, "measured_at": None, "error": None}


def measure_state() -> Dict[str, Any]:
    with _measure_lock:
        return dict(_measure)


def start_measure(db_path: str) -> bool:
    """measure table sizes on a thread of its own. False when one is running."""
    with _measure_lock:
        if _measure["state"] == "running":
            return False
        _measure.update(state="running", error=None)

    def run():
        try:
            uri = Path(db_path).resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=30)
            try:
                result = table_sizes(conn)
            finally:
                conn.close()
            with _measure_lock:
                _measure.update(state="done", result=result, measured_at=time.time())
        except Exception as exc:  # noqa: BLE001 - reported to the panel
            with _measure_lock:
                _measure.update(state="error", error=str(exc))

    threading.Thread(target=run, name="db-measure", daemon=True).start()
    return True


_checkpoint_started: set = set()
_checkpoint_lock = threading.Lock()


def start_checkpoint_thread(db_path: str) -> bool:
    """every CHECKPOINT_MINUTES, checkpoint the wal (one thread per file)."""
    if not CHECKPOINT_MINUTES:
        return False
    with _checkpoint_lock:
        if db_path in _checkpoint_started:
            return False
        _checkpoint_started.add(db_path)

    def loop():
        while True:
            time.sleep(CHECKPOINT_MINUTES * 60)
            try:
                if os.path.exists(db_path):
                    checkpoint(db_path)
            except Exception as exc:  # noqa: BLE001 - next round tries again
                logger.debug("wal checkpoint failed: %s", exc)

    threading.Thread(target=loop, name="db-wal-checkpoint", daemon=True).start()
    return True


__all__ = ["CHECKPOINT_MINUTES", "RETENTION_RULES", "checkpoint", "fts_tables", "measure_state",
           "optimize", "prune", "start_checkpoint_thread", "start_measure", "table_sizes",
           "wal_size"]
