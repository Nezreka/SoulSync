"""Read-only release duplicate evidence and reviewed catalogue consolidation."""
from __future__ import annotations

from collections import defaultdict
from contextlib import closing
import hashlib
import itertools
import json
import sqlite3
import unicodedata

from core.library2 import ADMIN_PROFILE_ID
from core.library2.catalogue_identity import wishlist_rows
from core.library2.dedup_repair import _release_ids
from core.library2.duplicate_relationship import _normalized_title, conflicting_recording_id, durations_compatible
from core.library2.provider_ids import provider_only, source_ids_from_values
from utils.logging_config import get_logger

logger = get_logger("library2.catalogue_duplicates")


SCHEMA = "native_duplicate_releases/v1"
# Waiting for a due retry is not a running search; such a request follows the merge.
DORMANT_REQUESTS = ("no_candidate", "failed")
# What a reviewer compares. Counters, retries, enrichment and provider
# refreshes rewrite the other columns in the background and must not void a
# review between scan and merge.
_FACTS = {
    "album": ("id", "primary_artist_id", "artist_ids", "title", "album_type", "release_date", "spotify_id",
              "musicbrainz_id", "external_ids", "upc", "soul_id", "track_count", "expected_track_count",
              "tracklist_status", "monitored", "quality_profile_id", "quality_profile_explicit", "server_source",
              "server_id", "legacy_album_id", "canonical_locked", "canonical_album_id", "art_locked"),
    "track": ("id", "album_id", "title", "track_number", "disc_number", "duration", "isrc", "musicbrainz_id",
              "spotify_id", "external_ids", "soul_id", "monitored", "quality_profile_id", "quality_profile_explicit",
              "canonical_track_id", "server_source", "server_id", "legacy_track_id", "artist_credits"),
    "file": ("id", "track_id", "path", "file_state", "owner_profile_id", "is_primary", "primary_manual", "file_role"),
    "lib2_monitor_rules": ("entity_id", "profile_id", "monitored", "provenance"),
    "lib2_metadata_overrides": ("entity_id", "profile_id", "field_name", "value_json"),
    "lib2_media_server_mappings": ("entity_id", "server_source", "server_library_id", "server_id"),
    "lib2_provider_attempts": ("entity_id", "service"),
    "library_provider_snapshots": ("entity_id", "provider", "scope", "provider_entity_id"),
    "wishlist_tracks": ("id", "lib2_track_id", "profile_id", "spotify_track_id", "quality_profile_id", "request_status"),
}


# monitor rules a person (or their wishlist) chose, as opposed to ones an
# import or scan derived
_DELIBERATE = frozenset({"user_explicit", "wishlist_import", "cascade", "new_release"})


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


def _pick(kind, rows):
    return [{key: row.get(key) for key in _FACTS[kind]} for row in rows]


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


def _related(conn, kind, ids):
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
    if kind == "track" and _table(conn, "wishlist_tracks"):
        rows["wishlist_tracks"] = wishlist_rows(conn, ids)
    return rows


