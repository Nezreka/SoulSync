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
2. Same-title/same-bucket album pairs inside one artist are folded with the
   §62.6-Stufe-3 rules: automatically only when one side is pristine
   (provider-only, unmonitored, trackless) and track counts are compatible —
   its provider ids survive as alternative editions. Anything else becomes a
   ``lib2_release_group_review`` finding (``duplicate_title_unmerged``) for
   the user.

Step 2 deliberately covers *every* artist holding a title twin, not only the
survivors of step 1. An artist merge is one way to end up with two rows for
one release, but it is not the only one: a discography expansion that answers
with a second provider edition, or a legacy re-import landing beside an
already-native row, produces the same pair under a single clean artist. On the
26 July real-DB run that was the entire population — three album twins and 112
files attached to more than one track, in a library whose artists were all
distinct, so the pass never ran and the user never even got the review finding.


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
    "lib2_tracks": "track",
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
        # Tracks are in scope too (L2-007): a Deezer/iTunes wishlist row wrote
        # its numeric track id into ``spotify_id``, which then failed to match
        # the same recording's real library row and left a duplicate wanted item.
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
                  al.spotify_id, al.musicbrainz_id, al.external_ids, al.soul_id,
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


def _artists_with_album_twins(conn: Any) -> List[int]:
    """Artists holding two albums with the same title key and bucket.

    One cheap full scan instead of ``_album_rows_for_artist`` per artist: that
    query carries two correlated sub-selects, and running it for every artist
    of a large library to discover that almost none of them has a twin is the
    kind of idle query flood BR-08 already had to undo once.
    """
    rows = conn.execute(
        """SELECT aa.artist_id, al.title, al.album_type, al.soul_id,
                  al.spotify_id, al.musicbrainz_id, al.external_ids
             FROM lib2_album_artists aa
             JOIN lib2_albums al ON al.id = aa.album_id""").fetchall()
    seen: Dict[int, set] = {}
    twins: set[int] = set()
    for row in rows:
        artist_id = int(row["artist_id"])
        keys = seen.setdefault(artist_id, set())
        for key in _identity_keys(row):
            if key in keys:
                twins.add(artist_id)
            else:
                keys.add(key)
    return sorted(twins)


#: Provider release ids a twin claim may be built on, and the one that may
#: not. Measured over the 23 August production DB, counting how many ids are
#: carried by more than one album row:
#:
#:     musicbrainz  0 groups        soul_id (Hydrabase)   6 groups
#:     audiodb      0 groups        spotify              11 groups
#:     discogs      2 groups        itunes    23 groups / 50 albums
#:     deezer       4 groups
#:
#: iTunes is not evidence of anything here — it hands `EVA 2` and `EVA 4`,
#: `NEON BLADE` and `NEON BLADE 2`, `2000` and `2000 - sped up` one and the
#: same album id — so it is excluded outright rather than merely outvoted
#: (docs §49.14). `soul_id` is Hydrabase's release id, a provider like any
#: other; it is listed because it happens to be accurate here, not because it
#: is ours.
_TRUSTED_RELEASE_ID_SOURCES = (
    "musicbrainz", "audiodb", "discogs", "deezer", "soul_id", "spotify",
)


def _release_ids(album: Any) -> Dict[str, str]:
    """This row's trusted release ids, from wherever they are stored."""
    ids: Dict[str, str] = {}
    try:
        raw = json.loads(album["external_ids"] or "{}")
    except (TypeError, ValueError, IndexError, KeyError):
        raw = {}
    if isinstance(raw, dict):
        for source, value in raw.items():
            key, text = str(source).strip().lower(), str(value).strip()
            if key in _TRUSTED_RELEASE_ID_SOURCES and text:
                ids[key] = text
    for column, key in (("spotify_id", "spotify"),
                        ("musicbrainz_id", "musicbrainz"),
                        ("soul_id", "soul_id")):
        try:
            value = str(album[column] or "").strip()
        except (IndexError, KeyError):
            continue
        if value:
            ids.setdefault(key, value)
    return ids


