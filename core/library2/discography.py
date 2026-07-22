"""Expand a Library-v2 artist with their full provider discography.

Lidarr shows *everything* an artist ever released (from its metadata provider)
and lets the user monitor any album/EP/single — owned or not. This module does
the same for Library v2: it fetches the artist's discography through SoulSync's
existing source-priority lookup (``core/metadata/discography.py`` — Spotify,
Deezer, iTunes, … with fallback) and persists every release as a ``lib2_albums``
row.

Persistence rules:
- Releases the library already has (matched by provider id or normalized title)
  are *enriched* in place (fill missing ids/artwork/dates/track counts) and keep
  ``origin='library'``.
- Genuinely new releases are inserted with ``origin='discography'`` and
  ``monitored=0`` — pure metadata, no wishlist side effects. Monitoring stays an
  explicit user action (the monitor endpoint materializes the tracklist and
  mirrors to the wishlist). ONE exception: on a *re*-expansion of a monitored
  artist, newly DISCOVERED releases are auto-monitored according to
  ``monitor_new_items``: 'all' accepts every discovery, while 'new' accepts
  only a dated release newer than the newest release known before this sync.
  Their ids come back in ``auto_monitor_album_ids`` so the caller can
  materialize + mirror them. The first expansion never does this — it would
  queue the whole back catalog in one click.
- Releases that disappeared from the provider are pruned again, but only when
  they are still pristine (no tracks, not monitored, origin='discography').
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

from .importer import normalize_name, release_title_key

logger = get_logger("library2.discography")

# Buckets for matching provider releases against library rows whose
# album_type came from the legacy one-track heuristic ('single' vs rest).
_SINGLE_TYPES = {"single"}
_sync_locks: Dict[tuple[str, int], threading.RLock] = {}
_sync_locks_guard = threading.Lock()


def _sync_lock(database, artist_id: int) -> threading.RLock:
    """One in-process refresh sequence per database + artist."""
    database_key = str(getattr(database, "database_path", id(database)))
    key = (database_key, int(artist_id))
    with _sync_locks_guard:
        lock = _sync_locks.get(key)
        if lock is None:
            lock = _sync_locks.setdefault(key, threading.RLock())
        return lock


def _bucket(album_type: str) -> str:
    return "single" if (album_type or "").lower() in _SINGLE_TYPES else "release"


def _normalize_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"album", "single", "ep", "compilation", "live"}:
        return value
    if value in {"appears_on", "appears-on"}:
        return "compilation"
    return "album"


def _existing_release_index(conn, artist_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """Index the artist's current lib2 releases by normalized title."""
    rows = conn.execute(
        """SELECT al.id, al.title, al.album_type, al.origin, al.spotify_id,
                  al.external_ids, al.monitored, al.release_date, al.year,
                  al.expected_track_count, al.track_count,
                  (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id) AS track_rows
             FROM lib2_album_artists aa JOIN lib2_albums al ON al.id = aa.album_id
            WHERE aa.artist_id = ?""",
        (artist_id,),
    ).fetchall()
    index: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        index.setdefault(release_title_key(r["title"]), []).append(dict(r))
    return index


def _external_ids(raw: Any) -> Dict[str, str]:
    import json

    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(source).strip().lower(): str(provider_id).strip()
        for source, provider_id in value.items()
        if str(source).strip() and str(provider_id).strip()
    }


def _merge_external_id_details(raw: Any, source: Optional[str],
                               provider_id: str) -> tuple[str, bool]:
    """Add/refresh one source's id — but never silently clobber an existing,
    DIFFERING id of the same source (G1). A cross-bucket title-fallback match
    (see ``_match_existing``) can hand this a release that legitimately
    belongs to a different catalog entry; overwriting here would poison the
    row's identity (the next tracklist fetch then resolves the wrong
    release). The row's id stays untouched; the second element reports the
    conflict so the caller can keep the alternative id as an edition
    (§62.6 Stufe 2) instead of losing it."""
    import json

    values = _external_ids(raw)
    conflicted = False
    if source and provider_id:
        key = str(source).strip().lower()
        candidate = str(provider_id).strip()
        current = values.get(key)
        if current and current != candidate:
            conflicted = True
            logger.info(
                "discography external-id conflict for source=%s: keeping %s, "
                "recording %s as alternative edition", key, current, candidate,
            )
        else:
            values[key] = candidate
    return json.dumps(values, sort_keys=True, separators=(",", ":")), conflicted


