"""Confirmed catalogue-merge identities; ordinary metadata edits are never aliases."""
from __future__ import annotations
import json

_TABLES = {"album": "lib2_albums", "track": "lib2_tracks"}


def ensure_catalogue_identity_schema(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS lib2_catalogue_redirects(
        entity_type TEXT NOT NULL CHECK(entity_type IN ('album','track')),
        old_id INTEGER NOT NULL,target_id INTEGER NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(entity_type,old_id),CHECK(old_id<>target_id))""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS lib2_catalogue_provider_aliases(
        entity_type TEXT NOT NULL CHECK(entity_type IN ('album','track')),
        provider TEXT NOT NULL,provider_id TEXT NOT NULL,target_id INTEGER NOT NULL,
        PRIMARY KEY(entity_type,provider,provider_id,target_id))""")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_catalogue_redirect_target ON lib2_catalogue_redirects(entity_type,target_id)")


def _exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def resolve_native_id(conn, kind, entity_id):
    if not _exists(conn, "lib2_catalogue_redirects"):
        return int(entity_id)
    seen = set()
    current = int(entity_id)
    while current not in seen:
        seen.add(current)
        row = conn.execute("SELECT target_id FROM lib2_catalogue_redirects WHERE entity_type=? AND old_id=?",
                           (kind, current)).fetchone()
        if not row:
            return current
        current = int(row[0])
    raise ValueError("Catalogue identity redirect cycle")


def merged_entity_ids(conn, kind, entity_ids):
    """Surviving ids plus every id merged into them, with one read."""
    ids = {int(i) for i in entity_ids}
    if not ids or not _exists(conn, "lib2_catalogue_redirects"):
        return sorted(ids)
    # record_redirect flattens chains, so one hop reaches the survivor.
    redirects = dict(conn.execute("SELECT old_id,target_id FROM lib2_catalogue_redirects WHERE entity_type=?",
                                  (kind,)).fetchall())
    current = {redirects.get(i, i) for i in ids}
    return sorted(current | {old for old, target in redirects.items() if target in current})


def provider_alias(conn, kind, provider, provider_id, *, album_id=None, artist_ids=None):
    if not provider or not provider_id or not _exists(conn, "lib2_catalogue_provider_aliases"):
        return None
    rows = conn.execute(f"""SELECT DISTINCT t.* FROM lib2_catalogue_provider_aliases a
        JOIN {_TABLES[kind]} t ON t.id=a.target_id
        WHERE a.entity_type=? AND a.provider=? AND a.provider_id=?""",
        (kind, provider, str(provider_id))).fetchall()
    if album_id is not None:
        rows = [r for r in rows if int(r["album_id"]) == int(album_id)]
    if artist_ids is not None:
        if not artist_ids:
            return None
        marks = ",".join("?" for _ in artist_ids)
        credit_table = {"album": "lib2_album_artists", "track": "lib2_track_artists"}[kind]
        # A provider may lead with a different credited collaborator on the
        # next import. Match the same artist scope used by ordinary autolink.
        rows = [r for r in rows if (kind == "album" and int(r["primary_artist_id"]) in artist_ids)
                or conn.execute(f"SELECT 1 FROM {credit_table} WHERE {kind}_id=? AND artist_id IN ({marks}) LIMIT 1",
                                (r["id"], *sorted(artist_ids))).fetchone()]
    # An ambiguous alias decides nothing; ordinary matching takes over.
    return int(rows[0]["id"]) if len(rows) == 1 else None


def record_redirect(conn, kind, old_id, target_id, provider_ids, payload, *, target_provider_ids=None):
    ensure_catalogue_identity_schema(conn.cursor())
    conn.execute("UPDATE lib2_catalogue_redirects SET target_id=? WHERE entity_type=? AND target_id=?",
                 (target_id, kind, old_id))
    for row in conn.execute("SELECT provider,provider_id FROM lib2_catalogue_provider_aliases WHERE entity_type=? AND target_id=?",
                            (kind, old_id)).fetchall():
        conn.execute("INSERT OR IGNORE INTO lib2_catalogue_provider_aliases VALUES(?,?,?,?)",
                     (kind, row[0], row[1], target_id))
    conn.execute("DELETE FROM lib2_catalogue_provider_aliases WHERE entity_type=? AND target_id=?", (kind, old_id))
    # Requests may still carry either original provider key after the queue
    # becomes one canonical row. Register both reviewed sides, by namespace.
    for identities in (provider_ids, target_provider_ids or {}):
        for provider, pid in identities.items():
            if provider not in {"isrc", "upc", "barcode"} and pid:
                conn.execute("INSERT OR IGNORE INTO lib2_catalogue_provider_aliases VALUES(?,?,?,?)",
                             (kind, provider, str(pid), target_id))
    conn.execute("INSERT INTO lib2_catalogue_redirects(entity_type,old_id,target_id,payload_json) VALUES(?,?,?,?)",
                 (kind, old_id, target_id, json.dumps(payload, sort_keys=True, default=str)))


def canonicalize_wishlist_identity(conn, data, source_info):
    """Only an explicitly merged identity can change a request's provider key."""
    from core.library2.importer import _wishlist_provider
    from core.library2.provider_ids import source_ids_from_values
    original_source_info = source_info
    if not isinstance(data, dict):
        return data, original_source_info
    if isinstance(source_info, str):
        try:
            source_info = json.loads(source_info)
        except (ValueError, TypeError):
            source_info = {}
    if not isinstance(source_info, dict):
        source_info = {}
    provider = _wishlist_provider(data, source_info)
    incoming_album = data.get("album") or {}
    if not isinstance(incoming_album, dict):
        return data, original_source_info
    incoming_album_id = incoming_album.get("id")
    # The recording may also occur on an unrelated compilation. Only the
    # exact reviewed source release is an alias to this release group.
    album_target = provider_alias(conn, "album", provider, incoming_album_id)
    if album_target is None:
        return data, original_source_info
    tid = provider_alias(conn, "track", provider, str(data.get("id") or "").split("::", 1)[0], album_id=album_target)
    if tid is None:
        return data, original_source_info
    track = dict(conn.execute("SELECT * FROM lib2_tracks WHERE id=?", (tid,)).fetchone())
    album = dict(conn.execute("SELECT * FROM lib2_albums WHERE id=?", (track["album_id"],)).fetchone())
    def ids(row):
        return source_ids_from_values(spotify_id=row.get("spotify_id"),
            musicbrainz_id=row.get("musicbrainz_id"), external_ids=row.get("external_ids"))
    track_pid, album_pid = ids(track).get(provider), ids(album).get(provider)
    if not track_pid or not album_pid:
        return data, original_source_info
    original = {"provider": provider, "track_id": data.get("id"),
                "album_id": (data.get("album") or {}).get("id")}
    changed = {**data, "id": track_pid, "source_track_id": track_pid,
               "album": {**(data.get("album") or {}), "id": album_pid}}
    info = {**(source_info or {}), "lib2_track_id": tid, "lib2_album_id": album["id"],
            "merged_provider_identity": original}
    return changed, info


def wishlist_rows(conn, track_ids, *, key=None):
    """Queue rows naming these native tracks (or holding ``key``), with the
    track id they name as ``lib2_track_id``."""
    named = "CAST(CASE WHEN json_valid(source_info) THEN json_extract(source_info,'$.lib2_track_id') END AS INTEGER)"
    ids = [int(i) for i in track_ids]
    return [dict(r) for r in conn.execute(
        f"""SELECT *,{named} AS lib2_track_id FROM wishlist_tracks
            WHERE {named} IN ({",".join("?" for _ in ids)}) OR spotify_track_id=? ORDER BY id""", (*ids, key))]


def consolidate_merged_wishlist(conn, track_pairs, old_album_id, new_album_id):
    """Collapse confirmed equivalents per profile and retain original contexts."""
    from core.wishlist.identity import wishlist_row_key
    mapping = {s["id"]: t["id"] for s, t in track_pairs}

    def entry(row, target):
        try:
            info, data = json.loads(row.get("source_info") or "{}"), json.loads(row.get("spotify_data") or "{}")
        except (ValueError, TypeError):
            return None
        if not isinstance(info, dict) or not isinstance(data, dict):
            return None
        original = {"source_type": row.get("source_type"), "source_info": info,
                    "spotify_data": data, "wishlist_id": row["id"], "queue_state": dict(row)}
        data, info = canonicalize_wishlist_identity(conn, data, info)
        # Both old aliases become one canonical row regardless of the user's
        # ordinary allow-duplicates preference (which still applies elsewhere).
        key = wishlist_row_key(data.get("id"), (data.get("album") or {}).get("id"), allow_duplicates=True) or data.get("id")
        return row, data, {**info, "lib2_track_id": target, "lib2_album_id": new_album_id}, original, key

    groups = {}
    for row in wishlist_rows(conn, [*mapping, *mapping.values()]):
        target = mapping.get(row["lib2_track_id"], row["lib2_track_id"])
        if found := entry(row, target):
            groups.setdefault((row["profile_id"], target), []).append(found)
    for (profile_id, target), entries in groups.items():
        # An unlinked row already holding a canonical key is the same request.
        seen = {e[0]["id"] for e in entries}
        for key in {e[4] for e in entries}:
            for row in wishlist_rows(conn, [], key=key):
                if row["profile_id"] == profile_id and row["id"] not in seen and (extra := entry(row, target)):
                    entries.append(extra)
        if len({e[0].get("quality_profile_id") for e in entries}) > 1:
            raise ValueError("Conflicting wishlist quality profiles; resolve before merging.")
        # Approval determines whether a non-downloading profile may acquire
        # this request; preserve the approved row and its resolution timestamp.
        entries.sort(key=lambda e: (e[0].get("request_status") != "approved",
                                   e[0].get("source_type") != "manual", e[0]["id"]))
        row, data, info, _, key = entries[0]
        info["merged_wishlist_sources"] = [e[3] for e in entries]
        for duplicate, *_ in entries[1:]:
            conn.execute("DELETE FROM wishlist_tracks WHERE id=?", (duplicate["id"],))
        conn.execute("UPDATE wishlist_tracks SET spotify_track_id=?,spotify_data=?,source_info=? WHERE id=?",
                     (key, json.dumps(data), json.dumps(info), row["id"]))


def merged_wishlist_key(conn, key, profile_id):
    """Resolve confirmed queue aliases without changing immutable request history."""
    raw_key = str(key)
    exact = conn.execute("SELECT spotify_track_id FROM wishlist_tracks WHERE profile_id=? AND spotify_track_id=?",
                         (profile_id, raw_key)).fetchone()
    if exact:
        return exact[0]
    if not _exists(conn, "lib2_catalogue_provider_aliases"):
        return None
    track_pid, _, album_pid = raw_key.partition("::")
    rows = conn.execute("""SELECT DISTINCT t.id, a.provider FROM lib2_catalogue_provider_aliases a
        JOIN lib2_tracks t ON t.id=a.target_id WHERE a.entity_type='track' AND a.provider_id=?""", (track_pid,)).fetchall()
    targets = set()
    for row in rows:
        if album_pid and provider_alias(conn, "album", row["provider"], album_pid) != conn.execute(
                "SELECT album_id FROM lib2_tracks WHERE id=?", (row["id"],)).fetchone()[0]:
            continue
        targets.add(int(row["id"]))
    if len(targets) != 1:
        return None
    queued = [r for r in wishlist_rows(conn, targets) if r["profile_id"] == profile_id]
    return queued[0]["spotify_track_id"] if queued else None
