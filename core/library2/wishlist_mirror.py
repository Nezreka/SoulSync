"""Mirror Library-v2 track monitoring into the legacy Wishlist.

Shared by the Library v2 API (monitor toggles, bulk monitor, profile assigns,
manual upgrade scan) and the periodic ``quality_upgrade_scan`` repair job — one
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
        """SELECT t.id AS track_id, t.spotify_id, t.musicbrainz_id,
                  t.external_ids, t.isrc, t.title, t.track_number,
                  t.disc_number, t.duration,
                  al.id AS album_id, al.title album_title,
                  al.spotify_id album_spotify,
                  al.musicbrainz_id album_musicbrainz,
                  al.external_ids album_external_ids,
                  al.track_count, al.expected_track_count, al.album_type,
                  EXISTS(SELECT 1 FROM lib2_track_files tf
                         WHERE tf.track_id = t.id
                           AND tf.path IS NOT NULL AND tf.path <> ''
                           AND COALESCE(tf.file_state,'active')
                               NOT IN ('missing_confirmed','deleted')) has_file
           FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
           WHERE t.id = ?""",
        (track_id,),
    ).fetchone()
    if not t:
        return None
    artist_rows = conn.execute(
        """SELECT ar.name, ar.spotify_id, ar.musicbrainz_id, ar.external_ids
           FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id = ta.artist_id
           WHERE ta.track_id = ? ORDER BY ta.position""", (track_id,)
    ).fetchall()
    from core.library2.provider_ids import (
        preferred_provider_identity,
        provider_only,
        source_ids_from_values,
    )
    from core.metadata.registry import get_primary_source, get_source_priority
    source_order = tuple(get_source_priority(get_primary_source()))

    track_provider_ids = provider_only(source_ids_from_values(
        spotify_id=t["spotify_id"],
        musicbrainz_id=t["musicbrainz_id"],
        external_ids=t["external_ids"],
        isrc=t["isrc"],
    ))
    album_provider_ids = provider_only(source_ids_from_values(
        spotify_id=t["album_spotify"],
        musicbrainz_id=t["album_musicbrainz"],
        external_ids=t["album_external_ids"],
    ))
    source_provider, provider_track_id = preferred_provider_identity(
        track_provider_ids, source_order,
    )
    artists = []
    for row in artist_rows:
        ids = provider_only(source_ids_from_values(
            spotify_id=row["spotify_id"],
            musicbrainz_id=row["musicbrainz_id"],
            external_ids=row["external_ids"],
        ))
        artists.append({"name": row["name"], "provider_ids": ids})
    # Provider-less rows use the persisted stable_id, never the rowid: a
    # library reset + reimport reproduces the same stable_id, so existing
    # wishlist rows keep matching instead of orphaning or double-queueing
    # against fresh rowids (audit P1-12).
    from core.library2.stable_ids import ensure_album_stable_id, ensure_track_stable_id
    source_track_id = provider_track_id or (
        f"lib2-track:{ensure_track_stable_id(conn, t['track_id'])}"
    )
    _album_provider, preferred_album_id = preferred_provider_identity(
        album_provider_ids, source_order,
    )
    source_album_id = (
        album_provider_ids.get(source_provider or "")
        or preferred_album_id
        or f"lib2-album:{ensure_album_stable_id(conn, t['album_id'])}"
    )
    # The PRIMARY file (ADR-03) is what upgrade decisions are made against —
    # never an arbitrary sibling copy of the recording.
    from core.library2.track_files import primary_file_row
    file_info = primary_file_row(conn, track_id)
    from core.library2.profile_lookup import effective_quality_profile
    resolved_profile = effective_quality_profile(conn, "tracks", track_id)
    profile_row = conn.execute(
        """SELECT id, name, upgrade_policy, upgrade_cutoff_index, ranked_targets
             FROM quality_profiles WHERE id=?""",
        (resolved_profile["id"],),
    ).fetchone()
    profile_info = {
        "id": resolved_profile["id"],
        "name": profile_row["name"] if profile_row else "",
        "upgrade_policy": profile_row["upgrade_policy"] if profile_row else "acceptable",
        "upgrade_cutoff_index": profile_row["upgrade_cutoff_index"] if profile_row else 0,
        "ranked_targets": profile_row["ranked_targets"] if profile_row else "[]",
        "source": resolved_profile["source"],
        "source_id": resolved_profile["source_id"],
        "explicit": resolved_profile["explicit"],
    }

    from core.library2.quality_eval import is_upgrade_policy
    should_queue = not bool(t["has_file"])
    quality_evaluation = "not_applicable"
    if t["has_file"] and is_upgrade_policy(profile_info["upgrade_policy"]):
        try:
            from core.library2.quality_eval import evaluate_file, profile_targets
            targets, upgrade_policy, cutoff = profile_targets(profile_info)
            evaluation = evaluate_file(file_info, targets, upgrade_policy, cutoff)
            candidate = evaluation["upgrade_candidate"]
            quality_evaluation = (
                "unknown" if candidate is None
                else "upgrade_candidate" if candidate
                else "satisfied"
            )
            # Unknown quality must enter the existing probe/upgrade pipeline;
            # silently treating it as satisfied would suppress re-evaluation.
            should_queue = candidate is not False
        except Exception as e:  # noqa: BLE001
            logger.debug("quality-profile upgrade check failed (track %s): %s", track_id, e)
            should_queue = False
            quality_evaluation = "error"

    return {
        "id": source_track_id, "name": t["title"],
        "provider": source_provider or "library_v2",
        "source": source_provider or "library_v2",
        "provider_ids": track_provider_ids,
        "artists": artists,
        "album": {
            "name": t["album_title"],
            "id": source_album_id,
            "provider_ids": album_provider_ids,
            "total_tracks": t["expected_track_count"] or t["track_count"] or 1,
            "album_type": t["album_type"],
        },
        "track_number": t["track_number"],
        "disc_number": t["disc_number"],
        "duration_ms": t["duration"],
        "quality_profile_id": resolved_profile["id"],
        "quality_profile": profile_info,
        "_album_type": t["album_type"],
        "_has_file": bool(t["has_file"]),
        "_should_queue": should_queue,
        "_source_album_id": source_album_id,
        "_source_info": {
            "source": "library_v2",
            "lib2_track_id": t["track_id"],
            "lib2_album_id": t["album_id"],
            "quality_profile_id": resolved_profile["id"],
            "quality_profile_name": profile_info["name"],
            "quality_profile_source": resolved_profile["source"],
            "quality_profile_source_id": resolved_profile["source_id"],
            "upgrade_policy": profile_info["upgrade_policy"],
            "upgrade_check": bool(t["has_file"]),
            "quality_evaluation": quality_evaluation,
            "metadata_source": source_provider,
            "track_provider_ids": track_provider_ids,
            "album_provider_ids": album_provider_ids,
        },
    }


def track_direct_download_payload(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """Build one transient pipeline input for a scoped track search.

    This intentionally does *not* write a Wishlist row.  It adapts the same
    server-resolved payload the Wishlist mirror uses into the shape consumed by
    ``run_full_missing_tracks_process`` so Automatic Search can run one track
    through the established candidate/retry/import pipeline without turning a
    one-shot user action into persistent monitoring state.
    """
    payload = track_wishlist_payload(conn, track_id)
    if not payload or not payload.get("_should_queue"):
        return None

    source_info = dict(payload.get("_source_info") or {})
    track_data = {
        key: value for key, value in payload.items()
        if not key.startswith("_")
    }
    album = dict(track_data.get("album") or {})
    artists = list(track_data.get("artists") or [])
    artist = artists[0] if artists else {"name": "Unknown Artist"}
    if not isinstance(artist, dict):
        artist = {"name": str(artist)}

    # The download worker consumes these top-level fields.  ``spotify_data``
    # remains a compatibility name for a provider-neutral, Spotify-shaped
    # payload; the provider/source fields inside it remain authoritative.
    return {
        "track_id": track_data.get("id"),
        "spotify_track_id": track_data.get("id"),
        "track_data": track_data,
        "spotify_data": track_data,
        "source_info": source_info,
        "quality_profile_id": track_data.get("quality_profile_id"),
        "id": track_data.get("id"),
        "name": track_data.get("name"),
        "artists": artists,
        "album": album,
        "duration_ms": track_data.get("duration_ms", 0),
        "track_number": track_data.get("track_number"),
        "disc_number": track_data.get("disc_number"),
        "_explicit_album_context": album,
        "_explicit_artist_context": artist,
        "_is_explicit_album_download": True,
        "_lib2_direct_search": True,
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


def mirror_projected_tracks_wishlist(
    db,
    conn,
    track_ids: List[int],
    *,
    profile_id: int = 1,
    user_initiated: bool = False,
) -> int:
    """Mirror current wanted projection states for the requested tracks."""
    from core.library2.mirror_outbox import drain, enqueue_projected_tracks
    outbox_ids = enqueue_projected_tracks(
        conn,
        track_ids,
        profile_id=profile_id,
        user_initiated=user_initiated,
    )
    if not outbox_ids:
        return 0
    conn.commit()
    drain(db)
    marks = ",".join("?" for _ in outbox_ids)
    row = conn.execute(
        f"SELECT COUNT(*) FROM lib2_mirror_outbox "
        f"WHERE id IN ({marks}) AND status='done'", outbox_ids
    ).fetchone()
    return int(row[0]) if row else 0


def upgrade_candidate_track_ids(conn, *, profile_id: int = 1) -> List[int]:
    """Wanted tracks with files whose profile keeps upgrading
    (``until_top``/``until_cutoff``). The per-track upgrade re-check happens in
    ``mirror_tracks_wishlist`` (only genuine candidates queue)."""
    from core.library2.wanted import PROJECTION_VERSION
    from core.library2.track_files import primary_order
    rows = conn.execute(
        f"""SELECT t.id,
                  (SELECT tf.path FROM lib2_track_files tf
                    WHERE tf.track_id=t.id AND tf.path IS NOT NULL AND tf.path<>''
                      AND COALESCE(tf.file_state,'active')
                          NOT IN ('missing_confirmed','deleted')
                    ORDER BY {primary_order('tf')} LIMIT 1) AS path
             FROM lib2_tracks t
           JOIN lib2_wanted_tracks wt ON wt.track_id=t.id
                AND wt.profile_id=? AND wt.wanted=1
           JOIN quality_profiles qp ON qp.id = t.quality_profile_id
          WHERE wt.projection_version=?
            AND qp.upgrade_policy IN ('until_top', 'until_cutoff')
            AND EXISTS (SELECT 1 FROM lib2_track_files f
                         WHERE f.track_id=t.id AND f.path IS NOT NULL AND f.path<>''
                           AND COALESCE(f.file_state,'active')
                               NOT IN ('missing_confirmed','deleted'))""",
        (int(profile_id), PROJECTION_VERSION),
    ).fetchall()
    from core.library2.manual_skips import active_skip_paths
    protected = active_skip_paths(
        conn, ("quality", "bit_depth"), profile_id=profile_id
    )
    return sorted({int(row["id"]) for row in rows if row["path"] not in protected})


__all__ = [
    "mirror_projected_tracks_wishlist",
    "mirror_tracks_wishlist",
    "track_direct_download_payload",
    "track_wishlist_payload",
    "upgrade_candidate_track_ids",
]
