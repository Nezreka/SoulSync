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
from contextlib import closing
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
        # numeric or UUID-shaped only: the startup pass reads this on every boot
        rows = conn.execute(
            f"SELECT id, spotify_id, external_ids FROM {lib2_table} "
            "WHERE spotify_id IS NOT NULL AND spotify_id != '' AND "
            "(spotify_id NOT GLOB '*[^0-9 ]*' OR spotify_id GLOB '*-*-*-*-*' "
            " OR instr(spotify_id, '::') > 0)").fetchall()
        for row in rows:
            stored = str(row["spotify_id"]).strip()
            # a wishlist row key, "<track>::<album>", stored where the track
            # id goes (imports wrote it until Nov 13 2026): the id is the
            # part before the separator
            value = stored.split("::", 1)[0].strip()
            if stored != value and value and not looks_like_foreign_provider_id(value):
                cursor.execute(
                    f"UPDATE {lib2_table} SET spotify_id=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?", (value, row["id"]))
                fixed += 1
                continue
            if not looks_like_foreign_provider_id(value):
                if stored != value:  # nothing before the separator: not an id at all
                    cursor.execute(
                        f"UPDATE {lib2_table} SET spotify_id=NULL, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=?", (row["id"],))
                    fixed += 1
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
            if lib2_table != "lib2_release_editions":
                # its "matched" Spotify attempt was this value: ask again
                from core.library2.match_status import _sync_attempt_ledger
                _sync_attempt_ledger(conn, _ENTITY_TYPE_BY_TABLE[lib2_table],
                                     int(row["id"]), "spotify", None, ask_again=True)
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


# the ids that name one artist on their own. a pair that agrees on two of
# them is one artist even where a lesser catalogue lists the artist twice
# (tidal keeps two pages for bbno$)
_STRONG_ARTIST_SOURCES = frozenset({"musicbrainz", "spotify", "deezer"})


def _id_values(value: str) -> frozenset:
    """One stored id, or the several a row collected ("a, b")."""
    return frozenset(part.strip() for part in str(value).split(",") if part.strip())


def _pair_has_conflict(first: Dict[str, str], second: Dict[str, str]) -> bool:
    agreed, disputed = set(), set()
    for source, value in first.items():
        if source not in second:
            continue
        (agreed if _id_values(value) & _id_values(second[source]) else disputed).add(source)
    if not disputed:
        return False
    return bool(disputed & _STRONG_ARTIST_SOURCES) or len(agreed & _STRONG_ARTIST_SOURCES) < 2


def _group_has_conflict(members: List[Any]) -> bool:
    ids = [_stored_ids(member) for member in members]
    return any(_pair_has_conflict(first, second)
               for index, first in enumerate(ids) for second in ids[index + 1:])


def _reviewed(conn: Any, entity_type: str, *ids: Any) -> bool:
    """one of these is locked against re-matching (#2046). a fold changes what
    a reviewed artist or album holds, so the repair leaves the pair as it is.
    a person running the repair from the page is not held back."""
    from core.library2 import locks
    try:
        return any(locks.blocks(entity_type, entity_id, "match", "Duplicate repair", conn=conn)
                   for entity_id in ids)
    except Exception as exc:  # noqa: BLE001 - no lock tables yet reads as unlocked
        logger.debug("lock check skipped in duplicate repair: %s", exc)
        return False


def _survivor_key(member: Any) -> tuple:
    # a canonical row before an alias: an alias kept over its own canonical
    # ended up pointing at itself and vanished from the artist list
    return (
        1 if member["canonical_artist_id"] is None else 0,
        len(_stored_ids(member)),
        -int(member["id"]),
    )


