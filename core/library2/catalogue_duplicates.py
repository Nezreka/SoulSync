"""Read-only release duplicate evidence and reviewed catalogue consolidation."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import hashlib
import itertools
import json
import sqlite3
import unicodedata

from core.library2.duplicate_relationship import _normalized_title, durations_compatible
from core.library2.provider_ids import source_ids_from_values


SCHEMA = "native_duplicate_releases/v1"
TRUSTED_RELEASE_IDS = {"spotify", "musicbrainz", "deezer", "discogs", "audiodb", "soul_id"}


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _json(value):
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _title(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _ids(row):
    return source_ids_from_values(spotify_id=row.get("spotify_id"),
        musicbrainz_id=row.get("musicbrainz_id"), external_ids=row.get("external_ids"),
        isrc=row.get("isrc"), upc=row.get("upc"))


def _recording(conn, track, album):
    """Cache supplements missing facts only, bound to exact track AND release."""
    row = dict(track)
    row["provider_ids"] = _ids(row)
    row["evidence_source"] = "catalogue"
    if not _table(conn, "metadata_cache_entities"):
        return row
    for provider, track_id in row["provider_ids"].items():
        album_id = _ids(album).get(provider)
        if provider in {"isrc", "upc", "barcode"} or not album_id:
            continue
        cached = conn.execute("""SELECT * FROM metadata_cache_entities
            WHERE source=? AND entity_type='track' AND entity_id=?""", (provider, track_id)).fetchone()
        if not cached:
            continue
        cached = dict(cached)
        raw = _json(cached.get("raw_json"))
        raw_album = raw.get("album") if isinstance(raw, dict) else None
        cached_album = cached.get("album_id") or (raw_album.get("id") if isinstance(raw_album, dict) else None)
        if str(cached_album or "") != str(album_id):
            continue
        raw_ids = raw.get("external_ids")
        code = cached.get("isrc") or (raw_ids.get("isrc") if isinstance(raw_ids, dict) else None)
        if not row.get("isrc") and code:
            row["isrc"] = str(code).strip()
            row["evidence_source"] = f"cache:{provider}:{track_id}"
        if not row.get("duration") and cached.get("duration_ms"):
            row["duration"] = cached["duration_ms"]
    return row


def _related(conn, kind, ids, *, include_wishlist=True):
    """Facts that must remain stable between review and apply."""
    rows = {}
    marks = ",".join("?" for _ in ids)
    for table in ("lib2_monitor_rules", "lib2_metadata_overrides",
                  "lib2_media_server_mappings", "lib2_provider_attempts",
                  "library_provider_snapshots"):
        if _table(conn, table):
            entity_type = "release_group" if table == "lib2_metadata_overrides" and kind == "album" else kind
            rows[table] = [dict(r) for r in conn.execute(
                f"SELECT * FROM {table} WHERE entity_type=? AND entity_id IN ({marks}) ORDER BY entity_id",
                (entity_type, *ids))]
    if include_wishlist and kind == "track" and _table(conn, "wishlist_tracks"):
        rows["wishlist_tracks"] = []
        for row in conn.execute("SELECT * FROM wishlist_tracks ORDER BY id"):
            if _json(row["source_info"]).get("lib2_track_id") in ids:
                rows["wishlist_tracks"].append(dict(row))
    return rows


def _load(conn, scope=None, check_stop=None, album_ids=None):
    predicate, params = "", ()
    if album_ids is not None:
        predicate = f" AND al.id IN ({','.join('?' for _ in album_ids)})"
        params = tuple(album_ids)
    albums = [dict(r) for r in conn.execute(f"""SELECT al.*,ar.name AS artist_name
        FROM lib2_albums al JOIN lib2_artists ar ON ar.id=al.primary_artist_id
        WHERE al.title IS NOT NULL AND al.title<>''{predicate} ORDER BY al.id""", params)]
    credits = defaultdict(set)
    for row in conn.execute("SELECT album_id,artist_id FROM lib2_album_artists"):
        credits[int(row[0])].add(int(row[1]))
    allowed_artist = None
    scope = scope or {}
    if scope.get("artist_id"):
        from core.library2.artist_aliases import resolve_alias_group
        allowed_artist = set(resolve_alias_group(conn, int(scope["artist_id"])))
    groups = defaultdict(list)
    for al in albums:
        artist_ids = tuple(sorted(credits[al["id"]] | {int(al["primary_artist_id"])}))
        groups[(artist_ids, _title(al["title"]), al["album_type"])].append(al["id"])
    possible = sorted({i for ids in groups.values() if len(ids) > 1 for i in ids})
    if allowed_artist is not None:
        possible = [al["id"] for al in albums if al["id"] in possible
                    and not allowed_artist.isdisjoint(credits[al["id"]] | {int(al["primary_artist_id"])})]
    elif scope.get("artist_name"):
        possible = [al["id"] for al in albums if al["id"] in possible
                    and al["artist_name"].casefold() == str(scope["artist_name"]).casefold()]
    tracks, files, track_credits = defaultdict(list), defaultdict(list), defaultdict(list)
    for offset in range(0, len(possible), 400):
        if check_stop and check_stop():
            return []
        batch = possible[offset:offset + 400]
        marks = ",".join("?" for _ in batch)
        for row in conn.execute(f"SELECT * FROM lib2_tracks WHERE album_id IN ({marks}) ORDER BY album_id,disc_number,track_number,id", batch):
            tracks[int(row["album_id"])].append(dict(row))
        for row in conn.execute(f"""SELECT f.* FROM lib2_track_files f JOIN lib2_tracks t ON t.id=f.track_id
            WHERE t.album_id IN ({marks}) AND COALESCE(f.file_state,'active')<>'deleted' ORDER BY f.id""", batch):
            files[int(row["track_id"])].append(dict(row))
        for row in conn.execute(f"""SELECT ta.* FROM lib2_track_artists ta JOIN lib2_tracks t ON t.id=ta.track_id
            WHERE t.album_id IN ({marks}) ORDER BY ta.track_id,ta.position,ta.artist_id""", batch):
            track_credits[int(row["track_id"])].append(dict(row))
    possible = set(possible)
    subjects = []
    for al in albums:
        if al["id"] not in possible:
            continue
        if check_stop and check_stop():
            break
        al["album_id"] = int(al["id"])
        al["artist_ids"] = sorted(credits[al["id"]] | {int(al["primary_artist_id"])})
        al["files"] = [f for t in tracks[al["id"]] for f in files[t["id"]]]
        if allowed_artist is not None:
            if allowed_artist.isdisjoint(al["artist_ids"]):
                continue
        elif scope.get("artist_name"):
            if al["artist_name"].casefold() != str(scope["artist_name"]).casefold():
                continue
        elif "file_paths" in scope:
            if not any(f["path"] in scope["file_paths"] for f in al["files"]):
                continue
        # A catalogue with files in another private library is not this scan's subject.
        from core.library2.sql_util import ANY_OWNER, ambient_scope
        owner = ambient_scope()
        owners = {f.get("owner_profile_id") for f in al["files"]}
        if owner is not ANY_OWNER and owner is not None:
            target = None if owner == "shared" else int(owner)
            if owners and target not in owners:
                continue
            if not owners and target is not None:
                track_ids = [t["id"] for t in tracks[al["id"]]]
                marks = ",".join("?" for _ in track_ids) or "NULL"
                visible = conn.execute(f"""SELECT 1 FROM lib2_monitor_rules WHERE profile_id=?
                    AND monitored=1 AND ((entity_type='album' AND entity_id=?)
                    OR (entity_type='artist' AND entity_id=?)
                    OR (entity_type='track' AND entity_id IN ({marks}))) LIMIT 1""",
                    (target, al["id"], al["primary_artist_id"], *track_ids)).fetchone()
                if not visible:
                    continue
        al["tracks"] = [_recording(conn, t, al) for t in tracks[al["id"]]]
        for track in al["tracks"]:
            track["artist_credits"] = track_credits[track["id"]]
        al["provider_ids"] = _ids(al)
        al["file_count"] = len(al["files"])
        subjects.append(al)
    return subjects


def _track_match(a, b):
    one = {(r["artist_id"], r["role"]) for r in a.get("artist_credits", [])}
    two = {(r["artist_id"], r["role"]) for r in b.get("artist_credits", [])}
    if one != two:
        return False
    if _normalized_title(a["title"]) != _normalized_title(b["title"]):
        return False
    if not durations_compatible(a.get("duration"), b.get("duration")):
        return False
    for key in ("isrc", "musicbrainz_id"):
        one, two = str(a.get(key) or "").casefold(), str(b.get(key) or "").casefold()
        if one and two and one != two:
            return False
    return any(a.get(k) and str(a[k]).casefold() == str(b.get(k) or "").casefold()
               for k in ("isrc", "musicbrainz_id", "spotify_id"))


def _blockers(conn, albums):
    reasons = []
    for al in albums:
        if any(f.get("primary_manual") for f in al["files"]):
            reasons.append("A file has a manually selected primary.")
        if al.get("canonical_locked") or al.get("art_locked"):
            reasons.append("A release has a manual pin or artwork selection.")
        if _table(conn, "lib2_metadata_overrides"):
            if conn.execute("""SELECT 1 FROM lib2_metadata_overrides
                WHERE (entity_type='release_group' AND entity_id=?)
                   OR (entity_type='track' AND entity_id IN (SELECT id FROM lib2_tracks WHERE album_id=?))
                LIMIT 1""", (al["id"], al["id"])).fetchone():
                reasons.append("A release or track has manual metadata.")
    pairs = [(albums[0], albums[1]), *list(zip(albums[0]["tracks"], albums[1]["tracks"], strict=False))]
    for a, b in pairs:
        for key in ("quality_profile_id", "legacy_album_id", "legacy_track_id"):
            if a.get(key) and b.get(key) and a[key] != b[key]:
                reasons.append(f"Conflicting {key.replace('_', ' ')}.")
        if (a.get("server_source") == b.get("server_source") and a.get("server_id")
                and b.get("server_id") and a["server_id"] != b["server_id"]):
            reasons.append("Different media-server identities must stay separate.")
        if a.get("canonical_track_id") or b.get("canonical_track_id"):
            reasons.append("An existing canonical relationship needs separate review.")
    album_ids = [a["id"] for a in albums]
    track_ids = [t["id"] for a in albums for t in a["tracks"]]
    for kind, pairs_of_ids in (("album", [(album_ids[0], album_ids[1])]),
                               ("track", [(a["id"], b["id"]) for a, b in zip(albums[0]["tracks"], albums[1]["tracks"], strict=False)])):
        for a, b in pairs_of_ids:
            facts = _related(conn, kind, [a, b], include_wishlist=False)
            rules = facts.get("lib2_monitor_rules", [])
            states = defaultdict(set)
            for rule in rules:
                states[rule["profile_id"]].add(rule["monitored"])
            if any(len(v) > 1 for v in states.values()):
                reasons.append("Conflicting monitoring decisions.")
            mappings = defaultdict(set)
            for mapping in facts.get("lib2_media_server_mappings", []):
                mappings[(mapping["server_source"], mapping.get("server_library_id", ""))].add(mapping["server_id"])
            if any(len(v) > 1 for v in mappings.values()):
                reasons.append("Different media-server identities must stay separate.")
    if _table(conn, "acquisition_requests"):
        editions = [r[0] for r in conn.execute(
            f"SELECT id FROM lib2_release_editions WHERE release_group_id IN ({','.join('?' for _ in album_ids)})", album_ids)]
        recordings = [r[0] for r in conn.execute(
            f"SELECT DISTINCT recording_id FROM lib2_release_tracks WHERE track_id IN ({','.join('?' for _ in track_ids)})", track_ids)] if track_ids else []
        for kind, ids in (("album", album_ids), ("release_group", album_ids),
                          ("track", track_ids), ("upgrade", track_ids),
                          ("release_edition", editions), ("recording", recordings)):
            if ids and conn.execute(f"""SELECT 1 FROM acquisition_requests WHERE scope=?
                AND entity_id IN ({','.join('?' for _ in ids)})
                AND status NOT IN ('completed','cancelled') LIMIT 1""", (kind, *ids)).fetchone():
                reasons.append("An acquisition is still active.")
    from core.runtime_state import download_tasks, tasks_lock
    with tasks_lock:
        tasks = list(download_tasks.values())
    for task in tasks:
        if task.get("status") in {"completed", "failed", "cancelled", "canceled", "error"}:
            continue
        track_info = task.get("track_info") or {}
        info = track_info.get("source_info") or {}
        info = _json(info) if isinstance(info, str) else info
        native = track_info.get("lib2_entity") or {}
        try:
            tid = int(info.get("lib2_track_id") or native.get("track_id") or 0)
            aid = int(info.get("lib2_album_id") or native.get("album_id") or 0)
        except (TypeError, ValueError):
            continue
        if tid in track_ids or aid in album_ids:
            reasons.append("An acquisition is still active.")
    # Rules at different levels can encode opposite user choices even when
    # same-level pairs do not conflict. Derived file/legacy rules are allowed
    # to unite, while two contradictory deliberate decisions need review.
    from core.library2.wanted import _decide
    artist_ids = {al["primary_artist_id"] for al in albums}
    rules = {}
    for kind, ids in (("track", track_ids), ("album", album_ids), ("artist", sorted(artist_ids))):
        for offset in range(0, len(ids), 900):
            batch = ids[offset:offset + 900]
            marks = ",".join("?" for _ in batch)
            for row in conn.execute(f"SELECT * FROM lib2_monitor_rules WHERE entity_type=? AND entity_id IN ({marks})", (kind, *batch)):
                rules[(kind, row["entity_id"], row["profile_id"])] = dict(row)
    for profile in {key[2] for key in rules}:
        for one, two in zip(albums[0]["tracks"], albums[1]["tracks"], strict=False):
            decisions = []
            for al, track in ((albums[0], one), (albums[1], two)):
                levels = [rules.get((kind, eid, profile), {}) for kind, eid in (
                    ("track", track["id"]), ("album", al["id"]), ("artist", al["primary_artist_id"]))]
                effective, _reason = _decide(*[v for row in levels for v in (row.get("monitored"), row.get("provenance"))])
                deliberate = any(row.get("provenance") in {"user_explicit", "wishlist_import", "cascade", "new_release"} for row in levels)
                decisions.append((effective, deliberate))
            if decisions[0][0] != decisions[1][0] and all(d[1] for d in decisions):
                reasons.append("Conflicting effective monitoring decisions.")
    wishlist = _related(conn, "track", track_ids).get("wishlist_tracks", [])
    for one, two in zip(albums[0]["tracks"], albums[1]["tracks"], strict=False):
        profile_choices = defaultdict(set)
        for row in wishlist:
            if _json(row["source_info"]).get("lib2_track_id") in {one["id"], two["id"]}:
                profile_choices[row.get("profile_id", 1)].add(row.get("quality_profile_id"))
        if any(len(v) > 1 for v in profile_choices.values()):
            reasons.append("Conflicting wishlist quality profiles.")
    return sorted(set(reasons))


def _candidate(conn, a, b):
    albums = [a, b]
    reasons = _blockers(conn, albums)
    complete = all(al["tracks"] and len(al["tracks"]) == (al.get("expected_track_count") or al.get("track_count"))
                   and al.get("tracklist_status") == "ready" for al in albums)
    if not complete:
        reasons.append("A complete release tracklist is unavailable.")
    for album in albums:
        positions = [(t.get("disc_number") or 1, t.get("track_number")) for t in album["tracks"]]
        if len(set(positions)) != len(positions):
            reasons.append("A release has repeated track positions; resolve its tracklist before merging.")
    mapping = []
    if len(a["tracks"]) != len(b["tracks"]):
        reasons.append("Release track counts differ.")
    else:
        for one, two in zip(a["tracks"], b["tracks"], strict=True):
            if (one.get("disc_number") or 1, one.get("track_number")) != (two.get("disc_number") or 1, two.get("track_number")) or not one.get("track_number") or not _track_match(one, two):
                reasons.append("Track positions, versions or recording identities are not confirmed identical.")
            mapping.append([one["id"], two["id"]])
    shared = TRUSTED_RELEASE_IDS & a["provider_ids"].keys() & b["provider_ids"].keys()
    identical_release = (len(shared) >= 2 and all(a["provider_ids"][k] == b["provider_ids"][k] for k in shared))
    facts = {"albums": albums,
             "album_references": _related(conn, "album", [a["id"], b["id"]]),
             "track_references": _related(conn, "track", [t["id"] for al in albums for t in al["tracks"]])}
    snapshot = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
    return {"schema": SCHEMA, "pair_key": f"{min(a['id'], b['id'])}:{max(a['id'], b['id'])}", "albums": albums, "track_pairs": mapping,
            "count": 2, "merge_eligible": not reasons,
            "auto_merge_eligible": not reasons and identical_release,
            "blocked_reasons": sorted(set(reasons)), "snapshot": snapshot,
            "recommended_album_id": min(albums, key=lambda al: (-al["file_count"], al["id"]))["id"],
            "library_v2_native": True,
            "library_v2": {"album_ids": [a["id"], b["id"]],
                          "track_ids": [t["id"] for al in albums for t in al["tracks"]]}}


def find_catalogue_duplicates(database, *, scope=None, check_stop=None):
    """Report suspected duplicate release groups, including fileless wishes."""
    with closing(database._get_connection()) as conn:
        groups = defaultdict(list)
        for album in _load(conn, scope, check_stop):
            groups[(tuple(album["artist_ids"]), _title(album["title"]), album["album_type"])].append(album)
        candidates = []
        for albums in groups.values():
            for a, b in itertools.combinations(albums, 2):
                if check_stop and check_stop():
                    return candidates
                candidates.append(_candidate(conn, a, b))
        return candidates


def _move_references(conn, kind, source_id, target_id):
    # Conflicts were rejected in the reviewed snapshot. Keep explicit intent
    # when two equivalent monitor rules carry different provenance.
    if _table(conn, "lib2_monitor_rules"):
        for row in conn.execute("SELECT * FROM lib2_monitor_rules WHERE entity_type=? AND entity_id=?", (kind, source_id)).fetchall():
            row = dict(row)
            existing = conn.execute("SELECT * FROM lib2_monitor_rules WHERE entity_type=? AND entity_id=? AND profile_id=?",
                                    (kind, target_id, row["profile_id"])).fetchone()
            if existing:
                rank = {"user_explicit": 5, "wishlist_import": 4, "cascade": 3,
                        "new_release": 3, "file_import": 2, "legacy_import": 0}
                if rank.get(row["provenance"], 1) > rank.get(existing["provenance"], 1):
                    conn.execute("UPDATE lib2_monitor_rules SET provenance=? WHERE id=?", (row["provenance"], existing["id"]))
                conn.execute("DELETE FROM lib2_monitor_rules WHERE id=?", (row["id"],))
            else:
                conn.execute("UPDATE lib2_monitor_rules SET entity_id=? WHERE id=?", (target_id, row["id"]))
    if _table(conn, "lib2_media_server_mappings"):
        conn.execute("UPDATE OR IGNORE lib2_media_server_mappings SET entity_id=? WHERE entity_type=? AND entity_id=?",
                     (target_id, kind, source_id))
        conn.execute("DELETE FROM lib2_media_server_mappings WHERE entity_type=? AND entity_id=?", (kind, source_id))
    if _table(conn, "library_provider_snapshots"):
        # Edition-specific scopes preserve both provider responses without
        # changing the surviving release's normal metadata context.
        conn.execute("UPDATE library_provider_snapshots SET entity_id=?,scope='merged:'||?||':'||scope WHERE entity_type=? AND entity_id=?",
                     (target_id, source_id, kind, source_id))
    if _table(conn, "lib2_provider_attempts"):
        rows = conn.execute("SELECT * FROM lib2_provider_attempts WHERE entity_type=? AND entity_id=?", (kind, source_id)).fetchall()
        for row in rows:
            values = dict(row)
            values["entity_id"] = target_id
            columns = ",".join(values)
            conn.execute(f"INSERT OR IGNORE INTO lib2_provider_attempts({columns}) VALUES({','.join('?' for _ in values)})", list(values.values()))
    if kind == "track":
        if _table(conn, "listening_history"):
            columns = {r[1] for r in conn.execute("PRAGMA table_info(listening_history)")}
            if "lib2_track_id" in columns:
                conn.execute("UPDATE listening_history SET lib2_track_id=? WHERE lib2_track_id=?", (target_id, source_id))
        conn.execute("UPDATE lib2_tracks SET canonical_track_id=? WHERE canonical_track_id=?", (target_id, source_id))
        for table, column, discriminator in (
            ("manual_library_track_matches", "library_track_id", "library_track_id_kind"),
            ("sample_stash", "track_id", "track_id_kind")):
            if _table(conn, table):
                columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if column in columns:
                    typed = f" AND {discriminator}='lib2'" if discriminator in columns else ""
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?{typed}", (target_id, source_id))
        conn.execute("DELETE FROM lib2_wanted_tracks WHERE track_id=?", (source_id,))


def _adopt_gaps(conn, table, source, target):
    ids = _ids(target)
    ids.update({k: v for k, v in _ids(source).items() if k not in ids and k not in {"isrc", "upc", "barcode"}})
    columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for column in ("legacy_album_id", "legacy_track_id", "legacy_import_run_id", "server_source", "server_id",
                   "stable_id", "soul_id", "image_url", "release_date", "year", "upc"):
        if column in columns and not target.get(column) and source.get(column):
            conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (source[column], target["id"]))
    conn.execute(f"""UPDATE {table} SET external_ids=?,monitored=MAX(monitored,?),
        spotify_id=COALESCE(NULLIF(spotify_id,''),?),
        musicbrainz_id=COALESCE(NULLIF(musicbrainz_id,''),?),
        quality_profile_id=COALESCE(quality_profile_id,?),
        quality_profile_explicit=MAX(quality_profile_explicit,?),
        updated_at=CURRENT_TIMESTAMP WHERE id=?""",
        (json.dumps(ids, sort_keys=True), source["monitored"], source.get("spotify_id"),
         source.get("musicbrainz_id"), source.get("quality_profile_id"),
         source.get("quality_profile_explicit") or 0, target["id"]))


def _affected_profiles(conn, albums):
    profiles = {1}
    track_ids = [t["id"] for al in albums for t in al["tracks"]]
    album_ids = {al["id"] for al in albums}
    artist_ids = {i for al in albums for i in al["artist_ids"]}
    for kind, ids in (("track", track_ids), ("album", sorted(album_ids)), ("artist", sorted(artist_ids))):
        for offset in range(0, len(ids), 900):
            batch = ids[offset:offset + 900]
            marks = ",".join("?" for _ in batch)
            profiles.update(int(r[0]) for r in conn.execute(
                f"SELECT profile_id FROM lib2_monitor_rules WHERE entity_type=? AND entity_id IN ({marks})", (kind, *batch)))
    for offset in range(0, len(track_ids), 900):
        batch = track_ids[offset:offset + 900]
        marks = ",".join("?" for _ in batch)
        profiles.update(int(r[0]) for r in conn.execute(
            f"SELECT profile_id FROM lib2_wanted_tracks WHERE track_id IN ({marks})", batch))
    return profiles


def merge_catalogue_duplicate(database, candidate, keep_album_id, *, automatic=False):
    """Validate a fresh snapshot and merge atomically; no filesystem writes."""
    from core.library2.catalogue_identity import record_redirect
    from core.library2.editions import attach_track_to_edition
    from core.library2.entity_history import record_entity_merge
    from core.library2.wanted import recompute_wanted_for_entity
    try:
        keep_album_id = int(keep_album_id)
        if candidate.get("schema") != SCHEMA:
            raise ValueError("Invalid release-duplicate review.")
        ids = [int(a["album_id"]) for a in candidate["albums"]]
        if len(ids) != 2 or len(set(ids)) != 2 or keep_album_id not in ids:
            raise ValueError("Select one of the two reviewed release groups.")
        with closing(database._get_connection()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            albums = {a["id"]: a for a in _load(conn, album_ids=ids)}
            if len(albums) != 2:
                raise ValueError("Catalogue changed; rescan before merging.")
            fresh = _candidate(conn, albums[ids[0]], albums[ids[1]])
            if fresh["snapshot"] != candidate.get("snapshot"):
                raise ValueError("Catalogue changed; rescan before merging.")
            if not fresh["merge_eligible"]:
                raise ValueError(" ".join(fresh["blocked_reasons"]))
            if automatic and not fresh["auto_merge_eligible"]:
                raise ValueError("This release pair requires manual review.")
            keeper = albums[keep_album_id]
            duplicate = albums[next(i for i in ids if i != keep_album_id)]
            pairs = list(zip(duplicate["tracks"], keeper["tracks"], strict=True))
            affected_profiles = _affected_profiles(conn, fresh["albums"])
            _adopt_gaps(conn, "lib2_albums", duplicate, keeper)
            for source, target in pairs:
                # Supplement only missing recording evidence already reviewed.
                for row in (source, target):
                    if row.get("isrc"):
                        conn.execute("UPDATE lib2_tracks SET isrc=COALESCE(NULLIF(isrc,''),?) WHERE id=?", (row["isrc"], row["id"]))
                    attach_track_to_edition(conn, row["id"])
                _adopt_gaps(conn, "lib2_tracks", source, target)
                shared_play_counter = (source.get("server_source") == target.get("server_source")
                    and source.get("server_id") and source.get("server_id") == target.get("server_id"))
                counts = [int(source.get("play_count") or 0), int(target.get("play_count") or 0)]
                played = max((v for v in (source.get("last_played"), target.get("last_played")) if v), default=None)
                conn.execute("UPDATE lib2_tracks SET play_count=?,last_played=? WHERE id=?",
                             (max(counts) if shared_play_counter else sum(counts), played, target["id"]))
                record_redirect(conn, "track", source["id"], target["id"], source["provider_ids"], {**source, "references": _related(conn, "track", [source["id"]])},
                                target_provider_ids=target["provider_ids"])
                # The source edition remains a real, separately identified edition.
                conn.execute("UPDATE lib2_release_tracks SET track_id=? WHERE track_id=?", (target["id"], source["id"]))
                conn.execute("UPDATE lib2_track_files SET track_id=? WHERE track_id=?", (target["id"], source["id"]))
                conn.execute("UPDATE OR IGNORE lib2_track_artists SET track_id=? WHERE track_id=?", (target["id"], source["id"]))
                conn.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (source["id"],))
                _move_references(conn, "track", source["id"], target["id"])
                record_entity_merge(conn, source_type="track", source_id=source["id"],
                    target_type="track", target_id=target["id"], change_source="catalogue_duplicate_merge")
            conn.execute("UPDATE lib2_release_editions SET release_group_id=?,is_default=0 WHERE release_group_id=?",
                         (keep_album_id, duplicate["id"]))
            record_redirect(conn, "album", duplicate["id"], keep_album_id, duplicate["provider_ids"], fresh,
                            target_provider_ids=keeper["provider_ids"])
            _move_references(conn, "album", duplicate["id"], keep_album_id)
            record_entity_merge(conn, source_type="release_group", source_id=duplicate["id"],
                target_type="release_group", target_id=keep_album_id, change_source="catalogue_duplicate_merge")
            if _table(conn, "wishlist_tracks"):
                from core.library2.catalogue_identity import consolidate_merged_wishlist
                consolidate_merged_wishlist(conn, pairs, duplicate["id"], keep_album_id)
            for source, target in pairs:
                conn.execute("DELETE FROM lib2_tracks WHERE id=?", (source["id"],))
                for profile in affected_profiles:
                    recompute_wanted_for_entity(conn, "track", target["id"], profile_id=profile)
            conn.execute("DELETE FROM lib2_album_artists WHERE album_id=?", (duplicate["id"],))
            conn.execute("DELETE FROM lib2_albums WHERE id=?", (duplicate["id"],))
            conn.execute("UPDATE lib2_albums SET track_count=?,expected_track_count=? WHERE id=?",
                         (len(keeper["tracks"]), len(keeper["tracks"]), keep_album_id))
            if _table(conn, "lib2_release_group_review"):
                conn.execute("UPDATE lib2_release_group_review SET resolved=1 WHERE album_id=? OR other_album_id=?",
                             (duplicate["id"], duplicate["id"]))
            if _table(conn, "repair_findings"):
                for finding in conn.execute("SELECT id,details_json FROM repair_findings WHERE finding_type='native_duplicate_releases' AND status='pending'").fetchall():
                    members = {a.get("album_id") for a in _json(finding["details_json"]).get("albums", [])}
                    if members == set(ids) or duplicate["id"] in members:
                        action = "catalogue_merge" if members == set(ids) else "catalogue_merge_superseded"
                        conn.execute("UPDATE repair_findings SET status=?,user_action=?,resolved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                     ("auto_fixed" if automatic else "resolved", action, finding["id"]))
            if _table(conn, "lib2_artist_rollup"):
                # Let its normal lazy reader refresh this cache after commit.
                conn.executemany("UPDATE lib2_artist_rollup SET computed_at=0 WHERE artist_id=?", [(i,) for i in keeper["artist_ids"]])
            conn.commit()
        from core.library2.validation import notify_changes
        notify_changes([f["id"] for a in fresh["albums"] for f in a["files"]])
        return {"success": True, "action": "catalogue_merge", "kept_album_id": keep_album_id,
                "merged_album_id": duplicate["id"], "message": "Release groups merged; editions and files preserved."}
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        return {"success": False, "error": str(exc)}

def dismissed_catalogue_review(database, candidate):
    """Auto merge never overrides a dismissed review of the same release pair."""
    with closing(database._get_connection()) as conn:
        if not _table(conn, "repair_findings"):
            return False
        pair = {a["album_id"] for a in candidate["albums"]}
        for row in conn.execute("SELECT details_json FROM repair_findings WHERE finding_type='native_duplicate_releases' AND status='dismissed'"):
            old = _json(row[0])
            if {a.get("album_id") for a in old.get("albums", [])} == pair:
                return True
    return False