def _identity_keys(album: Any) -> List[tuple]:
    """Every key under which this row claims to BE a particular release.

    The title key stays: it is what catches the plain same-name twin. Trusted
    provider release ids are added because they survive the two things the
    title key cannot cross — a different title (`Memory Reboot` vs
    `Memory Reboot (Slowed)`) and a different bucket (single vs album).

    Claiming a key only groups two rows so the pair is LOOKED AT.
    :func:`_identity_supports_fold` decides whether anything may happen to
    them automatically.
    """
    keys: List[tuple] = []
    title_key = release_title_key(album["title"])
    if title_key:
        # No identity, no twin — two untitled rows are not evidence.
        keys.append(("title", title_key, _bucket(album["album_type"])))
    for source, value in sorted(_release_ids(album).items()):
        keys.append((source, value))
    return keys


def _shares_title_key(a: Any, b: Any) -> bool:
    key_a = release_title_key(a["title"])
    return bool(key_a) and key_a == release_title_key(b["title"]) \
        and _bucket(a["album_type"]) == _bucket(b["album_type"])


def _identity_supports_fold(a: Any, b: Any) -> bool:
    """Is the id evidence strong enough to merge these two automatically?

    Two trusted ids must agree and none may disagree. One agreement is not
    enough — `XSCAPE` and its Track-by-Track Commentary share nothing but a
    Spotify id, and both are trackless provider stubs, so no other rule would
    stop the commentary disc from being swallowed. One disagreement is
    disqualifying — `Thriller` and `Thriller 40` agree on Spotify and part
    ways on MusicBrainz, Deezer and Discogs.
    """
    ids_a, ids_b = _release_ids(a), _release_ids(b)
    shared = [k for k in ids_a if k in ids_b]
    if any(ids_a[k] != ids_b[k] for k in shared):
        return False
    return len(shared) >= 2


