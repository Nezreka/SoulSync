"""Mirror Library-v2 track monitoring into the legacy Wishlist.

Shared by the Library v2 API (monitor toggles, bulk monitor, profile assigns,
manual upgrade scan) and the periodic ``lib2_upgrade_scan`` repair job — one
implementation so the queueing rules can't drift.

Key contract: ``add_to_wishlist(quality_profile_id=…)`` carries the app-wide
quality profile onto the wishlist row, which every pipeline stage resolves
live (``core/quality/selection.load_profile_by_id``). Under an upgrade policy
(``until_top``/``until_cutoff``) a track that already HAS a file is only
queued when its file is a genuine upgrade candidate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.wishlist_mirror")


def track_wishlist_payload(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """Build the wishlist payload for a lib2 track (or None when unknown)."""
    t = conn.execute(
        """SELECT t.id AS track_id, t.spotify_id, t.title, t.track_number,
                  t.disc_number, t.duration, t.quality_profile_id,
                  al.id AS album_id, al.title album_title, al.spotify_id album_spotify,
                  al.track_count, al.expected_track_count, al.album_type,
                  qp.name AS quality_profile_name, qp.upgrade_policy,
                  qp.upgrade_cutoff_index, qp.ranked_targets,
                  EXISTS(SELECT 1 FROM lib2_track_files tf
                         WHERE tf.track_id = t.id AND tf.path IS NOT NULL AND tf.path <> '') has_file
           FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
           LEFT JOIN quality_profiles qp ON qp.id = t.quality_profile_id
           WHERE t.id = ?""",
        (track_id,),
    ).fetchone()
    if not t:
        return None
    artists = [r["name"] for r in conn.execute(
        """SELECT ar.name FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id = ta.artist_id
           WHERE ta.track_id = ? ORDER BY ta.position""", (track_id,))]
    # Provider-less rows use the persisted stable_id, never the rowid: a
    # library reset + reimport reproduces the same stable_id, so existing
    # wishlist rows keep matching instead of orphaning or double-queueing
    # against fresh rowids (audit P1-12).
    from core.library2.stable_ids import ensure_album_stable_id, ensure_track_stable_id
    source_track_id = t["spotify_id"] or f"lib2-track:{ensure_track_stable_id(conn, t['track_id'])}"
    source_album_id = t["album_spotify"] or f"lib2-album:{ensure_album_stable_id(conn, t['album_id'])}"
    file_row = conn.execute(
        "SELECT * FROM lib2_track_files WHERE track_id = ? ORDER BY id LIMIT 1",
        (track_id,),
    ).fetchone()
    file_info = dict(file_row) if file_row else None
    profile_info = {
        "id": t["quality_profile_id"],
        "name": t["quality_profile_name"] or "",
        "upgrade_policy": t["upgrade_policy"] or "acceptable",
        "upgrade_cutoff_index": t["upgrade_cutoff_index"] or 0,
        "ranked_targets": t["ranked_targets"] or "[]",
    }

    from core.library2.quality_eval import is_upgrade_policy
    should_queue = not bool(t["has_file"])
    if t["has_file"] and is_upgrade_policy(profile_info["upgrade_policy"]):
        try:
            from core.library2.quality_eval import evaluate_file, profile_targets
            targets, upgrade_policy, cutoff = profile_targets(profile_info)
            should_queue = bool(evaluate_file(
                file_info, targets, upgrade_policy, cutoff)["upgrade_candidate"])
        except Exception as e:  # noqa: BLE001
            logger.debug("quality-profile upgrade check failed (track %s): %s", track_id, e)
            should_queue = False

    return {
        "id": source_track_id, "name": t["title"],
        "provider": "spotify" if t["spotify_id"] else "library_v2",
        "source": "library_v2",
        "artists": [{"name": n} for n in artists],
        "album": {
            "name": t["album_title"],
            "id": source_album_id,
            "total_tracks": t["expected_track_count"] or t["track_count"] or 1,
            "album_type": t["album_type"],
        },
        "track_number": t["track_number"],
        "disc_number": t["disc_number"],
        "duration_ms": t["duration"],
        "quality_profile_id": t["quality_profile_id"],
        "quality_profile": profile_info,
        "_album_type": t["album_type"],
        "_has_file": bool(t["has_file"]),
        "_should_queue": should_queue,
        "_source_album_id": source_album_id,
        "_source_info": {
            "source": "library_v2",
            "lib2_track_id": t["track_id"],
            "lib2_album_id": t["album_id"],
            "quality_profile_id": t["quality_profile_id"],
            "quality_profile_name": profile_info["name"],
            "upgrade_policy": profile_info["upgrade_policy"],
            "upgrade_check": bool(t["has_file"]),
        },
    }


def mirror_tracks_wishlist(db, conn, track_ids: List[int], monitored: bool,
                           *, profile_id: int = 1,
                           user_initiated: bool = False) -> int:
    """Add/remove the given lib2 tracks to/from the legacy Wishlist.

    Outbox-backed (audit P0-04): the intents are enqueued on ``conn``,
    committed, and drained against the legacy tables. A failing legacy write
    stays visible and retryable in ``lib2_mirror_outbox`` instead of being
    swallowed. Returns how many of THIS call's intents completed.

    NOTE: commits ``conn`` — callers must not be mid-transaction with
    unrelated pending writes (all current callers invoke this after their
    own commit; ``lib2_set_monitored`` enqueues inline instead, so its flag
    write and outbox rows share one transaction).

    ``profile_id`` is the legacy per-user profile scope of the wishlist, NOT a
    quality profile — the quality profile travels per item via
    ``quality_profile_id`` on the payload.

    ``user_initiated`` must only be True for a DIRECT user action on that
    specific track (the track-level monitor toggle). It bypasses the wishlist
    ignore-list AND clears a stale ignore. Cascades (album/artist toggles,
    bulk monitor, profile assignment) and scheduled jobs (upgrade scan,
    discography auto-monitor) must leave it False so a deliberate user
    cancel/remove keeps sticking (audit P1-11).
    """
    from core.library2.mirror_outbox import drain, enqueue_tracks

    outbox_ids = enqueue_tracks(conn, track_ids, monitored,
                                profile_id=profile_id,
                                user_initiated=user_initiated)
    if not outbox_ids:
        return 0
    conn.commit()
    drain(db)
    marks = ",".join("?" for _ in outbox_ids)
    row = conn.execute(
        f"SELECT COUNT(*) FROM lib2_mirror_outbox "
        f"WHERE id IN ({marks}) AND status='done'", outbox_ids).fetchone()
    return int(row[0]) if row else 0


def upgrade_candidate_track_ids(conn) -> List[int]:
    """Monitored tracks with files whose profile keeps upgrading
    (``until_top``/``until_cutoff``). The per-track upgrade re-check happens in
    ``mirror_tracks_wishlist`` (only genuine candidates queue)."""
    return [r["id"] for r in conn.execute(
        """SELECT t.id FROM lib2_tracks t
           JOIN quality_profiles qp ON qp.id = t.quality_profile_id
          WHERE t.monitored = 1
            AND qp.upgrade_policy IN ('until_top', 'until_cutoff')
            AND EXISTS (SELECT 1 FROM lib2_track_files tf
                        WHERE tf.track_id = t.id
                          AND tf.path IS NOT NULL AND tf.path <> '')"""
    )]


__all__ = ["track_wishlist_payload", "mirror_tracks_wishlist", "upgrade_candidate_track_ids"]
