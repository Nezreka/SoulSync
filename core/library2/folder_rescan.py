"""What the folder watcher does with the album folders that changed.

``core.library.folder_watch`` collects changes on disk into album folders and
hands each settled batch here. One batch is a few album folders under one
library folder, and for those folders alone:

1. **a vanished library folder is an outage**: nothing is read, nothing is
   marked missing (a share that dropped looks exactly like a mass delete);
2. **a moved file keeps its record**: a rename the system reported, or a file
   gone from its record's path that turns up unindexed with the same size and
   the same tags, is repointed through the File Locations journal
   (``core.library2.path_repoint``), so plays, padlocks, history and download
   records follow it, and the move can be undone from Tools;
3. **known files are rescanned** with the same code as the artist page's
   "Refresh & Scan" (``core.library2.scan.rescan_files``): new tags and
   quality read, a deleted file goes through the missing lifecycle and its
   outage guards, capped at "suspected" (the next library scan confirms);
4. **new files are indexed in place** through the same writer a media-server
   scan uses for a file it lists that the catalogue lacks
   (``media_server_sync.adopt_scanned_file``): identity from the tags, never
   monitored, never downloaded or upgraded because it was found;
5. open Library pages refresh (``library:changed``).

Paths SoulSync is writing itself (``core.library.own_writes``) are left out at
every step. Stored paths that are the media server's spelling of the folder
(a Docker install whose server sees ``/data/music``) are matched by the album
folder and file name, so a file the catalogue knows under another spelling is
never indexed twice.
"""

from __future__ import annotations

import json
import os
from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from core.library import own_writes
from core.library.folder_watch_paths import (
    ignored_dir_name, is_audio_name, is_under, norm, parent, relative_parts,
)
from utils.logging_config import get_logger

logger = get_logger("library2.folder_rescan")

#: the source stamped on a file row the watcher indexed
SOURCE = "folder_watch"
#: suffix patterns per query when stored paths are another machine's spelling
_SUFFIX_CHUNK = 40
#: a duration this close is the same recording
_DURATION_SLACK_MS = 2000


def _reachable(folder: str) -> bool:
    try:
        if not os.path.isdir(folder):
            return False
        with os.scandir(folder) as it:
            return next(iter(it), None) is not None
    except OSError:
        return False


def _audio_under(folder: str, recursive: bool, excluded: Iterable[str]) -> Dict[str, int]:
    """``{path: size}`` of the audio files in ``folder``."""
    out: Dict[str, int] = {}
    stack = [folder]
    excluded = tuple(excluded)
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if recursive and not ignored_dir_name(entry.name) and not any(
                                    is_under(entry.path, ex) for ex in excluded):
                                stack.append(entry.path)
                            continue
                        if is_audio_name(entry.name):
                            out[entry.path] = entry.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _spellings(folder: str) -> List[str]:
    """The folder as the catalogue may have stored it: on a Windows path
    both separators."""
    from core.library2.track_files import path_spellings
    return list(path_spellings(norm(folder))) or [norm(folder)]


def _range(folder: str) -> Tuple[str, str]:
    """``path >= lo AND path < hi`` = everything under ``folder``, on the path
    index (a LIKE would not use it)."""
    sep = "\\" if "\\" in folder else "/"
    base = folder.rstrip("\\/") + sep
    return base, base[:-1] + chr(ord(sep) + 1)


_COLUMNS = ("id, track_id, path, size, file_state, missing_scan_count, tags_json, "
            "owner_profile_id")


def _rows_under(conn, folder: str, recursive: bool) -> List[Dict[str, Any]]:
    rows: Dict[int, Dict[str, Any]] = {}
    for spelling in _spellings(folder):
        lo, hi = _range(spelling)
        for row in conn.execute(
                f"SELECT {_COLUMNS} FROM lib2_track_files WHERE path >= ? AND path < ? "
                "AND COALESCE(file_state,'active')<>'deleted'", (lo, hi)):
            item = dict(row)
            if not recursive and norm(parent(str(item["path"]))) != norm(folder):
                continue
            rows[int(item["id"])] = item
    return list(rows.values())


def _stored_under_root(conn, root: str) -> bool:
    for spelling in _spellings(root):
        lo, hi = _range(spelling)
        if conn.execute("SELECT 1 FROM lib2_track_files WHERE path >= ? AND path < ? LIMIT 1",
                        (lo, hi)).fetchone():
            return True
    return False


