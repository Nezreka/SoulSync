"""One-shot (idempotent) repair for duplicated Library-v2 artists/albums.

§62.5: three write paths could historically mint a same-named artist twin
(wishlist materialize, `upsert_legacy` before its §62.6-Stufe-4 fix, provider
fragment artists), and every twin turned into an album-duplicate factory
because all album matching is scoped per artist. The write paths are fixed;
this module heals what they already left behind:

1. Group artists by normalized name. A group merges into one survivor when
   no two members carry a DIFFERENT id of the same source (§16.3(b) — that
   would be a genuinely distinct same-named artist). Conflicting groups are
   soft-linked via the §40 alias registry instead, so "Update Discography"
   at least fans out over them.
2. After a merge, same-title/same-bucket album pairs inside the survivor are
   folded with the §62.6-Stufe-3 rules: automatically only when one side is
   pristine (provider-only, unmonitored, trackless) and track counts are
   compatible — its provider ids survive as alternative editions. Anything
   else becomes a ``lib2_release_group_review`` finding
   (``duplicate_title_unmerged``) for the user.

Runs at the end of every legacy import (cheap when there is nothing to do)
and on demand via the maintenance endpoint.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

from .importer import (
    looks_like_foreign_provider_id,
    normalize_name,
    release_title_key,
)

logger = get_logger("library2.dedup_repair")

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_ENTITY_TYPE_BY_TABLE = {
    "lib2_albums": "album",
    "lib2_artists": "artist",
    "lib2_release_editions": "release_edition",
}


def _table_columns(conn: Any, table: str) -> set:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:  # noqa: BLE001
        return set()


def _snapshot_namespace(
    conn: Any, table: str, entity_id: int, value: str,
) -> Optional[str]:
    """Recover an ID namespace only from provider-qualified V2 provenance."""

    entity_type = _ENTITY_TYPE_BY_TABLE.get(table)
    if not entity_type or not _table_columns(conn, "library_provider_snapshots"):
        return None
    rows = conn.execute(
        """SELECT DISTINCT provider FROM library_provider_snapshots
            WHERE entity_type=? AND entity_id=? AND provider_entity_id=?""",
        (entity_type, int(entity_id), value),
    ).fetchall()
    providers = {
        str(row[0]).strip().lower() for row in rows if str(row[0] or "").strip()
    }
    return providers.pop() if len(providers) == 1 else None


def _sanitize_provider_namespaces(conn: Any, cursor: Any) -> int:
    """Clear foreign-shaped (numeric/UUID) values out of spotify_id columns.

    The value is re-homed: UUIDs are MusicBrainz; a value the row already
    carries under another namespace just loses its bogus spotify copy; a
    value bound by one provider snapshot adopts that namespace; anything else
    parks under ``legacy_unknown`` so value-based matching keeps
    working without polluting a real provider namespace. Idempotent. Returns
    the number of rows fixed."""
    fixed = 0
    for lib2_table in _ENTITY_TYPE_BY_TABLE:
        if lib2_table not in ("lib2_albums", "lib2_artists",
                              "lib2_release_editions"):
            continue
        if not _table_columns(conn, lib2_table):
            continue
        has_mb_column = "musicbrainz_id" in _table_columns(conn, lib2_table)
        rows = conn.execute(
            f"SELECT id, spotify_id, external_ids FROM {lib2_table} "
            "WHERE spotify_id IS NOT NULL AND spotify_id != ''").fetchall()
        for row in rows:
            value = str(row["spotify_id"]).strip()
            if not looks_like_foreign_provider_id(value):
                continue
            try:
                ids = json.loads(row["external_ids"] or "{}")
            except (TypeError, ValueError):
                ids = {}
            if not isinstance(ids, dict):
                ids = {}
            ids = {str(k).strip().lower(): str(v).strip()
                   for k, v in ids.items() if str(k).strip() and str(v).strip()}
            namespace = next(
                (src for src, val in ids.items()
                 if val == value and src not in ("spotify", "legacy_unknown")),
                None)
            if namespace is None and _UUID_RE.match(value):
                namespace = "musicbrainz"
            if namespace is None:
                namespace = _snapshot_namespace(conn, lib2_table, row["id"], value)
            if namespace is None:
                namespace = "legacy_unknown"
            if ids.get("spotify") == value:
                ids.pop("spotify")
            ids.setdefault(namespace, value)
            cursor.execute(
                f"UPDATE {lib2_table} SET spotify_id=NULL, external_ids=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(ids, sort_keys=True, separators=(",", ":")),
                 row["id"]))
            if namespace == "musicbrainz" and has_mb_column:
                cursor.execute(
                    f"UPDATE {lib2_table} SET musicbrainz_id=COALESCE("
                    "NULLIF(musicbrainz_id,''), ?) WHERE id=?",
                    (value, row["id"]))
            fixed += 1
    if fixed:
        logger.info("Re-homed %d foreign-shaped spotify_id values", fixed)
    return fixed


def _stored_ids(row: Any) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    try:
        raw = json.loads(row["external_ids"] or "{}")
        if isinstance(raw, dict):
            for source, value in raw.items():
                src = str(source).strip().lower()
                val = str(value).strip()
                if src and val:
                    ids[src] = val
    except (TypeError, ValueError):
        pass
    if row["spotify_id"]:
        ids.setdefault("spotify", str(row["spotify_id"]))
    if row["musicbrainz_id"]:
        ids.setdefault("musicbrainz", str(row["musicbrainz_id"]))
    return ids


def _group_has_conflict(members: List[Any]) -> bool:
    seen: Dict[str, str] = {}
    for member in members:
        for source, value in _stored_ids(member).items():
            if source in seen and seen[source] != value:
                return True
            seen.setdefault(source, value)
    return False


def _survivor_key(member: Any) -> tuple:
    return (
        len(_stored_ids(member)),
        1 if member["canonical_artist_id"] is None else 0,
        -int(member["id"]),
    )


def _merge_artist(cursor: Any, survivor: Any, duplicate: Any) -> None:
    """Re-home everything hanging off the duplicate, merge ids, delete it."""
    survivor_id, duplicate_id = int(survivor["id"]), int(duplicate["id"])

    merged = _stored_ids(survivor)
    for source, value in _stored_ids(duplicate).items():
        merged.setdefault(source, value)
    cursor.execute(
        "UPDATE lib2_artists SET external_ids=?, "
        "spotify_id=COALESCE(NULLIF(spotify_id,''), ?), "
        "musicbrainz_id=COALESCE(NULLIF(musicbrainz_id,''), ?), "
        "image_url=COALESCE(image_url, ?), "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (json.dumps(merged, sort_keys=True, separators=(",", ":")),
         merged.get("spotify"), merged.get("musicbrainz"),
         duplicate["image_url"], survivor_id))

    cursor.execute(
        "UPDATE lib2_albums SET primary_artist_id=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE primary_artist_id=?", (survivor_id, duplicate_id))
    # Credit rows: move where the survivor is not already credited, then drop
    # the leftovers (the UNIQUE pair would collide on a plain UPDATE).
    for table in ("lib2_album_artists", "lib2_track_artists"):
        cursor.execute(
            f"""UPDATE OR IGNORE {table} SET artist_id=?
                 WHERE artist_id=?""", (survivor_id, duplicate_id))
        cursor.execute(f"DELETE FROM {table} WHERE artist_id=?", (duplicate_id,))
    cursor.execute(
        "UPDATE lib2_artists SET canonical_artist_id=? "
        "WHERE canonical_artist_id=?", (survivor_id, duplicate_id))
    cursor.execute(
        "UPDATE OR IGNORE lib2_monitor_rules SET entity_id=? "
        "WHERE entity_type='artist' AND entity_id=?", (survivor_id, duplicate_id))
    cursor.execute(
        "DELETE FROM lib2_monitor_rules WHERE entity_type='artist' AND entity_id=?",
        (duplicate_id,))
    cursor.execute("DELETE FROM lib2_artists WHERE id=?", (duplicate_id,))


def _album_rows_for_artist(conn: Any, artist_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT al.id, al.title, al.album_type, al.origin, al.monitored,
                  al.release_date, al.expected_track_count, al.track_count,
                  al.spotify_id, al.musicbrainz_id, al.external_ids,
                  (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id) AS track_rows,
                  (SELECT COUNT(*) FROM lib2_track_files tf
                    JOIN lib2_tracks t2 ON t2.id = tf.track_id
                   WHERE t2.album_id = al.id) AS file_rows
             FROM lib2_album_artists aa JOIN lib2_albums al ON al.id = aa.album_id
            WHERE aa.artist_id = ?""",
        (artist_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _bucket(album_type: Any) -> str:
    return "single" if str(album_type or "").lower() == "single" else "release"


def _fold_albums_within_artist(conn: Any, cursor: Any, artist_id: int,
                               stats: Dict[str, Any]) -> None:
    from core.library2.mb_reconcile import (
        LIB2_RELEASE_GROUP_REVIEW_DDL, _counts_compatible, _fold_duplicate,
        _is_pristine, _survivor_sort_key)

    cursor.execute(LIB2_RELEASE_GROUP_REVIEW_DDL)
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for album in _album_rows_for_artist(conn, artist_id):
        key = (release_title_key(album["title"]), _bucket(album["album_type"]))
        groups.setdefault(key, []).append(album)
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=_survivor_sort_key)
        survivor = members[0]
        for duplicate in members[1:]:
            if (_is_pristine(cursor, duplicate)
                    and _counts_compatible(survivor, duplicate)):
                _fold_duplicate(cursor, survivor, duplicate)
                stats["albums_folded"] += 1
            else:
                cursor.execute(
                    """INSERT OR IGNORE INTO lib2_release_group_review(
                           artist_id, album_id, other_album_id,
                           release_group_mbid, reason)
                       VALUES(?,?,?, NULL, 'duplicate_title_unmerged')""",
                    (artist_id, survivor["id"], duplicate["id"]))
                stats["album_review"] += cursor.rowcount