def _merge_external_id(raw: Any, source: Optional[str], provider_id: str) -> str:
    merged, _ = _merge_external_id_details(raw, source, provider_id)
    return merged


def _release_date_key(release_date: Any, year: Any = None) -> Optional[tuple[int, int, int]]:
    """Normalize provider/legacy partial dates for deterministic age ordering."""
    import re

    text = str(release_date or "").strip()
    match = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text)
    if match:
        candidate = (
            int(match.group(1)),
            int(match.group(2) or 1),
            int(match.group(3) or 1),
        )
    else:
        try:
            candidate = (int(year), 1, 1)
        except (TypeError, ValueError):
            return None
    try:
        datetime(*candidate)
    except ValueError:
        return None
    return candidate


def _should_auto_monitor(
    policy: str,
    *,
    eligible_reexpansion: bool,
    release_date: Any,
    year: Any,
    newest_existing: Optional[tuple[int, int, int]],
) -> bool:
    """Apply the single monitor-new-items policy for one discovered release."""
    if not eligible_reexpansion or policy == "none":
        return False
    if policy == "all":
        return True
    if policy != "new" or newest_existing is None:
        return False
    candidate = _release_date_key(release_date, year)
    return candidate is not None and candidate > newest_existing


_CONTENT_FILTER_DEFAULTS = {
    "include_live": False,
    "include_remixes": False,
    "include_acoustic": False,
    "include_compilations": False,
    "include_instrumentals": False,
}


def _artist_content_filters(conn, artist_id: int) -> Dict[str, bool]:
    """The admin watchlist's Live/Remix/Acoustic/Compilation/Instrumental
    opt-ins for one artist, same semantics ``core.watchlist_scanner
    ._should_include_track`` used to enforce before the native discography
    path replaced it (review A3). Best-effort: an artist with no watchlist
    row (e.g. a V2-native import) falls back to the same exclude-by-default
    values a fresh watchlist row would have."""
    try:
        from core.library2.artist_settings import get_artist_settings

        settings = get_artist_settings(conn, artist_id)
        return {
            key: bool(settings.get(key, default))
            for key, default in _CONTENT_FILTER_DEFAULTS.items()
        }
    except Exception:  # noqa: BLE001
        return dict(_CONTENT_FILTER_DEFAULTS)


def _full_release_date_key(release_date: Any) -> Optional[tuple[int, int, int]]:
    """A yyyy-mm-dd key ONLY when the raw date really carries all three parts.

    The §62.3 date+count fallback must never fire on a bare year/month —
    two different same-year releases with equal track counts are common."""
    import re

    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", str(release_date or "").strip())
    if not match:
        return None
    candidate = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    try:
        datetime(*candidate)
    except ValueError:
        return None
    return candidate


def _expected_count(row: Dict[str, Any]) -> Optional[int]:
    value = row.get("expected_track_count")
    if value is None:
        value = row.get("track_count")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _match_existing(index: Dict[str, List[Dict[str, Any]]], *, title: str,
                    album_type: str, provider_id: str, source: Optional[str],
                    release_date: Any = None,
                    track_count: Any = None) -> Optional[Dict[str, Any]]:
    """Find the library row a provider release corresponds to, if any."""
    # 1) Provider-id match beats everything (exact release identity).
    if provider_id:
        for candidates in index.values():
            for row in candidates:
                if source == "spotify" and row["spotify_id"] == provider_id:
                    return row
                external_ids = _external_ids(row["external_ids"])
                if source and external_ids.get(source.lower()) == provider_id:
                    return row
    # 2) Normalized-title match, preferring the same single-vs-release bucket
    #    (legacy imports classify by track count, so ep<->album mismatches are
    #    expected and still count as the same release). release_title_key
    #    (§62.3) folds width/punctuation variants so quote-less provider
    #    spellings of the same title still land on the library row.
    candidates = index.get(release_title_key(title)) or []
    want = _bucket(album_type)
    for row in candidates:
        if _bucket(row["album_type"]) == want:
            return row
    # Cross-bucket fallback (e.g. a legacy single filed as 'album') is only
    # safe when the provider release has no id of its own (G1): a Single
    # that DOES carry its own provider id but matches no library row is
    # genuinely new — not a legacy-misclassified duplicate of the Album
    # sharing its title — and must get its own row instead of a random
    # candidates[0] pick that would then poison that row's external_ids.
    if not provider_id:
        if candidates:
            return candidates[0]
    # 3) §62.6 Stufe 1: same full release day + same track count + same
    #    bucket = the same release group under a different provider release
    #    (JP vs. international edition, re-issue with a translated title).
    #    Titles diverge across scripts/languages, so they get no vote here;
    #    the guard is uniqueness — with two or more candidates (e.g. three
    #    1-track singles dropped the same day) nothing is merged.
    provider_key = _full_release_date_key(release_date)
    try:
        provider_count = int(track_count) if track_count is not None else None
    except (TypeError, ValueError):
        provider_count = None
    if provider_key is None or provider_count is None:
        return None
    day_matches = [
        row
        for rows in index.values()
        for row in rows
        if _bucket(row["album_type"]) == want
        and _full_release_date_key(row["release_date"]) == provider_key
        and _expected_count(row) == provider_count
    ]
    if len(day_matches) == 1:
        return day_matches[0]
    return None