def _catalogue_has_files(conn) -> bool:
    return conn.execute("SELECT 1 FROM lib2_track_files WHERE COALESCE(file_state,'active')"
                        "<>'deleted' LIMIT 1").fetchone() is not None


def _suffix_key(path: str, depth: int = 3) -> str:
    """The last ``depth`` components, lower case, ``/`` separated: what two
    spellings of one file have in common."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    return "/".join(parts[-depth:]).lower()


def _rows_by_suffix(conn, root: str, units: List[Tuple[str, bool]]) -> List[Dict[str, Any]]:
    """Rows stored under another spelling of this library folder, found by
    the album folder path below the root (``Artist/Album/``)."""
    patterns: List[str] = []
    for folder, _recursive in units:
        rel = relative_parts(folder, root)
        if not rel:
            continue
        tail = "/".join(rel[-2:])
        patterns.append("/" + tail + "/")
        patterns.append("\\" + tail.replace("/", "\\") + "\\")
    rows: Dict[int, Dict[str, Any]] = {}
    for start in range(0, len(patterns), _SUFFIX_CHUNK):
        chunk = patterns[start:start + _SUFFIX_CHUNK]
        where = " OR ".join("instr(path, ?) > 0" for _ in chunk)
        for row in conn.execute(
                f"SELECT {_COLUMNS} FROM lib2_track_files WHERE ({where}) "
                "AND COALESCE(file_state,'active')<>'deleted'", chunk):
            rows[int(row["id"])] = dict(row)
    return list(rows.values())


def _tags_of(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        value = json.loads(row.get("tags_json") or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _number(value: Any) -> Optional[int]:
    try:
        return int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return None


def same_recording(stored: Dict[str, Any], found: Dict[str, Any]) -> bool:
    """Whether a file's tags say it is the file a record remembers: the same
    title, and the same album, artist, track and disc numbers and length
    wherever both sides know them. A record with no title in its tags is never
    matched by tags alone."""
    title = _text(stored.get("title"))
    if not title or title != _text(found.get("title")):
        return False
    for key in ("album",):
        a, b = _text(stored.get(key)), _text(found.get(key))
        if a and b and a != b:
            return False
    artist_a = _text(stored.get("albumartist") or stored.get("artist"))
    artist_b = _text(found.get("albumartist") or found.get("album_artist") or found.get("artist"))
    if artist_a and artist_b and artist_a != artist_b and \
            _text(stored.get("artist")) != _text(found.get("artist")):
        return False
    for key in ("track_number", "disc_number"):
        a, b = _number(stored.get(key)), _number(found.get(key))
        if a is not None and b is not None and a != b:
            return False
    a, b = stored.get("duration_ms"), found.get("duration_ms")
    try:
        if a and b and abs(int(a) - int(b)) > _DURATION_SLACK_MS:
            return False
    except (TypeError, ValueError):
        pass
    return True


def _found_tags(path: str) -> Dict[str, Any]:
    from core.library2.tag_cache import normalized_tag_snapshot, read_tag_snapshot
    raw = read_tag_snapshot(path)
    if raw.get("error"):
        return {}
    return normalized_tag_snapshot(raw)


def _missing_candidates(conn, sizes: Set[int], skip: Set[int]) -> List[Dict[str, Any]]:
    """Records already marked missing with one of these sizes: a file that
    left another folder in an earlier batch."""
    out: List[Dict[str, Any]] = []
    values = sorted(s for s in sizes if s)
    for start in range(0, len(values), 500):
        chunk = values[start:start + 500]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
                f"SELECT {_COLUMNS} FROM lib2_track_files WHERE size IN ({marks}) "
                "AND file_state IN ('missing_suspected','missing_confirmed')", chunk):
            if int(row["id"]) not in skip:
                out.append(dict(row))
    return out


def _row_at(conn, path: str) -> Optional[Dict[str, Any]]:
    from core.library2.track_files import path_spellings
    forms = list(path_spellings(path)) or [path]
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM lib2_track_files WHERE path IN ({','.join('?' * len(forms))}) "
        "AND COALESCE(file_state,'active')<>'deleted' ORDER BY id LIMIT 1", forms).fetchone()
    return dict(row) if row else None


def _size_ok(row: Dict[str, Any], size: Optional[int], *, strict: bool) -> bool:
    stored = row.get("size")
    if stored in (None, 0):
        return not strict
    return size is not None and int(stored) == int(size)


def plan_moves(conn, moves: List[Tuple[str, str]], unknown: Dict[str, int],
               absent: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], str]]:
    """``[(record, new path)]`` for the files that moved.

    First the renames the system reported (a file or a whole album folder):
    the record at the old path, when the file at the new path has the size it
    remembers. Then, for what is left, a record gone from its path and an
    unindexed file with the same size and the same tags, when each has
    exactly one partner."""
    planned: Dict[int, str] = {}
    taken: Set[str] = set()
    by_row: Dict[int, Dict[str, Any]] = {}

    def _take(row: Dict[str, Any], new_path: str) -> None:
        rid = int(row["id"])
        if rid in planned or new_path in taken:
            return
        planned[rid] = new_path
        taken.add(new_path)
        by_row[rid] = row

    for src, dst in moves:
        if os.path.isdir(dst):
            pairs = [(os.path.join(src, os.path.relpath(path, dst)), path)
                     for path in unknown if is_under(path, dst)]
        else:
            pairs = [(src, dst)] if dst in unknown else []
        for old, new in pairs:
            if os.path.exists(old):
                continue  # a copy, not a move
            row = _row_at(conn, old)
            if row is not None and _size_ok(row, unknown.get(new), strict=False):
                _take(row, new)

    left = {p: s for p, s in unknown.items() if p not in taken}
    if not left:
        return [(by_row[rid], path) for rid, path in planned.items()]
    pool = [r for r in absent if int(r["id"]) not in planned]
    pool_ids = {int(r["id"]) for r in pool}
    pool += _missing_candidates(conn, set(left.values()), pool_ids | set(planned))
    by_size: Dict[int, List[Dict[str, Any]]] = {}
    for row in pool:
        if row.get("size"):
            by_size.setdefault(int(row["size"]), []).append(row)
    claims: Dict[int, List[str]] = {}
    files_matched: Dict[str, List[int]] = {}
    for path, size in sorted(left.items()):
        candidates = by_size.get(int(size or 0)) or []
        if not candidates:
            continue
        tags = _found_tags(path)
        if not tags:
            continue
        matched = [int(r["id"]) for r in candidates if same_recording(_tags_of(r), tags)]
        files_matched[path] = matched
        for rid in matched:
            claims.setdefault(rid, []).append(path)
    rows_by_id = {int(r["id"]): r for r in pool}
    for path, matched in files_matched.items():
        if len(matched) == 1 and len(claims.get(matched[0], [])) == 1:
            _take(rows_by_id[matched[0]], path)
    return [(by_row[rid], path) for rid, path in planned.items()]


def apply_moves(database, planned: List[Tuple[Dict[str, Any], str]]) -> Set[int]:
    """Repoint the records through the File Locations journal (undoable from
    Tools, references followed). Returns the file ids moved."""
    if not planned:
        return set()
    from core.library2 import path_repoint
    moves = [(row, path_repoint.Verdict(path_repoint.REPOINT, new_path=path,
                                        old_root=parent(str(row["path"])), new_root=parent(path)))
             for row, path in planned]
    run_id = path_repoint._start_run(database, "watch", resume=False)
    done, _refused = path_repoint._apply_batch(database, run_id, moves)
    path_repoint._bump_run(database, run_id, len(done))
    path_repoint._finish_run(database, run_id, "done")
    if done:
        logger.info("folder watch: %d moved file(s) kept their records (File Locations run %d)",
                    len(done), run_id)
    return done


def process_changes(database, root: str, units: List[Tuple[str, bool]],
                    moves: Optional[List[Tuple[str, str]]] = None, *,
                    excluded: Iterable[str] = (), config_manager: Any = None,
                    adopt: bool = True) -> Dict[str, Any]:
    """Read the changed album folders under one library folder. See the
    module docstring for the order. Returns counts; ``outage`` when the
    library folder itself is not reachable."""
    stats: Dict[str, Any] = {"folders": len(units), "rescanned": 0, "moved": 0, "added": 0,
                             "missing": 0, "recovered": 0, "own_writes_skipped": 0}
    root_n = norm(root)
    if not _reachable(root_n):
        logger.warning("folder watch: %s is not reachable; treating it as an outage, "
                       "nothing is marked missing", root_n)
        return {**stats, "outage": True}
    excluded = tuple(excluded)

    # what is on disk now, minus what SoulSync is writing itself
    disk: Dict[str, int] = {}
    for folder, recursive in units:
        if not os.path.isdir(folder):
            continue
        for path, size in _audio_under(folder, recursive, excluded).items():
            if own_writes.is_own(path):
                stats["own_writes_skipped"] += 1
                continue
            disk[path] = size

    with closing(database._get_connection()) as conn:
        rows: Dict[int, Dict[str, Any]] = {}
        mapped = False
        if not _stored_under_root(conn, root_n) and _catalogue_has_files(conn):
            mapped = True
        for folder, recursive in units:
            for row in _rows_under(conn, folder, recursive):
                rows[int(row["id"])] = row
        if mapped:
            for row in _rows_by_suffix(conn, root_n, units):
                rows.setdefault(int(row["id"]), row)

        # which records are there: by their own path, or (another machine's
        # spelling) by where the resolver finds them
        present: List[int] = []
        absent: List[Dict[str, Any]] = []
        known_disk: Set[str] = set()
        held_suffixes: Set[str] = set()
        for rid, row in rows.items():
            stored = str(row["path"])
            on_disk = stored if os.path.isfile(stored) else None
            if mapped:
                # another machine's spelling: a file with this album folder
                # and name is this record's, found or not
                held_suffixes.add(_suffix_key(stored))
                if on_disk is None:
                    from core.library2.paths import resolve_lib2_path
                    on_disk = resolve_lib2_path(stored, config_manager)
            if on_disk is not None and own_writes.is_own(on_disk):
                stats["own_writes_skipped"] += 1
                continue
            if on_disk is not None:
                present.append(rid)
                known_disk.add(own_writes.key(on_disk))
            elif str(row.get("file_state") or "active") != "deleted":
                absent.append(row)
        unknown = {p: s for p, s in disk.items()
                   if own_writes.key(p) not in known_disk and _suffix_key(p) not in held_suffixes}

        planned = plan_moves(conn, list(moves or []), unknown, absent) if unknown else []

    moved = apply_moves(database, planned)
    stats["moved"] = len(moved)
    moved_paths = {path for row, path in planned if int(row["id"]) in moved}
    for path in moved_paths:
        unknown.pop(path, None)

    scan_ids = sorted(set(present) | {int(r["id"]) for r in absent} | moved)
    presence: List[int] = []
    if scan_ids:
        from core.library2.scan import rescan_files
        scanned = rescan_files(database, file_ids=scan_ids, manual=False,
                               confirm_missing=False, on_presence_change=presence.extend)
        stats["rescanned"] = int(scanned.get("scanned", 0))
        stats["missing"] = int(scanned.get("missing", 0))
        stats["recovered"] = int(scanned.get("recovered", 0))
        if scanned.get("mass_missing_deferred"):
            stats["outage_deferred"] = int(scanned["mass_missing_deferred"])

    added_ids: List[int] = []
    if adopt and unknown:
        added_ids = _adopt(database, sorted(unknown))
        stats["added"] = len(added_ids)

    if presence:
        try:
            from core.library2.monitor_sync import sync_scanned_tracks_wishlist
            sync_scanned_tracks_wishlist(database, sorted(set(presence)))
        except Exception as exc:  # noqa: BLE001 - the hourly reconcile still runs
            logger.debug("folder watch: wanted sync after rescan failed: %s", exc)

    touched = sorted(set(scan_ids) | set(added_ids))
    if touched:
        from core.library2.validation import notify_changes
        notify_changes(touched)
    if stats["moved"] or stats["added"] or stats["missing"] or stats["recovered"]:
        logger.info("folder watch: %s: %d folder(s), %d new, %d moved, %d missing, "
                    "%d back, %d rescanned", root_n, len(units), stats["added"], stats["moved"],
                    stats["missing"], stats["recovered"], stats["rescanned"])
    return stats


def _adopt(database, paths: List[str]) -> List[int]:
    """Index unindexed files in place, through the media-server scan's writer."""
    from core.library2.media_server_sync import adopt_scanned_file
    from core.library2.track_files import path_spellings

    added: List[int] = []
    for path in paths:
        if own_writes.is_own(path):
            continue
        try:
            with closing(database._get_connection()) as conn:
                if not adopt_scanned_file(conn.cursor(), path, None, SOURCE):
                    continue
                forms = list(path_spellings(path)) or [path]
                row = conn.execute(
                    f"SELECT id FROM lib2_track_files WHERE path IN ({','.join('?' * len(forms))}) "
                    "AND COALESCE(file_state,'active')<>'deleted' ORDER BY id DESC LIMIT 1",
                    forms).fetchone()
                if row:
                    added.append(int(row[0]))
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the batch
            logger.warning("folder watch: could not index %s: %s", path, exc)
    return added


__all__ = ["SOURCE", "apply_moves", "plan_moves", "process_changes", "same_recording"]