def _load(conn, scope=None, check_stop=None, album_ids=None):
    predicate, params = "", ()
    if album_ids is not None:
        predicate = f" AND al.id IN ({','.join('?' for _ in album_ids)})"
        params = tuple(album_ids)
    source = """FROM lib2_albums al JOIN lib2_artists ar ON ar.id=al.primary_artist_id
        WHERE al.title IS NOT NULL AND al.title<>''"""
    credits = defaultdict(set)
    primaries = defaultdict(set)
    # third #1062 review P2D-8: a load for named albums (every merge, inside
    # its write lock, and each re-check after one) read the whole credit and
    # artist tables; it reads only what those albums name
    credit_sql = "SELECT album_id,artist_id,role FROM lib2_album_artists"
    if album_ids is not None:
        credit_sql += f" WHERE album_id IN ({','.join('?' for _ in album_ids)})"
    for row in conn.execute(credit_sql, params):
        credits[int(row[0])].add(int(row[1]))
        if row[2] == "primary":
            primaries[int(row[0])].add(int(row[1]))
    # an alias row is its canonical artist: each media server's copy of an
    # artist (plex's bbno$, navidrome's bbno$) files its albums under its own
    # row, and grouping by the raw id never put those twins side by side
    if album_ids is None:
        canonical = {int(r[0]): int(r[1]) for r in conn.execute(
            "SELECT id, COALESCE(canonical_artist_id, id) FROM lib2_artists")}
    else:
        named = {a for ids in credits.values() for a in ids} | {
            int(r[0]) for r in conn.execute(
                f"SELECT primary_artist_id FROM lib2_albums WHERE id IN ({','.join('?' for _ in album_ids)})"
                " AND primary_artist_id IS NOT NULL", params)}
        canonical = {}
        named_list = sorted(named)
        for start in range(0, len(named_list), 500):
            chunk = named_list[start:start + 500]
            canonical.update({int(r[0]): int(r[1]) for r in conn.execute(
                f"SELECT id, COALESCE(canonical_artist_id, id) FROM lib2_artists"
                f" WHERE id IN ({','.join('?' for _ in chunk)})", chunk)})

    def canonical_of(artist_id):
        # R4-A-1: the narrow load read the alias map for album credits only,
        # so a track's featured guest or a compilation performer kept its raw
        # per-server row id, the two twins' credits differed, and a pair the
        # full scan called proven failed every merge. an artist not read yet
        # is looked up here, whatever names it
        artist_id = int(artist_id)
        if artist_id not in canonical and album_ids is not None:
            row = conn.execute("SELECT COALESCE(canonical_artist_id, id) FROM lib2_artists WHERE id=?",
                               (artist_id,)).fetchone()
            canonical[artist_id] = int(row[0]) if row else artist_id
        return canonical.get(artist_id, artist_id)

    def group_ids(ids):
        return tuple(sorted({canonical_of(i) for i in ids}))
    # Grouping needs titles only; full rows are read for possible duplicates.
    groups = defaultdict(list)
    for al in conn.execute(f"SELECT al.id,al.title,al.album_type,al.primary_artist_id {source}{predicate}", params):
        # grouped by who the release is BY: one copy's enrichment adding
        # featured artists to the album must not keep the twins apart (the
        # id proof and the blockers still decide any merge)
        groups[(group_ids(primaries[al[0]] | {int(al[3])}), _title(al[1]), al[2])].append(al[0])
    possible = sorted(i for ids in groups.values() if len(ids) > 1 for i in ids)
    albums = []
    for offset in range(0, len(possible), 400):
        batch = possible[offset:offset + 400]
        albums += [dict(r) for r in conn.execute(f"""SELECT al.*,ar.name AS artist_name {source}
            AND al.id IN ({','.join('?' for _ in batch)}) ORDER BY al.id""", batch)]
    for al in albums:
        al["album_id"] = int(al["id"])
        al["artist_ids"] = sorted(credits[al["id"]] | {int(al["primary_artist_id"])})
        al["group_artist_ids"] = list(group_ids(primaries[al["id"]] | {int(al["primary_artist_id"])}))
    scope = scope or {}
    if scope.get("artist_id"):
        from core.library2.artist_aliases import resolve_alias_group
        allowed_artist = set(resolve_alias_group(conn, int(scope["artist_id"])))
        albums = [al for al in albums if not allowed_artist.isdisjoint(al["artist_ids"])]
    elif scope.get("artist_name"):
        albums = [al for al in albums if al["artist_name"].casefold() == str(scope["artist_name"]).casefold()]
    tracks, files, track_credits = defaultdict(list), defaultdict(list), defaultdict(list)
    ids = [al["id"] for al in albums]
    for offset in range(0, len(ids), 400):
        if check_stop and check_stop():
            return []
        batch = ids[offset:offset + 400]
        marks = ",".join("?" for _ in batch)
        for row in conn.execute(f"SELECT * FROM lib2_tracks WHERE album_id IN ({marks}) ORDER BY album_id,disc_number,track_number,id", batch):
            tracks[int(row["album_id"])].append(dict(row))
        for row in conn.execute(f"""SELECT f.* FROM lib2_track_files f JOIN lib2_tracks t ON t.id=f.track_id
            WHERE t.album_id IN ({marks}) AND COALESCE(f.file_state,'active')<>'deleted' ORDER BY f.id""", batch):
            files[int(row["track_id"])].append(dict(row))
        for row in conn.execute(f"""SELECT ta.* FROM lib2_track_artists ta JOIN lib2_tracks t ON t.id=ta.track_id
            WHERE t.album_id IN ({marks}) ORDER BY ta.track_id,ta.position,ta.artist_id""", batch):
            credit = dict(row)
            credit["canonical_artist_id"] = canonical_of(credit["artist_id"])
            track_credits[int(row["track_id"])].append(credit)
    # A catalogue with files in another private library is not this scan's subject.
    from core.library2.sql_util import ANY_OWNER, ambient_scope
    owner = ambient_scope()
    subjects = []
    for al in albums:
        if check_stop and check_stop():
            break
        al["files"] = [f for t in tracks[al["id"]] for f in files[t["id"]]]
        if (not scope.get("artist_id") and not scope.get("artist_name") and "file_paths" in scope
                and not any(f["path"] in scope["file_paths"] for f in al["files"])):
            continue
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