def _resolve_group(database, artist_id: int) -> List[int]:
    from core.library2.artist_aliases import resolve_alias_group
    conn = database._get_connection()
    try:
        return resolve_alias_group(conn, artist_id)
    finally:
        conn.close()


def _aggregate_group_stats(requested_id: int, per_member: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Combine per-member stats into one dict, keyed on the requested member's
    own scalar fields (source/is_complete/...) so its meaning is unambiguous —
    a group can legitimately span different providers per member."""
    base = per_member.get(requested_id) or next(iter(per_member.values()), {})
    aggregate = dict(base)
    for key in ("added", "enriched", "removed", "total"):
        aggregate[key] = sum(int(s.get(key) or 0) for s in per_member.values())
    aggregate["auto_monitor_album_ids"] = [
        album_id
        for s in per_member.values()
        for album_id in (s.get("auto_monitor_album_ids") or [])
    ]
    aggregate["group"] = list(per_member.keys())
    aggregate["members"] = per_member
    return aggregate


def expand_artist_discography(database, artist_id: int) -> Dict[str, Any]:
    """Fetch and persist one artist catalog under the shared sync boundary.

    §40: a standalone artist (no linked aliases — the overwhelming common
    case) behaves exactly as before: one lock, one fetch, the original stats
    shape untouched. A linked alias group (docs §24) fans out instead — every
    member gets its own unchanged fetch+persist call (its own ``lib2_albums``
    rows, no ``primary_artist_id`` reassignment), because a member is only
    ever linked when its provider catalog is NOT reachable through any other
    member's own external ids; a click on any one member must refresh all of
    them or "Update Discography" would keep missing the other alias's
    releases (see docs §24.3 for why the sweep job must NOT do this same
    fan-out per group member — that would multiply fetches by group size).
    """
    members = _resolve_group(database, artist_id)
    if len(members) == 1:
        with _sync_lock(database, artist_id):
            return _expand_artist_discography(database, artist_id)

    per_member: Dict[int, Dict[str, Any]] = {}
    for member_id in members:
        with _sync_lock(database, member_id):
            try:
                per_member[member_id] = _expand_artist_discography(database, member_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Alias-group discography fetch failed for artist %s "
                    "(group requested via %s): %s", member_id, artist_id, e)
                per_member[member_id] = {
                    "added": 0, "enriched": 0, "removed": 0, "total": 0,
                    "auto_monitor_album_ids": [], "error": str(e),
                }
    return _aggregate_group_stats(artist_id, per_member)


def _expand_artist_discography(
    database, artist_id: int, *, defer_auto_monitor: bool = False,
) -> Dict[str, Any]:
    """Fetch + persist the artist's full discography. Returns stats.

    Safe to re-run: existing rows are enriched, not duplicated; pruning only
    touches pristine provider-only rows that vanished from the provider.
    """
    import json

    stats: Dict[str, Any] = {
        "added": 0, "enriched": 0, "removed": 0, "total": 0,
        "source": None, "is_complete": None, "snapshot_changed": None,
        "prune_skipped": False, "auto_monitor_album_ids": [],
    }
    conn = database._get_connection()
    try:
        artist = conn.execute(
            "SELECT id, name, spotify_id, external_ids, quality_profile_id, monitored, monitor_new_items, "
            "discography_synced_at FROM lib2_artists WHERE id=?",
            (artist_id,),
        ).fetchone()
        if not artist:
            raise ValueError(f"Artist {artist_id} not found")

        # Retry interrupted/failed auto-monitor materialization independently
        # of whether the provider catalog still considers the release "new".
        # ``idle`` + no rows also recovers albums stranded by pre-marker builds.
        # G8: joins through lib2_album_artists like _existing_release_index
        # and the prune query below — filtering on primary_artist_id alone
        # would silently skip an album whose primary is a different (linked)
        # artist, leaving it stuck forever.
        retry_rows = conn.execute(
            """SELECT al.id FROM lib2_album_artists aa
                JOIN lib2_albums al ON al.id = aa.album_id
                WHERE aa.artist_id=? AND al.origin='discography'
                  AND al.monitored=1
                  AND (
                      al.tracklist_status='pending'
                      OR (al.tracklist_status='failed' AND (
                          al.tracklist_retry_at IS NULL
                          OR al.tracklist_retry_at <= CURRENT_TIMESTAMP
                      ))
                      OR (al.tracklist_status='idle' AND NOT EXISTS (
                          SELECT 1 FROM lib2_tracks t WHERE t.album_id=al.id
                      ))
                  )
                ORDER BY al.id""",
            (artist_id,),
        ).fetchall()
        stats["auto_monitor_album_ids"] = [row["id"] for row in retry_rows]

        source_artist_ids = {}
        try:
            source_artist_ids.update(json.loads(artist["external_ids"] or "{}"))
        except (TypeError, ValueError):
            logger.warning("Artist %s has invalid external_ids JSON", artist_id)
        if artist["spotify_id"]:
            source_artist_ids["spotify"] = artist["spotify_id"]

        from core.library2.provider_adapters import fetch_artist_discography
        provider_result = fetch_artist_discography(
            artist["name"], source_artist_ids=source_artist_ids)
        if provider_result is None:
            return stats
        source = provider_result.provider
        stats["source"] = source
        stats["total"] = len(provider_result.releases)
        stats["is_complete"] = provider_result.is_complete

        from core.library2.provider_snapshots import record_provider_snapshot
        snapshot_write = record_provider_snapshot(
            conn,
            provider=source,
            entity_type="artist",
            entity_id=artist_id,
            scope="discography",
            provider_entity_id=provider_result.provider_entity_id,
            etag=provider_result.etag,
            provider_version=provider_result.provider_version,
            parser_version=provider_result.parser_version,
            payload=provider_result.snapshot_payload(),
            is_complete=provider_result.is_complete,
            cursor=provider_result.cursor,
            page_count=provider_result.page_count,
        )
        stats["snapshot_changed"] = snapshot_write.payload_changed

        index = _existing_release_index(conn, artist_id)
        # "Monitor new items" applies to releases DISCOVERED after the catalog
        # was first expanded — never to the first expansion itself (that would
        # queue an artist's whole back catalog in one click). The explicit
        # ``discography_synced_at`` marker survives even when every provider
        # row has since been claimed or monitored (the old "any pristine
        # discography row left?" heuristic did not).
        had_discography = artist["discography_synced_at"] is not None or any(
            row["origin"] == "discography"
            for rows in index.values() for row in rows
        )
        monitor_new_policy = artist["monitor_new_items"] or "all"
        eligible_reexpansion = bool(had_discography and artist["monitored"])
        content_filters = (
            _artist_content_filters(conn, artist_id) if eligible_reexpansion else None
        )
        existing_dates = [
            key
            for rows in index.values()
            for row in rows
            if (key := _release_date_key(row["release_date"], row["year"])) is not None
        ]
        # Fixed pre-sync cutoff: provider ordering must not decide whether two
        # releases discovered in the same snapshot count as new.
        newest_existing = max(existing_dates) if existing_dates else None
        from core.library2.profile_lookup import default_quality_profile_id
        fallback_profile = default_quality_profile_id(conn)
        seen_ids: set = set()
        cursor = conn.cursor()

        for release in provider_result.releases:
            title = release.title
            provider_id = release.provider_id
            album_type = _normalize_type(release.album_type)
            release_date = release.release_date
            year = release.year
            track_count = release.track_count or None
            image_url = release.image_url
            spotify_id = provider_id if source == "spotify" else None
            external_ids = json.dumps({source: provider_id}) if (source and provider_id) else "{}"
            auto_monitor_release = _should_auto_monitor(
                monitor_new_policy,
                eligible_reexpansion=eligible_reexpansion,
                release_date=release_date,
                year=year,
                newest_existing=newest_existing,
            )
            if (
                auto_monitor_release
                and content_filters is not None
                and not content_filters["include_compilations"]
            ):
                from core.watchlist_scanner import is_compilation_album

                if is_compilation_album(title):
                    auto_monitor_release = False

            existing = _match_existing(index, title=title, album_type=album_type,
                                       provider_id=provider_id, source=source,
                                       release_date=release_date,
                                       track_count=track_count)
            if existing:
                seen_ids.add(existing["id"])
                merged_external_ids, id_conflict = _merge_external_id_details(
                    existing["external_ids"], source, provider_id)
                cursor.execute(
                    """UPDATE lib2_albums SET
                           spotify_id = COALESCE(spotify_id, ?),
                           image_url = COALESCE(image_url, ?),
                           release_date = COALESCE(release_date, ?),
                           year = COALESCE(year, ?),
                           expected_track_count = MAX(
                               COALESCE(expected_track_count, 0),
                               COALESCE(?, 0)
                           ),
                           external_ids = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (spotify_id, image_url, release_date, year, track_count,
                     merged_external_ids, existing["id"]),
                )
                existing["external_ids"] = merged_external_ids
                if id_conflict:
                    from core.library2.editions import record_alternative_edition
                    record_alternative_edition(
                        cursor, existing["id"], source=source,
                        provider_id=provider_id, title=title,
                        release_date=release_date, track_count=track_count)
                stats["enriched"] += 1
                continue

            cursor.execute(
                """INSERT INTO lib2_albums(primary_artist_id, title, album_type,
                       release_date, year, spotify_id, external_ids, image_url,
                       track_count, expected_track_count, origin, monitored,
                       quality_profile_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?, 'discography', ?, ?)""",
                (artist_id, title, album_type, release_date, year, spotify_id,
                 external_ids, image_url, track_count, track_count,
                 1 if auto_monitor_release and not defer_auto_monitor else 0,
                 artist["quality_profile_id"] or fallback_profile),
            )
            new_id = cursor.lastrowid
            seen_ids.add(new_id)
            if auto_monitor_release:
                if not defer_auto_monitor:
                    cursor.execute(
                        "UPDATE lib2_albums SET tracklist_status='pending', "
                        "tracklist_error=NULL, tracklist_retry_at=NULL WHERE id=?",
                        (new_id,),
                    )
                    from core.library2.monitor_rules import (
                        PROVENANCE_NEW_RELEASE, record_rule)
                    record_rule(conn, "album", new_id, True, PROVENANCE_NEW_RELEASE)
                stats["auto_monitor_album_ids"].append(new_id)
            cursor.execute(
                "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
                "VALUES(?,?, 'primary')", (new_id, artist_id),
            )
            index.setdefault(release_title_key(title), []).append({
                "id": new_id, "title": title, "album_type": album_type,
                "origin": "discography", "spotify_id": spotify_id,
                "external_ids": external_ids, "monitored": 0, "track_rows": 0,
                "release_date": release_date, "year": year,
                "expected_track_count": track_count, "track_count": track_count,
            })
            stats["added"] += 1

        # Prune provider-only rows that vanished from the provider — but never
        # rows the user monitored or that grew tracks/files since.
        if provider_result.is_complete:
            stale = conn.execute(
                """SELECT al.id FROM lib2_album_artists aa
                   JOIN lib2_albums al ON al.id = aa.album_id
                  WHERE aa.artist_id = ? AND al.origin = 'discography'
                    AND al.monitored = 0
                    AND NOT EXISTS (SELECT 1 FROM lib2_tracks t WHERE t.album_id = al.id)""",
                (artist_id,),
            ).fetchall()
            for row in stale:
                if row["id"] in seen_ids:
                    continue
                cursor.execute("DELETE FROM lib2_album_artists WHERE album_id=?", (row["id"],))
                cursor.execute("DELETE FROM lib2_albums WHERE id=?", (row["id"],))
                stats["removed"] += 1
        else:
            stats["prune_skipped"] = True
            logger.warning(
                "Discography snapshot for artist %s from %s is partial; "
                "stale-release pruning suppressed", artist_id, source)

        cursor.execute(
            "UPDATE lib2_artists SET discography_synced_at=CURRENT_TIMESTAMP WHERE id=?",
            (artist_id,))
        conn.commit()
    finally:
        conn.close()
    logger.info("Discography expand for artist %s: +%d new, %d enriched, -%d stale (source=%s)",
                artist_id, stats["added"], stats["enriched"], stats["removed"], stats["source"])
    return stats