def _merge_artist(cursor: Any, survivor: Any, duplicate: Any, reason: str = "") -> None:
    """Re-home everything hanging off the duplicate, merge ids, delete it.

    What it moves is read first and filed after (core.library2.artist_merges),
    so the merge can be looked at later and undone (#2073)."""
    from core.library2 import artist_merges
    survivor_id, duplicate_id = int(survivor["id"]), int(duplicate["id"])
    try:
        before = artist_merges.snapshot_before(cursor.connection, survivor_id, duplicate_id)
    except Exception as exc:  # noqa: BLE001 - the journal is never why a merge fails
        logger.debug("artist merge snapshot skipped: %s", exc)
        before = None

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
    # Aliases follow to the survivor's own root, never onto an alias (a chain
    # resolve_alias_group can't follow) and never onto the survivor itself.
    # A survivor that was an alias of the duplicate takes the duplicate's
    # place: its parent becomes the duplicate's parent (usually none).
    survivor_parent = survivor["canonical_artist_id"]
    if survivor_parent is not None and int(survivor_parent) == duplicate_id:
        survivor_parent = duplicate["canonical_artist_id"]
        cursor.execute("UPDATE lib2_artists SET canonical_artist_id=? WHERE id=?",
                       (survivor_parent, survivor_id))
    root = int(survivor_parent) if survivor_parent is not None else survivor_id
    cursor.execute(
        "UPDATE lib2_artists SET canonical_artist_id=? "
        "WHERE canonical_artist_id=? AND id<>?", (root, duplicate_id, root))
    cursor.execute(
        "UPDATE lib2_artists SET canonical_artist_id=NULL WHERE id=? AND canonical_artist_id=id",
        (root,))
    from core.library2.monitor_rules import merge_rules_into
    merge_rules_into(cursor, "artist", duplicate_id, survivor_id)
    # an own library's twin carries that library's server id (#1199); one
    # that collides with the survivor's own mapping goes with the duplicate
    cursor.execute(
        "UPDATE OR IGNORE lib2_media_server_mappings SET entity_id=? "
        "WHERE entity_type='artist' AND entity_id=?", (survivor_id, duplicate_id))
    cursor.execute(
        "DELETE FROM lib2_media_server_mappings WHERE entity_type='artist' AND entity_id=?",
        (duplicate_id,))
    # third #1062 review P2A-6: a finding filed on the twin (a 3.6.1
    # dismissal carried onto the jellyfin copy) follows the survivor. left
    # naming a deleted id, the next scan raised it again on the survivor
    try:
        cursor.execute(
            "UPDATE repair_findings SET entity_id=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE entity_type='artist' AND entity_id=?",
            (f"lib2:{survivor_id}", f"lib2:{duplicate_id}"))
    except Exception as exc:  # noqa: BLE001 - no findings table yet
        logger.debug("artist merge: findings not re-pointed: %s", exc)
    cursor.execute("DELETE FROM lib2_artists WHERE id=?", (duplicate_id,))
    if before is not None:
        artist_merges.record(cursor.connection, survivor, duplicate, before, reason)


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


def _active_server() -> Optional[str]:
    try:
        from core.settings import config_manager
        return config_manager.get_active_media_server()
    except Exception:  # noqa: BLE001
        return None