def _credits(track):
    """(artist, role) by canonical artist, without a featured credit of an
    artist who is already primary: plex credits bbno$ as featured on his own
    song, navidrome doesn't, and that junk alone split the pair"""
    rows = {(r.get("canonical_artist_id", r["artist_id"]), r["role"]) for r in track.get("artist_credits", [])}
    primary = {artist for artist, role in rows if role == "primary"}
    return {(artist, role) for artist, role in rows if not (role == "featured" and artist in primary)}


def _track_match(a, b, *, same_release=False):
    """Same recording? ``same_release``: the two releases are already proven
    identical, so the track at the same position with the same title, length
    and credits is that recording even without a track id (a media server
    that never had one)."""
    one, two = _credits(a), _credits(b)
    if same_release:
        # a proven release: the primaries must agree; one copy may know a
        # featured artist the other's enrichment never added (3mo with or
        # without its feature). the merge keeps every credit of both
        primaries = ({c for c in one if c[1] == "primary"}, {c for c in two if c[1] == "primary"})
        rest = (one - primaries[0], two - primaries[1])
        credits_ok = primaries[0] == primaries[1] and (rest[0] <= rest[1] or rest[1] <= rest[0])
    else:
        credits_ok = one == two
    if not credits_ok or _normalized_title(a["title"]) != _normalized_title(b["title"]):
        return False
    if not durations_compatible(a.get("duration"), b.get("duration")) or conflicting_recording_id(a, b):
        return False
    if any(a.get(k) and str(a[k]).casefold() == str(b.get(k) or "").casefold()
           for k in ("isrc", "musicbrainz_id", "spotify_id")):
        return True
    return same_release


