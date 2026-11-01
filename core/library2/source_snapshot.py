"""Cheap persisted invalidation of legacy upgrade checkpoints.

Counts/max IDs alone cannot detect edits to a file's path or owner. Watching
the frozen compatibility tables avoids hashing a 300k-track library on every
upgrade-state poll; native writes do not change this revision.
"""

from __future__ import annotations

import json

from utils.logging_config import get_logger

logger = get_logger("library2.source_snapshot")

SOURCE_TABLES = ("artists", "albums", "tracks")
#: Where a finished upgrade moves the legacy catalogue (#1062 review, point 3):
#: a reader still asking for ``tracks`` fails loudly instead of quietly
#: reading a frozen copy. Only the importer reads them, as ``tracks`` again.
RETIRED_TABLES = {table: f"legacy_{table}" for table in SOURCE_TABLES}


def source_table(tables, table: str):
    """The name the legacy ``table`` lives under in ``tables``, or None."""
    if table in tables:
        return table
    return RETIRED_TABLES[table] if RETIRED_TABLES[table] in tables else None


def retire_legacy_tables(conn) -> list:
    """Rename the imported legacy catalogue, once the upgrade is done.

    Triggers and foreign keys follow a rename, so the source revision keeps
    its observers. One transaction: a crash leaves all three or none. A legacy
    name already taken (an upstream build ran on this database again) is left
    alone. Returns the renamed tables; never raises.
    """
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        state = conn.execute("SELECT status FROM lib2_bootstrap_state WHERE id=1").fetchone() \
            if "lib2_bootstrap_state" in tables else None
        pending = [t for t in SOURCE_TABLES if t in tables]
        if not state or state[0] != "done" or not pending:
            return []
        if any(RETIRED_TABLES[t] in tables for t in pending):
            logger.warning("Legacy catalogue tables not renamed: %s already exist",
                           ", ".join(RETIRED_TABLES[t] for t in pending))
            return []
        conn.execute("BEGIN IMMEDIATE")
        for table in pending:
            conn.execute(f'ALTER TABLE "{table}" RENAME TO "{RETIRED_TABLES[table]}"')
        conn.commit()
        logger.info("Legacy catalogue renamed after the upgrade: %s",
                    ", ".join(RETIRED_TABLES[t] for t in pending))
        return pending
    except Exception as exc:  # noqa: BLE001 - the upgrade is done either way
        if conn.in_transaction:
            conn.rollback()
        logger.warning("Could not rename the legacy catalogue tables: %s", exc)
        return []


def drop_empty_shadow_tables(conn) -> list:
    """Drop an empty legacy table an older build recreated beside its retired copy.

    a pre-v2 build booting on an upgraded database recreates ``artists``,
    ``albums`` and ``tracks`` empty. the plain name wins in ``source_table``, so
    the next v2 boot read a changed, empty source, re-ran the import and its
    snapshot pass erased the catalogue. only after a finished upgrade, only
    tables with no rows: a downgraded build that really scanned keeps its data.
    returns the dropped tables; never raises.
    """
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        state = conn.execute("SELECT status FROM lib2_bootstrap_state WHERE id=1").fetchone() \
            if "lib2_bootstrap_state" in tables else None
        if not state or state[0] != "done":
            return []
        shadows = [t for t in SOURCE_TABLES
                   if t in tables and RETIRED_TABLES[t] in tables
                   and conn.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone() is None]
        if not shadows:
            return []
        conn.execute("BEGIN IMMEDIATE")
        for table in shadows:
            conn.execute(f'DROP TABLE "{table}"')
        conn.commit()
        logger.warning("Dropped empty legacy tables an older build recreated: %s",
                       ", ".join(shadows))
        return shadows
    except Exception as exc:  # noqa: BLE001 - the skip below still holds
        if conn.in_transaction:
            conn.rollback()
        logger.warning("Could not drop the empty legacy tables: %s", exc)
        return []


def expose_retired_tables(conn) -> None:
    """Give one connection -- the importer's -- the legacy names back as
    temporary views, so an explicit rebuild reads the frozen catalogue while
    every other connection still fails loudly. ``rowid`` stays addressable:
    the walks page and resume by it."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, retired in RETIRED_TABLES.items():
        if table not in tables and retired in tables:
            conn.execute(f'CREATE TEMP VIEW IF NOT EXISTS "{table}" AS '
                         f'SELECT rowid AS rowid, * FROM "{retired}"')


def ensure_source_revision(conn) -> None:
    objects = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    triggers = {f'trg_lib2_source_{table}_{action.lower()}'
                for table in SOURCE_TABLES if source_table(objects, table)
                for action in ('INSERT', 'UPDATE', 'DELETE')}
    if ('lib2_legacy_source_revision' in objects and triggers <= objects
            and conn.execute('SELECT 1 FROM lib2_legacy_source_revision WHERE id=1').fetchone()):
        return
    # Publish the table, seed row and observers in one transaction. Concurrent
    # claimants otherwise see a table whose seed INSERT has not committed yet.
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    conn.execute("CREATE TABLE IF NOT EXISTS lib2_legacy_source_revision "
                 "(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO lib2_legacy_source_revision VALUES(1,0)")
    for table in SOURCE_TABLES:
        source = source_table(objects, table)
        if not source:
            continue
        for action in ("INSERT", "UPDATE", "DELETE"):
            name = f"trg_lib2_source_{table}_{action.lower()}"
            if name not in objects:
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {action} ON {source} BEGIN "
                    "UPDATE lib2_legacy_source_revision SET revision=revision+1 WHERE id=1; END")


def snapshot(conn) -> str:
    """Return counts plus mutation revision and import routing configuration."""
    ensure_source_revision(conn)
    # Install observers before opening the read snapshot. The revision and
    # source counts below must describe one SQLite snapshot, not mixed commits.
    conn.commit()
    conn.execute("BEGIN")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {"_revision": int(conn.execute(
        "SELECT revision FROM lib2_legacy_source_revision WHERE id=1").fetchone()[0]),
        "_profiles": [], "_roots": []}
    for table in SOURCE_TABLES:
        source = source_table(tables, table)
        row = conn.execute(f"SELECT COUNT(*), MAX(id) FROM {source}").fetchone() if source else (0, "")
        result[table] = [int(row[0] or 0), str(row[1] or "")]
    if "profiles" in tables:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)")}
        routing = [c for c in ("id", "library_mode", "library_root") if c in cols]
        if routing:
            result["_profiles"] = [list(r) for r in conn.execute(
                "SELECT " + ",".join(routing) + " FROM profiles ORDER BY id")]
    if "lib2_library_roots" in tables:
        result["_roots"] = [list(r) for r in conn.execute(
            "SELECT prefix,profile_id FROM lib2_library_roots ORDER BY prefix")]
    return json.dumps(result, sort_keys=True, separators=(",", ":"))
