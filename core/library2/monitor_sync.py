"""Library-v2 ↔ legacy Watchlist/Wishlist synchronisation (§69.1).

The forward edges (a lib2 monitor toggle mirrors into the legacy Watchlist /
Wishlist) are outbox-backed and live in ``mirror_outbox`` / ``wishlist_mirror``.
They fire only on an explicit toggle, which leaves three gaps this module closes:

1. **Watchlist → Library demonitor** (event-driven). When the user removes an
   artist from the legacy Watchlist (single, batch, or a full clear), the
   matching monitored lib2 artist stays ``monitored`` — the states diverge.
   ``demonitor_lib2_artists_for_removed_watchlist`` flips the matching lib2
   artist(s) back to unmonitored and pulls any now-unwanted tracks from the
   Wishlist. It must be called ONLY from the user-facing removal endpoints,
   never from the forward mirror's own ``remove_artist_from_watchlist`` call
   (that path removes the row BECAUSE lib2 demonitored — re-entering here would
   loop and re-record rules).

2. **Wishlist → Library track demonitor** (event-driven). A user-facing
   single, album, batch or full clear is an explicit unmonitor decision for the
   represented Library-v2 tracks. Internal post-download cleanup deliberately
   bypasses this module so monitoring survives a successful download.

3. **Wanted projection → Wishlist re-assertion** (reconcile). The Wishlist is a
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

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

    Idempotent: records a ``user_explicit`` unmonitor rule even when a stale
    compatibility flag was already off, because the Watchlist removal is the
    authoritative user decision. Records the flag/rule change
    (removing an artist from the Watchlist is a deliberate decision about that
    artist), recomputes the wanted projection for the artist's tracks, and
    mirrors the projection so any track that was wanted only via the artist tier
    is pulled from the Wishlist. A newer ``watchlist_remove`` is enqueued even
    though the row is already gone: it must supersede any older pending add that
    survived a crash or transient DB failure.

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
            demonitored += int(cur.rowcount)
            record_rule(conn, "artist", artist_id, False, PROVENANCE_USER,
                        profile_id=profile_id)

        track_ids: List[int] = []
        for artist_id in artist_ids:
            track_ids.extend(entity_track_ids(conn, "artist", artist_id))
        track_ids = sorted(set(track_ids))
        recompute_wanted(conn, profile_id=profile_id, track_ids=track_ids)

        from core.library2.mirror_outbox import (
            drain,
            enqueue_artist_watchlist,
            enqueue_projected_tracks,
        )
        outbox_ids: List[int] = []
        for artist_id in artist_ids:
            outbox_ids.extend(enqueue_artist_watchlist(
                conn, artist_id, False, profile_id=profile_id,
            ))
        if track_ids:
            # Mirroring every affected projection is intentional: explicit
            # album/track rules remain wanted and are reasserted; artist-tier
            # tracks become removes. The final state is therefore authoritative
            # even when older pending outbox rows are replayed first.
            outbox_ids.extend(enqueue_projected_tracks(
                conn, track_ids, profile_id=profile_id, user_initiated=False,
            ))
        conn.commit()
        if outbox_ids:
            drain(db)
        mirrored = _completed_outbox_count(conn, outbox_ids)
        logger.info(
            "watchlist→library demonitor: %d matched, %d demonitored, %d tracks mirrored",
            len(artist_ids), demonitored, mirrored,
        )
        return {"matched": len(artist_ids), "demonitored": demonitored,
                "tracks_mirrored": mirrored}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Wishlist → Library demonitor (event-driven reverse edge)
# ---------------------------------------------------------------------------


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _provider_ids(value: Any) -> Dict[str, str]:
    return {
        str(source).strip().lower(): str(provider_id).strip()
        for source, provider_id in _as_mapping(value).items()
        if str(source).strip() and str(provider_id).strip()
    }


def _descriptor_lib2_track_ids(
    conn: Any, descriptors: Sequence[Mapping[str, Any]],
) -> List[int]:
    """Resolve removed Wishlist rows to Library-v2 tracks by strong identity."""
    direct_ids: set[int] = set()
    stable_ids: set[str] = set()
    generic_ids: set[str] = set()
    qualified_ids: Dict[str, set[str]] = {}

    def add_qualified(source: Any, provider_id: Any) -> None:
        source_key = str(source or "").strip().lower()
        value = str(provider_id or "").strip()
        if source_key and value and source_key != "library_v2":
            qualified_ids.setdefault(source_key, set()).add(value)

    for descriptor in descriptors:
        if not isinstance(descriptor, Mapping):
            continue
        source_info = _as_mapping(descriptor.get("source_info"))
        track_data = _as_mapping(
            descriptor.get("track_data") or descriptor.get("spotify_data")
        )
        raw_lib2_id = source_info.get("lib2_track_id") or descriptor.get("lib2_track_id")
        try:
            if raw_lib2_id is not None:
                direct_ids.add(int(raw_lib2_id))
        except (TypeError, ValueError):
            pass

        for values in (
            source_info.get("track_provider_ids"),
            track_data.get("provider_ids"),
            track_data.get("external_ids"),
        ):
            for source, provider_id in _provider_ids(values).items():
                add_qualified(source, provider_id)

        raw_id = (
            descriptor.get("spotify_track_id")
            or descriptor.get("track_id")
            or track_data.get("id")
        )
        raw_id = str(raw_id or "").split("::", 1)[0].strip()
        if raw_id.startswith("lib2-track:"):
            stable_ids.add(raw_id.removeprefix("lib2-track:"))
        elif raw_id:
            source = (
                track_data.get("provider")
                or track_data.get("source")
                or descriptor.get("provider")
                or source_info.get("metadata_source")
            )
            if source:
                add_qualified(source, raw_id)
            else:
                generic_ids.add(raw_id)

    matched: set[int] = set()
    if direct_ids:
        marks = ",".join("?" for _ in direct_ids)
        matched.update(int(row[0]) for row in conn.execute(
            f"SELECT id FROM lib2_tracks WHERE id IN ({marks})", sorted(direct_ids),
        ))
    rows = conn.execute(
        "SELECT id, spotify_id, musicbrainz_id, external_ids, isrc, stable_id "
        "FROM lib2_tracks"
    ).fetchall()
    for row in rows:
        if row["stable_id"] and str(row["stable_id"]) in stable_ids:
            matched.add(int(row["id"]))
            continue
        row_ids = _provider_ids(row["external_ids"])
        if row["spotify_id"]:
            row_ids.setdefault("spotify", str(row["spotify_id"]))
        if row["musicbrainz_id"]:
            row_ids.setdefault("musicbrainz", str(row["musicbrainz_id"]))
        if row["isrc"]:
            row_ids.setdefault("isrc", str(row["isrc"]))
        if any(
            row_ids.get(source) in provider_values
            for source, provider_values in qualified_ids.items()
        ) or generic_ids.intersection(row_ids.values()):
            matched.add(int(row["id"]))
    return sorted(matched)


def _completed_outbox_count(conn: Any, outbox_ids: Sequence[int]) -> int:
    if not outbox_ids:
        return 0
    marks = ",".join("?" for _ in outbox_ids)
    row = conn.execute(
        f"SELECT COUNT(*) FROM lib2_mirror_outbox "
        f"WHERE id IN ({marks}) AND status='done'",
        list(outbox_ids),
    ).fetchone()
    return int(row[0]) if row else 0


def demonitor_lib2_tracks_for_removed_wishlist(
    db: Any,
    descriptors: Sequence[Mapping[str, Any]],
    *,
    profile_id: int = 1,
) -> Dict[str, int]:
    """Apply a user-facing Wishlist removal as explicit track unmonitoring.

    The caller must capture descriptors before deleting the Wishlist rows.
    Successful-download cleanup calls the database layer directly and never
    invokes this function, so a downloaded track remains monitored for future
    cutoff upgrades.
    """
    from core.library2.monitor_rules import PROVENANCE_USER, record_rule
    from core.library2.wanted import recompute_wanted

    conn = db._get_connection()
    try:
        track_ids = _descriptor_lib2_track_ids(conn, descriptors)
        if not track_ids:
            return {"matched": 0, "demonitored": 0, "tracks_mirrored": 0}
        marks = ",".join("?" for _ in track_ids)
        cur = conn.execute(
            f"UPDATE lib2_tracks SET monitored=0, updated_at=CURRENT_TIMESTAMP "
            f"WHERE id IN ({marks}) AND monitored=1",
            track_ids,
        )
        demonitored = int(cur.rowcount)
        for track_id in track_ids:
            record_rule(
                conn, "track", track_id, False, PROVENANCE_USER,
                profile_id=profile_id,
            )
        recompute_wanted(conn, profile_id=profile_id, track_ids=track_ids)
        from core.library2.mirror_outbox import drain, enqueue_projected_tracks
        outbox_ids = enqueue_projected_tracks(
            conn,
            track_ids,
            profile_id=profile_id,
            user_initiated=False,
        )
        conn.commit()
        if outbox_ids:
            drain(db)
        mirrored = _completed_outbox_count(conn, outbox_ids)
        logger.info(
            "wishlist→library demonitor: %d matched, %d demonitored, %d mirrors",
            len(track_ids), demonitored, mirrored,
        )
        return {
            "matched": len(track_ids),
            "demonitored": demonitored,
            "tracks_mirrored": mirrored,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Feature-gated route adapters
# ---------------------------------------------------------------------------


def _is_admin_profile(profile_id: int) -> bool:
    try:
        from core.library2 import ADMIN_PROFILE_ID
        return int(profile_id) == int(ADMIN_PROFILE_ID)
    except (TypeError, ValueError):
        return False


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
        if not _is_admin_profile(profile_id):
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


def sync_wishlist_removal(
    db: Any,
    config_manager: Any,
    descriptors: Sequence[Mapping[str, Any]],
    *,
    profile_id: int = 1,
) -> Dict[str, int]:
    """Feature-gated, best-effort adapter for user-facing Wishlist removes."""
    try:
        if config_manager is not None and \
                config_manager.get("features.library_v2", False) is not True:
            return {"matched": 0, "demonitored": 0, "tracks_mirrored": 0}
        if not _is_admin_profile(profile_id) or not descriptors:
            return {"matched": 0, "demonitored": 0, "tracks_mirrored": 0}
        return demonitor_lib2_tracks_for_removed_wishlist(
            db, descriptors, profile_id=profile_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("wishlist reverse-sync skipped: %s", exc)
        return {"matched": 0, "demonitored": 0, "tracks_mirrored": 0}


# ---------------------------------------------------------------------------
# Artist monitoring ↔ Watchlist repair
# ---------------------------------------------------------------------------


def _watchlist_artist_snapshot(
    conn: Any, *, profile_id: int,
) -> tuple[bool, set[str], set[str]]:
    """Return normalized names and provider-id values in the Watchlist."""
    try:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(watchlist_artists)")
        }
        if not columns:
            return False, set(), set()
        id_columns = [
            column for column in (
                "spotify_artist_id",
                "itunes_artist_id",
                "deezer_artist_id",
                "discogs_artist_id",
                "amazon_artist_id",
                "musicbrainz_artist_id",
            )
            if column in columns
        ]
        select = ["artist_name", *id_columns]
        sql = f"SELECT {', '.join(select)} FROM watchlist_artists"
        params: tuple[Any, ...] = ()
        if "profile_id" in columns:
            sql += " WHERE profile_id=?"
            params = (int(profile_id),)
        rows = conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001 - fresh/test DB without legacy tables
        return False, set(), set()
    names = {
        str(row["artist_name"] or "").strip().casefold()
        for row in rows if str(row["artist_name"] or "").strip()
    }
    ids = {
        str(row[column]).strip()
        for row in rows
        for column in id_columns
        if row[column] is not None and str(row[column]).strip()
    }
    return True, names, ids


def reconcile_artist_watchlist(
    db: Any,
    *,
    profile_id: int = 1,
) -> Dict[str, int]:
    """Repair the Artist-monitor ⇄ Watchlist invariant.

    A direct Library-v2 artist decision (``user_explicit``) wins and is
    reasserted into the Watchlist. For imported/default rows without such a
    decision, the Watchlist remains the source of truth; this also clears old
    ``monitored=1`` schema-default drift instead of legitimizing it by adding
    every phantom artist to the Watchlist.
    """
    from core.library2.monitor_rules import PROVENANCE_LEGACY, PROVENANCE_USER, record_rule
    from core.library2.provider_ids import source_ids_from_values
    from core.library2.wanted import entity_track_ids, recompute_wanted

    conn = db._get_connection()
    try:
        watchlist_available, watchlist_names, watchlist_ids = _watchlist_artist_snapshot(
            conn, profile_id=profile_id,
        )
        if not watchlist_available:
            return {
                "scanned": 0,
                "monitor_flags_changed": 0,
                "watchlist_mirrors": 0,
                "track_mirrors": 0,
                "unmirrorable": 0,
                "mirrored": 0,
            }
        rows = conn.execute(
            """SELECT ar.id, ar.name, ar.monitored, ar.spotify_id,
                      ar.musicbrainz_id, ar.external_ids,
                      rule.monitored AS rule_monitored,
                      rule.provenance AS rule_provenance
                 FROM lib2_artists ar
                 LEFT JOIN lib2_monitor_rules rule
                   ON rule.entity_type='artist' AND rule.entity_id=ar.id
                  AND rule.profile_id=?""",
            (int(profile_id),),
        ).fetchall()
        from core.library2.mirror_outbox import (
            drain,
            enqueue_artist_watchlist,
            enqueue_projected_tracks,
        )
        stats = {
            "scanned": len(rows),
            "monitor_flags_changed": 0,
            "watchlist_mirrors": 0,
            "track_mirrors": 0,
            "unmirrorable": 0,
        }
        affected_tracks: set[int] = set()
        outbox_ids: List[int] = []
        for row in rows:
            artist_id = int(row["id"])
            ids = source_ids_from_values(
                spotify_id=row["spotify_id"],
                musicbrainz_id=row["musicbrainz_id"],
                external_ids=row["external_ids"],
            )
            on_watchlist = (
                str(row["name"] or "").strip().casefold() in watchlist_names
                or bool({str(value) for value in ids.values()} & watchlist_ids)
            )
            explicit = row["rule_provenance"] == PROVENANCE_USER
            desired = bool(row["rule_monitored"]) if explicit else on_watchlist

            if bool(row["monitored"]) != desired:
                conn.execute(
                    "UPDATE lib2_artists SET monitored=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (1 if desired else 0, artist_id),
                )
                stats["monitor_flags_changed"] += 1
                affected_tracks.update(entity_track_ids(conn, "artist", artist_id))
            if not explicit:
                record_rule(
                    conn,
                    "artist",
                    artist_id,
                    desired,
                    PROVENANCE_LEGACY,
                    profile_id=profile_id,
                )

            # Explicit V2 intent wins in either direction. Imported/default
            # rows already mirror the Watchlist and need no outgoing op.
            if explicit and desired != on_watchlist:
                created = enqueue_artist_watchlist(
                    conn, artist_id, desired, profile_id=profile_id,
                )
                if created:
                    outbox_ids.extend(created)
                    stats["watchlist_mirrors"] += len(created)
                else:
                    stats["unmirrorable"] += 1

        if affected_tracks:
            track_ids = sorted(affected_tracks)
            recompute_wanted(conn, profile_id=profile_id, track_ids=track_ids)
            track_ops = enqueue_projected_tracks(
                conn, track_ids, profile_id=profile_id, user_initiated=False,
            )
            outbox_ids.extend(track_ops)
            stats["track_mirrors"] = len(track_ops)
        conn.commit()
        if outbox_ids:
            drain(db)
        stats["mirrored"] = _completed_outbox_count(conn, outbox_ids)
        logger.info(
            "artist/watchlist reconcile: %d scanned, %d flags changed, %d mirrors",
            stats["scanned"], stats["monitor_flags_changed"], stats["mirrored"],
        )
        return stats
    finally:
        conn.close()


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
    "demonitor_lib2_tracks_for_removed_wishlist",
    "reconcile_artist_watchlist",
    "reconcile_track_wishlist",
    "sync_watchlist_removal",
    "sync_wishlist_removal",
]