def _blockers(conn, albums, refs):
    reasons = []
    for al in albums:
        if any(f.get("primary_manual") for f in al["files"]):
            reasons.append("A file has a manually selected primary.")
        if al.get("canonical_locked") or al.get("art_locked"):
            reasons.append("A release has a manual pin or artwork selection.")
    if (all(al.get("genres_locked") for al in albums)
            and _genre_set(albums[0].get("genres")) != _genre_set(albums[1].get("genres"))):
        reasons.append("Both releases have locked genres that differ.")
    for al in albums:
        if _table(conn, "lib2_metadata_overrides"):
            if conn.execute("""SELECT 1 FROM lib2_metadata_overrides
                WHERE (entity_type='release_group' AND entity_id=?)
                   OR (entity_type='track' AND entity_id IN (SELECT id FROM lib2_tracks WHERE album_id=?))
                LIMIT 1""", (al["id"], al["id"])).fetchone():
                reasons.append("A release or track has manual metadata.")
    track_pairs = list(zip(albums[0]["tracks"], albums[1]["tracks"], strict=False))
    for a, b in [(albums[0], albums[1]), *track_pairs]:
        for key in ("quality_profile_id", "legacy_album_id", "legacy_track_id"):
            # 3.6.1 kept one legacy row per media server, so two servers'
            # copies of a release always carry different legacy ids. only
            # the same server's two ids are a conflict
            if key.startswith("legacy_") and a.get("server_source") != b.get("server_source"):
                continue
            if a.get(key) and b.get(key) and a[key] != b[key]:
                reasons.append(f"Conflicting {key.replace('_', ' ')}.")
        if (a.get("server_source") == b.get("server_source") and a.get("server_id")
                and b.get("server_id") and a["server_id"] != b["server_id"]):
            reasons.append("Different media-server identities must stay separate.")
        # a single linked to its album track (the link v2 keeps) is not a
        # conflict when both copies agree or only one knows it: only two
        # different targets, or one copy pointing at the other, need a person
        ca, cb = a.get("canonical_track_id"), b.get("canonical_track_id")
        if (ca and cb and ca != cb) or (ca and ca == b.get("id")) or (cb and cb == a.get("id")):
            reasons.append("An existing canonical relationship needs separate review.")
    album_ids = [a["id"] for a in albums]
    track_ids = [t["id"] for a in albums for t in a["tracks"]]
    for kind, pairs in (("album", [tuple(album_ids)]), ("track", [(a["id"], b["id"]) for a, b in track_pairs])):
        for pair in pairs:
            states, mappings = defaultdict(set), defaultdict(set)
            for rule in refs[kind].get("lib2_monitor_rules", []):
                # only choices someone made can conflict. an upgrade or a scan
                # wrote "legacy_import" / "file_import" rules for each server's
                # copy, monitored on one and not the other, and that alone
                # held every such pair back (the deeper check below already
                # lets derived rules unite)
                if rule["entity_id"] in pair and rule.get("provenance") in _DELIBERATE:
                    states[rule["profile_id"]].add(rule["monitored"])
            for mapping in refs[kind].get("lib2_media_server_mappings", []):
                if mapping["entity_id"] in pair:
                    mappings[(mapping["server_source"], mapping.get("server_library_id", ""))].add(mapping["server_id"])
            if any(len(v) > 1 for v in states.values()):
                reasons.append("Conflicting monitoring decisions.")
            if any(len(v) > 1 for v in mappings.values()):
                reasons.append("Different media-server identities must stay separate.")
    if _table(conn, "acquisition_requests"):
        from core.acquisition.requests import TERMINAL_STATUSES
        settled = sorted({*TERMINAL_STATUSES, *DORMANT_REQUESTS})
        editions = [r[0] for r in conn.execute(
            f"SELECT id FROM lib2_release_editions WHERE release_group_id IN ({','.join('?' for _ in album_ids)})", album_ids)]
        recordings = [r[0] for r in conn.execute(
            f"SELECT DISTINCT recording_id FROM lib2_release_tracks WHERE track_id IN ({','.join('?' for _ in track_ids)})", track_ids)] if track_ids else []
        for kind, ids in (("album", album_ids), ("release_group", album_ids),
                          ("track", track_ids), ("upgrade", track_ids),
                          ("release_edition", editions), ("recording", recordings)):
            if ids and conn.execute(f"""SELECT 1 FROM acquisition_requests WHERE scope=?
                AND entity_id IN ({','.join('?' for _ in ids)})
                AND status NOT IN ({','.join('?' for _ in settled)}) LIMIT 1""", (kind, *ids, *settled)).fetchone():
                reasons.append("An acquisition is still active.")
    from core.downloads.cancel import _TERMINAL_STATUSES
    from core.runtime_state import download_tasks, tasks_lock
    with tasks_lock:
        tasks = list(download_tasks.values())
    for task in tasks:
        if task.get("status") in _TERMINAL_STATUSES | {"error", "canceled"}:
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
        for one, two in track_pairs:
            decisions = []
            for al, track in ((albums[0], one), (albums[1], two)):
                levels = [rules.get((kind, eid, profile), {}) for kind, eid in (
                    ("track", track["id"]), ("album", al["id"]), ("artist", al["primary_artist_id"]))]
                effective, _reason = _decide(*[v for row in levels for v in (row.get("monitored"), row.get("provenance"))])
                deliberate = any(row.get("provenance") in _DELIBERATE for row in levels)
                decisions.append((effective, deliberate))
            if decisions[0][0] != decisions[1][0] and all(d[1] for d in decisions):
                reasons.append("Conflicting effective monitoring decisions.")
    for one, two in track_pairs:
        profile_choices = defaultdict(set)
        for row in refs["track"].get("wishlist_tracks", []):
            if row["lib2_track_id"] in {one["id"], two["id"]}:
                profile_choices[row.get("profile_id", 1)].add(row.get("quality_profile_id"))
        if any(len(v) > 1 for v in profile_choices.values()):
            reasons.append("Conflicting wishlist quality profiles.")
    return sorted(set(reasons))