def fold_shared_file_tracks(conn: Any) -> Dict[str, int]:
    """One file, one track. Idempotent; does not commit.

    The legacy library kept a row set per media server and only ever showed the
    active one; ``clear_server_data`` deliberately spared the others. A library
    that once lived on another server therefore imports the same files twice.
    The active server's track keeps the file, the other row is merged into it
    (media mappings move over), and a release left without files folds into
    the survivor's release like any pristine twin.
    """
    from core.library2.mb_reconcile import _fold_duplicate

    from core.library2.track_files import elect_primary_file, path_identity, path_spellings

    active = _active_server() or ""
    cursor = conn.cursor()
    stats = {"shared_files_folded": 0, "server_albums_folded": 0, "file_rows_folded": 0}
    select = """SELECT f.id AS file_id, f.path, t.id AS track_id, t.album_id,
                  COALESCE(t.server_source, '') AS server_source,
                  COALESCE(f.file_state, 'active') AS file_state
             FROM lib2_track_files f JOIN lib2_tracks t ON t.id = f.track_id
            WHERE COALESCE(f.file_state, 'active') <> 'deleted' AND """
    rows = conn.execute(
        select + """f.path IN (
                  SELECT path FROM lib2_track_files
                   WHERE COALESCE(file_state, 'active') <> 'deleted'
                   GROUP BY path HAVING COUNT(DISTINCT track_id) > 1)"""
    ).fetchall()
    # The same file under its other spelling (H:\Music\a.flac beside
    # H:/Music/a.flac): the scan wrote the first, the import the second, and
    # neither found the other. Only the rows written with a backslash are read,
    # then each one's forward-slash partner through the path index.
    backslashed = conn.execute(select + "instr(f.path, char(92)) > 0").fetchall()
    partners: List[Any] = []
    wanted = sorted({form for row in backslashed for form in path_spellings(row["path"])
                     if form != row["path"]})
    for start in range(0, len(wanted), 500):
        part = wanted[start:start + 500]
        partners.extend(conn.execute(
            select + f"f.path IN ({','.join('?' * len(part))})", part).fetchall())
    seen_files = set()
    groups: Dict[str, List[Any]] = {}
    for row in (*rows, *backslashed, *partners):
        if row["file_id"] in seen_files:
            continue
        seen_files.add(row["file_id"])
        groups.setdefault(path_identity(row["path"]), []).append(row)
    emptied: Dict[int, int] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (r["server_source"] != active, int(r["track_id"]),
                                    r["file_state"] != "active", int(r["file_id"])))
        keep = members[0]
        for dup in members[1:]:
            if dup["track_id"] == keep["track_id"]:
                # the same file twice on one track: one row is enough
                cursor.execute("DELETE FROM lib2_track_files WHERE id=?", (dup["file_id"],))
                elect_primary_file(conn, int(keep["track_id"]))
                stats["file_rows_folded"] += 1
                continue
            cursor.execute("DELETE FROM lib2_track_files WHERE id=?", (dup["file_id"],))
            cursor.execute(
                "UPDATE OR IGNORE lib2_media_server_mappings SET entity_id=? "
                "WHERE entity_type='track' AND entity_id=?", (keep["track_id"], dup["track_id"]))
            if not conn.execute("SELECT 1 FROM lib2_track_files WHERE track_id=?",
                                (dup["track_id"],)).fetchone():
                # plays, rules, wishlist links, edits and matches follow the
                # file to the kept track before the twin row goes
                _carry_folded_track(conn, int(dup["track_id"]), int(keep["track_id"]))
                cursor.execute("DELETE FROM lib2_media_server_mappings "
                               "WHERE entity_type='track' AND entity_id=?", (dup["track_id"],))
                _delete_placeholder_track(cursor, int(dup["track_id"]))
            stats["shared_files_folded"] += 1
            if dup["album_id"] != keep["album_id"]:
                emptied[int(dup["album_id"])] = int(keep["album_id"])
    for dup_album, keep_album in emptied.items():
        artist = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?",
                              (keep_album,)).fetchone()
        albums = {a["id"]: a for a in _album_rows_for_artist(conn, artist[0])} if artist else {}
        cross_artist = dup_album not in albums
        if cross_artist:
            # the other server's copy can sit under its own artist row
            twin_artist = conn.execute(
                "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (dup_album,)).fetchone()
            if twin_artist and twin_artist[0] is not None:
                for row in _album_rows_for_artist(conn, twin_artist[0]):
                    if row["id"] == dup_album:
                        albums[dup_album] = row
        duplicate, survivor = albums.get(dup_album), albums.get(keep_album)
        if cross_artist and survivor and duplicate and not _same_album_title(survivor, duplicate):
            duplicate = None
        if survivor and duplicate and not duplicate["file_rows"]:
            cursor.execute(
                "UPDATE OR IGNORE lib2_media_server_mappings SET entity_id=? "
                "WHERE entity_type='album' AND entity_id=?", (keep_album, dup_album))
            # A wish hung on the twin moves over; only a same-title leftover dies.
            from core.library2.recording_links import normalize_title
            have = {normalize_title(r[0]) for r in conn.execute(
                "SELECT title FROM lib2_tracks WHERE album_id=?", (keep_album,))}
            for track_id, title in conn.execute(
                    "SELECT id, title FROM lib2_tracks WHERE album_id=?", (dup_album,)).fetchall():
                if normalize_title(title) not in have:
                    cursor.execute("UPDATE lib2_tracks SET album_id=? WHERE id=?",
                                   (keep_album, track_id))
            # the release's own rules, snapshots and requests move too
            from core.library2.catalogue_duplicates import _move_references
            _move_references(conn, "album", dup_album, keep_album)
            _move_overrides(conn, "release_group", dup_album, keep_album)
            _fold_duplicate(cursor, survivor, duplicate)
            stats["server_albums_folded"] += 1
    if any(stats.values()):
        logger.info("Shared-file tracks folded: %s", stats)
    return stats


def _years_disagree(a: Any, b: Any) -> bool:
    def year(row):
        value = str(row.get("release_date") or row.get("year") or "")[:4]
        return value if value.isdigit() and value != "0000" else None
    first, second = year(a), year(b)
    return bool(first and second and first != second)


def _release_ids_disagree(a: Any, b: Any) -> bool:
    first, second = _release_ids(a), _release_ids(b)
    return any(source in first and first[source] != value for source, value in second.items())


def _twin_album_rows(conn: Any, artist_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        """SELECT al.id, al.title, al.album_type, al.origin, al.year,
                  al.release_date, al.expected_track_count, al.track_count,
                  al.spotify_id, al.musicbrainz_id, al.external_ids, al.soul_id,
                  COALESCE(al.server_source, '') AS server_source,
                  (SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id) AS track_rows,
                  (SELECT COUNT(*) FROM lib2_track_files tf
                    JOIN lib2_tracks t2 ON t2.id = tf.track_id
                   WHERE t2.album_id = al.id
                     AND COALESCE(tf.file_state, 'active') <> 'deleted') AS file_rows
             FROM lib2_albums al WHERE al.primary_artist_id = ?""", (artist_id,))]


