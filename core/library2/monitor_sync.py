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

from core.library2.sql_util import select_existing_ids
from utils.logging_config import get_logger

logger = get_logger("library2.monitor_sync")


# ---------------------------------------------------------------------------
# Watchlist → Library demonitor (event-driven reverse edge)
# ---------------------------------------------------------------------------


_WATCHLIST_ID_COLUMNS = {
    "spotify_artist_id": "spotify",
    "itunes_artist_id": "itunes",
    "deezer_artist_id": "deezer",
    "discogs_artist_id": "discogs",
    "amazon_artist_id": "amazon",
    "musicbrainz_artist_id": "musicbrainz",
}


def _artist_identity_matches(
    candidate_name: Optional[str],
    candidate_ids: Mapping[str, Any],
    watchlist_name: Optional[str],
    watchlist_ids: Mapping[str, Any],
) -> bool:
    """Namespace-aware artist match with conflict-safe name fallback."""
    from core.library2.importer import normalize_name

    candidate = {
        str(source).lower(): str(value).strip()
        for source, value in (candidate_ids or {}).items() if str(value or "").strip()
    }
    watched = {
        str(source).lower(): str(value).strip()
        for source, value in (watchlist_ids or {}).items() if str(value or "").strip()
    }
    shared = set(candidate) & set(watched)
    if any(candidate[source] == watched[source] for source in shared):
        return True
    # A same-name fallback must never overrule a contradictory strong id in
    # the same namespace (two artists called the same thing, different Spotify ids).
    if any(candidate[source] != watched[source] for source in shared):
        return False
    return bool(
        normalize_name(candidate_name)
        and normalize_name(candidate_name) == normalize_name(watchlist_name)
    )