def _keeper(albums):
    """most files; on a tie the active server's copy (the one the user
    sees and curates), then a genre-locked one, then the older row"""
    from core.library2.dedup_repair import _active_server
    try:
        active = _active_server() or ""
    except Exception:  # noqa: BLE001 - no config: file count and age decide
        active = ""
    return min(albums, key=lambda al: (
        -al["file_count"],
        0 if active and (al.get("server_source") or "") == active else 1,
        0 if al.get("genres_locked") else 1,
        al["id"]))


def _explicit_conflict(one, two) -> bool:
    """both rows know their explicit flag and they disagree"""
    flags = [x.get("explicit") if isinstance(x, dict) else None for x in (one, two)]
    known = [bool(int(f)) for f in flags if f is not None and str(f).strip() != ""]
    return len(known) == 2 and known[0] != known[1]


def _candidate(conn, a, b):
    albums = [a, b]
    refs = {"album": _related(conn, "album", [a["id"], b["id"]]),
            "track": _related(conn, "track", [t["id"] for al in albums for t in al["tracks"]])}
    reasons = _blockers(conn, albums, refs)
    ids_a, ids_b = _release_ids(a), _release_ids(b)
    # soul_id is a hash of artist + title, so every same-titled pair shares
    # it: it proves nothing about the release and never counts as evidence
    shared = (ids_a.keys() & ids_b.keys()) - {"soul_id"}
    # a clean and an explicit copy are two releases, whatever ids a name
    # match handed both
    clean_vs_explicit = _explicit_conflict(a, b) or any(
        _explicit_conflict(one, two) for one, two in zip(a["tracks"], b["tracks"]))
    identical_release = (len(shared) >= 2 and all(ids_a[k] == ids_b[k] for k in shared)
                         and not clean_vs_explicit)
    # a proven release needs its full tracklist on both rows, not the
    # provider's "fetched" flag: two servers' copies of an album often never
    # fetched one, though each row holds every track
    complete = all(al["tracks"] and len(al["tracks"]) == (al.get("expected_track_count") or al.get("track_count"))
                   and (identical_release or al.get("tracklist_status") == "ready") for al in albums)
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
            if (one.get("disc_number") or 1, one.get("track_number")) != (two.get("disc_number") or 1, two.get("track_number")) or not one.get("track_number") or not _track_match(one, two, same_release=identical_release):
                reasons.append("Track positions, versions or recording identities are not confirmed identical.")
            mapping.append([one["id"], two["id"]])
    facts = {"albums": [{**_pick("album", [al])[0], "tracks": _pick("track", al["tracks"]),
                         "files": _pick("file", al["files"])} for al in albums],
             **{f"{kind}:{table}": _pick(table, rows) for kind, related in refs.items() for table, rows in related.items()}}
    snapshot = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
    return {"schema": SCHEMA, "pair_key": f"{min(a['id'], b['id'])}:{max(a['id'], b['id'])}", "albums": albums, "track_pairs": mapping,
            "count": 2, "merge_eligible": not reasons,
            "auto_merge_eligible": not reasons and identical_release,
            "blocked_reasons": sorted(set(reasons)), "snapshot": snapshot,
            "recommended_album_id": _keeper(albums)["id"],
            "library_v2_native": True,
            "library_v2": {"album_ids": [a["id"], b["id"]],
                          "track_ids": [t["id"] for al in albums for t in al["tracks"]]}}


