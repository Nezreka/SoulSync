"""Materialized wanted projection for Library v2 (audit §11.2, ADR-02 Stufe 2).

``lib2_monitor_rules`` records WHY entities are (un)monitored; the
``monitored`` flags remain a compatibility projection. This module owns
``lib2_wanted_tracks``, the effective
wanted state PER TRACK consumed by acquisition/mirroring and computed from
the rules alone, with the deciding
rule level recorded — fast to query, idempotent for acquisition, and
auditable when it disagrees with a flag.

Priority (the audit demands this be pinned in tests before use; see
``tests/library2/test_wanted_projection.py``):

1. **explicit track rule** (``user_explicit``) — a direct user decision on
   exactly this track beats everything, in both directions (P1-14).
2. **imported Wishlist track rule** (``wishlist_import``) — a concrete
   admin-Wishlist item beats inherited album/artist state.
3. **projected track rule** (``cascade`` / ``new_release``) — the most
   recent bulk action projected onto this track (album toggle cascade,
   profile-assign opt-in, new-release enforcement).
4. **deliberate album rule** (anything except ``legacy_import``) — a runtime
   parent choice still beats file-derived upgrade monitoring.
5. **imported file track rule** (``file_import``) — a concrete local file is
   monitored for upgrades even when its release has a derived, incomplete
   ``legacy_import`` baseline.
6. **derived album rule** (``legacy_import``) — decides otherwise unruled
   tracks imported under the release.
7. **artist rule** — any provenance. Note: artist toggles never cascade
   flags onto tracks, so this level is exactly the "monitored heißt nicht
   gesucht" gap (P1-13) the projection closes.
8. **legacy track rule** (``legacy_import``) — a flag whose origin is
   unknown ranks below deliberate parent rules but above the default.
9. **default: unmonitored** — no recorded intent anywhere means not wanted.

Album rules therefore retain their old authority when they reflect a user or
runtime action. Only the importer's derived incomplete-parent baseline is
overridden by concrete file coverage.

``wanted`` is the effective *intent*: whether acquisition should consider
the track at all. Whether a wanted track actually queues (missing file,
upgrade candidate) stays a live decision of the acquisition path
(``wishlist_mirror``) — file state is deliberately NOT materialized here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.wanted")

PROJECTION_VERSION = 2

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
    if trk_prov == "wishlist_import":
        return bool(trk_mon), "track_rule:wishlist_import"
    if trk_prov in ("cascade", "new_release"):
        return bool(trk_mon), f"track_rule:{trk_prov}"
    if alb_mon is not None and alb_prov != "legacy_import":
        return bool(alb_mon), f"album_rule:{alb_prov}"
    if trk_prov == "file_import":
        return bool(trk_mon), "track_rule:file_import"
    if alb_mon is not None:
        return bool(alb_mon), f"album_rule:{alb_prov}"
    if art_mon is not None:
        return bool(art_mon), f"artist_rule:{art_prov}"
    if trk_prov == "legacy_import":
        return bool(trk_mon), "track_rule:legacy_import"
    return False, "default_unmonitored"


def recompute_wanted(conn: Any, *, profile_id: int = 1,
                     track_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Recompute the projection — fully, or scoped to ``track_ids``.

    Upserts one row per (profile, track); full runs also prune rows of
    deleted tracks. ``flag_mismatches`` counts tracks whose projected wanted
    state differs from the operative ``monitored`` flag — observability for
    the staged ADR-02 cutover, no flags are changed. ``projected`` counts
    every row CONSIDERED (the scope size), not rows actually written — the
    upsert's WHERE clause skips writing/touching ``updated_at`` for rows
    whose wanted/reason/profile/version didn't change, so use ``written``
    (== ``len(changed_track_ids)``) to gauge real write volume. Does not
    commit.
    """
    stats = {"projected": 0, "wanted": 0, "pruned": 0, "flag_mismatches": 0}
    changed_track_ids: List[int] = []
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

    # The effective-profile cascade columns (track/album/artist) are joined
    # in here so the projection resolves each track's profile from this one
    # query instead of a per-track effective_quality_profile() call — that
    # was an N+1 (a 3-table join + a default-profile lookup PER track) on a
    # full recompute of a large library (review Teil B, efficiency cluster).
    # The artist join is LEFT so a track whose album has a dangling
    # primary_artist_id falls through to the default profile rather than
    # crashing the whole projection (the old per-track resolver used an inner
    # join and would raise LookupError on such a row).
    rows = conn.execute(
        f"""SELECT t.id AS track_id, t.monitored AS flag,
                   t.quality_profile_id AS trk_prof,
                   COALESCE(t.quality_profile_explicit, 0) AS trk_prof_expl,
                   al.id AS album_id,
                   al.quality_profile_id AS alb_prof,
                   COALESCE(al.quality_profile_explicit, 0) AS alb_prof_expl,
                   al.primary_artist_id AS artist_id,
                   art.quality_profile_id AS art_prof,
                   COALESCE(art.quality_profile_explicit, 0) AS art_prof_expl,
                   tr.monitored AS trk_mon, tr.provenance AS trk_prov,
                   ab.monitored AS alb_mon, ab.provenance AS alb_prov,
                   aa.monitored AS art_mon, aa.provenance AS art_prov
              FROM lib2_tracks t
              JOIN lib2_albums al ON al.id = t.album_id
              LEFT JOIN lib2_artists art ON art.id = al.primary_artist_id
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

    from core.library2.profile_lookup import (
        default_quality_profile_id,
        resolve_profile_cascade,
    )
    default_profile_id = default_quality_profile_id(conn)

    for r in rows:
        wanted, reason = _decide(r["trk_mon"], r["trk_prov"],
                                 r["alb_mon"], r["alb_prov"],
                                 r["art_mon"], r["art_prov"])
        # Same Track > Album > Artist > Global cascade the per-entity
        # effective_quality_profile uses (shared resolve_profile_cascade).
        effective_profile_id = resolve_profile_cascade(
            (
                ("track", r["track_id"], r["trk_prof"], r["trk_prof_expl"]),
                ("album", r["album_id"], r["alb_prof"], r["alb_prof_expl"]),
                ("artist", r["artist_id"], r["art_prof"], r["art_prof_expl"]),
            ),
            default_profile_id,
        )["id"]
        # The WHERE on the upsert skips the write (and its updated_at bump)
        # when nothing changed — a full hourly recompute otherwise re-writes
        # every unchanged row, churning indexes for no reason (review Teil B).
        cur = conn.execute(
            """INSERT INTO lib2_wanted_tracks(
                   profile_id, track_id, wanted, reason,
                   effective_profile_id, projection_version)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(profile_id, track_id) DO UPDATE SET
                   wanted=excluded.wanted,
                   reason=excluded.reason,
                   effective_profile_id=excluded.effective_profile_id,
                   projection_version=excluded.projection_version,
                   updated_at=CURRENT_TIMESTAMP
               WHERE lib2_wanted_tracks.wanted IS NOT excluded.wanted
                  OR lib2_wanted_tracks.reason IS NOT excluded.reason
                  OR lib2_wanted_tracks.effective_profile_id
                     IS NOT excluded.effective_profile_id
                  OR lib2_wanted_tracks.projection_version
                     IS NOT excluded.projection_version""",
            (int(profile_id), r["track_id"], 1 if wanted else 0, reason,
             effective_profile_id, PROJECTION_VERSION))
        # rowcount is 0 when the upsert's WHERE found nothing to change (a
        # genuine no-op row) — callers (e.g. the wishlist reconcile) use this
        # to re-mirror a track whose wanted/profile state just changed even
        # when it's already wishlisted, without re-touching every unchanged
        # row (review Teil B's original efficiency goal).
        if cur.rowcount:
            changed_track_ids.append(int(r["track_id"]))
        stats["projected"] += 1
        if wanted:
            stats["wanted"] += 1
        if wanted != bool(r["flag"]):
            stats["flag_mismatches"] += 1
    if stats["flag_mismatches"]:
        logger.info("Wanted projection: %d of %d tracks diverge from their "
                    "monitored flag (profile %s)", stats["flag_mismatches"],
                    stats["projected"], profile_id)
    stats["changed_track_ids"] = changed_track_ids
    stats["written"] = len(changed_track_ids)
    return stats


def entity_track_ids(conn: Any, entity: str, entity_id: int) -> List[int]:
    """All lib2 track ids belonging to one artist/album/track.

    Artists scope through the PRIMARY-artist chain — the same chain the
    projection's artist tier reads; featured credits don't inherit rules.
    """
    if entity in ("track", "tracks"):
        return [int(entity_id)]
    if entity in ("album", "albums"):
        return [r[0] for r in conn.execute(
            "SELECT id FROM lib2_tracks WHERE album_id=?", (int(entity_id),))]
    if entity in ("artist", "artists"):
        return [r[0] for r in conn.execute(
            """SELECT t.id FROM lib2_tracks t
               JOIN lib2_albums al ON al.id = t.album_id
              WHERE al.primary_artist_id=?""", (int(entity_id),))]
    return []


def recompute_wanted_for_entity(conn: Any, entity: str, entity_id: int,
                                *, profile_id: int = 1) -> Dict[str, int]:
    """Scoped recompute after a monitor mutation on one entity."""
    return recompute_wanted(
        conn, profile_id=profile_id, track_ids=entity_track_ids(conn, entity, entity_id))


def wanted_track_ids(conn: Any, *, profile_id: int = 1) -> List[int]:
    """Track ids the projection currently marks wanted (fast indexed read)."""
    return [r[0] for r in conn.execute(
        "SELECT track_id FROM lib2_wanted_tracks WHERE profile_id=? AND wanted=1",
        (int(profile_id),))]


def track_wanted_states(
    conn: Any, track_ids: List[int], *, profile_id: int = 1
) -> Dict[int, bool]:
    """Current authoritative states for existing tracks.

    Mirror/acquisition consumers must not silently fall back to compatibility
    flags when a projection row is missing or stale.
    """
    normalized = sorted({int(track_id) for track_id in track_ids})
    if not normalized:
        return {}
    marks = ",".join("?" for _ in normalized)
    rows = conn.execute(
        f"""SELECT t.id, w.wanted, w.projection_version
              FROM lib2_tracks t
              LEFT JOIN lib2_wanted_tracks w
                     ON w.track_id=t.id AND w.profile_id=?
             WHERE t.id IN ({marks})""",
        (int(profile_id), *normalized),
    ).fetchall()
    found = {int(row["id"]): row for row in rows}
    incomplete = [
        track_id for track_id in normalized
        if track_id not in found
        or found[track_id]["wanted"] is None
        or found[track_id]["projection_version"] != PROJECTION_VERSION
    ]
    if incomplete:
        raise RuntimeError(
            "wanted projection missing or stale for tracks: "
            + ",".join(str(track_id) for track_id in incomplete[:20])
        )
    return {track_id: bool(found[track_id]["wanted"]) for track_id in normalized}


def track_is_wanted(conn: Any, track_id: int, *, profile_id: int = 1) -> bool:
    return track_wanted_states(
        conn, [int(track_id)], profile_id=profile_id
    )[int(track_id)]


def wanted_projection_status(conn: Any, *, profile_id: int = 1) -> Dict[str, Any]:
    """Read-only completeness/drift metrics for the staged consumer cutover."""
    profile_id = int(profile_id)
    row = conn.execute(
        """SELECT COUNT(*) AS tracks,
                  SUM(CASE WHEN w.track_id IS NULL THEN 1 ELSE 0 END) AS missing,
                  SUM(CASE WHEN w.track_id IS NOT NULL
                                AND w.projection_version<>? THEN 1 ELSE 0 END) AS stale,
                  SUM(CASE WHEN w.wanted=1 THEN 1 ELSE 0 END) AS wanted,
                  SUM(CASE WHEN w.track_id IS NOT NULL
                                AND w.projection_version=?
                                AND w.wanted<>t.monitored THEN 1 ELSE 0 END)
                      AS flag_mismatches
             FROM lib2_tracks t
             LEFT JOIN lib2_wanted_tracks w
                    ON w.track_id=t.id AND w.profile_id=?""",
        (PROJECTION_VERSION, PROJECTION_VERSION, profile_id),
    ).fetchone()
    values = {
        "profile_id": profile_id,
        "projection_version": PROJECTION_VERSION,
        "tracks": int(row["tracks"] or 0),
        "wanted": int(row["wanted"] or 0),
        "missing": int(row["missing"] or 0),
        "stale": int(row["stale"] or 0),
        "flag_mismatches": int(row["flag_mismatches"] or 0),
    }
    # Flag mismatches are observable migration drift, but can be intentional:
    # parent rules are exactly what the old per-track flag failed to express.
    values["consumer_ready"] = values["missing"] == 0 and values["stale"] == 0
    return values


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
    "entity_track_ids",
    "ensure_wanted_projection",
    "ensure_wanted_schema",
    "recompute_wanted",
    "recompute_wanted_for_entity",
    "track_is_wanted",
    "track_wanted_states",
    "wanted_projection_status",
    "wanted_track_ids",
]