def _match_lib2_artists(
    conn, external_ids: Any, name: Optional[str]
) -> List[int]:
    """lib2 artist ids matching a removed Watchlist row.

    Strong match first: any of the Watchlist row's provider ids equals a lib2
    artist's ``spotify_id`` / ``musicbrainz_id`` or appears as a value in its
    ``external_ids`` JSON. Only when no provider id matches does the normalized
    name act as a fallback (the same name-equality the legacy manual-match
    bridge accepts, ``web_server._watchlist_row_matches_legacy_artist``).
    """
    from core.library2.provider_ids import source_ids_from_values

    qualified = (
        {str(k).lower(): str(v) for k, v in external_ids.items() if v}
        if isinstance(external_ids, Mapping) else {}
    )
    exts = [str(e) for e in (external_ids or []) if e] if not qualified else []
    ids: set[int] = set()
    rows = conn.execute(
        "SELECT id, name, spotify_id, musicbrainz_id, external_ids FROM lib2_artists"
    ).fetchall()
    if qualified:
        for row in rows:
            candidate_ids = source_ids_from_values(
                spotify_id=row["spotify_id"],
                musicbrainz_id=row["musicbrainz_id"],
                external_ids=row["external_ids"],
            )
            if _artist_identity_matches(row["name"], candidate_ids, name, qualified):
                ids.add(int(row["id"]))
    elif exts:
        # Backward compatibility for descriptors captured by older builds.
        wanted_ext = set(exts)
        for row in rows:
            candidate_ids = source_ids_from_values(
                spotify_id=row["spotify_id"],
                musicbrainz_id=row["musicbrainz_id"],
                external_ids=row["external_ids"],
            )
            if wanted_ext & {str(value) for value in candidate_ids.values()}:
                ids.add(int(row["id"]))
    if name and not ids and not qualified:
        # A9: a bare name match is a weak fallback — only usable when it
        # resolves to exactly ONE lib2 artist. Two rows sharing the removed
        # watchlist name (genuine same-name artists, or an unmerged
        # duplicate — see duplicate-artist-name-ordering) must not both get
        # demonitored/dropped from the Wishlist just because the user
        # intended only one of them.
        name_matches = {
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM lib2_artists WHERE LOWER(name) = LOWER(?)",
                (str(name),),
            )
        }
        if len(name_matches) > 1:
            logger.warning(
                "Watchlist removal name fallback matched %d lib2 artists for "
                "%r — ambiguous, skipping rather than risk demonitoring the "
                "wrong artist", len(name_matches), name,
            )
        else:
            ids |= name_matches
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
    """Resolve each removed Wishlist row without widening composite identity.

    Embedded lib2/stable ids are terminal. Provider fallbacks are accepted
    only when unique, or when the composite album suffix uniquely identifies
    one release of a shared recording.
    """
    matched: set[int] = set()
    rows = conn.execute(
        """SELECT t.id, t.spotify_id, t.musicbrainz_id, t.external_ids,
                  t.isrc, t.stable_id, al.spotify_id AS album_spotify_id,
                  al.musicbrainz_id AS album_musicbrainz_id,
                  al.external_ids AS album_external_ids,
                  al.stable_id AS album_stable_id
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id=t.album_id"""
    ).fetchall()
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
                direct = conn.execute(
                    "SELECT id FROM lib2_tracks WHERE id=?", (int(raw_lib2_id),)
                ).fetchone()
                if direct:
                    matched.add(int(direct[0]))
                    continue
        except (TypeError, ValueError):
            pass

        qualified_ids: Dict[str, set[str]] = {}
        generic_ids: set[str] = set()

        def add_qualified(source: Any, provider_id: Any) -> None:
            source_key = str(source or "").strip().lower()
            value = str(provider_id or "").strip()
            if source_key and value and source_key != "library_v2":
                qualified_ids.setdefault(source_key, set()).add(value)

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
        raw_key = str(raw_id or "").strip()
        raw_id, _, album_identity = raw_key.partition("::")
        raw_id = raw_id.strip()
        album_identity = album_identity.strip()
        if not album_identity:
            album_data = _as_mapping(track_data.get("album"))
            album_candidates = [
                album_data.get("id"),
                source_info.get("album_id"),
                descriptor.get("album_id"),
            ]
            album_candidates.extend(_provider_ids(
                album_data.get("provider_ids") or album_data.get("external_ids")
            ).values())
            album_identity = next(
                (str(value).strip() for value in album_candidates
                 if str(value or "").strip()),
                "",
            )
        if raw_id.startswith("lib2-track:"):
            stable = raw_id.removeprefix("lib2-track:")
            direct = conn.execute(
                "SELECT id FROM lib2_tracks WHERE stable_id=?", (stable,)
            ).fetchone()
            if direct:
                matched.add(int(direct[0]))
                continue
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

        candidates = []
        for row in rows:
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
                candidates.append(row)

        if len(candidates) > 1 and album_identity:
            narrowed = []
            for row in candidates:
                album_ids = set(_provider_ids(row["album_external_ids"]).values())
                album_ids.update(str(value) for value in (
                    row["album_spotify_id"], row["album_musicbrainz_id"],
                    row["album_stable_id"],
                ) if value)
                if album_identity in album_ids:
                    narrowed.append(row)
            candidates = narrowed
        if len(candidates) == 1:
            matched.add(int(candidates[0]["id"]))
        elif len(candidates) > 1:
            logger.warning(
                "Wishlist removal identity %r matched %d Library-v2 tracks; "
                "skipping ambiguous provider fallback", raw_key, len(candidates),
            )
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
        from core.library2.feature import library_v2_enabled
        library_v2_enabled(config_manager)
        if not _is_admin_profile(profile_id):
            return {"matched": 0, "demonitored": 0}
        if not descriptor:
            return {"matched": 0, "demonitored": 0}
        return demonitor_lib2_artists_for_removed_watchlist(
            db,
            descriptor.get("provider_ids") or descriptor.get("external_ids") or [],
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
        from core.library2.feature import library_v2_enabled
        library_v2_enabled(config_manager)
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
) -> tuple[bool, list[dict[str, Any]]]:
    """Return per-row namespaced identities from the Watchlist."""
    try:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(watchlist_artists)")
        }
        if not columns:
            return False, []
        id_columns = [column for column in _WATCHLIST_ID_COLUMNS if column in columns]
        select = ["artist_name", *id_columns]
        sql = f"SELECT {', '.join(select)} FROM watchlist_artists"
        params: tuple[Any, ...] = ()
        if "profile_id" in columns:
            sql += " WHERE profile_id=?"
            params = (int(profile_id),)
        rows = conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001 - fresh/test DB without legacy tables
        return False, []
    entries = [{
        "name": row["artist_name"],
        "provider_ids": {
            _WATCHLIST_ID_COLUMNS[column]: str(row[column]).strip()
            for column in id_columns
            if row[column] is not None and str(row[column]).strip()
        },
    } for row in rows]
    return True, entries