def find_catalogue_duplicates(database, *, scope=None, check_stop=None, album_ids=None):
    """Report suspected duplicate release groups, including fileless wishes."""
    with closing(database._get_connection()) as conn:
        groups = defaultdict(list)
        for album in _load(conn, scope, check_stop, album_ids):
            groups[(tuple(album["group_artist_ids"]), _title(album["title"]), album["album_type"])].append(album)
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
        from core.library2.monitor_rules import merge_rules_into
        merge_rules_into(conn, kind, source_id, target_id)
    finding_cols = ({r[1] for r in conn.execute("PRAGMA table_info(repair_findings)")}
                    if _table(conn, "repair_findings") else set())
    if {"entity_type", "entity_id"} <= finding_cols:
        # review 4 R4-A-7: an open finding describes the twin (its ids, its
        # files), and moved it read as the survivor's, sometimes twice. it
        # is settled as obsolete; the next scan raises it again if it holds
        if {"status", "user_action", "resolved_at"} <= finding_cols:
            conn.execute("UPDATE repair_findings SET status='resolved', user_action='obsolete', "
                         "resolved_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                         "WHERE entity_type=? AND entity_id=? AND status='pending'",
                         (kind, f"lib2:{source_id}"))
        # P2A-6, for releases and tracks too: a dismissal (any settled
        # finding) on the twin follows the survivor instead of naming a
        # deleted row
        conn.execute("UPDATE repair_findings SET entity_id=?,updated_at=CURRENT_TIMESTAMP "
                     "WHERE entity_type=? AND entity_id=?",
                     (f"lib2:{target_id}", kind, f"lib2:{source_id}"))
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
    if _table(conn, "acquisition_requests"):
        # A due retry would search for the deleted row; the survivor's own
        # wanted projection asks again. Running requests blocked the merge.
        conn.execute(f"""UPDATE acquisition_requests SET status='cancelled',last_error=?,completed_at=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP WHERE scope=? AND entity_id=? AND status IN ({','.join('?' for _ in DORMANT_REQUESTS)})""",
            (f"Merged into {kind} {target_id}", "release_group" if kind == "album" else "upgrade", source_id, *DORMANT_REQUESTS))
    if kind == "track":
        if _table(conn, "listening_history"):
            columns = {r[1] for r in conn.execute("PRAGMA table_info(listening_history)")}
            if "lib2_track_id" in columns:
                conn.execute("UPDATE listening_history SET lib2_track_id=? WHERE lib2_track_id=?", (target_id, source_id))
        conn.execute("UPDATE lib2_tracks SET canonical_track_id=? WHERE canonical_track_id=?", (target_id, source_id))
        for table, column, discriminator in (
            ("manual_library_track_matches", "library_track_id", "library_track_id_kind"),
            ("sample_stash", "track_id", "track_id_kind"),
            # review 4 R4-A-6: Source Info reads the survivor's downloads
            ("track_downloads", "track_id", "track_id_kind")):
            if _table(conn, table):
                columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if column in columns:
                    typed = f" AND {discriminator}='lib2'" if discriminator in columns else ""
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?{typed}", (target_id, source_id))
        conn.execute("DELETE FROM lib2_wanted_tracks WHERE track_id=?", (source_id,))


def _adopt_gaps(conn, table, source, target):
    """Fill the survivor's empty columns. A release group keeps its own release
    identity: the duplicate's ids stay on its edition and as merge aliases."""
    fill = ["legacy_album_id", "legacy_track_id", "legacy_import_run_id", "server_source", "server_id",
            "stable_id", "image_url", "release_date", "year", "quality_profile_id"]
    if table == "lib2_tracks":
        # the single -> album-track link survives when only the duplicate had it
        if source.get("canonical_track_id") and source["canonical_track_id"] != target["id"]:
            fill.append("canonical_track_id")
        from core.library2.importer import _merge_external_ids
        fill += ["spotify_id", "musicbrainz_id", "soul_id"]
        known = _ids(target)
        # isrc/mbid/spotify keep their own columns, never external_ids.
        _merge_external_ids(conn.cursor(), table, target["id"], {k: v for k, v in provider_only(_ids(source)).items()
                                                                 if k not in known and k not in {"spotify", "musicbrainz"}})
    # what a curator or a provider filled on the duplicate fills the
    # survivor's gaps too ("[]" and 0 are gaps)
    fill += ["genres", "style", "mood", "label", "copyright"]
    if table == "lib2_tracks":
        fill += ["bpm", "genius_lyrics"]
    columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for column in fill:
        if column in columns and _empty(target.get(column)) and not _empty(source.get(column)):
            conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (source[column], target["id"]))
            target[column] = source[column]
    # explicit 0 is "clean", a real answer: only an unknown flag is a gap
    if "explicit" in columns and target.get("explicit") is None and source.get("explicit") is not None:
        conn.execute(f"UPDATE {table} SET explicit=? WHERE id=?", (source["explicit"], target["id"]))
    # a genre lock is the user's cleaned genres: an unlocked survivor takes
    # them and the lock (two locked rows that disagree never get here)
    if ("genres_locked" in columns and source.get("genres_locked")
            and not target.get("genres_locked")):
        conn.execute(f"UPDATE {table} SET genres=?, genres_locked=1 WHERE id=?",
                     (source.get("genres"), target["id"]))
    conn.execute(f"UPDATE {table} SET quality_profile_explicit=MAX(quality_profile_explicit,?),updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (source.get("quality_profile_explicit") or 0, target["id"]))


def _genre_set(value) -> frozenset:
    try:
        items = json.loads(value) if isinstance(value, str) else (value or [])
    except (TypeError, ValueError):
        items = [part for part in str(value).split(",")]
    if not isinstance(items, list):
        items = [items]
    return frozenset(str(g).strip().casefold() for g in items if str(g).strip())


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in ("", "[]", "{}")) or value == 0