def _identity_groups(albums: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Albums grouped so that sharing ANY identity key puts them together.

    A row can claim two keys at once (its title and its soul id), so the
    groups are the connected components over those claims — not one bucket per
    key, which would report the same pair twice and fold it twice.
    """
    parent: Dict[int, int] = {}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    by_key: Dict[tuple, int] = {}
    for album in albums:
        album_id = int(album["id"])
        parent.setdefault(album_id, album_id)
        for key in _identity_keys(album):
            first = by_key.setdefault(key, album_id)
            union(first, album_id)

    components: Dict[int, List[Dict[str, Any]]] = {}
    for album in albums:
        components.setdefault(find(int(album["id"])), []).append(album)
    return [members for members in components.values() if len(members) > 1]


def fold_duplicate_track_rows(conn: Any) -> Dict[str, int]:
    """Collapse two rows of one album that are the same recording (docs §49.4).

    A row may be removed only when it is a pure placeholder: no active file
    and no legacy binding of its own. That is the provider's own slot sitting
    beside the local row that holds the file — the shape behind "the same song
    twice, once with a track number that makes no sense".

    Anything else becomes a ``lib2_recording_review`` finding. Two rows that
    BOTH hold a file are two real files (a rename that left the old one
    behind); removing either row would orphan audio the user still has, and
    that is a file decision the user makes, not a repair that happens quietly.

    Rows are grouped by shared recording AND equal folded title, so the
    provider ISRC collisions between a studio take and its live cut never
    reach this function. Idempotent; does not commit.
    """
    from core.library2.editions import LIB2_RECORDING_REVIEW_DDL
    from core.library2.recording_links import normalize_title

    cursor = conn.cursor()
    cursor.execute(LIB2_RECORDING_REVIEW_DDL)
    stats = {"tracks_folded": 0, "findings": 0}

    rows = conn.execute(
        """SELECT t.id, t.album_id, t.title, t.legacy_track_id,
                  rt.recording_id,
                  EXISTS(SELECT 1 FROM lib2_track_files f
                          WHERE f.track_id=t.id
                            AND COALESCE(f.file_state,'active')
                                NOT IN ('missing_confirmed','deleted')) AS has_file
             FROM lib2_tracks t
             JOIN lib2_release_tracks rt ON rt.track_id = t.id
            WHERE rt.recording_id IN (
                SELECT rt2.recording_id
                  FROM lib2_release_tracks rt2
                  JOIN lib2_tracks t2 ON t2.id = rt2.track_id
                 GROUP BY rt2.recording_id, t2.album_id
                HAVING COUNT(*) > 1)
            ORDER BY t.id"""
    ).fetchall()

    groups: Dict[tuple, List[Any]] = {}
    for row in rows:
        key = (int(row["album_id"]), int(row["recording_id"]),
               normalize_title(row["title"]))
        groups.setdefault(key, []).append(row)

    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (0 if r["has_file"] else 1,
                                    0 if r["legacy_track_id"] else 1,
                                    int(r["id"])))
        survivor = members[0]
        for duplicate in members[1:]:
            if duplicate["has_file"] or duplicate["legacy_track_id"]:
                cursor.execute(
                    """INSERT OR IGNORE INTO lib2_recording_review(
                           track_id, other_track_id, reason)
                       VALUES(?,?,'duplicate_row_unmerged')""",
                    (int(survivor["id"]), int(duplicate["id"])))
                stats["findings"] += cursor.rowcount or 0
                continue
            _delete_placeholder_track(cursor, int(duplicate["id"]))
            stats["tracks_folded"] += 1
    if stats["tracks_folded"] or stats["findings"]:
        logger.info("Duplicate track rows: %s", stats)
    return stats


def _delete_placeholder_track(cursor: Any, track_id: int) -> None:
    """Remove a fileless, legacy-unbound duplicate row and what hangs off it.

    ``lib2_release_tracks.track_id`` is ``ON DELETE SET NULL``, so the shadow
    row would survive as an unattached position; it is dropped explicitly.
    """
    cursor.execute("DELETE FROM lib2_release_tracks WHERE track_id=?", (track_id,))
    cursor.execute("DELETE FROM lib2_wanted_tracks WHERE track_id=?", (track_id,))
    cursor.execute(
        "DELETE FROM lib2_monitor_rules WHERE entity_type='track' AND entity_id=?",
        (track_id,))
    cursor.execute("DELETE FROM lib2_tracks WHERE id=?", (track_id,))


def _fold_albums_within_artist(conn: Any, cursor: Any, artist_id: int,
                               stats: Dict[str, Any]) -> None:
    from core.library2.mb_reconcile import (
        LIB2_RELEASE_GROUP_REVIEW_DDL, _counts_compatible, _fold_duplicate,
        _is_pristine, _survivor_sort_key)

    cursor.execute(LIB2_RELEASE_GROUP_REVIEW_DDL)
    for members in _identity_groups(_album_rows_for_artist(conn, artist_id)):
        if len(members) < 2:
            continue
        members.sort(key=_survivor_sort_key)
        survivor = members[0]
        for duplicate in members[1:]:
            # A title twin keeps the rule it always had. A pair that only ever
            # met through provider ids has to clear the id evidence first.
            evidence = (_shares_title_key(survivor, duplicate)
                        or _identity_supports_fold(survivor, duplicate))
            if (evidence
                    and _is_pristine(cursor, duplicate)
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

        # Merged artists first (their re-homed albums are twins that did not
        # exist a moment ago), then every other artist a twin scan finds — the
        # album pass is not a merge follow-up, it is its own repair.
        for artist_id in sorted(
                touched_artists.union(_artists_with_album_twins(conn))):
            _fold_albums_within_artist(conn, cursor, artist_id, stats)
        # docs §49.4: the album fold cannot see two rows INSIDE one album.
        # Library-wide and cheap — the candidate query is a grouped index
        # read that finds nothing on a healthy catalogue.
        stats.update(fold_duplicate_track_rows(conn))
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