def artist_is_watchlisted(
    conn: Any,
    name: Optional[str],
    provider_ids: Optional[Mapping[str, Any]] = None,
    *,
    profile_id: int = 1,
) -> bool:
    """Return the real Watchlist state for a newly materialized artist.

    This is the insert-time counterpart to ``reconcile_artist_watchlist``.
    Missing legacy tables fail closed to unmonitored; provider identity wins,
    with the same case-insensitive name fallback used by the repair pass.
    """
    available, watchlist_entries = _watchlist_artist_snapshot(
        conn, profile_id=profile_id,
    )
    if not available:
        return False
    return any(
        _artist_identity_matches(
            name, provider_ids or {}, entry["name"], entry["provider_ids"])
        for entry in watchlist_entries
    )


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
    from core.library2.importer import normalize_name
    from core.library2.monitor_rules import PROVENANCE_LEGACY, PROVENANCE_USER, record_rule
    from core.library2.provider_ids import source_ids_from_values
    from core.library2.wanted import entity_track_ids, recompute_wanted

    conn = db._get_connection()
    try:
        watchlist_available, watchlist_entries = _watchlist_artist_snapshot(
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
            on_watchlist = any(
                _artist_identity_matches(
                    row["name"], ids, entry["name"], entry["provider_ids"])
                for entry in watchlist_entries
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
                # Skip the no-op rewrite when the legacy rule already matches
                # (right provenance AND value): a full hourly reconcile
                # otherwise re-upserts EVERY non-explicit artist's rule,
                # bumping updated_at and churning the index for nothing
                # (review Teil B). A differently-provenanced non-user rule
                # (e.g. wishlist_import) is still normalized to legacy, same
                # as before.
                rule_already_matches = (
                    row["rule_provenance"] == PROVENANCE_LEGACY
                    and bool(row["rule_monitored"]) == desired
                )
                if not rule_already_matches:
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

    New rows carry ``lib2_track_id`` in ``source_info``. Older rows can contain
    a bare or album-qualified provider id without that marker, so feed every
    available identity field through the same ambiguity-safe resolver used by
    reverse Wishlist sync. Otherwise each reconcile run re-adds an already
    present legacy row under a second identity.
    """
    try:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(wishlist_tracks)").fetchall()
        }
        wanted_columns = (
            "spotify_track_id", "spotify_data", "source_info",
        )
        selected = [name for name in wanted_columns if name in columns]
        if "spotify_track_id" not in selected:
            return []
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM wishlist_tracks WHERE profile_id=?",
            (int(profile_id),),
        ).fetchall()
    except Exception:  # noqa: BLE001 — table absent (fresh install / test DB)
        return []
    return _descriptor_lib2_track_ids(conn, [dict(row) for row in rows])


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
        recompute_stats = recompute_wanted(conn, profile_id=profile_id)
        conn.commit()
        changed_set = set(recompute_stats.get("changed_track_ids") or [])

        wanted = wanted_track_ids(conn, profile_id=profile_id)
        wanted_set = set(wanted)
        stats["wanted"] = len(wanted)

        # Only prune tracks that still exist in lib2 (track_wanted_states raises
        # on unknown ids); orphaned wishlist rows are the delete path's concern.
        # `wishlisted` scales with the library, so the existence check goes
        # through the chunk-safe helper — a raw `IN (?, …)` over every
        # wishlisted id would blow SQLite's variable limit on a large library.
        wishlisted = _wishlisted_lib2_track_ids(conn, profile_id=profile_id)
        wishlisted_set = set(wishlisted)
        stats["wishlisted"] = len(wishlisted)
        existing = select_existing_ids(conn, "lib2_tracks", wishlisted)
        prune = [t for t in wishlisted if t in existing and t not in wanted_set]

        # Only mirror tracks whose Wishlist membership or projected state
        # actually needs to change:
        #   * wanted tracks NOT yet in the Wishlist — re-add the missing/upgrade-
        #     eligible ones (a wanted track already present is already queued;
        #     add_to_wishlist upserts, so rebuilding its ~6-query payload every
        #     hour just to re-write an unchanged row is the waste review Teil B
        #     flagged — several 100k idle queries/hour at 100k tracks);
        #   * wishlisted tracks no longer wanted — prune;
        #   * wanted tracks already wishlisted whose projection just changed
        #     (e.g. a quality-profile reassignment) — recompute_wanted's
        #     upsert only touches rows it actually changed, so this re-mirrors
        #     exactly the tracks whose cached wishlist payload (quality
        #     target, etc.) would otherwise go stale until the track happens
        #     to leave and re-enter `wanted`, without re-touching every
        #     genuinely-unchanged row.
        adds = [t for t in wanted if t not in wishlisted_set]
        refresh = [t for t in wanted if t in wishlisted_set and t in changed_set]
        target = sorted(set(adds) | set(prune) | set(refresh))
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
    "artist_is_watchlisted",
    "demonitor_lib2_artists_for_removed_watchlist",
    "demonitor_lib2_tracks_for_removed_wishlist",
    "reconcile_artist_watchlist",
    "reconcile_track_wishlist",
    "sync_watchlist_removal",
    "sync_wishlist_removal",
]