def _twin_tracks(conn: Any, album_id: int) -> List[Dict[str, Any]]:
    from core.library2.recording_links import normalize_title

    tracks = [dict(r) for r in conn.execute(
        "SELECT id, title, COALESCE(disc_number, 1) AS disc, track_number, duration "
        "FROM lib2_tracks WHERE album_id=? ORDER BY id", (album_id,))]
    for track in tracks:
        track["key"] = normalize_title(track["title"] or "")
        track["files"] = [dict(f) for f in conn.execute(
            "SELECT id, path, owner_profile_id FROM lib2_track_files "
            "WHERE track_id=? AND COALESCE(file_state, 'active') <> 'deleted'", (track["id"],))]
    return tracks


def _lengths_disagree(a: Any, b: Any) -> bool:
    try:
        first, second = float(a or 0), float(b or 0)
    except (TypeError, ValueError):
        return False
    return min(first, second) > 0 and abs(first - second) > max(5000, max(first, second) * .1)


def _pair_twin_tracks(keep: List[Dict[str, Any]], twin: List[Dict[str, Any]]) -> List[tuple]:
    """Which track of the kept release each track of the twin is: same title,
    the same number when two share a title, never two lengths apart."""
    by_title: Dict[str, List[Dict[str, Any]]] = {}
    for track in keep:
        by_title.setdefault(track["key"], []).append(track)
    used: set = set()
    pairs = []
    for track in twin:
        candidates = [k for k in by_title.get(track["key"], ())
                      if k["id"] not in used and not _lengths_disagree(k["duration"], track["duration"])]
        placed = [k for k in candidates if k["track_number"] == track["track_number"]]
        exact = [k for k in placed if k["disc"] == track["disc"]]
        match = (exact[0] if len(exact) == 1 else placed[0] if len(placed) == 1
                 else candidates[0] if len(candidates) == 1 else None)
        if match is not None:
            used.add(match["id"])
            pairs.append((match, track))
    return pairs


