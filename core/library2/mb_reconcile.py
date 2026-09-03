"""MusicBrainz release-group reconcile for Library v2 (§62.6 Stufe 3).

The Sawano finding (§62.2): one release group ships as several concrete
provider releases — JP pressing, international pressing, re-issue — with
different provider ids AND different-language titles, so neither id- nor
title-matching can unify them. MusicBrainz already models exactly this
level: both pressings hang under one release group. This module browses the
artist's MB release groups, stamps the RG MBID onto matching ``lib2_albums``
rows, and folds rows that turn out to share one RG:

- automatic fold ONLY when the losing row is pristine (origin='discography',
  unmonitored, no track rows, no preserved user intent) and the track counts
  are compatible — its provider ids survive as alternative editions
  (Stufe 2 mechanism);
- everything else becomes a ``lib2_release_group_review`` finding for the
  user instead of a silent merge (same philosophy as
  ``lib2_recording_review``, ADR-04).

A release group is NOT a release. ``lib2_albums.musicbrainz_id`` holds the
concrete MB *release* id (what a file's ``MUSICBRAINZ_ALBUMID`` tag carries and
what ``lib2_release_editions.musicbrainz_id`` stores); the group id this module
assigns lives in ``lib2_albums.musicbrainz_release_group_id``. They used to
share one column, which cost three things at once: an album the importer had
already tagged looked "assigned" and was skipped, the fold compared ids from
two namespaces that can never match, and the UI's "open on MusicBrainz" link
resolved a group id under ``/release/``. Any group id still sitting in the
release column is moved across on the next run — the browse result names this
artist's groups, so recognising one is exact rather than a guess.

MB is rate-limited (1 req/s, enforced by the client decorator), so this runs
OUTSIDE the discography hot path — the refresh endpoint kicks it as a
background thread; it is also directly callable/testable with an injected
client stub.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

from .importer import release_title_key

logger = get_logger("library2.mb_reconcile")

LIB2_RELEASE_GROUP_REVIEW_DDL = """
CREATE TABLE IF NOT EXISTS lib2_release_group_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id INTEGER NOT NULL,
    album_id INTEGER NOT NULL,
    other_album_id INTEGER NOT NULL,
    release_group_mbid TEXT,              -- NULL: finding not anchored to MB (title-based)
    reason TEXT NOT NULL,                 -- 'shared_release_group_unmerged' | 'duplicate_title_unmerged'
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(album_id, other_album_id, reason)
)
"""

_PAGE_SIZE = 100

# Where a release-GROUP mbid lives. ``lib2_albums.musicbrainz_id`` is the
# concrete release, and the two are not interchangeable (see module docstring).
_RG_COLUMN = "musicbrainz_release_group_id"


def _full_date(value: Any) -> Optional[str]:
    """yyyy-mm-dd ONLY when all three parts are present — a bare year/month
    is far too coarse to identify a release group."""
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(value or "").strip())
    return match.group(1) if match else None


def _artist_mbid(artist_row: Any) -> Optional[str]:
    if artist_row["musicbrainz_id"]:
        return str(artist_row["musicbrainz_id"]).strip() or None
    try:
        external = json.loads(artist_row["external_ids"] or "{}")
    except (TypeError, ValueError):
        return None
    if isinstance(external, dict):
        value = str(external.get("musicbrainz") or "").strip()
        return value or None
    return None


def _default_mb_client() -> Optional[Any]:
    """The raw MusicBrainzClient (has release-group browse). The registry
    hands out the SEARCH adapter, which only wraps it — unwrap when needed."""
    candidate = None
    try:
        from core.metadata.registry import get_musicbrainz_client
        candidate = get_musicbrainz_client()
    except Exception:  # noqa: BLE001
        candidate = None
    if candidate is not None and not hasattr(candidate, "browse_artist_release_groups"):
        candidate = getattr(candidate, "_client", None)
    if candidate is not None and hasattr(candidate, "browse_artist_release_groups"):
        return candidate
    try:
        from core.musicbrainz_client import MusicBrainzClient
        return MusicBrainzClient()
    except Exception:  # noqa: BLE001
        return None


def _fetch_all_release_groups(client: Any, artist_mbid: str) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = client.browse_artist_release_groups(
            artist_mbid, limit=_PAGE_SIZE, offset=offset) or []
        groups.extend(g for g in page if isinstance(g, dict))
        if len(page) < _PAGE_SIZE:
            return groups
        offset += _PAGE_SIZE


def _renamespace_group_ids(cursor: Any, artist_id: int,
                           group_mbids: set) -> int:
    """Move release-GROUP ids out of the two places that hold release ids.

    Three writers put a MusicBrainz id on an album, and they did not agree on
    which entity they meant. The importer and the embedded-tag reconcile store
    the concrete release from a file's ``MUSICBRAINZ_ALBUMID`` tag (promoted
    column + ``external_ids['musicbrainz']``); the discography sync stored what
    MusicBrainz's browse returns, which is the release GROUP; and this module
    used to stamp the group onto the promoted column as well.

    The two cannot be told apart by shape — both are uuids — but they CAN be
    told apart by membership: an id that appears in this artist's browsed
    release-group list is a group id, full stop. Those move to the group
    column; everything else is left exactly where it is.
    """
    if not group_mbids:
        return 0
    rows = cursor.execute(
        """SELECT al.id, al.musicbrainz_id, al.external_ids,
                  al.musicbrainz_release_group_id
             FROM lib2_album_artists aa JOIN lib2_albums al ON al.id = aa.album_id
            WHERE aa.artist_id = ?""",
        (artist_id,),
    ).fetchall()
    moved = 0
    for row in rows:
        external = _external_ids(row["external_ids"])
        promoted = str(row["musicbrainz_id"] or "").strip()
        json_value = external.get("musicbrainz", "")
        group_id = next(
            (value for value in (promoted, json_value) if value in group_mbids), None)
        if not group_id:
            continue
        # Whichever of the two carried the group id loses it; a genuine release
        # id in the other one stays.
        if json_value in group_mbids:
            external.pop("musicbrainz", None)
        cursor.execute(
            f"""UPDATE lib2_albums
                   SET musicbrainz_id=CASE WHEN ? THEN NULL ELSE musicbrainz_id END,
                       external_ids=?,
                       {_RG_COLUMN}=COALESCE(NULLIF({_RG_COLUMN}, ''), ?),
                       updated_at=CURRENT_TIMESTAMP
                 WHERE id=?""",
            (promoted in group_mbids,
             json.dumps(external, sort_keys=True, separators=(",", ":")),
             group_id, row["id"]))
        moved += cursor.rowcount
        logger.info("Album %s: %s is a release group, not a release — moved to %s",
                    row["id"], group_id, _RG_COLUMN)
    return moved


def _album_rows(conn: Any, artist_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT al.id, al.title, al.album_type, al.origin, al.monitored,
                  al.release_date, al.expected_track_count, al.track_count,
                  al.musicbrainz_id, al.musicbrainz_release_group_id,
                  al.spotify_id, al.external_ids,
                  (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id) AS track_rows,
                  (SELECT COUNT(*) FROM lib2_track_files tf
                    JOIN lib2_tracks t2 ON t2.id = tf.track_id
                   WHERE t2.album_id = al.id) AS file_rows
             FROM lib2_album_artists aa JOIN lib2_albums al ON al.id = aa.album_id
            WHERE aa.artist_id = ?""",
        (artist_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _external_ids(raw: Any) -> Dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(source).strip().lower(): str(pid).strip()
        for source, pid in value.items()
        if str(source).strip() and str(pid).strip()
    }


