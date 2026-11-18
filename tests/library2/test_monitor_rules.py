"""Monitor rules with provenance (audit P1-13/P1-14).

The ``monitored`` flags stay the effective projection; the rules table
records WHY. Explicit per-track choices must survive album cascades, and
import-derived flags must be labelled ``legacy_import`` instead of passing
as deliberate decisions.
"""

from __future__ import annotations

from core.library2.monitor_rules import (
    PROVENANCE_CASCADE,
    PROVENANCE_FILE,
    PROVENANCE_LEGACY,
    PROVENANCE_USER,
    explicitly_unmonitored_track_ids,
    prune_orphaned_rules,
    record_rule,
    seed_legacy_rules,
)


def _rule(conn, entity_type, entity_id, profile_id=1):
    return conn.execute(
        "SELECT monitored, provenance FROM lib2_monitor_rules "
        "WHERE entity_type=? AND entity_id=? AND profile_id=?",
        (entity_type, entity_id, profile_id)).fetchone()


def test_import_labels_flags_with_import_provenance(imported_conn):
    conn = imported_conn
    track = conn.execute(
        "SELECT id, monitored FROM lib2_tracks WHERE legacy_track_id=100"
    ).fetchone()
    rule = _rule(conn, "track", track["id"])
    assert rule is not None
    assert rule["provenance"] == PROVENANCE_FILE
    assert rule["monitored"] == track["monitored"]
    missing = conn.execute(
        "SELECT id, monitored FROM lib2_tracks WHERE legacy_track_id=101"
    ).fetchone()
    assert _rule(conn, "track", missing["id"])["provenance"] == PROVENANCE_LEGACY
    # Every imported entity got labelled.
    for entity_type, table in (("artist", "lib2_artists"),
                               ("album", "lib2_albums"),
                               ("track", "lib2_tracks")):
        missing = conn.execute(
            f"""SELECT COUNT(*) FROM {table} e
                 WHERE NOT EXISTS (SELECT 1 FROM lib2_monitor_rules r
                     WHERE r.entity_type=? AND r.entity_id=e.id)""",
            (entity_type,)).fetchone()[0]
        assert missing == 0


def test_record_rule_upserts_and_seed_never_downgrades(imported_conn):
    conn = imported_conn
    track_id = conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()["id"]
    record_rule(conn, "track", track_id, True, PROVENANCE_USER)
    assert _rule(conn, "track", track_id)["provenance"] == PROVENANCE_USER
    # Re-seeding (e.g. a later import over the same library) must not
    # downgrade recorded intent back to legacy_import.
    seed_legacy_rules(conn.cursor())
    assert _rule(conn, "track", track_id)["provenance"] == PROVENANCE_USER
    record_rule(conn, "track", track_id, False, PROVENANCE_CASCADE)
    rule = _rule(conn, "track", track_id)
    assert rule["provenance"] == PROVENANCE_CASCADE and rule["monitored"] == 0


def test_explicit_lookups(imported_conn):
    conn = imported_conn
    row = conn.execute(
        "SELECT id, album_id FROM lib2_tracks LIMIT 1").fetchone()
    record_rule(conn, "track", row["id"], False, PROVENANCE_USER)
    assert explicitly_unmonitored_track_ids(conn, [row["id"]]) == {row["id"]}
    # Cascade/legacy rules are not explicit intent.
    other = conn.execute(
        "SELECT id FROM lib2_tracks WHERE id != ? LIMIT 1", (row["id"],)).fetchone()
    assert explicitly_unmonitored_track_ids(conn, [other["id"]]) == set()


def test_prune_drops_rules_of_deleted_entities(imported_conn):
    conn = imported_conn
    track_id = conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()["id"]
    record_rule(conn, "track", track_id, True, PROVENANCE_USER)
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (track_id,))
    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (track_id,))
    pruned = prune_orphaned_rules(conn.cursor())
    assert pruned >= 1
    assert _rule(conn, "track", track_id) is None


def test_the_init_step_leaves_the_orphan_prune_to_the_deferred_backfills(tmp_path):
    """review 5 R5-X2-2: the prune scanned every rule (~880k live) inside the
    init transaction on every start; it runs with the deferred backfills,
    and reads before it deletes"""
    import sqlite3
    from core.library2.schema import ensure_library_v2_schema, run_library_v2_backfills
    path = str(tmp_path / "r.db")
    conn = sqlite3.connect(path)
    ensure_library_v2_schema(conn, run_backfills=False)
    conn.execute("INSERT INTO lib2_monitor_rules(entity_type, entity_id, monitored, provenance) "
                 "VALUES('track', 424242, 1, 'user_explicit')")
    conn.commit()

    seen = []
    conn.set_trace_callback(seen.append)
    ensure_library_v2_schema(conn, run_backfills=False)
    conn.set_trace_callback(None)
    conn.commit()
    assert not [q for q in seen if "lib2_monitor_rules" in q and "NOT IN" in q]
    assert conn.execute("SELECT COUNT(*) FROM lib2_monitor_rules").fetchone()[0] == 1

    stats = run_library_v2_backfills(conn, commit=True)
    assert stats["orphaned_monitor_rules"] == 1
    assert conn.execute("SELECT COUNT(*) FROM lib2_monitor_rules").fetchone()[0] == 0


def test_a_no_op_orphan_prune_leaves_the_write_lock_free(imported_conn, tmp_path):
    import sqlite3
    from core.library2.schema import ensure_library_v2_schema
    path = str(tmp_path / "l.db")
    conn = sqlite3.connect(path)
    ensure_library_v2_schema(conn, run_backfills=False)
    conn.commit()
    conn.execute("PRAGMA journal_mode=WAL")
    assert prune_orphaned_rules(conn.cursor()) == 0
    other = sqlite3.connect(path, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.rollback()
    finally:
        other.close()