def _fold_twin_track(conn: Any, keep: Dict[str, Any], twin: Dict[str, Any],
                     resolve, stats: Dict[str, int]) -> None:
    """The twin's listing of the track, onto the kept one. Its file row goes
    when it names the kept file by another path or names nothing on disk; a
    second real file stays, as another copy of the kept track."""
    import os
    from core.library2.track_files import elect_primary_file

    cursor = conn.cursor()
    kept_paths = {str(f["path"] or "") for f in keep["files"]}
    kept_real: Optional[set] = None
    moved = False
    for file in twin["files"]:
        path = str(file["path"] or "")
        second_copy = False
        if path and path not in kept_paths:
            real = resolve(path)
            if real and os.path.isfile(real):
                if kept_real is None:
                    kept_real = {os.path.realpath(r) for r in
                                 (resolve(p) for p in kept_paths if p) if r}
                second_copy = os.path.realpath(real) not in kept_real
        if second_copy:
            cursor.execute(
                "UPDATE lib2_track_files SET track_id=?, is_primary=0, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (keep["id"], file["id"]))
            keep["files"].append(file)
            kept_paths.add(path)
            if kept_real is not None:
                kept_real.add(os.path.realpath(resolve(path)))
            stats["server_twin_copies_kept"] += 1
            moved = True
        else:
            cursor.execute("DELETE FROM lib2_track_files WHERE id=?", (file["id"],))
    if moved:
        elect_primary_file(conn, int(keep["id"]))
    _carry_folded_track(conn, int(twin["id"]), int(keep["id"]))
    cursor.execute("DELETE FROM lib2_track_files WHERE track_id=?", (twin["id"],))
    cursor.execute("DELETE FROM lib2_media_server_mappings "
                   "WHERE entity_type='track' AND entity_id=?", (twin["id"],))
    _delete_placeholder_track(cursor, int(twin["id"]))
    stats["server_twins_folded"] += 1


def fold_server_twins(conn: Any, *, resolve=None, commit=None, should_stop=None) -> Dict[str, int]:
    """One release, one album, however many media servers listed it (#1985).

    3.6.1 kept a row set per media server and showed the active one. A library
    that had been on Plex, Jellyfin and Navidrome imported the same album
    three or four times: ``fold_shared_file_tracks`` only knows two rows as one
    file when the PATH is the same text, and every server names the same file
    its own way (Plex's mount, Navidrome's path under its music folder, a path
    from before a reorganize). An artist with 85 tracks showed 290.

    Two releases of one artist are the same release here when they come from
    different servers, share the title and the single/release bucket, do not
    disagree on year or release ids, and at least half the smaller one's
    tracks pair up by title (and number, where a title repeats). The active
    server's row is kept. Each paired track of the other folds into it: plays,
    rules, wishes, edits and server mappings move, exactly as for a shared
    file. The other row's file is the same file under another name, or a
    listing of something no longer on disk, and goes; when it does resolve to
    a second real file, that file stays as another copy of the kept track.
    Tracks only the other server lists stay where they are.

    Idempotent. ``commit`` is called after each artist that changed, so a big
    library never holds the write lock for the whole pass.
    """
    from core.library2.mb_reconcile import _fold_duplicate
    from core.library2.catalogue_duplicates import _move_references

    if resolve is None:
        from core.library2.paths import resolve_lib2_path
        resolve = resolve_lib2_path
    active = _active_server() or ""
    stats = {"server_twins_folded": 0, "server_twin_copies_kept": 0,
             "server_twin_albums_folded": 0}
    artists = [int(r[0]) for r in conn.execute(
        """SELECT primary_artist_id FROM lib2_albums
            WHERE COALESCE(server_source, '') <> '' AND primary_artist_id IS NOT NULL
            GROUP BY primary_artist_id HAVING COUNT(DISTINCT server_source) > 1
           UNION
           SELECT al.primary_artist_id FROM lib2_albums al
            WHERE al.origin = 'library' AND COALESCE(al.server_source, '') = ''
              AND al.primary_artist_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM lib2_track_files tf
                                JOIN lib2_tracks t ON t.id = tf.track_id
                               WHERE t.album_id = al.id
                                 AND COALESCE(tf.file_state, 'active') <> 'deleted')""")]
    cursor = conn.cursor()
    for artist_id in artists:
        if should_stop and should_stop():
            break
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for album in _twin_album_rows(conn, artist_id):
            # a listing that kept its tracks and lost both its server and its
            # files is what a server's copy looks like once its artist was
            # merged or its files were folded away: a twin like any other
            album["shell"] = (not album["server_source"] and album["origin"] == "library"
                              and not album["file_rows"])
            if album["track_rows"] and (album["server_source"] or album["shell"]):
                groups.setdefault(
                    (release_title_key(album["title"]), _bucket(album["album_type"])), []
                ).append(album)
        changed = False
        for (title_key, _kind), members in groups.items():
            if not title_key or len({a["server_source"] for a in members}) < 2:
                continue
            members.sort(key=lambda a: (a["shell"], a["server_source"] != active,
                                        -int(a["file_rows"] or 0), int(a["id"])))
            survivor = members[0]
            keep_tracks: Optional[List[Dict[str, Any]]] = None
            for twin in members[1:]:
                if (twin["server_source"] == survivor["server_source"]
                        or _years_disagree(survivor, twin)
                        or _release_ids_disagree(survivor, twin)):
                    # two releases of one name on one server are two releases
                    # (#1299), and so are two that disagree about what they are
                    continue
                if _reviewed(conn, "album", survivor["id"], twin["id"]):
                    continue        # someone reviewed one and locked it (#2046)
                if keep_tracks is None:
                    keep_tracks = _twin_tracks(conn, survivor["id"])
                twin_tracks = _twin_tracks(conn, twin["id"])
                owners = lambda tracks: {f["owner_profile_id"] for t in tracks for f in t["files"]}  # noqa: E731
                if not twin["shell"] and owners(keep_tracks) != owners(twin_tracks):
                    continue        # another profile's own library is not a server twin
                pairs = _pair_twin_tracks(keep_tracks, twin_tracks)
                smaller = min(len(keep_tracks), len(twin_tracks))
                if not pairs or len(pairs) * 2 < smaller:
                    continue
                for keep, dup in pairs:
                    _fold_twin_track(conn, keep, dup, resolve, stats)
                changed = True
                if not conn.execute("SELECT 1 FROM lib2_tracks WHERE album_id=? LIMIT 1",
                                    (twin["id"],)).fetchone():
                    cursor.execute(
                        "UPDATE OR IGNORE lib2_media_server_mappings SET entity_id=? "
                        "WHERE entity_type='album' AND entity_id=?", (survivor["id"], twin["id"]))
                    _move_references(conn, "album", twin["id"], survivor["id"])
                    _move_overrides(conn, "release_group", twin["id"], survivor["id"])
                    _fold_duplicate(cursor, survivor, twin)
                    stats["server_twin_albums_folded"] += 1
        if changed and commit is not None:
            commit()
    if any(stats.values()):
        logger.info("Server twins folded: %s", stats)
    return stats


def fold_fileless_twins(conn: Any, track_ids) -> int:
    """A track that just lost its last file, and shares its album, title and
    position with a track that still has one, is that track's leftover row
    (#1985). Two copies of a song in one album are two track rows; removing
    one copy left its row behind as a "missing" track, so an album with 12
    songs read 12 of 24 and the artist's totals were off by every copy that
    had ever been removed. The leftover goes: its plays and server mappings
    move to the track that has the file, its removed-file records travel
    along as history, and its wishes are withdrawn. A track with no such twin
    is a missing track and stays. Does not commit."""
    from core.library2.recording_links import normalize_title

    live = "COALESCE(f.file_state, 'active') NOT IN ('deleted', 'missing_confirmed')"
    cursor = conn.cursor()
    folded = 0
    for track_id in sorted({int(t) for t in track_ids or ()}):
        row = conn.execute(
            f"""SELECT t.id, t.album_id, t.title, t.track_number, COALESCE(t.disc_number, 1) AS disc,
                       COALESCE(t.play_count, 0) AS plays, t.last_played,
                       EXISTS(SELECT 1 FROM lib2_track_files f WHERE f.track_id=t.id AND {live}) AS has_file
                  FROM lib2_tracks t WHERE t.id=?""", (track_id,)).fetchone()
        if row is None or row["has_file"] or row["album_id"] is None or row["track_number"] is None:
            continue
        key = normalize_title(row["title"] or "")
        twins = [t for t in conn.execute(
            f"""SELECT t.id, t.title, COALESCE(t.play_count, 0) AS plays, t.last_played
                  FROM lib2_tracks t
                 WHERE t.album_id=? AND t.id<>? AND t.track_number=?
                   AND COALESCE(t.disc_number, 1)=?
                   AND EXISTS(SELECT 1 FROM lib2_track_files f WHERE f.track_id=t.id AND {live})""",
            (row["album_id"], track_id, row["track_number"], row["disc"])).fetchall()
            if normalize_title(t["title"] or "") == key]
        if len(twins) != 1:
            continue
        keep = twins[0]
        played = max((v for v in (row["last_played"], keep["last_played"]) if v), default=None)
        cursor.execute("UPDATE lib2_tracks SET play_count=?, last_played=? WHERE id=?",
                       (int(row["plays"]) + int(keep["plays"]), played, keep["id"]))
        # the removed files stay on record, under the track that is left
        cursor.execute("UPDATE lib2_track_files SET track_id=?, is_primary=0 WHERE track_id=?",
                       (keep["id"], track_id))
        cursor.execute(
            "UPDATE OR IGNORE lib2_media_server_mappings SET entity_id=? "
            "WHERE entity_type='track' AND entity_id=?", (keep["id"], track_id))
        cursor.execute("DELETE FROM lib2_media_server_mappings "
                       "WHERE entity_type='track' AND entity_id=?", (track_id,))
        # a wish for the leftover is a wish for a song that is right there
        try:
            from core.library2.mirror_outbox import enqueue_tracks
            profiles = {1} | {int(r[0]) for r in conn.execute(
                """SELECT profile_id FROM lib2_wanted_tracks WHERE track_id=?
                   UNION SELECT profile_id FROM lib2_monitor_rules
                    WHERE entity_type='track' AND entity_id=?""",
                (track_id, track_id)) if r[0] is not None}
            for profile_id in sorted(profiles):
                enqueue_tracks(conn, [track_id], False, profile_id=profile_id)
        except Exception as exc:  # noqa: BLE001 - the fold stands without the un-mirror
            logger.debug("un-mirror for a folded leftover track skipped: %s", exc)
        try:
            from core.library2.catalogue_identity import record_redirect
            record_redirect(conn, "track", track_id, int(keep["id"]), {}, {"folded": "fileless_twin"})
        except Exception as exc:  # noqa: BLE001
            logger.debug("redirect for a folded leftover track skipped: %s", exc)
        cursor.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (track_id,))
        _delete_placeholder_track(cursor, track_id)
        folded += 1
    if folded:
        logger.info("Folded %d leftover track row(s) into the copy that kept its file", folded)
    return folded


