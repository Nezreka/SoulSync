"""Materialized wanted projection for Library v2 (audit §11.2, ADR-02 Stufe 2).

``lib2_monitor_rules`` records WHY entities are (un)monitored; the
``monitored`` flags remain today's operative projection (ADR-02: staged).
This module adds the next stage: ``lib2_wanted_tracks``, the effective
wanted state PER TRACK computed from the rules alone, with the deciding
rule level recorded — fast to query, idempotent for acquisition, and
auditable when it disagrees with a flag.

Priority (the audit demands this be pinned in tests before use; see
``tests/library2/test_wanted_projection.py``):

1. **explicit track rule** (``user_explicit``) — a direct user decision on
   exactly this track beats everything, in both directions (P1-14).
2. **projected track rule** (``cascade`` / ``new_release``) — the most
   recent bulk action projected onto this track (album toggle cascade,
   profile-assign opt-in, new-release enforcement).
3. **album rule** — any provenance; decides tracks with no own rule (e.g.
   rows materialized from a provider tracklist after the album was toggled).
4. **artist rule** — any provenance. Note: artist toggles never cascade
   flags onto tracks, so this level is exactly the "monitored heißt nicht
   gesucht" gap (P1-13) the projection closes.
5. **legacy track rule** (``legacy_import``) — a flag whose origin is
   unknown ranks below deliberate parent rules but above the default.
6. **default: unmonitored** — no recorded intent anywhere means not wanted.

``wanted`` is the effective *intent*: whether acquisition should consider
the track at all. Whether a wanted track actually queues (missing file,
upgrade candidate) stays a live decision of the acquisition path
(``wishlist_mirror``) — file state is deliberately NOT materialized here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.wanted")

PROJECTION_VERSION = 1

LIB2_WANTED_TRACKS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_wanted_tracks (
    profile_id INTEGER NOT NULL DEFAULT 1,
    track_id INTEGER NOT NULL,
    wanted INTEGER NOT NULL,
    reason TEXT NOT NULL,                 -- deciding rule level (see module doc)
    effective_profile_id INTEGER,         -- resolved quality profile
    projection_version INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile_id, track_id)
)
"""


def ensure_wanted_schema(cursor: Any) -> None:
    """Create the projection table + index. Idempotent."""
    cursor.execute(LIB2_WANTED_TRACKS_DDL)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_lib2_wanted_tracks_wanted "
        "ON lib2_wanted_tracks(profile_id, wanted)")


def _decide(trk_mon: Optional[int], trk_prov: Optional[str],
            alb_mon: Optional[int], alb_prov: Optional[str],
            art_mon: Optional[int], art_prov: Optional[str]) -> tuple:
    """Apply the documented priority to one track's rule set."""
    if trk_prov == "user_explicit":
        return bool(trk_mon), "track_explicit"
    if trk_prov in ("cascade", "new_release"):
        return bool(trk_mon), f"track_rule:{trk_prov}"
    if alb_mon is not None:
        return bool(alb_mon), f"album_rule:{alb_prov}"
    if art_mon is not None:
        return bool(art_mon), f"artist_rule:{art_prov}"
    if trk_prov == "legacy_import":
        return bool(trk_mon), "track_rule:legacy_import"
    return False, "default_unmonitored"