def _expected_count(row: Dict[str, Any]) -> Optional[int]:
    for key in ("expected_track_count", "track_count"):
        value = row.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _match_by_title(album: Dict[str, Any],
                    by_title: Dict[str, List[Dict[str, Any]]]) -> Optional[str]:
    candidates = by_title.get(release_title_key(album["title"])) or []
    unique_ids = {str(g.get("id")) for g in candidates if g.get("id")}
    if len(unique_ids) == 1:
        return next(iter(unique_ids))
    if len(unique_ids) > 1:
        album_type = str(album.get("album_type") or "").lower()
        same_type = {
            str(g.get("id"))
            for g in candidates
            if str(g.get("primary-type") or "").lower() == album_type
        }
        if len(same_type) == 1:
            return next(iter(same_type))
    return None  # ambiguous — leave for the user


def _match_by_date(album: Dict[str, Any],
                   by_date: Dict[str, List[Dict[str, Any]]]) -> Optional[str]:
    """Full-date fallback, only when exactly ONE release group has that day."""
    date = _full_date(album.get("release_date"))
    if not date:
        return None
    day_groups = {str(g.get("id")) for g in (by_date.get(date) or []) if g.get("id")}
    if len(day_groups) == 1:
        return next(iter(day_groups))
    return None


def _has_preserved_intent(cursor: Any, album_id: int) -> bool:
    return cursor.execute(
        """SELECT 1 FROM lib2_monitor_rules
            WHERE entity_type='album' AND entity_id=?
              AND provenance IN ('user_explicit', 'wishlist_import')
            LIMIT 1""",
        (album_id,),
    ).fetchone() is not None


def _machine_monitor_only(cursor: Any, album: Dict[str, Any]) -> bool:
    """Monitoring that only a machine turned on (auto 'new_release' policy,
    legacy import) does not make a row user-owned. A monitored flag WITHOUT
    any rule row has unknown origin and blocks, conservatively."""
    if not album["monitored"]:
        return True
    rules = cursor.execute(
        "SELECT provenance FROM lib2_monitor_rules "
        "WHERE entity_type='album' AND entity_id=?",
        (album["id"],)).fetchall()
    if not rules:
        return False
    return all(str(rule["provenance"]) not in ("user_explicit", "wishlist_import")
               for rule in rules)