def _track_content_excluded(
    track_title: str, album_title: str, filters: Dict[str, bool],
) -> bool:
    """True when ``track_title`` is a Live/Remix/Acoustic/Instrumental
    version the artist's watchlist settings don't opt into (review A3).
    Explicit params (not a closure) — this is called from inside a loop over
    an album's tracks and must not depend on loop-variable capture."""
    from core.watchlist_scanner import (
        is_acoustic_version,
        is_instrumental_version,
        is_live_version,
        is_remix_version,
    )

    if not filters["include_live"] and is_live_version(track_title, album_title):
        return True
    if not filters["include_remixes"] and is_remix_version(track_title, album_title):
        return True
    if not filters["include_acoustic"] and is_acoustic_version(track_title, album_title):
        return True
    if not filters["include_instrumentals"] and is_instrumental_version(
        track_title, album_title,
    ):
        return True
    return False


def auto_monitor_releases(db, config_manager, album_ids: List[int],
                          *, wishlist_profile_id: int = 1) -> int:
    """Make freshly discovered releases genuinely wanted.

    For each album: materialize its provider tracklist into real track rows,
    flip them monitored, and mirror them into the Wishlist (carrying the
    per-item quality profile). Shared by the discography-refresh endpoint and
    the periodic ``monitored_discography_refresh`` repair job so the
    monitor_new_items enforcement can't drift between the two.

    ``wishlist_profile_id`` is the legacy per-user wishlist scope (resolve it
    in request context — background threads have none). Returns the number of
    tracks mirrored. Never raises for individual albums.
    """
    from core.library2.completeness import resolve_tracklist

    mirrored = 0
    filters_cache: Dict[int, Dict[str, bool]] = {}
    conn = db._get_connection()
    try:
        # Batched once up front (not per-album inside the loop below): a
        # sweep over every monitored artist's releases can cover thousands
        # of albums, and title/primary_artist_id never change mid-loop.
        album_meta: Dict[int, Any] = {}
        unique_album_ids = list({int(a) for a in album_ids})
        for start in range(0, len(unique_album_ids), 900):
            chunk = unique_album_ids[start:start + 900]
            marks = ",".join("?" for _ in chunk)
            for row in conn.execute(
                f"SELECT id, title, primary_artist_id FROM lib2_albums "
                f"WHERE id IN ({marks})", chunk,
            ):
                album_meta[row["id"]] = row

        for album_id in album_ids:
            error = None
            try:
                tracks = resolve_tracklist(config_manager, conn, album_id)
            except Exception as e:  # noqa: BLE001
                tracks = None
                error = str(e) or e.__class__.__name__
            if not tracks:
                row = conn.execute(
                    "SELECT tracklist_attempts FROM lib2_albums WHERE id=?",
                    (album_id,),
                ).fetchone()
                if row is None:
                    continue
                attempts = int(row["tracklist_attempts"] or 0) + 1
                delay_minutes = min(
                    5 * (2 ** min(attempts - 1, 9)),
                    24 * 60,
                )
                retry_at = (
                    datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                ).strftime("%Y-%m-%d %H:%M:%S")
                message = (error or "No metadata provider returned a tracklist")[:1000]
                conn.execute(
                    """UPDATE lib2_albums
                          SET tracklist_status='failed', tracklist_attempts=?,
                              tracklist_error=?, tracklist_retry_at=?,
                              updated_at=CURRENT_TIMESTAMP
                        WHERE id=?""",
                    (attempts, message, retry_at, album_id),
                )
                conn.commit()
                logger.warning(
                    "auto-monitor tracklist unavailable for album %s; retry %s at %s: %s",
                    album_id, attempts, retry_at, message,
                )
                continue
            conn.execute(
                """UPDATE lib2_albums
                      SET tracklist_status='ready', tracklist_attempts=0,
                          tracklist_error=NULL, tracklist_retry_at=NULL,
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (album_id,),
            )
            # Monitor provenance (audit P1-13): this album became wanted via
            # the "monitor new items" enforcement, not a user click. The track
            # flips are its cascade projection and stay rule-less — EXCEPT a
            # track the user already explicitly unmonitored (G8): a
            # rematerialize retry must not overturn that, same veto pattern
            # as the bulk monitor-toggle endpoint uses.
            from core.library2.monitor_rules import (
                PROVENANCE_NEW_RELEASE,
                explicitly_unmonitored_track_ids,
                record_rule,
            )
            album_tracks = conn.execute(
                "SELECT id, title FROM lib2_tracks WHERE album_id=?", (album_id,)).fetchall()
            album_track_ids = [r["id"] for r in album_tracks]
            vetoed = explicitly_unmonitored_track_ids(
                conn, album_track_ids, profile_id=wishlist_profile_id)

            # Content-type filters (review A3): a Live/Remix/Acoustic/
            # Instrumental track must not become wanted unless the artist's
            # watchlist settings opt in, same enforcement
            # core.watchlist_scanner._should_include_track used to apply
            # before the native discography path replaced it. Applied here
            # (not at album-insert time) so it also covers the materialize
            # retry path, and via the same "skip the monitored flip" veto
            # pattern used for explicitly-unmonitored tracks just above, so
            # the wanted projection excludes them the same way.
            album_row = album_meta.get(album_id)
            album_title = (album_row["title"] if album_row else "") or ""
            content_artist_id = album_row["primary_artist_id"] if album_row else None
            if content_artist_id is None:
                filters = dict(_CONTENT_FILTER_DEFAULTS)
            elif content_artist_id in filters_cache:
                filters = filters_cache[content_artist_id]
            else:
                filters = filters_cache[content_artist_id] = _artist_content_filters(
                    conn, content_artist_id)

            auto_monitor_ids = [
                r["id"] for r in album_tracks
                if r["id"] not in vetoed
                and not _track_content_excluded(r["title"] or "", album_title, filters)
            ]
            if auto_monitor_ids:
                marks = ",".join("?" for _ in auto_monitor_ids)
                conn.execute(
                    f"UPDATE lib2_tracks SET monitored=1 WHERE id IN ({marks})",
                    auto_monitor_ids,
                )
            record_rule(conn, "album", album_id, True, PROVENANCE_NEW_RELEASE,
                        profile_id=wishlist_profile_id)
            # The freshly materialized tracks inherit the album's new_release
            # rule through the projection's album tier (audit §11.2).
            from core.library2.wanted import recompute_wanted_for_entity
            recompute_wanted_for_entity(conn, "album", album_id,
                                        profile_id=wishlist_profile_id)
            # Commit before mirroring: add_to_wishlist opens its own connection.
            conn.commit()
            track_ids = [r[0] for r in conn.execute(
                "SELECT id FROM lib2_tracks WHERE album_id=?", (album_id,))]
            if track_ids:
                from core.library2.wishlist_mirror import (
                    mirror_projected_tracks_wishlist,
                )
                mirrored += mirror_projected_tracks_wishlist(
                    db,
                    conn,
                    track_ids,
                    profile_id=wishlist_profile_id,
                )
    finally:
        conn.close()
    return mirrored


def repair_track_number_collisions(database, config_manager, artist_id: int) -> List[int]:
    """Re-resolve the tracklist of already-owned albums whose tracks collide
    on (disc_number, track_number) — the §16.3/§17.2 "SWAG" symptom.

    ``auto_monitor_releases`` (and its ``resolve_tracklist`` call, which carries
    the §16.3 title-healing fix) only ever runs for ``auto_monitor_album_ids``:
    newly discovered releases, or discography-origin rows stuck mid-materialize.
    An ``origin='library'`` album imported before the healing fix existed never
    appears in that list, so its corrupted track numbers never get a healing
    pass no matter how often "Update Discography" is clicked. This runs
    ``resolve_tracklist`` directly for such albums instead — deliberately NOT
    through ``auto_monitor_releases``, since that also force-monitors every
    track and stamps a "new_release" provenance rule, which would misrepresent
    why an already-owned album is monitored.
    """
    from core.library2.completeness import resolve_tracklist

    conn = database._get_connection()
    repaired: List[int] = []
    try:
        album_ids = [row["id"] for row in conn.execute(
            """SELECT al.id FROM lib2_albums al
                WHERE al.primary_artist_id=? AND al.origin='library'
                  AND EXISTS (
                      SELECT 1 FROM lib2_tracks t
                       WHERE t.album_id = al.id
                       GROUP BY COALESCE(t.disc_number, 1), t.track_number
                      HAVING COUNT(*) > 1
                  )
                ORDER BY al.id""",
            (artist_id,),
        ).fetchall()]
        for album_id in album_ids:
            try:
                if resolve_tracklist(config_manager, conn, album_id):
                    repaired.append(album_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Track-number collision repair failed for album %s: %s",
                    album_id, e)
    finally:
        conn.close()
    return repaired


def repair_incomplete_library_tracklists(
    database, config_manager, artist_id: int,
) -> List[int]:
    """Materialize canonical rows for underfilled imported releases.

    Discography refresh first raises a stale imported expectation from the
    provider catalog.  This second pass then resolves any library release whose
    persisted rows are still fewer than that expectation.  It intentionally
    calls ``resolve_tracklist`` directly: browsing repair must not auto-monitor
    the missing tracks or stamp a ``new_release`` rule.
    """
    from core.library2.completeness import resolve_tracklist

    conn = database._get_connection()
    repaired: List[int] = []
    try:
        album_ids = [row["id"] for row in conn.execute(
            """SELECT DISTINCT al.id
                  FROM lib2_album_artists aa
                  JOIN lib2_albums al ON al.id=aa.album_id
                 WHERE aa.artist_id=?
                   AND COALESCE(al.origin,'library')='library'
                   AND MAX(COALESCE(al.expected_track_count, 0),
                           COALESCE(al.track_count, 0)) >
                       (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id=al.id)
                 ORDER BY al.id""",
            (artist_id,),
        ).fetchall()]
        for album_id in album_ids:
            try:
                if resolve_tracklist(config_manager, conn, album_id):
                    repaired.append(album_id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Incomplete library tracklist repair failed for album %s: %s",
                    album_id, e,
                )
    finally:
        conn.close()
    return repaired


def _refresh_one_artist(
    database, artist_id: int, config_manager, *, wishlist_profile_id: int,
    auto_monitor: bool = True,
) -> tuple[Dict[str, Any], int]:
    """The original single-artist refresh sequence, unchanged: one lock held
    across fetch + auto-monitor + track-number-collision repair."""
    with _sync_lock(database, artist_id):
        stats = (
            _expand_artist_discography(database, artist_id)
            if auto_monitor
            else _expand_artist_discography(
                database, artist_id, defer_auto_monitor=True)
        )
        album_ids = stats.get("auto_monitor_album_ids") or []
        mirrored = 0
        if album_ids and auto_monitor:
            mirrored = auto_monitor_releases(
                database,
                config_manager,
                album_ids,
                wishlist_profile_id=wishlist_profile_id,
            )
        stats["repaired_track_number_collisions"] = repair_track_number_collisions(
            database, config_manager, artist_id)
        stats["repaired_incomplete_tracklists"] = repair_incomplete_library_tracklists(
            database, config_manager, artist_id)
        return stats, mirrored


def refresh_artist_discography(
    database,
    artist_id: int,
    config_manager,
    *,
    wishlist_profile_id: int = 1,
    auto_monitor: bool = True,
) -> tuple[Dict[str, Any], int]:
    """Run snapshot refresh and its auto-monitor side effects as one sequence.

    §40: a standalone artist runs the exact single-artist sequence as before
    (same lock scope, same return shape). A linked alias group (docs §24)
    fans out — every member runs its own unchanged refresh sequence (fetch +
    auto-monitor + track-number-collision repair), and the results are
    aggregated the same way ``expand_artist_discography`` does.
    """
    members = _resolve_group(database, artist_id)
    if len(members) == 1:
        return _refresh_one_artist(
            database, artist_id, config_manager,
            wishlist_profile_id=wishlist_profile_id, auto_monitor=auto_monitor)

    per_member: Dict[int, Dict[str, Any]] = {}
    total_mirrored = 0
    total_repaired: List[int] = []
    for member_id in members:
        try:
            member_stats, member_mirrored = _refresh_one_artist(
                database, member_id, config_manager,
                wishlist_profile_id=wishlist_profile_id, auto_monitor=auto_monitor)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Alias-group discography refresh failed for artist %s "
                "(group requested via %s): %s", member_id, artist_id, e)
            member_stats, member_mirrored = {
                "added": 0, "enriched": 0, "removed": 0, "total": 0,
                "auto_monitor_album_ids": [], "repaired_track_number_collisions": [],
                "error": str(e),
            }, 0
        per_member[member_id] = member_stats
        total_mirrored += member_mirrored
        total_repaired.extend(member_stats.get("repaired_track_number_collisions") or [])

    stats = _aggregate_group_stats(artist_id, per_member)
    stats["repaired_track_number_collisions"] = total_repaired
    return stats, total_mirrored


__all__ = [
    "auto_monitor_releases",
    "expand_artist_discography",
    "refresh_artist_discography",
]