def recompute_wanted(conn: Any, *, profile_id: int = 1,
                     track_ids: Optional[List[int]] = None) -> Dict[str, int]:
    """Recompute the projection — fully, or scoped to ``track_ids``.

    Upserts one row per (profile, track); full runs also prune rows of
    deleted tracks. ``flag_mismatches`` counts tracks whose projected wanted
    state differs from the operative ``monitored`` flag — observability for
    the staged ADR-02 cutover, no flags are changed. Does not commit.
    """
    stats = {"projected": 0, "wanted": 0, "pruned": 0, "flag_mismatches": 0}
    scope_sql, scope_args = "", []
    if track_ids is not None:
        if not track_ids:
            return stats
        marks = ",".join("?" for _ in track_ids)
        scope_sql = f" WHERE t.id IN ({marks})"
        scope_args = [int(t) for t in track_ids]
    else:
        cur = conn.execute(
            "DELETE FROM lib2_wanted_tracks WHERE profile_id=? AND track_id "
            "NOT IN (SELECT id FROM lib2_tracks)", (int(profile_id),))
        stats["pruned"] = cur.rowcount

    rows = conn.execute(
        f"""SELECT t.id AS track_id, t.monitored AS flag,
                   t.quality_profile_id,
                   tr.monitored AS trk_mon, tr.provenance AS trk_prov,
                   ab.monitored AS alb_mon, ab.provenance AS alb_prov,
                   aa.monitored AS art_mon, aa.provenance AS art_prov
              FROM lib2_tracks t
              JOIN lib2_albums al ON al.id = t.album_id
              LEFT JOIN lib2_monitor_rules tr
                     ON tr.entity_type='track' AND tr.entity_id=t.id
                    AND tr.profile_id=?
              LEFT JOIN lib2_monitor_rules ab
                     ON ab.entity_type='album' AND ab.entity_id=al.id
                    AND ab.profile_id=?
              LEFT JOIN lib2_monitor_rules aa
                     ON aa.entity_type='artist'
                    AND aa.entity_id=al.primary_artist_id
                    AND aa.profile_id=?{scope_sql}""",
        (int(profile_id), int(profile_id), int(profile_id), *scope_args),
    ).fetchall()

    for r in rows:
        wanted, reason = _decide(r["trk_mon"], r["trk_prov"],
                                 r["alb_mon"], r["alb_prov"],
                                 r["art_mon"], r["art_prov"])
        conn.execute(
            """INSERT INTO lib2_wanted_tracks(
                   profile_id, track_id, wanted, reason,
                   effective_profile_id, projection_version)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(profile_id, track_id) DO UPDATE SET
                   wanted=excluded.wanted,
                   reason=excluded.reason,
                   effective_profile_id=excluded.effective_profile_id,
                   projection_version=excluded.projection_version,
                   updated_at=CURRENT_TIMESTAMP""",
            (int(profile_id), r["track_id"], 1 if wanted else 0, reason,
             r["quality_profile_id"], PROJECTION_VERSION))
        stats["projected"] += 1
        if wanted:
            stats["wanted"] += 1
        if wanted != bool(r["flag"]):
            stats["flag_mismatches"] += 1
    if stats["flag_mismatches"]:
        logger.info("Wanted projection: %d of %d tracks diverge from their "
                    "monitored flag (profile %s)", stats["flag_mismatches"],
                    stats["projected"], profile_id)
    return stats


def recompute_wanted_for_entity(conn: Any, entity: str, entity_id: int,
                                *, profile_id: int = 1) -> Dict[str, int]:
    """Scoped recompute after a monitor mutation on one entity.

    Artists scope through the PRIMARY-artist chain — the same chain the
    projection's artist tier reads; featured credits don't inherit rules.
    """
    if entity in ("track", "tracks"):
        ids = [int(entity_id)]
    elif entity in ("album", "albums"):
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=?", (int(entity_id),))]
    elif entity in ("artist", "artists"):
        ids = [r[0] for r in conn.execute(
            """SELECT t.id FROM lib2_tracks t
               JOIN lib2_albums al ON al.id = t.album_id
              WHERE al.primary_artist_id=?""", (int(entity_id),))]
    else:
        return {"projected": 0, "wanted": 0, "pruned": 0, "flag_mismatches": 0}
    return recompute_wanted(conn, profile_id=profile_id, track_ids=ids)


def wanted_track_ids(conn: Any, *, profile_id: int = 1) -> List[int]:
    """Track ids the projection currently marks wanted (fast indexed read)."""
    return [r[0] for r in conn.execute(
        "SELECT track_id FROM lib2_wanted_tracks WHERE profile_id=? AND wanted=1",
        (int(profile_id),))]


def ensure_wanted_projection(cursor: Any) -> None:
    """Schema-ensure hook: create the table; rebuild the projection when it
    is empty-but-should-not-be or was built by an older priority version.
    Otherwise just prune rows of deleted tracks (cheap)."""
    ensure_wanted_schema(cursor)
    row = cursor.execute(
        "SELECT COUNT(*), COALESCE(MIN(projection_version), 0) "
        "FROM lib2_wanted_tracks").fetchone()
    have_rows, min_version = int(row[0]), int(row[1])
    tracks_exist = cursor.execute(
        "SELECT 1 FROM lib2_tracks LIMIT 1").fetchone() is not None
    if tracks_exist and (have_rows == 0 or min_version < PROJECTION_VERSION):
        stats = recompute_wanted(cursor)
        logger.info("Wanted projection rebuilt: %s", stats)
    else:
        cursor.execute(
            "DELETE FROM lib2_wanted_tracks WHERE track_id NOT IN "
            "(SELECT id FROM lib2_tracks)")


__all__ = [
    "PROJECTION_VERSION",
    "ensure_wanted_projection",
    "ensure_wanted_schema",
    "recompute_wanted",
    "recompute_wanted_for_entity",
    "wanted_track_ids",
]
