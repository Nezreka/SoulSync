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
    explicit_track_rules_for_album,
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
    assert explicit_track_rules_for_album(conn, row["album_id"]) == {row["id"]: False}
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
