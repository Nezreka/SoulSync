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
  artist whose ``monitor_new_items`` is 'all'/'new', newly DISCOVERED releases
  are auto-monitored (their ids come back in ``auto_monitor_album_ids`` so the
  caller can materialize + mirror them). The first expansion never does this —
  it would queue the whole back catalog in one click.
- Releases that disappeared from the provider are pruned again, but only when
  they are still pristine (no tracks, not monitored, origin='discography').
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

from .importer import normalize_name

logger = get_logger("library2.discography")

# Buckets for matching provider releases against library rows whose
# album_type came from the legacy one-track heuristic ('single' vs rest).
_SINGLE_TYPES = {"single"}


def _bucket(album_type: str) -> str:
    return "single" if (album_type or "").lower() in _SINGLE_TYPES else "release"


def _normalize_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"album", "single", "ep", "compilation", "live"}:
        return value
    if value in {"appears_on", "appears-on"}:
        return "compilation"
    return "album"


def _fetch_discography_cards(artist_name: str, spotify_id: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """All release cards (albums+eps+singles) via the source-priority lookup."""
    from core.metadata.discography import get_artist_detail_discography
    from core.metadata.lookup import MetadataLookupOptions

    result = get_artist_detail_discography(
        spotify_id or "",
        artist_name=artist_name,
        options=MetadataLookupOptions(limit=200),
    )
    if not result.get("success"):
        return [], result.get("source")
    cards: List[Dict[str, Any]] = []
    for group in ("albums", "eps", "singles"):
        cards.extend(result.get(group) or [])
    return cards, result.get("source")


def _existing_release_index(conn, artist_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """Index the artist's current lib2 releases by normalized title."""
    rows = conn.execute(
        """SELECT al.id, al.title, al.album_type, al.origin, al.spotify_id,
                  al.external_ids, al.monitored,
                  (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id) AS track_rows
             FROM lib2_album_artists aa JOIN lib2_albums al ON al.id = aa.album_id
            WHERE aa.artist_id = ?""",
        (artist_id,),
    ).fetchall()
    index: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        index.setdefault(normalize_name(r["title"]), []).append(dict(r))
    return index


def _match_existing(index: Dict[str, List[Dict[str, Any]]], *, title: str,
                    album_type: str, provider_id: str, source: Optional[str]) -> Optional[Dict[str, Any]]:
    """Find the library row a provider release corresponds to, if any."""
    # 1) Provider-id match beats everything (exact release identity).
    if provider_id:
        for candidates in index.values():
            for row in candidates:
                if source == "spotify" and row["spotify_id"] == provider_id:
                    return row
                ext = row["external_ids"] or ""
                if provider_id and f'"{provider_id}"' in ext:
                    return row
    # 2) Normalized-title match, preferring the same single-vs-release bucket
    #    (legacy imports classify by track count, so ep<->album mismatches are
    #    expected and still count as the same release).
    candidates = index.get(normalize_name(title)) or []
    want = _bucket(album_type)
    for row in candidates:
        if _bucket(row["album_type"]) == want:
            return row
    return candidates[0] if candidates else None


def expand_artist_discography(database, artist_id: int) -> Dict[str, Any]:
    """Fetch + persist the artist's full discography. Returns stats.

    Safe to re-run: existing rows are enriched, not duplicated; pruning only
    touches pristine provider-only rows that vanished from the provider.
    """
    import json

    stats: Dict[str, Any] = {"added": 0, "enriched": 0, "removed": 0, "total": 0,
                             "source": None, "auto_monitor_album_ids": []}
    conn = database._get_connection()
    try:
        artist = conn.execute(
            "SELECT id, name, spotify_id, quality_profile_id, monitored, monitor_new_items, "
            "discography_synced_at FROM lib2_artists WHERE id=?",
            (artist_id,),
        ).fetchone()
        if not artist:
            raise ValueError(f"Artist {artist_id} not found")

        # Retry interrupted/failed auto-monitor materialization independently
        # of whether the provider catalog still considers the release "new".
        # ``idle`` + no rows also recovers albums stranded by pre-marker builds.
        retry_rows = conn.execute(
            """SELECT al.id FROM lib2_albums al
                WHERE al.primary_artist_id=? AND al.origin='discography'
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

        cards, source = _fetch_discography_cards(artist["name"], artist["spotify_id"])
        stats["source"] = source
        stats["total"] = len(cards)
        if not cards:
            return stats

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
        auto_monitor_new = bool(
            had_discography
            and artist["monitored"]
            and (artist["monitor_new_items"] or "all") in ("all", "new")
        )
        from core.library2.profile_lookup import default_quality_profile_id
        fallback_profile = default_quality_profile_id(conn)
        seen_ids: set = set()
        cursor = conn.cursor()

        for card in cards:
            title = str(card.get("title") or card.get("name") or "").strip()
            if not title:
                continue
            provider_id = str(card.get("id") or "").strip()
            album_type = _normalize_type(card.get("album_type"))
            release_date = card.get("release_date")
            year = None
            if card.get("year"):
                try:
                    year = int(str(card["year"])[:4])
                except (TypeError, ValueError):
                    year = None
            track_count = card.get("track_count") or None
            image_url = card.get("image_url")
            spotify_id = provider_id if source == "spotify" else None
            external_ids = json.dumps({source: provider_id}) if (source and provider_id) else "{}"

            existing = _match_existing(index, title=title, album_type=album_type,
                                       provider_id=provider_id, source=source)
            if existing:
                seen_ids.add(existing["id"])
                cursor.execute(
                    """UPDATE lib2_albums SET
                           spotify_id = COALESCE(spotify_id, ?),
                           image_url = COALESCE(image_url, ?),
                           release_date = COALESCE(release_date, ?),
                           year = COALESCE(year, ?),
                           expected_track_count = COALESCE(expected_track_count, ?),
                           external_ids = CASE WHEN external_ids IN ('', '{}')
                                               THEN ? ELSE external_ids END,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (spotify_id, image_url, release_date, year, track_count,
                     external_ids, existing["id"]),
                )
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
                 1 if auto_monitor_new else 0,
                 artist["quality_profile_id"] or fallback_profile),
            )
            new_id = cursor.lastrowid
            seen_ids.add(new_id)
            if auto_monitor_new:
                cursor.execute(
                    "UPDATE lib2_albums SET tracklist_status='pending', "
                    "tracklist_error=NULL, tracklist_retry_at=NULL WHERE id=?",
                    (new_id,),
                )
                stats["auto_monitor_album_ids"].append(new_id)
            cursor.execute(
                "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
                "VALUES(?,?, 'primary')", (new_id, artist_id),
            )
            index.setdefault(normalize_name(title), []).append({
                "id": new_id, "title": title, "album_type": album_type,
                "origin": "discography", "spotify_id": spotify_id,
                "external_ids": external_ids, "monitored": 0, "track_rows": 0,
            })
            stats["added"] += 1

        # Prune provider-only rows that vanished from the provider — but never
        # rows the user monitored or that grew tracks/files since.
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

        cursor.execute(
            "UPDATE lib2_artists SET discography_synced_at=CURRENT_TIMESTAMP WHERE id=?",
            (artist_id,))
        conn.commit()
    finally:
        conn.close()
    logger.info("Discography expand for artist %s: +%d new, %d enriched, -%d stale (source=%s)",
                artist_id, stats["added"], stats["enriched"], stats["removed"], stats["source"])
    return stats