def _move_overrides(conn: Any, entity_type: str, source_id: int, target_id: int) -> None:
    """a hand edit on the folded row moves to the kept one (the kept row's
    own edit of the same field wins)"""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='lib2_metadata_overrides'").fetchone():
        conn.execute("UPDATE OR IGNORE lib2_metadata_overrides SET entity_id=? "
                     "WHERE entity_type=? AND entity_id=?", (target_id, entity_type, source_id))
        conn.execute("DELETE FROM lib2_metadata_overrides WHERE entity_type=? AND entity_id=?",
                     (entity_type, source_id))


def _carry_folded_track(conn: Any, dup_id: int, keep_id: int) -> None:
    """what hangs off a twin track, onto the track that kept the file: the
    catalogue merge's own carry (rules, mappings, plays history, manual
    matches, stash), plus play counts, wishlist links, edits and a redirect"""
    from core.library2.catalogue_duplicates import _move_references
    from core.library2.catalogue_identity import consolidate_merged_wishlist, record_redirect
    rows = {int(r["id"]): dict(r) for r in conn.execute(
        "SELECT * FROM lib2_tracks WHERE id IN (?,?)", (dup_id, keep_id))}
    source, target = rows.get(dup_id), rows.get(keep_id)
    if not source or not target:
        return
    # two servers counted their own plays; one server's two rows shared a counter
    shared = (source.get("server_source") == target.get("server_source")
              and source.get("server_id") and source.get("server_id") == target.get("server_id"))
    counts = [int(source.get("play_count") or 0), int(target.get("play_count") or 0)]
    played = max((v for v in (source.get("last_played"), target.get("last_played")) if v), default=None)
    conn.execute("UPDATE lib2_tracks SET play_count=?, last_played=? WHERE id=?",
                 (max(counts) if shared else sum(counts), played, keep_id))
    _move_references(conn, "track", dup_id, keep_id)
    _move_overrides(conn, "track", dup_id, keep_id)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='wishlist_tracks'").fetchone():
        try:
            consolidate_merged_wishlist(conn, [(source, target)], source["album_id"], target["album_id"])
        except ValueError:
            # two quality profiles asked for it: keep both rows, both naming the kept track
            conn.execute(
                "UPDATE wishlist_tracks SET source_info=json_set(source_info,'$.lib2_track_id',?) "
                "WHERE json_valid(source_info) AND "
                "CAST(json_extract(source_info,'$.lib2_track_id') AS INTEGER)=?", (keep_id, dup_id))
    record_redirect(conn, "track", dup_id, keep_id, {}, {"folded": "shared_file"})


