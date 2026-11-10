"""Native duplicate candidates and explicitly approved, recoverable Keep Best.

Candidate discovery never establishes recording links. Similar tags or a
download filename are review evidence only; removal additionally needs a
shared recording identity or the user's explicit recording confirmation.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from difflib import SequenceMatcher
import hashlib
import json
import os
import re
from types import SimpleNamespace
from typing import Optional

from core.library.duplicate_rules import is_lossy_companion_file, is_lossy_companion_pair, lossy_companion_exts
from core.library2.duplicate_relationship import (
    DuplicateRelationshipError, _normalized_title, same_recording,
    validate_duplicate_pair,
)
from core.library2.maintenance_subjects import active_file_subjects
from core.library2.media_mappings import track_server_playlists
from core.library2.track_files import quality_order, quality_tier_columns
from core.repair_jobs.base import (
    hand_tagged_path_keys, is_hand_tagged_path, scoped_file_subjects,
)
from utils.logging_config import get_logger

logger = get_logger("library2.duplicate_review")

REVIEW_SCHEMA = "native_duplicate_review/v1"
DEFAULT_SETTINGS = {
    "title_similarity": 0.85, "artist_similarity": 0.80,
    "ignore_cross_album": False,
}

# Ported from the current upstream review detector: numbered performances
# differ, while years that merely describe a remaster/edition do not.
_ROMAN_SEQUENCE = re.compile(r"\b(?:pt|pts|part|parts|movement|movements|segue|interlude|chapter|act)\s*([ivx]+)\b")
_ROMAN_VALUES = dict(zip("i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx".split(), range(1, 21), strict=True))
_EDITION_YEAR = re.compile(
    r"\b(?:remaster(?:ed)?|mix|edition|version)\s*(?P<after>(?:19|20)\d{2})\b"
    r"|\b(?P<before>(?:19|20)\d{2})(?=\s*(?:remaster(?:ed)?|mix|edition|version)\b)"
)
_LIVE_YEAR = re.compile(r"\blive\s+(?:(?:mix|edition|version)\s+)?(?P<year>(?:19|20)\d{2})\b")


def _title_numbers(title: str) -> tuple[int, ...]:
    years = {m.start("after" if m.group("after") else "before") for m in _EDITION_YEAR.finditer(title)}
    years.difference_update(m.start("year") for m in _LIVE_YEAR.finditer(title))
    numbers = [(m.start(), int(m.group())) for m in re.finditer(r"\d+", title) if m.start() not in years]
    for match in _ROMAN_SEQUENCE.finditer(title):
        if value := _ROMAN_VALUES.get(match.group(1)):
            numbers.append((match.start(1), value))
    return tuple(value for _, value in sorted(numbers))


def _filename_key(path: str) -> str:
    from core.imports.file_ops import _strip_slskd_dedup_suffix

    name = os.path.basename(str(path or "").replace("\\", "/"))
    return _strip_slskd_dedup_suffix(os.path.splitext(name)[0]).casefold()


def _physical_key(path: str):
    try:
        stat = os.stat(path)
        return stat.st_dev, stat.st_ino
    except OSError:
        return None


def _file_snapshot(subject: dict) -> dict:
    try:
        stat = os.stat(subject["resolved_path"])
        disk = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    except OSError:
        disk = None
    return {
        "file_id": int(subject["file_id"]), "track_id": int(subject["track_id"]),
        "path": subject["path"], "resolved_path": subject["resolved_path"],
        "owner_profile_id": subject.get("owner_profile_id"), "disk": disk,
        "title": subject["title"], "artist": subject["artist_name"],
        "album_id": subject["album_id"], "duration": subject.get("duration"),
        "isrc": subject.get("isrc"), "musicbrainz_id": subject.get("musicbrainz_id"),
        "canonical_track_id": subject.get("canonical_track_id"),
        "content_hash": subject.get("content_hash"),
    }


def _playlist_context(playlist_membership, server_source):
    if playlist_membership is not None:
        return playlist_membership, server_source
    from core.library.playlist_membership import _active_server_and_client, server_playlist_membership

    source, _client = _active_server_and_client()
    return server_playlist_membership(), source


def _load_files(database, config_manager, *, scope=None, playlist_membership=None,
                server_source=None, only_file_ids=None) -> list[dict]:
    """Read native subjects and protection facts, without mutating the catalogue.

    ``only_file_ids`` (Keep Best on one group): just those subjects, and of
    the library-wide references only the rows that could be the same physical
    file as one of them (same name, or same size: a hardlink always is). The
    whole library walk -- a stat and a resolve per file row, twice per apply --
    is the scan's job, not one finding's."""
    from core.library2.paths import resolve_lib2_path

    # review 5 R5-E-1: one group's Keep Best read every file, every credit
    # and every mapping in the library (about 51 s of SQL on a big one),
    # twice per click, to keep two or three files. narrowed, each read below
    # covers just the group's own files, tracks, releases and artists
    narrow = only_file_ids is not None
    wanted = {int(i) for i in only_file_ids} if narrow else set()
    subjects = scoped_file_subjects(SimpleNamespace(scope=scope), active_file_subjects(
        database, config_manager, file_ids=sorted(wanted) if narrow else None))
    if narrow:
        subjects = [s for s in subjects if int(s["file_id"]) in wanted]
    if not subjects:
        return []
    membership, source = _playlist_context(playlist_membership, server_source)
    hand_tags = hand_tagged_path_keys(database)
    companion_exts = lossy_companion_exts(config_manager, database)
    ids = sorted({int(s["track_id"]) for s in subjects})
    album_ids = sorted({int(s["album_id"]) for s in subjects})
    artist_ids = sorted({int(s["artist_id"]) for s in subjects if s.get("artist_id") is not None})
    file_ids = sorted({int(s["file_id"]) for s in subjects})
    each = "SELECT value FROM json_each(?)"

    def only(column: str, values) -> tuple[str, tuple]:
        """`` AND column IN (values)`` when narrowed, else nothing."""
        if not narrow:
            return "", ()
        return f" AND {column} IN ({each})", (json.dumps(list(values)),)

    with closing(database._get_connection()) as conn:
        # the quality order, and a tier that two files of the same quality
        # share (every sort column but the id), so a tie can go to the album.
        # ranks only ever compare inside one group, so the group's own files
        # rank the same among themselves as among the whole library
        order, tiers = {}, {}
        last, tier = None, -1
        where, args = only("id", file_ids)
        for i, r in enumerate(conn.execute(
                f"SELECT id, {quality_tier_columns()} FROM lib2_track_files"
                f" WHERE 1=1{where} ORDER BY {quality_order()}", args)):
            if tuple(r[1:]) != last:
                last, tier = tuple(r[1:]), tier + 1
            order[int(r[0])], tiers[int(r[0])] = i, tier
        # All owners participate in reference protection, including files
        # outside this scan's allowlist or library scope.
        if not narrow:
            references = [dict(r) for r in conn.execute(
                "SELECT id,track_id,path,owner_profile_id,size FROM lib2_track_files "
                "WHERE COALESCE(file_state,'active')<>'deleted'")]
        else:
            # only rows that could be the same physical file as one of the
            # group's: same name, or same size (a hardlink always is), or a
            # size nobody recorded. sql narrows (LIKE is a superset: it
            # ignores ascii case); the exact test below still decides
            names = {os.path.basename(str(s["path"] or "").replace("\\", "/")) for s in subjects}
            names.discard("")
            sizes = {r[0] for r in conn.execute(
                f"SELECT size FROM lib2_track_files WHERE id IN ({each}) AND size"
                " AND COALESCE(file_state,'active')<>'deleted'",
                (json.dumps(sorted(wanted)),))}
            for subject in subjects:  # the group's own files, measured: a few stats
                try:
                    sizes.add(os.path.getsize(str(subject["path"])))
                except OSError:
                    pass

            def _like(name: str) -> str:
                return "%/" + name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

            name_terms = " OR ".join(
                "path = ? OR REPLACE(path, '\\', '/') LIKE ? ESCAPE '\\'" for _ in names)
            name_args = [arg for name in sorted(names) for arg in (name, _like(name))]
            references = [dict(r) for r in conn.execute(
                "SELECT id,track_id,path,owner_profile_id,size FROM lib2_track_files "
                "WHERE COALESCE(file_state,'active')<>'deleted' AND ("
                f"id IN ({each}) OR size IS NULL OR size = 0 OR size IN ({each})"
                + (f" OR {name_terms}" if name_terms else "") + ")",
                (json.dumps(sorted(wanted)), json.dumps(sorted(sizes)), *name_args))]
            references = [r for r in references
                          if r["id"] in wanted or not r["size"] or r["size"] in sizes
                          or os.path.basename(str(r["path"] or "").replace("\\", "/")) in names]
        if narrow:
            overrides = {(r[0], int(r[1])) for r in conn.execute(
                "SELECT entity_type,entity_id FROM lib2_metadata_overrides "
                f"WHERE (entity_type='track' AND entity_id IN ({each}))"
                f" OR (entity_type='release_group' AND entity_id IN ({each}))"
                f" OR (entity_type='artist' AND entity_id IN ({each}))",
                (json.dumps(ids), json.dumps(album_ids), json.dumps(artist_ids)))}
        else:
            overrides = {(r[0], int(r[1])) for r in conn.execute(
                "SELECT entity_type,entity_id FROM lib2_metadata_overrides")}
        where, args = only("id", album_ids)
        pins = {int(r[0]) for r in conn.execute(
            f"SELECT id FROM lib2_albums WHERE canonical_locked=1{where}", args)}
        where, args = only("canonical_track_id", ids)
        dependents = {int(r[0]) for r in conn.execute(
            f"SELECT canonical_track_id FROM lib2_tracks WHERE canonical_track_id IS NOT NULL{where}",
            args)}
        where, args = only("derived_from_file_id", file_ids)
        derivative_parents = {int(r[0]) for r in conn.execute(
            "SELECT derived_from_file_id FROM lib2_track_files "
            f"WHERE derived_from_file_id IS NOT NULL AND COALESCE(file_state,'active')<>'deleted'{where}",
            args)}
        mappings = defaultdict(set)
        where, args = only("entity_id", ids)
        for row in conn.execute("SELECT entity_id,server_source FROM lib2_media_server_mappings "
                                f"WHERE entity_type='track'{where}", args):
            mappings[int(row[0])].add(str(row[1] or "").lower())
        playlists = {}
        for start in range(0, len(ids), 500):
            playlists.update(track_server_playlists(conn, ids[start:start + 500], source, membership or {}))
        credits = defaultdict(list)
        where, args = only("ta.track_id", ids)
        for row in conn.execute("SELECT ta.track_id,a.name FROM lib2_track_artists ta "
                                f"JOIN lib2_artists a ON a.id=ta.artist_id WHERE 1=1{where} "
                                "ORDER BY ta.position", args):
            credits[int(row[0])].append(row[1])
        # who performs each song, as the row says (a compilation's own credit)
        where, args = only("id", ids)
        performers = {int(r[0]): r[1] for r in conn.execute(
            "SELECT id, track_artist FROM lib2_tracks"
            f" WHERE track_artist IS NOT NULL AND track_artist <> ''{where}", args)}

    reference_keys = defaultdict(set)
    inode_keys = defaultdict(set)
    mount_keys = defaultdict(set)
    for row in references:
        path = str(row["path"] or "")
        resolved = path if os.path.isfile(path) else (resolve_lib2_path(path, config_manager=config_manager) or path)
        reference_keys[os.path.realpath(resolved)].add(int(row["id"]))
        if inode := _physical_key(resolved):
            inode_keys[inode].add(int(row["id"]))
        parts = path.replace("\\", "/").split("/")
        if len(parts) > 3:
            mount_keys[tuple(parts[-3:])].add((tuple(parts[:-3]), int(row["id"])))

    for subject in subjects:
        fid, tid = int(subject["file_id"]), int(subject["track_id"])
        subject["musicbrainz_id"] = subject.get("musicbrainz_recording_id")
        subject["resolved_path"] = (subject["path"] if os.path.isfile(subject["path"]) else
                                    resolve_lib2_path(subject["path"], config_manager=config_manager) or subject["path"])
        subject["norm_title"] = _normalized_title(subject["title"])
        subject["title_numbers"] = _title_numbers(subject["norm_title"])
        subject["artist_names"] = _performer_names(
            credits.get(tid) or [subject["artist_name"]], performers.get(tid), subject)
        subject["quality_order"] = order[fid]
        subject["quality_tier"] = tiers[fid]
        subject["playlists"] = playlists.get(tid, [])
        reasons = []
        if subject.get("primary_manual"):
            reasons.append("manual_primary")
        if is_hand_tagged_path(subject["path"], hand_tags):
            reasons.append("hand_tagged")
        if subject.get("file_role") in {"derivative", "alternate"} or subject.get("source") == "companion" or subject.get("derived_from_file_id"):
            reasons.append("intentional_format")
        if fid in derivative_parents:
            reasons.append("derivative_source")
        stem = os.path.splitext(subject["resolved_path"])[0]
        if (is_lossy_companion_file(subject["resolved_path"], companion_exts)
                or any(os.path.isfile(stem + ext) and is_lossy_companion_pair(
                    subject["resolved_path"], stem + ext, companion_exts) for ext in companion_exts)):
            reasons.append("intentional_format")
        if subject.get("retention_json") not in (None, "", "{}", "null"):
            reasons.append("retention_policy")
        if any(key in overrides for key in (("track", tid), ("release_group", int(subject["album_id"])), ("artist", int(subject["artist_id"])))):
            reasons.append("manual_metadata")
        if int(subject["album_id"]) in pins:
            reasons.append("pinned_release")
        if tid in dependents:
            reasons.append("canonical_reference")
        path = os.path.realpath(subject["resolved_path"])
        inode = _physical_key(subject["resolved_path"])
        parts = subject["path"].replace("\\", "/").split("/")
        aliases = mount_keys.get(tuple(parts[-3:]), set())
        if (reference_keys[path] - {fid} or (inode and inode_keys[inode] - {fid})
                or any(root != tuple(parts[:-3]) and other != fid for root, other in aliases)):
            reasons.append("shared_file_reference")
        if subject["playlists"]:
            reasons.append("playlist_reference")
        # The active server's playlists were read above, so its mapping alone
        # protects nothing (or every server-known copy would stay forever).
        # Another server's playlists are invisible here: never guess it safe.
        if mappings.get(tid, set()) - {str(source or "").lower()}:
            reasons.append("media_server_reference")
        if not os.path.isfile(subject["resolved_path"]):
            reasons.append("file_unavailable")
        subject["protected_reasons"] = reasons
        subject["snapshot"] = _file_snapshot(subject)
    return subjects


def _performer_names(credited: list, track_artist, subject: dict) -> list:
    """The names a song is credited to, folded, for the artist half of a
    tag match. "Various Artists" is never one of them: it is the album's
    credit on every song of a compilation, so two different performers'
    "Intro" on one compilation matched on it (#1263). A compilation song
    whose only credit is that falls back to its performer, as the row has
    it (track_artist), then as the file's own artist tag has it (3.6.1's
    #1315 healing). Nothing left means no artist evidence at all."""
    from core.imports.compilation import is_various_artists_name
    from core.library2.importer import split_artist_credits

    names = [n for n in credited if n and not is_various_artists_name(n)]
    if not names:
        tag = None
        try:
            tag = (json.loads(subject.get("tags_json") or "{}") or {}).get("artist")
        except (TypeError, ValueError):
            pass
        names = [n for n in (track_artist, tag) if n and not is_various_artists_name(n)][:1]
    return [str(n).casefold().strip() for name in names
            for n in split_artist_credits(name or "") if n and not is_various_artists_name(n)]


def _strong_evidence(a: dict, b: dict) -> bool:
    if a["track_id"] == b["track_id"]:
        return True
    if a.get("canonical_track_id") == b["track_id"] or b.get("canonical_track_id") == a["track_id"]:
        return True
    if a.get("canonical_track_id") and a["canonical_track_id"] == b.get("canonical_track_id"):
        return True
    for key in ("isrc", "musicbrainz_id", "content_hash"):
        one, two = str(a.get(key) or "").strip().casefold(), str(b.get(key) or "").strip().casefold()
        if one and one == two:
            return True
    return False


def _candidate_pair(a: dict, b: dict, settings: dict, companion_exts: set) -> bool:
    if a["owner_profile_id"] != b["owner_profile_id"] or not same_recording(a, b):
        return False
    if settings["ignore_cross_album"] and a["album_id"] != b["album_id"]:
        return False
    one, two = a["title_numbers"], b["title_numbers"]
    if one and two and one != two:
        return False
    strong = _strong_evidence(a, b)
    if not strong and not _similar_tags(a, b, settings):
        return False
    # 3.6.1's Single/Album Dedup never paired a live, acoustic, remix or edit
    # with the studio cut ("Smells Like Teen Spirit (Live)" scores 0.90 on
    # the title alone); only real recording evidence overrides the words
    if not strong:
        from core.library2.version_words import DEDUP_3_6_1, versions_differ
        if versions_differ(a.get("title") or "", b.get("title") or "", DEDUP_3_6_1):
            return False
    # third #1062 review P2D-7: one file seen at two mount roots (plex's
    # /media/Music/A/B/01.flac, SoulSync's /app/Transfer/A/B/01.flac) is not
    # a duplicate; 3.6.1 never filed it, v2 filed one Keep Best can't fix
    if _mount_aliases(a, b):
        return False
    # Filesystem identity last: only plausible pairs pay for realpath/stat.
    if os.path.realpath(a["resolved_path"]) == os.path.realpath(b["resolved_path"]):
        return False
    inode = _physical_key(a["resolved_path"])
    if inode and inode == _physical_key(b["resolved_path"]):
        return False
    return not (a.get("derived_from_file_id") == b["file_id"] or b.get("derived_from_file_id") == a["file_id"]
                or is_lossy_companion_pair(a["resolved_path"], b["resolved_path"], companion_exts))


def _mount_aliases(a: dict, b: dict) -> bool:
    """the same artist/album/file under different roots, the same length
    (within a second): 3.6.1's _is_same_physical_file"""
    parts = []
    for row in (a, b):
        parts.append([x for x in str(row.get("path") or "").replace("\\", "/").split("/") if x])
    one, two = parts
    if len(one) < 3 or len(two) < 3:
        return False
    if [x.lower() for x in one[-3:]] != [x.lower() for x in two[-3:]] or one[:-3] == two[:-3]:
        return False
    if a.get("duration") and b.get("duration"):
        return abs(int(a["duration"]) - int(b["duration"])) <= 1000
    return True


def _similar_tags(a: dict, b: dict, settings: dict) -> bool:
    artist_score = max((SequenceMatcher(None, x, y).ratio() for x in a["artist_names"] for y in b["artist_names"]), default=0)
    if a["norm_title"] and b["norm_title"] and artist_score >= settings["artist_similarity"]:
        if SequenceMatcher(None, a["norm_title"], b["norm_title"]).ratio() >= settings["title_similarity"]:
            return True
    if _filename_key(a["path"]) and _filename_key(a["path"]) == _filename_key(b["path"]):
        if a.get("duration") and b.get("duration"):
            same_length = abs(int(a["duration"]) - int(b["duration"])) <= 3000
            # slskd's "_<timestamp>" twin of a file often has no artist tag at
            # all: the same name and the same length are the evidence then
            # (3.6.1 paired these); a blank never pairs without both lengths
            return same_length and (artist_score >= 0.6 or _no_artist(a) or _no_artist(b))
        return artist_score >= 0.6
    return False


_UNKNOWN_ARTISTS = frozenset({"unknown artist", "unknown", "[unknown]", "<unknown>", "various"})


def _no_artist(subject: dict) -> bool:
    """No usable artist name: none at all, or only a placeholder."""
    return all(n in _UNKNOWN_ARTISTS for n in subject.get("artist_names") or [])


def _recommended(files: list[dict]) -> dict:
    # Retain upstream's playlist-first selection, then preserve native manual
    # picks, then the native quality/profile order. Between two copies of the
    # same quality the album's wins over the single's (3.6.1's Single/Album
    # Dedup kept the album version): the album keeps the track numbering and
    # the song's place in the record. A better single is never given up for it.
    from core.library2.recording_links import _home_rank
    return min(files, key=lambda f: (
        not bool(f["playlists"]), not bool(f.get("primary_manual")),
        f.get("quality_tier", f["quality_order"]), -_home_rank(f.get("album_type")),
        f["quality_order"]))


def find_duplicate_candidates(database, config_manager=None, *, settings=None, scope=None,
                              playlist_membership=None, server_source=None, check_stop=None) -> list[dict]:
    """Find unlinked and confirmed, multi-file and cross-format review groups."""
    options = {**DEFAULT_SETTINGS, **(settings or {})}
    for name in ("title_similarity", "artist_similarity"):
        options[name] = float(options[name])
        if not 0 <= options[name] <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
    files = _load_files(database, config_manager, scope=scope, playlist_membership=playlist_membership, server_source=server_source)
    # a file the user tagged by hand is what they say it is (often the live
    # take or the bootleg next to the studio copy): never one of a pair, or
    # Keep Best could keep it and quarantine the studio copy (3.6.1 left
    # these out before grouping too)
    files = [f for f in files if "hand_tagged" not in f["protected_reasons"]]
    companion_exts = lossy_companion_exts(config_manager, database)
    buckets = defaultdict(list)
    artists = defaultdict(list)
    for f in files:
        owner = f.get("owner_profile_id")
        keys = [("filename", _filename_key(f["path"])), ("track", f["track_id"])]
        for name in set(f['artist_names']):
            artists[(owner, name)].append(f)
        keys.extend((key, str(f.get(key) or "").strip().casefold()) for key in ("isrc", "musicbrainz_id", "content_hash"))
        if f.get("canonical_track_id"):
            keys.append(("track", f["canonical_track_id"]))
        for kind, value in keys:
            if value:
                buckets[(owner, kind, value)].append(f)
    # Fuzzy title comparison cannot require an exact title prefix. Similar
    # credit names are compared inside blocks of the same leading or trailing
    # letters, never every name against every other name of the library.
    blocks = defaultdict(set)
    for owner, name in artists:
        buckets[(owner, 'artist', name)] = artists[(owner, name)]
        letters = re.sub(r"\W+", "", name)
        blocks[(owner, letters[:3])].add(name)
        blocks[(owner, "", letters[-3:])].add(name)
    for (owner, *_), block in blocks.items():
        block = sorted(block)
        for i, name in enumerate(block):
            if check_stop and check_stop():
                return []
            for other in block[i + 1:]:
                if SequenceMatcher(None, name, other).ratio() >= options['artist_similarity']:
                    buckets[(owner, 'artist_pair', (name, other))] = artists[(owner, name)] + artists[(owner, other)]
    neighbors = defaultdict(set)
    for members in buckets.values():
        for i, a in enumerate(members):
            if check_stop and check_stop():
                return []
            for b in members[i + 1:]:
                if b["file_id"] in neighbors.get(a["file_id"], ()):
                    continue
                if _candidate_pair(a, b, options, companion_exts):
                    neighbors[a["file_id"]].add(b["file_id"])
                    neighbors[b["file_id"]].add(a["file_id"])
    by_id = {f["file_id"]: f for f in files}
    used = set()
    candidates = []
    for fid in sorted(neighbors):
        if fid in used:
            continue
        group = [fid]
        for other in sorted(neighbors[fid] - used):
            # Every pair must agree: an incomplete-ID/numberless row must
            # never bridge genuinely conflicting recordings into one group.
            if all(other in neighbors[member] for member in group):
                group.append(other)
        if len(group) < 2:
            continue
        used.update(group)
        members = [by_id[value] for value in group]
        keeper = _recommended(members)
        weak = any(not _strong_evidence(a, b) or a["norm_title"] != b["norm_title"]
                   for i, a in enumerate(members) for b in members[i + 1:])
        snapshots = [f["snapshot"] for f in members]
        token = hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest()
        candidates.append({
            "schema": REVIEW_SCHEMA, "review_token": token, "settings": options,
            "scope": scope, "count": len(members), "recommended_file_id": keeper["file_id"],
            "requires_recording_confirmation": weak, "owner_profile_id": keeper.get("owner_profile_id"),
            "tracks": [{**f["snapshot"], "id": f["file_id"], "artist_name": f["artist_name"],
                        "album": f["album_title"], "file_path": f["path"], "format": f.get("format"),
                        "bitrate": f.get("bitrate"), "playlists": f["playlists"],
                        "protected_reasons": f["protected_reasons"]} for f in members],
            "artist_id": keeper["artist_id"], "album_thumb_url": keeper.get("album_image"),
            "artist_thumb_url": keeper.get("artist_image"),
            "library_v2": {
                "track_ids": sorted({f["track_id"] for f in members}),
                "file_ids": [f["file_id"] for f in members],
            },
        })
    return candidates


_PERMISSION_MARKERS = ("permission denied", "errno 13", "operation not permitted", "errno 1]",
                       "read-only file system", "errno 30")


def _quarantine_failure(failed: list, rollback: Optional[dict] = None) -> str:
    """What stopped Keep Best, in words: which file and why, and for a
    permission error what to check (3.6.1 said "usually a PUID/PGID
    permission mismatch on the media volume"; v2 said only "Duplicate
    quarantine failed"). Logged as well as returned."""
    first = (failed or [{}])[0]
    path = first.get("path") or "a copy"
    reason = str(first.get("error") or "unknown error")
    message = f"Couldn't move {path} to the deleted-files folder ({reason})."
    if (rollback or {}).get("errors"):
        message += (" Some copies already moved could not be put back; they are in the "
                    "deleted-files folder and can be restored from there.")
    else:
        message += " Nothing was removed; the copies are where they were."
    if any(marker in reason.lower() for marker in _PERMISSION_MARKERS):
        message += (" SoulSync isn't allowed to change that folder. In Docker this is "
                    "usually a PUID/PGID mismatch with the owner of your music volume.")
    if len(failed or []) > 1:
        message += f" ({len(failed)} files failed.)"
    logger.warning("Keep Best: %s", message)
    return message


def _rollback_moves(database, files: list[dict], transfer_folder: str) -> dict:
    from core.library.deleted_quarantine import list_entries, restore_entries

    entries = list_entries(transfer_folder)["entries"]
    paths = {os.path.realpath(f["resolved_path"]): f for f in files}
    ids = [e["id"] for e in entries if e["source"] == "native_duplicate_detector"
           and os.path.realpath(e["original_path"]) in paths]
    result = restore_entries(transfer_folder, ids)
    with closing(database._get_connection()) as conn:
        for f in files:
            if os.path.isfile(f["resolved_path"]):
                conn.execute("UPDATE lib2_track_files SET file_state='active',is_primary=?,primary_manual=? WHERE id=?",
                             (f.get("is_primary", 0), f.get("primary_manual", 0), f["file_id"]))
        conn.commit()
    return result


def apply_keep_best(database, review: dict, *, config_manager=None, transfer_folder: str,
                    approved: bool = False, keep_file_id=None, confirm_recording: bool = False,
                    playlist_membership=None, server_source=None) -> dict:
    """Apply a reviewed native group; legacy IDs and stale payloads fail closed."""
    from core.library.deleted_quarantine import quarantine_mover
    from core.library2.file_delete import delete_files_journaled

    if approved is not True:
        return {"success": False, "error": "Keep Best requires approval"}
    if review.get("schema") != REVIEW_SCHEMA or not review.get("review_token"):
        return {"success": False, "error": "Native duplicate review required; legacy IDs cannot be applied"}
    original = review.get("tracks") or []
    try:
        ids = [int(f["file_id"]) for f in original]
        if len(set(ids)) != len(ids) or len(ids) < 2:
            raise ValueError("Invalid duplicate group")
        # Read the server's playlists once for this group, not once per move.
        playlist_membership, server_source = _playlist_context(playlist_membership, server_source)
        load = lambda: {f["file_id"]: f for f in _load_files(  # noqa: E731
            database, config_manager, scope=review.get("scope"),
            playlist_membership=playlist_membership, server_source=server_source,
            only_file_ids=ids)}
        by_id = load()
        files = [by_id[fid] for fid in ids]
        if not playlist_membership:
            # An empty answer may be a failed read: keep the scan's playlists.
            for f, seen in zip(files, original, strict=False):
                if seen.get("playlists") and "playlist_reference" not in f["protected_reasons"]:
                    f["protected_reasons"].append("playlist_reference")
        snapshots = [f["snapshot"] for f in files]
        token = hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest()
        if token != review["review_token"]:
            raise ValueError("Files or recording identity changed; refresh this review")
        keeper = by_id[int(keep_file_id)] if keep_file_id is not None else _recommended(files)
        if keeper["file_id"] not in ids or "file_unavailable" in keeper["protected_reasons"]:
            raise ValueError("The selected keeper is not an available copy on this review")
        if "hand_tagged" in keeper["protected_reasons"]:
            # tagged by hand since the review: it is its own recording now, and
            # keeping it would quarantine the copy it was paired with
            raise ValueError("The selected keeper was tagged by hand since the review, "
                             "so it is no longer a duplicate; refresh this review")
        options = {**DEFAULT_SETTINGS, **(review.get("settings") or {})}
        companions = lossy_companion_exts(config_manager, database)
        for i, a in enumerate(files):
            for b in files[i + 1:]:
                if not _candidate_pair(a, b, options, companions):
                    raise ValueError("The reviewed copies no longer form a compatible duplicate group")
                if (not _strong_evidence(a, b) or a["norm_title"] != b["norm_title"]) and confirm_recording is not True:
                    raise ValueError("Similar tags are review evidence only; confirm the same recording before Keep Best")
        removable = [f for f in files if f["file_id"] != keeper["file_id"] and not f["protected_reasons"]]
        # Only empty tracks get a new link, and only when the existing manual
        # relationship validator accepts it. No canonical choice is rewritten.
        links = []
        removing_ids = {f["file_id"] for f in removable}
        with closing(database._get_connection()) as conn:
            target = int(keeper.get("canonical_track_id") or keeper["track_id"])
            for source in sorted({int(f["track_id"]) for f in removable} - {target, int(keeper["track_id"])}):
                remaining = conn.execute("SELECT id FROM lib2_track_files WHERE track_id=? AND COALESCE(file_state,'active')='active'", (source,)).fetchall()
                if any(int(row[0]) not in removing_ids for row in remaining):
                    continue
                row = conn.execute("SELECT canonical_track_id FROM lib2_tracks WHERE id=?", (source,)).fetchone()
                if row[0] is not None:
                    continue
                validate_duplicate_pair(conn, source, target, confirmed_recording=confirm_recording)
                links.append((source, target))
    except (KeyError, TypeError, ValueError, DuplicateRelationshipError) as exc:
        return {"success": False, "error": str(exc)}

    operations = []
    moved = []
    mover = quarantine_mover(transfer_folder, "native_duplicate_detector")
    fresh = {}
    for f in removable:
        # Re-read protection facts once, right before the first physical move.
        # A playlist mapping, hand tag or shared reference added after scan wins.
        def guarded_move(path, file=f):
            if "files" not in fresh:
                fresh["files"] = load()
            lookup = fresh["files"]
            now = lookup.get(file["file_id"])
            kept = lookup.get(keeper["file_id"])
            if not now or now["snapshot"] != file["snapshot"] or now["protected_reasons"]:
                raise ValueError("Duplicate gained protection or changed before removal")
            if not kept or kept["snapshot"] != keeper["snapshot"] or not os.path.isfile(kept["resolved_path"]):
                raise ValueError("Keeper changed before removal")
            mover(path)
        outcome = delete_files_journaled(
            database, targets=[{"path": f["resolved_path"], "stored_path": f["path"],
                                "file_ids": [f["file_id"]], "track_ids": [f["track_id"]]}],
            entity_type="albums", entity_id=int(f["album_id"]), actor="repair:native_duplicate_detector",
            actor_profile_id=f.get("owner_profile_id"), config_manager=config_manager,
            unlink=guarded_move, mode="quarantine",
        )
        operations.append(outcome["operation_id"])
        if outcome["failed"]:
            rollback = _rollback_moves(database, moved, transfer_folder)
            return {"success": False, "error": _quarantine_failure(outcome["failed"], rollback),
                    "failed": outcome["failed"],
                    "rollback": rollback, "operation_ids": operations}
        moved.append(f)
    try:
        with closing(database._get_connection()) as conn:
            for source, target in links:
                validate_duplicate_pair(conn, source, target, confirmed_recording=confirm_recording)
                conn.execute("UPDATE lib2_tracks SET canonical_track_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (target, source))
            conn.commit()
    except Exception as exc:
        rollback = _rollback_moves(database, moved, transfer_folder)
        return {"success": False, "error": str(exc), "rollback": rollback, "operation_ids": operations}
    protected = [{"file_id": f["file_id"], "reasons": f["protected_reasons"]} for f in files
                 if f["file_id"] != keeper["file_id"] and f["protected_reasons"]]
    return {
        "success": True, "action": "keep_best", "kept_file_id": keeper["file_id"],
        "kept_track_id": keeper["track_id"], "removed_file_ids": [f["file_id"] for f in moved],
        "protected_files": protected, "operation_ids": operations,
        # The journal has already retired exactly the removed rows. The
        # generic worker's single-file deletion flag would also retire the
        # keeper named by this multi-file finding, so report concrete IDs and
        # recompute ownership/wanted state without repeating retirement.
        "library_v2_recompute_wanted": bool(moved), "track_ids": sorted({f["track_id"] for f in files}),
        "message": f"Kept selected copy; quarantined {len(moved)} redundant file(s), preserved {len(protected)} protected copy/copies",
    }


__all__ = ["apply_keep_best", "find_duplicate_candidates", "DEFAULT_SETTINGS", "REVIEW_SCHEMA"]