def _is_pristine(cursor: Any, album: Dict[str, Any]) -> bool:
    # FILES are the protection criterion, not track rows: an auto-monitored
    # provider row carries a full fileless placeholder tracklist ("0/33") and
    # is still entirely machine-made (§62 real-DB catch).
    return (
        album["origin"] == "discography"
        and int(album.get("file_rows") or 0) == 0
        and _machine_monitor_only(cursor, album)
        and not _has_preserved_intent(cursor, album["id"])
    )


def _survivor_sort_key(album: Dict[str, Any]) -> tuple:
    """Who survives a fold: real FILES first (fileless placeholder tracks
    don't count), then library origin, then track rows, then age."""
    return (
        -(1 if int(album.get("file_rows") or 0) > 0 else 0),
        -(1 if album["origin"] == "library" else 0),
        -(1 if int(album.get("track_rows") or 0) > 0 else 0),
        -(1 if album["monitored"] else 0),
        album["id"],
    )


def _counts_compatible(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    count_a, count_b = _expected_count(a), _expected_count(b)
    return count_a is None or count_b is None or count_a == count_b


def _fold_duplicate(cursor: Any, survivor: Dict[str, Any],
                    duplicate: Dict[str, Any]) -> None:
    """Fold a pristine duplicate row into the survivor: provider ids become
    alternative editions (or fill gaps), its editions re-home, the row goes."""
    from core.library2.editions import record_alternative_edition

    survivor_ids = _external_ids(survivor["external_ids"])
    if survivor["spotify_id"]:
        survivor_ids.setdefault("spotify", str(survivor["spotify_id"]))
    duplicate_ids = _external_ids(duplicate["external_ids"])
    if duplicate["spotify_id"]:
        duplicate_ids.setdefault("spotify", str(duplicate["spotify_id"]))

    adopted = dict(survivor_ids)
    for source, pid in duplicate_ids.items():
        current = adopted.get(source)
        if current is None:
            adopted[source] = pid
        elif current != pid:
            record_alternative_edition(
                cursor, survivor["id"], source=source, provider_id=pid,
                title=duplicate["title"], release_date=duplicate["release_date"],
                track_count=_expected_count(duplicate))
    if adopted != survivor_ids:
        cursor.execute(
            "UPDATE lib2_albums SET external_ids=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (json.dumps(adopted, sort_keys=True, separators=(",", ":")),
             survivor["id"]))
        survivor["external_ids"] = json.dumps(adopted)

    # Re-home any editions the duplicate collected; never a second default.
    cursor.execute(
        "UPDATE lib2_release_editions SET release_group_id=?, is_default=0 "
        "WHERE release_group_id=?", (survivor["id"], duplicate["id"]))
    # Fileless placeholder tracks die with the row — but their wishlist
    # mirrors must be withdrawn first (same outbox pattern as the delete
    # routes, audit P0-04) or the dead row's wanted tracks re-download
    # forever. The caller drains the outbox after committing.
    track_ids = [row["id"] for row in cursor.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=?", (duplicate["id"],))]
    if track_ids:
        from core.library2.mirror_outbox import enqueue_tracks
        enqueue_tracks(cursor.connection, track_ids, False)
        marks = ",".join("?" for _ in track_ids)
        for table, column in (("lib2_wanted_tracks", "track_id"),
                              ("lib2_track_artists", "track_id"),
                              ("lib2_monitor_rules", "entity_id")):
            if table == "lib2_monitor_rules":
                cursor.execute(
                    f"DELETE FROM {table} WHERE entity_type='track' "
                    f"AND {column} IN ({marks})", track_ids)
            else:
                cursor.execute(
                    f"DELETE FROM {table} WHERE {column} IN ({marks})", track_ids)
        cursor.execute(
            "DELETE FROM lib2_tracks WHERE album_id=?", (duplicate["id"],))
    cursor.execute(
        "DELETE FROM lib2_monitor_rules WHERE entity_type='album' AND entity_id=?",
        (duplicate["id"],))
    cursor.execute(
        "DELETE FROM lib2_album_artists WHERE album_id=?", (duplicate["id"],))
    cursor.execute("DELETE FROM lib2_albums WHERE id=?", (duplicate["id"],))


def reconcile_artist_release_groups(database: Any, artist_id: int, *,
                                    client: Any = None) -> Dict[str, Any]:
    """Assign MB release-group MBIDs to one artist's albums and fold rows
    that share a group. Returns stats; safe to re-run (idempotent)."""
    stats: Dict[str, Any] = {
        "assigned": 0, "merged": 0, "review": 0, "renamespaced": 0,
        "release_groups": 0, "skipped": None,
    }
    conn = database._get_connection()
    try:
        conn.execute(LIB2_RELEASE_GROUP_REVIEW_DDL)
        artist = conn.execute(
            "SELECT id, name, musicbrainz_id, external_ids FROM lib2_artists "
            "WHERE id=?", (artist_id,)).fetchone()
        if not artist:
            raise ValueError(f"Artist {artist_id} not found")
        mbid = _artist_mbid(artist)
        if not mbid:
            stats["skipped"] = "no_mbid"
            return stats

        if client is None:
            client = _default_mb_client()
        if client is None:
            stats["skipped"] = "no_client"
            return stats

        groups = _fetch_all_release_groups(client, mbid)
        stats["release_groups"] = len(groups)
        if not groups:
            stats["skipped"] = "no_release_groups"
            return stats

        by_title: Dict[str, List[Dict[str, Any]]] = {}
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        group_mbids = set()
        for group in groups:
            if not group.get("id") or not group.get("title"):
                continue
            group_mbids.add(str(group["id"]))
            by_title.setdefault(release_title_key(group["title"]), []).append(group)
            date = _full_date(group.get("first-release-date"))
            if date:
                by_date.setdefault(date, []).append(group)

        cursor = conn.cursor()
        stats["renamespaced"] = _renamespace_group_ids(cursor, artist_id, group_mbids)
        albums = _album_rows(conn, artist_id)
        # Track counts already claiming each RG (pre-existing + title pass) —
        # the date fallback below must stay count-compatible with them, or a
        # same-day DIFFERENT release (an EP dropped alongside an OST) would
        # steal the day's only release group.
        holder_counts: Dict[str, List[Optional[int]]] = {}
        for album in albums:
            if album[_RG_COLUMN]:
                holder_counts.setdefault(
                    str(album[_RG_COLUMN]), []).append(_expected_count(album))

        def _assign(album: Dict[str, Any], rg_mbid: str) -> None:
            cursor.execute(
                f"UPDATE lib2_albums SET {_RG_COLUMN}=?, updated_at=CURRENT_TIMESTAMP "
                f"WHERE id=? AND ({_RG_COLUMN} IS NULL OR {_RG_COLUMN}='')",
                (rg_mbid, album["id"]))
            if cursor.rowcount:
                stats["assigned"] += 1
                album[_RG_COLUMN] = rg_mbid
                holder_counts.setdefault(rg_mbid, []).append(_expected_count(album))

        for album in albums:
            if album[_RG_COLUMN]:
                continue
            rg_mbid = _match_by_title(album, by_title)
            if rg_mbid:
                _assign(album, rg_mbid)
        for album in albums:
            if album[_RG_COLUMN]:
                continue
            rg_mbid = _match_by_date(album, by_date)
            if not rg_mbid:
                continue
            own_count = _expected_count(album)
            holders = holder_counts.get(rg_mbid) or []
            if any(count is not None and own_count is not None
                   and count != own_count for count in holders):
                continue
            _assign(album, rg_mbid)

        shared: Dict[str, List[Dict[str, Any]]] = {}
        for album in _album_rows(conn, artist_id):
            if album[_RG_COLUMN]:
                shared.setdefault(str(album[_RG_COLUMN]), []).append(album)

        for rg_mbid, members in shared.items():
            if len(members) < 2:
                continue
            members.sort(key=_survivor_sort_key)
            survivor = members[0]
            for duplicate in members[1:]:
                if (_is_pristine(cursor, duplicate)
                        and _counts_compatible(survivor, duplicate)):
                    _fold_duplicate(cursor, survivor, duplicate)
                    stats["merged"] += 1
                    logger.info(
                        "Folded duplicate release %s (%r) into %s (%r) via "
                        "release group %s", duplicate["id"], duplicate["title"],
                        survivor["id"], survivor["title"], rg_mbid)
                else:
                    cursor.execute(
                        """INSERT OR IGNORE INTO lib2_release_group_review(
                               artist_id, album_id, other_album_id,
                               release_group_mbid, reason)
                           VALUES(?,?,?,?, 'shared_release_group_unmerged')""",
                        (artist_id, survivor["id"], duplicate["id"], rg_mbid))
                    stats["review"] += cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    if stats["merged"]:
        # Deliver any wishlist un-mirrors the folds enqueued (best-effort;
        # a later drain retries whatever this one misses).
        try:
            from core.library2.mirror_outbox import drain
            drain(database)
        except Exception as drain_error:  # noqa: BLE001
            logger.debug("post-reconcile outbox drain failed: %s", drain_error)
    return stats


__all__ = [
    "LIB2_RELEASE_GROUP_REVIEW_DDL",
    "reconcile_artist_release_groups",
]