def _merge_monitored(conn, kind, source, target):
    """The column is the shared library's intent: its (moved) rule decides;
    only without one does a monitored duplicate keep the survivor monitored."""
    rule = conn.execute("SELECT monitored FROM lib2_monitor_rules WHERE entity_type=? AND entity_id=? AND profile_id=?",
                        (kind, target["id"], ADMIN_PROFILE_ID)).fetchone()
    conn.execute(f"UPDATE lib2_{kind}s SET monitored=? WHERE id=?",
                 (rule[0] if rule else max(source.get("monitored") or 0, target.get("monitored") or 0), target["id"]))


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
            # Each side's edition is created from its own row before any gap
            # filling, so the two editions never claim the same release.
            for row in (t for pair in pairs for t in pair):
                # Supplement only missing recording evidence already reviewed.
                if row.get("isrc"):
                    conn.execute("UPDATE lib2_tracks SET isrc=COALESCE(NULLIF(isrc,''),?) WHERE id=?", (row["isrc"], row["id"]))
                attach_track_to_edition(conn, row["id"])
            _adopt_gaps(conn, "lib2_albums", duplicate, keeper)
            for source, target in pairs:
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
                _merge_monitored(conn, "track", source, target)
                record_entity_merge(conn, source_type="track", source_id=source["id"],
                    target_type="track", target_id=target["id"], change_source="catalogue_duplicate_merge")
            conn.execute("UPDATE lib2_release_editions SET release_group_id=?,is_default=0 WHERE release_group_id=?",
                         (keep_album_id, duplicate["id"]))
            record_redirect(conn, "album", duplicate["id"], keep_album_id, duplicate["provider_ids"], fresh,
                            target_provider_ids=keeper["provider_ids"])
            _move_references(conn, "album", duplicate["id"], keep_album_id)
            _merge_monitored(conn, "album", duplicate, keeper)
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
                    details = _json(finding["details_json"])
                    order = [a.get("album_id") for a in details.get("albums", [])]
                    if set(order) == set(ids) or duplicate["id"] in order:
                        action = "catalogue_merge" if set(order) == set(ids) else "catalogue_merge_superseded"
                        conn.execute("UPDATE repair_findings SET status=?,user_action=?,resolved_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                     ("auto_fixed" if automatic else "resolved", action, finding["id"]))
                    elif keep_album_id in order:
                        # The survivor changed: re-review its other pairs now
                        # instead of failing them as stale until the next scan.
                        current = {a["id"]: a for a in _load(conn, album_ids=order)}
                        if len(current) == 2:
                            details.update(_candidate(conn, *(current[i] for i in order)))
                            conn.execute("UPDATE repair_findings SET details_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                         (json.dumps(details), finding["id"]))
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

def auto_merge_proven(database, *, scope=None, check_stop=None, candidates=None, accept=None):
    """merge every proven twin pair (auto_merge_eligible) that wasn't dismissed.

    returns {"merged": n, "candidates": what's left for review}. a merge only
    changes its own group, so that group is re-read before going on."""
    if candidates is None:
        candidates = find_catalogue_duplicates(database, scope=scope, check_stop=check_stop)

    def pair(candidate):
        return frozenset(a["id"] for a in candidate["albums"])

    skip = dismissed_catalogue_pairs(database)
    merged = 0
    while candidate := next((c for c in candidates
                             if c["auto_merge_eligible"] and pair(c) not in skip
                             and (accept is None or accept(c))), None):
        if check_stop and check_stop():
            break
        applied = merge_catalogue_duplicate(
            database, candidate, candidate["recommended_album_id"], automatic=True)
        if not applied["success"]:
            skip.add(pair(candidate))
            continue
        merged += 1
        group = {i for c in candidates if pair(c) & pair(candidate) for i in pair(c)}
        candidates = [c for c in candidates if not pair(c) & group] + find_catalogue_duplicates(
            database, scope=scope, check_stop=check_stop,
            album_ids=sorted(group - {applied["merged_album_id"]}))
    return {"merged": merged, "candidates": candidates}


# v2 (review 4): the first version merged every proven pair on the install
# and lost the pairs a featured guest kept apart. an install that ran it gets
# the narrower, fixed pass once more
_IMPORT_TWINS_MARKER = "_lib2_import_twins_merged_v2"


def _is_import_twin(candidate) -> bool:
    """the pair 3.6.1 left behind: both rows came from the upgrade import and
    from two different media servers (3.6.1 kept a row set per server).
    R4-A-3: anything else is a decision for the opt-in Duplicate Detector,
    not something the upgrade does on its own"""
    albums = candidate.get("albums") or []
    if len(albums) != 2:
        return False
    sources = [str(a.get("server_source") or "").strip().lower() for a in albums]
    return (all(a.get("legacy_album_id") is not None for a in albums)
            and all(sources) and sources[0] != sources[1])


def merge_import_twins(database):
    """once, after the 3.6.1 upgrade import: one release row per release.

    3.6.1 kept a row set per media server, so an install that ever switched
    servers imports each release once per server. the proven pairs merge here
    (the other server's copy stays as a mapping on the kept row); anything
    not proven is left for the Duplicate Detector. waits for the import to be
    done, so the pass that runs before it can't use up the marker. never raises."""
    stats = {"merged": 0, "left_for_review": 0}
    try:
        with closing(database._get_connection()) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (_IMPORT_TWINS_MARKER,)).fetchone():
                return stats
            if not _table(conn, "lib2_albums"):
                return stats
        from core.library2.bootstrap import get_state
        if get_state(database).get("status") != "done":
            return stats
        result = auto_merge_proven(database, accept=_is_import_twin)
        stats["merged"] = result["merged"]
        stats["left_for_review"] = len(result["candidates"])
        with closing(database._get_connection()) as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {_IMPORT_TWINS_MARKER} "
                         "(applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, merged INTEGER)")
            conn.execute(f"INSERT INTO {_IMPORT_TWINS_MARKER}(merged) VALUES(?)", (stats["merged"],))
            conn.commit()
        if stats["merged"]:
            logger.info("upgrade: merged %d duplicate releases from older media servers "
                        "(%d left for review)", stats["merged"], stats["left_for_review"])
            try:
                from core.runtime_state import add_activity_item
                add_activity_item(
                    "🧹", "Duplicate releases merged",
                    f"{stats['merged']} releases your older media servers listed twice are one "
                    "release now. Tools → Duplicate Detector has any left to review.")
            except Exception:  # noqa: BLE001 - the note is a courtesy
                pass
    except Exception as exc:  # noqa: BLE001 - a duplicate row is never a blocker
        logger.warning("upgrade twin merge skipped (retries next start): %s", exc)
    return stats


def dismissed_catalogue_pairs(database):
    """Release pairs whose review was dismissed; auto merge never overrides them."""
    with closing(database._get_connection()) as conn:
        if not _table(conn, "repair_findings"):
            return set()
        return {frozenset(a.get("album_id") for a in _json(row[0]).get("albums", [])) for row in conn.execute(
            "SELECT details_json FROM repair_findings WHERE finding_type='native_duplicate_releases' AND status='dismissed'")}