def auto_monitor_releases(db, config_manager, album_ids: List[int],
                          *, wishlist_profile_id: int = 1) -> int:
    """Make freshly discovered releases genuinely wanted.

    For each album: materialize its provider tracklist into real track rows,
    flip them monitored, and mirror them into the Wishlist (carrying the
    per-item quality profile). Shared by the discography-refresh endpoint and
    the periodic ``lib2_discography_refresh`` repair job so the
    monitor_new_items enforcement can't drift between the two.

    ``wishlist_profile_id`` is the legacy per-user wishlist scope (resolve it
    in request context — background threads have none). Returns the number of
    tracks mirrored. Never raises for individual albums.
    """
    from core.library2.completeness import resolve_tracklist
    from core.library2.wishlist_mirror import mirror_tracks_wishlist

    mirrored = 0
    conn = db._get_connection()
    try:
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
            conn.execute("UPDATE lib2_tracks SET monitored=1 WHERE album_id=?", (album_id,))
            # Commit before mirroring: add_to_wishlist opens its own connection.
            conn.commit()
            track_ids = [r[0] for r in conn.execute(
                "SELECT id FROM lib2_tracks WHERE album_id=?", (album_id,))]
            if track_ids:
                mirrored += mirror_tracks_wishlist(
                    db, conn, track_ids, True, profile_id=wishlist_profile_id)
    finally:
        conn.close()
    return mirrored


__all__ = ["expand_artist_discography", "auto_monitor_releases"]