def repair_duplicate_artists(database: Any) -> Dict[str, Any]:
    """Fold artist twins by normalized name or shared catalog identity.

    Conflicting same-name groups remain alias-linked. Different display names
    carrying the same provider id are merged only when their other stored ids
    do not conflict — this heals fragments such as ``Odetari w`` that were
    later matched to Odetari's exact Spotify identity.
    """
    stats: Dict[str, Any] = {
        "artists_merged": 0, "alias_linked": 0,
        "albums_folded": 0, "album_review": 0,
    }
    conn = database._get_connection()
    try:
        cursor = conn.cursor()
        # Namespace hygiene FIRST: a fake "spotify" id (really iTunes/Deezer)
        # on one twin would otherwise read as a same-source conflict and
        # block the merge below.
        stats["namespaces_fixed"] = _sanitize_provider_namespaces(conn, cursor)
        rows = conn.execute(
            "SELECT id, name, spotify_id, musicbrainz_id, external_ids, "
            "image_url, canonical_artist_id "
            "FROM lib2_artists").fetchall()
        by_name: Dict[str, List[Any]] = {}
        for row in rows:
            key = normalize_name(row["name"])
            if key:
                by_name.setdefault(key, []).append(row)

        touched_artists: set[int] = set()
        for members in by_name.values():
            if len(members) < 2:
                continue
            if _group_has_conflict(members):
                members.sort(key=_survivor_key, reverse=True)
                canonical = members[0]
                from core.library2.artist_aliases import (
                    AliasLinkError, link_artist_alias)
                for member in members[1:]:
                    if member["canonical_artist_id"] is not None:
                        continue
                    try:
                        link_artist_alias(conn, member["id"], canonical["id"])
                        stats["alias_linked"] += 1
                    except AliasLinkError as link_error:
                        logger.info(
                            "Same-name conflict group %r: could not alias-link "
                            "%s -> %s: %s", canonical["name"], member["id"],
                            canonical["id"], link_error)
                continue
            members.sort(key=_survivor_key, reverse=True)
            survivor = members[0]
            for duplicate in members[1:]:
                _merge_artist(cursor, survivor, duplicate)
                stats["artists_merged"] += 1
                logger.info(
                    "Merged duplicate artist %s into %s (%r)",
                    duplicate["id"], survivor["id"], survivor["name"])
            touched_artists.add(int(survivor["id"]))

        # A spelling/parser fragment can receive the exact same catalog id as
        # the real artist during later enrichment while retaining a different
        # normalized name. Name-only repair can never see that pair. Group a
        # fresh post-merge snapshot by authoritative provider id and fold only
        # conflict-free groups.
        provider_rows = conn.execute(
            "SELECT id, name, spotify_id, musicbrainz_id, external_ids, "
            "image_url, canonical_artist_id "
            "FROM lib2_artists"
        ).fetchall()
        by_provider_id: Dict[tuple[str, str], List[Any]] = {}
        catalog_sources = {"spotify", "musicbrainz", "deezer", "tidal", "qobuz"}
        for row in provider_rows:
            for source, value in _stored_ids(row).items():
                if source in catalog_sources and value:
                    by_provider_id.setdefault((source, value), []).append(row)

        for (source, value), members in by_provider_id.items():
            if len(members) < 2:
                continue
            active_members: List[Any] = []
            for member in members:
                current = conn.execute(
                    "SELECT id, name, spotify_id, musicbrainz_id, external_ids, "
                    "image_url, canonical_artist_id "
                    "FROM lib2_artists WHERE id=?",
                    (member["id"],),
                ).fetchone()
                if current is not None:
                    active_members.append(current)
            if len(active_members) < 2 or _group_has_conflict(active_members):
                continue
            active_members.sort(key=_survivor_key, reverse=True)
            survivor = active_members[0]
            for duplicate in active_members[1:]:
                _merge_artist(cursor, survivor, duplicate)
                stats["artists_merged"] += 1
                logger.info(
                    "Merged catalog-identity artist %s into %s "
                    "(%s=%s, %r -> %r)",
                    duplicate["id"], survivor["id"], source, value,
                    duplicate["name"], survivor["name"],
                )
            touched_artists.add(int(survivor["id"]))

        for artist_id in touched_artists:
            _fold_albums_within_artist(conn, cursor, artist_id, stats)
        conn.commit()
    finally:
        conn.close()
    if stats["albums_folded"]:
        # Deliver any wishlist un-mirrors the folds enqueued (best-effort).
        try:
            from core.library2.mirror_outbox import drain
            drain(database)
        except Exception as drain_error:  # noqa: BLE001
            logger.debug("post-repair outbox drain failed: %s", drain_error)
    return stats


__all__ = ["repair_duplicate_artists"]
