"""Monitor rules with provenance for Library v2 (audit P1-13/P1-14, Phase 1).

The ``monitored`` columns on lib2 rows are the effective *projection* the
rest of the app consumes (wishlist mirror, queries, UI). What was missing is
WHY a row is (un)monitored — without that, an album cascade destroys
deliberate per-track choices (P1-14) and nothing can distinguish "the user
chose this" from "an import copied a flag" (P1-13).

``lib2_monitor_rules`` records the intent per (entity, profile):

- ``user_explicit`` — a direct user action on exactly that entity. Survives
  album/artist cascades in both directions: a cascade only re-projects rows
  WITHOUT an explicit rule; re-deciding an explicit row takes another direct
  action on it.
- ``cascade``       — a bulk action projected onto the row (album toggle,
  profile-assign auto-monitor). Freely overwritten by later actions.
- ``new_release``   — auto-monitored by the discography "monitor new items"
  enforcement.
- ``legacy_import`` — the flag existed before rules were introduced (or came
  from the legacy import); provenance unknown, never blocks a cascade.

Absence of a rule means "no recorded intent" — the flag is whatever the last
projection wrote, exactly like before this table existed.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

from utils.logging_config import get_logger

logger = get_logger("library2.monitor_rules")

PROVENANCE_USER = "user_explicit"
PROVENANCE_CASCADE = "cascade"
PROVENANCE_NEW_RELEASE = "new_release"
PROVENANCE_LEGACY = "legacy_import"

_ENTITY_TABLES = {"artist": "lib2_artists", "album": "lib2_albums", "track": "lib2_tracks"}


def record_rule(conn, entity_type: str, entity_id: int, monitored: bool,
                provenance: str, *, profile_id: int = 1) -> None:
    """Upsert the monitor intent for one entity. Does not commit."""
    conn.execute(
        """INSERT INTO lib2_monitor_rules(entity_type, entity_id, profile_id,
                                          monitored, provenance)
           VALUES(?,?,?,?,?)
           ON CONFLICT(entity_type, entity_id, profile_id) DO UPDATE SET
               monitored=excluded.monitored,
               provenance=excluded.provenance,
               updated_at=CURRENT_TIMESTAMP""",
        (entity_type, int(entity_id), int(profile_id), 1 if monitored else 0,
         provenance))


def record_rules(conn, entity_type: str, entity_ids: Iterable[int],
                 monitored: bool, provenance: str, *, profile_id: int = 1) -> None:
    for eid in entity_ids:
        record_rule(conn, entity_type, eid, monitored, provenance,
                    profile_id=profile_id)


def explicit_track_rules_for_album(conn, album_id: int,
                                   *, profile_id: int = 1) -> Dict[int, bool]:
    """track_id -> explicitly chosen monitored value, for one album's tracks."""
    return {
        r[0]: bool(r[1]) for r in conn.execute(
            """SELECT r.entity_id, r.monitored
                 FROM lib2_monitor_rules r
                 JOIN lib2_tracks t ON t.id = r.entity_id
                WHERE r.entity_type='track' AND r.provenance=?
                  AND r.profile_id=? AND t.album_id=?""",
            (PROVENANCE_USER, int(profile_id), int(album_id)))
    }


def explicitly_unmonitored_track_ids(conn, track_ids: List[int],
                                     *, profile_id: int = 1) -> set:
    """Which of the given tracks the user explicitly set to unmonitored."""
    if not track_ids:
        return set()
    marks = ",".join("?" for _ in track_ids)
    return {
        r[0] for r in conn.execute(
            f"""SELECT entity_id FROM lib2_monitor_rules
                 WHERE entity_type='track' AND provenance=?
                   AND profile_id=? AND monitored=0
                   AND entity_id IN ({marks})""",
            (PROVENANCE_USER, int(profile_id), *[int(t) for t in track_ids]))
    }


def seed_legacy_rules(cursor) -> int:
    """One-time provenance for flags that predate the rules table.

    Marks every existing entity's current monitored flag as
    ``legacy_import`` — states whose origin is unknown must be labelled as
    such, not mistaken for deliberate choices (they never block a cascade).
    Only fills entities that have NO rule yet, so recorded intent is never
    downgraded. Returns the number of seeded rows.
    """
    seeded = 0
    for entity_type, table in _ENTITY_TABLES.items():
        cursor.execute(
            f"""INSERT INTO lib2_monitor_rules(entity_type, entity_id, profile_id,
                                               monitored, provenance)
                SELECT ?, e.id, 1, e.monitored, ?
                  FROM {table} e
                 WHERE NOT EXISTS (
                     SELECT 1 FROM lib2_monitor_rules r
                      WHERE r.entity_type=? AND r.entity_id=e.id AND r.profile_id=1)""",
            (entity_type, PROVENANCE_LEGACY, entity_type))
        seeded += cursor.rowcount
    return seeded


def prune_orphaned_rules(cursor) -> int:
    """Drop rules whose entity no longer exists (entity deletes don't cascade
    into this table). Idempotent; called from the schema-ensure step."""
    pruned = 0
    for entity_type, table in _ENTITY_TABLES.items():
        cursor.execute(
            f"""DELETE FROM lib2_monitor_rules
                 WHERE entity_type=?
                   AND entity_id NOT IN (SELECT id FROM {table})""",
            (entity_type,))
        pruned += cursor.rowcount
    return pruned


__all__ = [
    "PROVENANCE_CASCADE",
    "PROVENANCE_LEGACY",
    "PROVENANCE_NEW_RELEASE",
    "PROVENANCE_USER",
    "explicit_track_rules_for_album",
    "explicitly_unmonitored_track_ids",
    "prune_orphaned_rules",
    "record_rule",
    "record_rules",
    "seed_legacy_rules",
]
