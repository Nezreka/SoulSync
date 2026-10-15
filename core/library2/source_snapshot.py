"""Cheap persisted invalidation of legacy upgrade checkpoints.

Counts/max IDs alone cannot detect edits to a file's path or owner. Watching
the frozen compatibility tables avoids hashing a 300k-track library on every
upgrade-state poll; native writes do not change this revision.
"""

from __future__ import annotations

import json

SOURCE_TABLES = ("artists", "albums", "tracks")


def ensure_source_revision(conn) -> None:
    objects = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    if "lib2_legacy_source_revision" not in objects:
        conn.execute("CREATE TABLE lib2_legacy_source_revision "
                     "(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)")
        conn.execute("INSERT INTO lib2_legacy_source_revision VALUES(1,0)")
    for table in SOURCE_TABLES:
        if table not in objects:
            continue
        for action in ("INSERT", "UPDATE", "DELETE"):
            name = f"trg_lib2_source_{table}_{action.lower()}"
            if name not in objects:
                conn.execute(
                    f"CREATE TRIGGER {name} AFTER {action} ON {table} BEGIN "
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
        row = conn.execute(f"SELECT COUNT(*), MAX(id) FROM {table}").fetchone() if table in tables else (0, "")
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