def _same_album_title(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    from core.library2.recording_links import normalize_title
    return normalize_title(a.get("title") or "") == normalize_title(b.get("title") or "")


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

        def _takes(home: Dict[str, Any], duplicate: Dict[str, Any]) -> bool:
            # A title twin keeps the rule it always had. A pair that only ever
            # met through provider ids has to clear the id evidence first.
            evidence = (_shares_title_key(home, duplicate)
                        or _identity_supports_fold(home, duplicate))
            return bool(evidence
                        and _counts_compatible(home, duplicate)
                        and not _reviewed(conn, "album", home["id"], duplicate["id"]))

        for duplicate in members[1:]:
            # The survivor is asked first, then every other copy with files.
            # An artist can own a release twice (a stray track of it filed in
            # another folder is its own album on the server, "1/22"), and the
            # provider's stub then has two rows it could be. Asking only the
            # first left the stub standing beside the complete album, wanted,
            # because the stray copy's track count was one off.
            home = None
            if _is_pristine(cursor, duplicate):
                home = next(
                    (member for member in members
                     if member is not duplicate
                     and (member is survivor or int(member.get("file_rows") or 0) > 0)
                     and _takes(member, duplicate)),
                    None)
            if home is not None:
                _fold_duplicate(cursor, home, duplicate)
                stats["albums_folded"] += 1
            else:
                cursor.execute(
                    """INSERT OR IGNORE INTO lib2_release_group_review(
                           artist_id, album_id, other_album_id,
                           release_group_mbid, reason)
                       VALUES(?,?,?, NULL, 'duplicate_title_unmerged')""",
                    (artist_id, survivor["id"], duplicate["id"]))
                stats["album_review"] += cursor.rowcount


def _name_words(name: Any) -> List[str]:
    return release_title_key(str(name or "")).split()


def _is_name_fragment(first: Any, second: Any) -> bool:
    """one name is the other with words cut off or left on: "Odetari w" beside
    "Odetari". the same words with other punctuation is not a fragment."""
    a, b = _name_words(first), _name_words(second)
    if not a or not b or len(a) == len(b):
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return long_[:len(short)] == short


def _shares_strong_ids(first: Any, second: Any, needed: int = 2) -> bool:
    a, b = _stored_ids(first), _stored_ids(second)
    return sum(1 for source in _STRONG_ARTIST_SOURCES
               if source in a and source in b
               and _id_values(a[source]) & _id_values(b[source])) >= needed


def _catalog_twin_evidence(conn: Any, survivor: Any, duplicate: Any) -> Optional[str]:
    """why two rows with different names and one shared catalogue id are one
    artist, or None when nothing says so (#2073).

    the shared id alone is not it: enrichment stamps ids from a name search,
    and two bands with near names end up holding the same one. Attack Attack!
    (US) was folded into Attack! Attack! (Wales) on a deezer id, and twenty
    albums changed hands. so the pair needs something more: a release they
    both hold, a name that is a cut of the other, or two of the strong ids
    agreeing. and two rows that each own files and hold no release in common
    are two discographies, whatever the ids say.
    """
    # someone, or an earlier pass, already said these are one artist under
    # two names (a translated name beside its romanised one)
    roots = {int(row["canonical_artist_id"] if row["canonical_artist_id"] is not None
                 else row["id"]) for row in (survivor, duplicate)}
    if len(roots) == 1:
        return "linked alias"
    mine = _album_rows_for_artist(conn, int(survivor["id"]))
    theirs = _album_rows_for_artist(conn, int(duplicate["id"]))
    shared = any(
        _same_album_title(a, b) and not _years_disagree(a, b)
        for a in mine for b in theirs if a["id"] != b["id"])
    if shared:
        return "shared release"
    both_own_files = (any(int(a.get("file_rows") or 0) > 0 for a in mine)
                      and any(int(b.get("file_rows") or 0) > 0 for b in theirs))
    if both_own_files:
        return None
    if _is_name_fragment(survivor["name"], duplicate["name"]):
        return "name fragment"
    if _shares_strong_ids(survivor, duplicate):
        return "two catalogue ids"
    return None


def merge_artist_twins(conn: Any, stats: Dict[str, Any]) -> set:
    """Fold artist rows that are one artist, by name and then by catalogue id;
    alias-link the same-named ones that are not. Returns the kept artists that
    took something in."""
    from core.library2.artist_merges import kept_apart, note_doubt
    cursor = conn.cursor()
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
            if _reviewed(conn, "artist", survivor["id"], duplicate["id"]):
                continue
            if kept_apart(conn, survivor["id"], duplicate["id"]):
                continue    # a person, or an undo, said these are two artists
            _merge_artist(cursor, survivor, duplicate, "same name")
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
        merged_here = False
        for duplicate in active_members[1:]:
            if _reviewed(conn, "artist", survivor["id"], duplicate["id"]):
                continue
            if kept_apart(conn, survivor["id"], duplicate["id"]):
                continue
            evidence = _catalog_twin_evidence(conn, survivor, duplicate)
            if evidence is None:
                stats["artists_left_apart"] = int(stats.get("artists_left_apart") or 0) + 1
                # a person can still say they are one: Artist Merge Review asks
                note_doubt(conn, survivor["id"], duplicate["id"], source, value)
                logger.info(
                    "Left apart: artists %s %r and %s %r share %s=%s and "
                    "nothing else says they are one artist",
                    duplicate["id"], duplicate["name"], survivor["id"],
                    survivor["name"], source, value)
                continue
            _merge_artist(cursor, survivor, duplicate, f"shared {source} id, {evidence}")
            merged_here = True
            stats["artists_merged"] += 1
            logger.info(
                "Merged catalog-identity artist %s into %s "
                "(%s=%s, %s, %r -> %r)",
                duplicate["id"], survivor["id"], source, value, evidence,
                duplicate["name"], survivor["name"],
            )
        if merged_here:
            touched_artists.add(int(survivor["id"]))
    return touched_artists


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
    with closing(database._get_connection()) as conn:
        cursor = conn.cursor()
        # Namespace hygiene FIRST: a fake "spotify" id (really iTunes/Deezer)
        # on one twin would otherwise read as a same-source conflict and
        # block the merge below.
        stats["namespaces_fixed"] = _sanitize_provider_namespaces(conn, cursor)
        touched_artists = merge_artist_twins(conn, stats)

        # One file, one track first: it is what empties a stale server's twin.
        stats.update(fold_shared_file_tracks(conn))
        # then the same release listed by several servers under their own paths
        stats.update(fold_server_twins(conn))
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
    if stats["albums_folded"]:
        # Deliver any wishlist un-mirrors the folds enqueued (best-effort).
        try:
            from core.library2.mirror_outbox import drain
            drain(database)
        except Exception as drain_error:  # noqa: BLE001
            logger.debug("post-repair outbox drain failed: %s", drain_error)
    return stats


__all__ = ["fold_fileless_twins", "fold_server_twins", "merge_artist_twins",
           "repair_duplicate_artists"]
