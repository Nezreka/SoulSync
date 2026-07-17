"""Reverse-direction Library-v2 ↔ legacy Watchlist/Wishlist synchronisation (§69.1).

The forward edges (a lib2 monitor toggle mirrors into the legacy Watchlist /
Wishlist) are outbox-backed and live in ``mirror_outbox`` / ``wishlist_mirror``.
They fire only on an explicit toggle, which leaves two gaps this module closes:

1. **Watchlist → Library demonitor** (event-driven). When the user removes an
   artist from the legacy Watchlist (single, batch, or a full clear), the
   matching monitored lib2 artist stays ``monitored`` — the states diverge.
   ``demonitor_lib2_artists_for_removed_watchlist`` flips the matching lib2
   artist(s) back to unmonitored and pulls any now-unwanted tracks from the
   Wishlist. It must be called ONLY from the user-facing removal endpoints,
   never from the forward mirror's own ``remove_artist_from_watchlist`` call
   (that path removes the row BECAUSE lib2 demonitored — re-entering here would
   loop and re-record rules).

2. **Wanted projection → Wishlist re-assertion** (reconcile). The Wishlist is a
   volatile queue: entries leave when downloaded, cleared, or aged out. Nothing
   re-adds a still-``wanted`` + missing track once its entry is gone, because
   the mirror is edge-triggered. ``reconcile_track_wishlist`` re-derives the
   authoritative wanted projection and mirrors it into the Wishlist — adding
   wanted+missing tracks back and pruning entries whose track is no longer
   wanted. Idempotent; respects the ignore-list (``user_initiated=False``).

Both operate on the admin profile (ADR-01). Never touches files.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("library2.monitor_sync")


# ---------------------------------------------------------------------------
# Watchlist → Library demonitor (event-driven reverse edge)
# ---------------------------------------------------------------------------


def _match_lib2_artists(
    conn, external_ids: Sequence[str], name: Optional[str]
) -> List[int]:
    """lib2 artist ids matching a removed Watchlist row.

    Strong match first: any of the Watchlist row's provider ids equals a lib2
    artist's ``spotify_id`` / ``musicbrainz_id`` or appears as a value in its
    ``external_ids`` JSON. Only when no provider id matches does the normalized
    name act as a fallback (the same name-equality the legacy manual-match
    bridge accepts, ``web_server._watchlist_row_matches_legacy_artist``).
    """
    exts = [str(e) for e in external_ids if e]
    ids: set[int] = set()
    if exts:
        marks = ",".join("?" for _ in exts)
        for row in conn.execute(
            f"""SELECT id FROM lib2_artists
                 WHERE spotify_id IN ({marks}) OR musicbrainz_id IN ({marks})""",
            (*exts, *exts),
        ):
            ids.add(int(row["id"]))
        wanted_ext = set(exts)
        for row in conn.execute(
            "SELECT id, external_ids FROM lib2_artists "
            "WHERE external_ids IS NOT NULL AND external_ids NOT IN ('', '{}')"
        ):
            try:
                values = {str(v) for v in json.loads(row["external_ids"]).values()}
            except Exception:  # noqa: BLE001
                continue
            if values & wanted_ext:
                ids.add(int(row["id"]))
    if name and not ids:
        for row in conn.execute(
            "SELECT id FROM lib2_artists WHERE LOWER(name) = LOWER(?)",
            (str(name),),
        ):
            ids.add(int(row["id"]))
    return sorted(ids)


def demonitor_lib2_artists_for_removed_watchlist(
    db,
    external_ids: Sequence[str],
    name: Optional[str] = None,
    *,
    profile_id: int = 1,
) -> Dict[str, int]:
    """Demonitor the lib2 artist(s) behind a removed Watchlist artist (§69.1).

    Idempotent: only artists currently ``monitored`` are touched; already
    unmonitored matches are a no-op. Records a ``user_explicit`` unmonitor rule
    (removing an artist from the Watchlist is a deliberate decision about that
    artist), recomputes the wanted projection for the artist's tracks, and
    mirrors the projection so any track that was wanted only via the artist tier
    is pulled from the Wishlist. Does NOT re-enqueue a ``watchlist_remove`` — the
    row is already gone; the forward mirror is not our job here.

    Best-effort by contract: raises nothing the caller can't ignore, but returns
    counts so the endpoint can log/observe.
    """
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    from core.library2.wanted import entity_track_ids, recompute_wanted

    conn = db._get_connection()
    try:
        artist_ids = _match_lib2_artists(conn, external_ids, name)
        if not artist_ids:
            return {"matched": 0, "demonitored": 0}

        demonitored = 0
        for artist_id in artist_ids:
            cur = conn.execute(
                "UPDATE lib2_artists SET monitored=0, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND monitored=1",
                (artist_id,),
            )
            if cur.rowcount:
                record_rule(conn, "artist", artist_id, False, PROVENANCE_USER,
                            profile_id=profile_id)
                demonitored += 1

        if not demonitored:
            conn.commit()
            return {"matched": len(artist_ids), "demonitored": 0}

        track_ids: List[int] = []
        for artist_id in artist_ids:
            track_ids.extend(entity_track_ids(conn, "artist", artist_id))
        track_ids = sorted(set(track_ids))
        recompute_wanted(conn, profile_id=profile_id, track_ids=track_ids)

        # Only tracks whose wanted state is governed by the artist tier can flip
        # when the artist rule flips — mirror just those (owned tracks keep their
        # album/track rule and don't change). The forward artist toggle likewise
        # never churns a track flag; this keeps the reverse edge symmetric.
        mirror_ids: List[int] = []
        if track_ids:
            marks = ",".join("?" for _ in track_ids)
            mirror_ids = [int(r[0]) for r in conn.execute(
                f"""SELECT track_id FROM lib2_wanted_tracks
                     WHERE profile_id=? AND track_id IN ({marks})
                       AND reason LIKE 'artist_rule%'""",
                (int(profile_id), *track_ids))]
        conn.commit()

        pulled = 0
        if mirror_ids:
            from core.library2.wishlist_mirror import mirror_projected_tracks_wishlist
            pulled = mirror_projected_tracks_wishlist(
                db, conn, mirror_ids, profile_id=profile_id, user_initiated=False)
        logger.info(
            "watchlist→library demonitor: %d matched, %d demonitored, %d tracks mirrored",
            len(artist_ids), demonitored, pulled,
        )
        return {"matched": len(artist_ids), "demonitored": demonitored,
                "tracks_mirrored": pulled}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Wanted projection → Wishlist re-assertion (reconcile)
# ---------------------------------------------------------------------------


def sync_watchlist_removal(
    db,
    config_manager,
    descriptor: Optional[Dict[str, Any]],
    *,
    profile_id: int = 1,
) -> Dict[str, int]:
    """Feature-gated, best-effort entry point for the removal endpoints.

    ``descriptor`` is the removed row's identity from
    ``db.get_watchlist_artist_descriptor`` (captured BEFORE the delete). Never
    raises — a reverse-sync hiccup must not fail the user's watchlist removal.
    """
    try:
        if config_manager is not None and \
                config_manager.get("features.library_v2", False) is not True:
            return {"matched": 0, "demonitored": 0}
        if not descriptor:
            return {"matched": 0, "demonitored": 0}
        return demonitor_lib2_artists_for_removed_watchlist(
            db,
            descriptor.get("external_ids") or [],
            descriptor.get("name"),
            profile_id=profile_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("watchlist reverse-sync skipped: %s", e)
        return {"matched": 0, "demonitored": 0}


def _wishlisted_lib2_track_ids(conn, *, profile_id: int) -> List[int]:
    """lib2 track ids currently represented in the legacy Wishlist.

    Library-v2 wishlist rows carry ``lib2_track_id`` in their ``source_info``
    JSON (``wishlist_mirror.track_wishlist_payload``). Parsed in Python because
    the payload is opaque JSON; the ``LIKE`` prefilter keeps it cheap.
    """
    ids: set[int] = set()
    try:
        rows = conn.execute(
            "SELECT source_info FROM wishlist_tracks "
            "WHERE profile_id=? AND source_info LIKE '%library_v2%'",
            (int(profile_id),),
        ).fetchall()
    except Exception:  # noqa: BLE001 — table absent (fresh install / test DB)
        return []
    for row in rows:
        try:
            info = json.loads(row["source_info"] or "{}")
        except Exception:  # noqa: BLE001
            continue
        tid = info.get("lib2_track_id")
        if isinstance(tid, int):
            ids.add(tid)
        elif isinstance(tid, str) and tid.isdigit():
            ids.add(int(tid))
    return sorted(ids)


def reconcile_track_wishlist(
    db,
    *,
    profile_id: int = 1,
    batch: int = 200,
    should_stop: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, int]:
    """Re-assert the authoritative wanted projection into the Wishlist (§69.1).

    Recomputes the full projection, then mirrors two sets through the same
    outbox path the toggles use:

    * every currently-``wanted`` track — re-adds any wanted+missing track whose
      Wishlist entry was downloaded/cleared/aged away (the reported gap: a
      monitored missing track that never re-enters the Wishlist);
    * every existing lib2 track currently in the Wishlist that is no longer
      wanted — prunes stale entries (the "und umgekehrt" half).

    ``user_initiated=False`` so a deliberate user cancel/ignore keeps sticking.
    Returns ``{scanned, wanted, wishlisted, mirrored}``. Does not touch files.
    """
    from core.library2.wanted import recompute_wanted, wanted_track_ids
    from core.library2.wishlist_mirror import mirror_projected_tracks_wishlist

    stats = {"scanned": 0, "wanted": 0, "wishlisted": 0, "mirrored": 0}
    conn = db._get_connection()
    try:
        recompute_wanted(conn, profile_id=profile_id)
        conn.commit()

        wanted = wanted_track_ids(conn, profile_id=profile_id)
        wanted_set = set(wanted)
        stats["wanted"] = len(wanted)

        # Only prune tracks that still exist in lib2 (track_wanted_states raises
        # on unknown ids); orphaned wishlist rows are the delete path's concern.
        wishlisted = _wishlisted_lib2_track_ids(conn, profile_id=profile_id)
        stats["wishlisted"] = len(wishlisted)
        existing = {
            int(r[0]) for r in conn.execute(
                "SELECT id FROM lib2_tracks WHERE id IN ("
                + ",".join("?" for _ in wishlisted) + ")",
                wishlisted,
            )
        } if wishlisted else set()
        prune = [t for t in wishlisted if t in existing and t not in wanted_set]

        target = sorted(wanted_set | set(prune))
        total = len(target)
        for start in range(0, total, max(1, batch)):
            if should_stop and should_stop():
                break
            chunk = target[start:start + max(1, batch)]
            stats["mirrored"] += mirror_projected_tracks_wishlist(
                db, conn, chunk, profile_id=profile_id, user_initiated=False)
            stats["scanned"] += len(chunk)
            if progress:
                progress(stats["scanned"], total)
        logger.info(
            "wishlist reconcile (profile %s): %d wanted, %d wishlisted, "
            "%d mirror ops", profile_id, stats["wanted"], stats["wishlisted"],
            stats["mirrored"],
        )
        return stats
    finally:
        conn.close()


__all__ = [
    "demonitor_lib2_artists_for_removed_watchlist",
    "reconcile_track_wishlist",
    "sync_watchlist_removal",
]
