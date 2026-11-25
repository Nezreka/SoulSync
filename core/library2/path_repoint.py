"""Update file locations: the music library folder moved, the records follow.

Storm changed the music library folder from ``/app/Transfer`` to ``/music``
and the docker mount with it. Every file kept working, because the shared
resolver (``core.library.path_resolver``) walks a stored
``/app/Transfer/Artist/Album/x.flac`` down to ``/music/Artist/Album/x.flac``,
but nothing ever wrote the new location back. The old root showed everywhere a
path is shown and every open paid the suffix walk.

This module finds those records and points them at where the file is now. It
writes ``lib2_track_files.path`` (where SoulSync opens the file) and the rows
that name a file by that exact path (download history, library history,
manual-skip audit rows, hand-tagged file memory). It never touches the file,
and never the media server's own path (``lib2_media_server_mappings.server_path``).

A record is only moved when all of this holds:

- its stored path is not on disk as written;
- the resolver finds it by exact suffix alone (``allow_fuzzy=False``: no
  case folding, no sibling album folder, no made-up filename), and finds ONE
  physical file. A stored path under a folder that is still configured is only
  looked for in that folder, so an unmounted drive that is still in Settings
  never gives its records away to another copy;
- the file found sits under a configured library folder (the music library
  folder or a Settings > Library music path; never the download folder);
- the stored path and the file found share at least the album folder and the
  filename, and the file is the same size the catalogue recorded (a record
  with no size is left alone);
- no other live record already holds the new path.

The automatic pass a library scan runs adds one more: the old root has to be
gone (missing, or an empty mount point). A folder that is still there with
files in it is not a move, it is something to look at.

Every write is journaled per run (``lib2_path_repoint_journal``) so a run can
be undone. Rows are streamed by id in batches, the disk is checked with no
database connection open, and each batch commits on its own short write, so a
300k-file library never holds the write lock for long and an interrupted run
continues where it stopped: a record already moved is simply on disk now.
"""

from __future__ import annotations

import json
import os
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.path_repoint")

BATCH = 500
#: groups listed in a preview; anything past this is counted under "other"
MAX_GROUPS = 40
EXAMPLES = 3

REPOINT = "repoint"
PRESENT = "present"
NOT_FOUND = "not_found"
AMBIGUOUS = "ambiguous"
DIFFERENT = "different_file"
UNVERIFIED = "unverified"
OUTSIDE = "outside_library"
PARTIAL = "partial_match"
CLAIMED = "claimed"
OLD_ROOT_LIVE = "old_root_live"

#: every outcome a preview counts, in the order the page lists them
OUTCOMES = (REPOINT, PRESENT, NOT_FOUND, AMBIGUOUS, DIFFERENT, UNVERIFIED,
            OUTSIDE, PARTIAL, CLAIMED, OLD_ROOT_LIVE)

#: other tables that name a library file by its exact stored path:
#: (table, key column, path column). each one is followed with the record.
REFERENCES = (
    ("track_downloads", "id", "file_path"),
    ("library_history", "id", "file_path"),
    ("lib2_manual_skips", "id", "file_path"),
    ("manual_metadata_files", "path_key", "file_path"),
)

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_path_repoint_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'manual',          -- 'manual' (Tools) | 'scan' (automatic)
    status TEXT NOT NULL DEFAULT 'running',       -- 'running' | 'done' | 'stopped' | 'undone'
    repointed INTEGER NOT NULL DEFAULT 0,
    groups_json TEXT NOT NULL DEFAULT '[]',       -- [{old_root, new_root, count}]
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    undone_at TIMESTAMP
)
"""

JOURNAL_DDL = """
CREATE TABLE IF NOT EXISTS lib2_path_repoint_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    old_path TEXT NOT NULL,
    new_path TEXT NOT NULL,
    old_root TEXT,
    new_root TEXT,
    refs_json TEXT NOT NULL DEFAULT '{}',         -- {table: [keys]} rows that followed
    undone INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def ensure_schema(conn) -> None:
    """The run and journal tables. Idempotent."""
    conn.execute(RUNS_DDL)
    conn.execute(JOURNAL_DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lib2_path_repoint_journal_run "
                 "ON lib2_path_repoint_journal(run_id, id)")


# ── judging one record ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    status: str
    new_path: Optional[str] = None
    old_root: Optional[str] = None
    new_root: Optional[str] = None


def _config(config_manager: Any = None) -> Any:
    if config_manager is not None:
        return config_manager
    try:
        from core.settings import config_manager as cm
        return cm
    except Exception:  # noqa: BLE001 - no config, no roots, nothing moves
        return None


def _parts(path: str) -> List[str]:
    return [p for p in str(path or "").replace("\\", "/").split("/") if p]


def repoint_roots(config_manager: Any = None) -> List[str]:
    """The folders a record may be moved into: the music library folder and
    every Settings > Library music path that exists here, absolute, deepest
    first. The download folder is not one: a copy waiting there is not the
    library's file."""
    from core.library.path_resolver import _docker_resolve_path

    cm = _config(config_manager)
    raw: List[str] = []
    if cm is not None:
        try:
            transfer = cm.get("soulseek.transfer_path", "") or ""
            if isinstance(transfer, str) and transfer.strip():
                raw.append(transfer.strip())
            music_paths = cm.get("library.music_paths", []) or []
            if isinstance(music_paths, str):
                music_paths = [music_paths]
            raw.extend(p.strip() for p in music_paths if isinstance(p, str) and p.strip())
        except Exception as exc:  # noqa: BLE001
            logger.debug("repoint roots unreadable: %s", exc)
    roots: List[str] = []
    for value in raw:
        root = os.path.abspath(os.path.expanduser(_docker_resolve_path(value) or value))
        if root not in roots and os.path.isdir(root):
            roots.append(root)
    return sorted(roots, key=len, reverse=True)


def _root_holding(path: str, roots: List[str]) -> Optional[str]:
    """The deepest root ``path`` is inside, compared on real paths too."""
    candidate = os.path.abspath(path)
    real = os.path.realpath(candidate)
    for root in roots:
        for left, right in ((candidate, root), (real, os.path.realpath(root))):
            if left == right or left.startswith(right.rstrip(os.sep) + os.sep):
                return root
    return None


def _populated(folder: str) -> bool:
    try:
        with os.scandir(folder) as entries:
            return any(True for _ in entries)
    except OSError:
        return False


class _DiskCache:
    """Directory facts asked again and again across one run: whether a folder
    exists (the ambiguity walk) and whether an old root still holds files."""

    def __init__(self):
        self.dirs: Dict[str, bool] = {}
        self.live: Dict[str, bool] = {}

    def isdir(self, folder: str) -> bool:
        if folder not in self.dirs:
            if len(self.dirs) > 50000:
                self.dirs.clear()
            self.dirs[folder] = os.path.isdir(folder)
        return self.dirs[folder]

    def old_root_live(self, folder: str) -> bool:
        if folder not in self.live:
            self.live[folder] = os.path.isdir(folder) and _populated(folder)
        return self.live[folder]


def _ambiguous(parts: List[str], bases: List[str], cache: _DiskCache) -> bool:
    """Whether the suffix walk stopped on two different files at one level.
    The resolver answers None for both "nothing" and "two"; the page counts
    them apart. Folders that are not there are skipped from a cache, so a
    library that is simply gone costs little here."""
    for i in range(len(parts)):
        found: List[str] = []
        for base in bases:
            candidate = os.path.join(base, *parts[i:])
            if not cache.isdir(os.path.dirname(candidate)):
                continue
            if os.path.exists(candidate):
                found.append(candidate)
        if found:
            if len(found) < 2:
                return False
            first = found[0]
            for other in found[1:]:
                try:
                    if not os.path.samefile(first, other):
                        return True
                except OSError:
                    return True
            return False
    return False


def judge(stored: str, size: Any, *, roots: List[str], config_manager: Any = None,
          require_old_root_gone: bool = False, cache: Optional[_DiskCache] = None) -> Verdict:
    """Where the file a record names is now, if it can be told for certain."""
    from core.library.path_resolver import resolve_library_file_path_with_diagnostic

    cache = cache or _DiskCache()
    if not isinstance(stored, str) or not stored.strip():
        return Verdict(NOT_FOUND)
    if os.path.exists(stored):
        return Verdict(PRESENT)
    parts = _parts(stored)
    if not parts or ".." in parts:
        return Verdict(NOT_FOUND)
    resolved, attempt = resolve_library_file_path_with_diagnostic(
        stored, config_manager=_config(config_manager), allow_fuzzy=False)
    if not resolved:
        if attempt.base_dirs_tried and _ambiguous(parts, attempt.base_dirs_tried, cache):
            return Verdict(AMBIGUOUS)
        return Verdict(NOT_FOUND)
    if attempt.raw_path_existed:
        return Verdict(PRESENT)
    if not os.path.isfile(resolved):
        return Verdict(NOT_FOUND)
    resolved = os.path.abspath(resolved)
    root = _root_holding(resolved, roots)
    if root is None:
        return Verdict(OUTSIDE)
    found_parts = _parts(resolved)
    inside = _parts(os.path.relpath(resolved, root)) if resolved != root else []
    shared = 0
    while (shared < min(len(parts), len(found_parts), len(inside))
           and parts[-1 - shared] == found_parts[-1 - shared]):
        shared += 1
    # the folder and the file at least: a bare filename is too little to call
    # it the same file, even at the same size
    if shared < 2:
        return Verdict(PARTIAL)
    old_root = _join_like(stored, parts[:-shared])
    new_root = _join_like(resolved, found_parts[:-shared])
    try:
        expected = int(size or 0)
    except (TypeError, ValueError):
        expected = 0
    if expected <= 0:
        return Verdict(UNVERIFIED, resolved, old_root, new_root)
    try:
        if os.path.getsize(resolved) != expected:
            return Verdict(DIFFERENT, resolved, old_root, new_root)
    except OSError:
        return Verdict(NOT_FOUND)
    # the scan only follows a folder it can see is gone; a relative stored
    # path names no folder, so that is left to the Tools check
    if require_old_root_gone and (not old_root or cache.old_root_live(old_root)):
        return Verdict(OLD_ROOT_LIVE, resolved, old_root, new_root)
    return Verdict(REPOINT, resolved, old_root, new_root)


def _join_like(original: str, parts: List[str]) -> str:
    """A root rebuilt in the separator style the path was written in."""
    text = str(original)
    if "\\" in text and "/" not in text:
        joined = "\\".join(parts)
        return ("\\\\" + joined) if text.startswith("\\\\") else joined
    joined = "/".join(parts)
    # a relative stored path (a media server's library-relative one) has no
    # root at all, and says so with an empty string
    return ("/" + joined) if text.startswith("/") else joined


# ── the walk ─────────────────────────────────────────────────────────────


class Tally:
    """What a walk found: counts per outcome and moves grouped by root pair."""

    def __init__(self):
        self.counts: Dict[str, int] = {name: 0 for name in OUTCOMES}
        self.groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.other_groups = 0
        self.checked = 0
        self.total = 0
        self.repointed = 0

    def add(self, verdict: Verdict, stored: str) -> None:
        self.counts[verdict.status] = self.counts.get(verdict.status, 0) + 1
        if verdict.status != REPOINT:
            return
        key = (verdict.old_root or "", verdict.new_root or "")
        group = self.groups.get(key)
        if group is None:
            if len(self.groups) >= MAX_GROUPS:
                self.other_groups += 1
                return
            group = self.groups[key] = {"old_root": key[0], "new_root": key[1],
                                        "count": 0, "examples": []}
        group["count"] += 1
        if len(group["examples"]) < EXAMPLES:
            group["examples"].append({"old": stored, "new": verdict.new_path})

    def as_dict(self) -> Dict[str, Any]:
        groups = sorted(self.groups.values(), key=lambda g: -g["count"])
        return {
            "checked": self.checked, "total": self.total, "repointed": self.repointed,
            "counts": dict(self.counts), "groups": groups,
            "other_groups": self.other_groups,
        }


def _page(database, after: int, limit: int) -> List[Dict[str, Any]]:
    with closing(database._get_connection()) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, path, size FROM lib2_track_files WHERE id>? "
            "AND path IS NOT NULL AND TRIM(path)<>'' "
            "AND COALESCE(file_state,'active')<>'deleted' ORDER BY id LIMIT ?",
            (int(after), int(limit))).fetchall()]


def _count(database) -> int:
    with closing(database._get_connection()) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM lib2_track_files WHERE path IS NOT NULL AND TRIM(path)<>'' "
            "AND COALESCE(file_state,'active')<>'deleted'").fetchone()[0])


def _wanted(verdict: Verdict, groups: Optional[Iterable[Tuple[str, str]]]) -> bool:
    if verdict.status != REPOINT:
        return False
    return groups is None or (verdict.old_root, verdict.new_root) in groups


def walk(database, *, apply: bool = False, groups: Optional[Iterable] = None,
         config_manager: Any = None, progress: Optional[Callable[[int, int], None]] = None,
         should_stop: Optional[Callable[[], bool]] = None, batch: int = BATCH,
         kind: str = "manual") -> Dict[str, Any]:
    """Look at every live record; with ``apply`` move the ones that qualify.

    ``groups`` limits an apply to some ``(old_root, new_root)`` pairs from a
    preview. Returns the tally, plus ``run_id`` when anything was written."""
    cm = _config(config_manager)
    roots = repoint_roots(cm)
    wanted_groups = None if groups is None else {tuple(g) for g in groups}
    tally = Tally()
    tally.total = _count(database)
    cache = _DiskCache()
    run_id: Optional[int] = None
    after = 0
    stopped = False
    while True:
        if should_stop and should_stop():
            stopped = True
            break
        rows = _page(database, after, batch)
        if not rows:
            break
        after = int(rows[-1]["id"])
        moves: List[Tuple[Dict[str, Any], Verdict]] = []
        for row in rows:
            verdict = judge(row["path"], row.get("size"), roots=roots,
                            config_manager=cm, cache=cache)
            tally.checked += 1
            if apply and _wanted(verdict, wanted_groups):
                moves.append((row, verdict))
            else:
                tally.add(verdict, row["path"])
        if moves:
            if run_id is None:
                run_id = _start_run(database, kind, resume=(kind == "manual"))
            done, refused = _apply_batch(database, run_id, moves)
            for row, verdict in moves:
                tally.add(verdict if int(row["id"]) in done else Verdict(CLAIMED), row["path"])
            tally.repointed += len(done)
            _bump_run(database, run_id, len(done))
            if refused:
                logger.debug("path repoint: %d records left alone at write time", refused)
        if progress:
            progress(tally.checked, tally.total)
    result = tally.as_dict()
    if run_id is not None:
        _finish_run(database, run_id, "stopped" if stopped else "done")
        result["run_id"] = run_id
    result["stopped"] = stopped
    result["roots"] = roots
    return result


def preview(database, **kwargs) -> Dict[str, Any]:
    """The walk with nothing written."""
    return walk(database, apply=False, **kwargs)


# ── writing ──────────────────────────────────────────────────────────────


def _existing_references(conn) -> List[Tuple[str, str, str]]:
    present = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return [ref for ref in REFERENCES if ref[0] in present]


def _start_run(database, kind: str, *, resume: bool) -> int:
    """A new run, or the manual run an interrupted apply left 'running'."""
    with closing(database._get_connection()) as conn:
        ensure_schema(conn)
        if resume:
            row = conn.execute(
                "SELECT id FROM lib2_path_repoint_runs WHERE kind=? AND status IN "
                "('running','stopped') ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
            if row is not None:
                conn.execute("UPDATE lib2_path_repoint_runs SET status='running', "
                             "finished_at=NULL WHERE id=?", (int(row[0]),))
                conn.commit()
                return int(row[0])
        run_id = int(conn.execute(
            "INSERT INTO lib2_path_repoint_runs(kind, status) VALUES(?, 'running')",
            (kind,)).lastrowid)
        conn.commit()
        return run_id


def _bump_run(database, run_id: int, moved: int) -> None:
    with closing(database._get_connection()) as conn:
        conn.execute("UPDATE lib2_path_repoint_runs SET repointed=repointed+? WHERE id=?",
                     (int(moved), int(run_id)))
        conn.commit()


def _finish_run(database, run_id: int, status: str) -> None:
    with closing(database._get_connection()) as conn:
        groups = _run_groups(conn, run_id)
        conn.execute(
            "UPDATE lib2_path_repoint_runs SET status=?, finished_at=CURRENT_TIMESTAMP, "
            "groups_json=? WHERE id=?", (status, json.dumps(groups), int(run_id)))
        conn.commit()


def _run_groups(conn, run_id: int) -> List[Dict[str, Any]]:
    """The run's moves by root pair, read back from its journal."""
    return [{"old_root": r[0], "new_root": r[1], "count": int(r[2])} for r in conn.execute(
        "SELECT old_root, new_root, COUNT(*) AS n FROM lib2_path_repoint_journal "
        "WHERE run_id=? AND undone=0 GROUP BY old_root, new_root ORDER BY n DESC LIMIT ?",
        (int(run_id), MAX_GROUPS))]


def _apply_batch(database, run_id: int,
                 moves: List[Tuple[Dict[str, Any], Verdict]]) -> Tuple[set, int]:
    """One short write: move the batch's records, follow their references,
    journal every row touched. Returns (file ids moved, how many refused)."""
    from core.library2.track_files import path_spellings

    done: set = set()
    refused = 0
    seen_targets: Dict[str, int] = {}
    for _row, verdict in moves:
        seen_targets[verdict.new_path] = seen_targets.get(verdict.new_path, 0) + 1
    with closing(database._get_connection()) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            refs = _existing_references(conn)
            for row, verdict in moves:
                file_id = int(row["id"])
                old_path, new_path = str(row["path"]), str(verdict.new_path)
                if seen_targets.get(new_path, 0) > 1:
                    refused += 1   # two records point at one file: neither is sure
                    continue
                forms = list(path_spellings(new_path)) or [new_path]
                holder = conn.execute(
                    f"SELECT 1 FROM lib2_track_files WHERE path IN ({','.join('?' * len(forms))}) "
                    "AND id<>? AND COALESCE(file_state,'active')<>'deleted' LIMIT 1",
                    (*forms, file_id)).fetchone()
                if holder is not None:
                    refused += 1
                    continue
                moved = conn.execute(
                    "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND path=? AND COALESCE(file_state,'active')<>'deleted'",
                    (new_path, file_id, old_path)).rowcount
                if not moved:
                    refused += 1   # someone changed the record since it was read
                    continue
                followed: Dict[str, List[Any]] = {}
                for table, key, column in refs:
                    keys = [r[0] for r in conn.execute(
                        f"SELECT {key} FROM {table} WHERE {column}=?", (old_path,))]
                    if keys:
                        conn.executemany(
                            f"UPDATE {table} SET {column}=? WHERE {key}=? AND {column}=?",
                            [(new_path, k, old_path) for k in keys])
                        followed[table] = keys
                conn.execute(
                    "INSERT INTO lib2_path_repoint_journal(run_id, file_id, old_path, new_path, "
                    "old_root, new_root, refs_json) VALUES(?,?,?,?,?,?,?)",
                    (int(run_id), file_id, old_path, new_path, verdict.old_root,
                     verdict.new_root, json.dumps(followed)))
                done.add(file_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if done:
        try:
            from core.library2.validation import notify_changes
            notify_changes(sorted(done))
        except Exception as exc:  # noqa: BLE001 - the write stands
            logger.debug("repoint change notice failed: %s", exc)
    return done, refused


# ── automatic, from a library scan ───────────────────────────────────────


def repoint_scanned(database, rows: List[Dict[str, Any]], *,
                    config_manager: Any = None) -> Dict[int, str]:
    """The scan's pass: rows whose stored path is not on disk but resolved.

    Same rules as the Tools action, plus the old root has to be gone. Returns
    ``{file id: new path}`` for the records moved. Never raises."""
    if not rows:
        return {}
    try:
        cm = _config(config_manager)
        roots = repoint_roots(cm)
        if not roots:
            return {}
        cache = _DiskCache()
        moves: List[Tuple[Dict[str, Any], Verdict]] = []
        for row in rows:
            verdict = judge(row.get("path"), row.get("size"), roots=roots,
                            config_manager=cm, require_old_root_gone=True, cache=cache)
            if verdict.status == REPOINT:
                moves.append((row, verdict))
        if not moves:
            return {}
        run_id = _start_run(database, "scan", resume=False)
        moved: Dict[int, str] = {}
        for start in range(0, len(moves), BATCH):
            part = moves[start:start + BATCH]
            done, _refused = _apply_batch(database, run_id, part)
            for row, verdict in part:
                if int(row["id"]) in done:
                    moved[int(row["id"])] = str(verdict.new_path)
            _bump_run(database, run_id, len(done))
        _finish_run(database, run_id, "done")
        if moved:
            logger.info("Library scan: %d file records followed the music library folder "
                        "to where the files are now (run %d)", len(moved), run_id)
        return moved
    except Exception as exc:  # noqa: BLE001 - a scan never fails on this
        logger.error("automatic file location update failed: %s", exc, exc_info=True)
        return {}


# ── history and undo ─────────────────────────────────────────────────────


def list_runs(database, limit: int = 10) -> List[Dict[str, Any]]:
    with closing(database._get_connection()) as conn:
        ensure_schema(conn)
        conn.commit()
        out = []
        for row in conn.execute(
                "SELECT id, kind, status, repointed, groups_json, started_at, finished_at, "
                "undone_at FROM lib2_path_repoint_runs ORDER BY id DESC LIMIT ?", (int(limit),)):
            item = dict(row)
            try:
                item["groups"] = json.loads(item.pop("groups_json") or "[]")
            except ValueError:
                item["groups"] = []
            out.append(item)
        return out


def undo_run(database, run_id: int, *, batch: int = BATCH,
             progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, int]:
    """Put a run's records back where they were. A record changed since (moved
    again, deleted, or its old path taken by another record) is left as it is.
    Resumable: each undone journal row is marked in the same write."""
    stats = {"restored": 0, "skipped": 0}
    with closing(database._get_connection()) as conn:
        ensure_schema(conn)
        conn.commit()
        if conn.execute("SELECT 1 FROM lib2_path_repoint_runs WHERE id=?",
                        (int(run_id),)).fetchone() is None:
            raise KeyError(run_id)
        total = int(conn.execute(
            "SELECT COUNT(*) FROM lib2_path_repoint_journal WHERE run_id=? AND undone=0",
            (int(run_id),)).fetchone()[0])
    after = 0
    handled = 0
    restored_ids: List[int] = []
    while True:
        with closing(database._get_connection()) as conn:
            entries = [dict(r) for r in conn.execute(
                "SELECT id, file_id, old_path, new_path, refs_json FROM lib2_path_repoint_journal "
                "WHERE run_id=? AND undone=0 AND id>? ORDER BY id LIMIT ?",
                (int(run_id), after, int(batch))).fetchall()]
            if not entries:
                break
            after = int(entries[-1]["id"])
            try:
                conn.execute("BEGIN IMMEDIATE")
                refs = {ref[0]: ref for ref in _existing_references(conn)}
                for entry in entries:
                    taken = conn.execute(
                        "SELECT 1 FROM lib2_track_files WHERE path=? AND id<>? "
                        "AND COALESCE(file_state,'active')<>'deleted' LIMIT 1",
                        (entry["old_path"], int(entry["file_id"]))).fetchone()
                    back = 0 if taken else conn.execute(
                        "UPDATE lib2_track_files SET path=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND path=?",
                        (entry["old_path"], int(entry["file_id"]), entry["new_path"])).rowcount
                    if back:
                        stats["restored"] += 1
                        restored_ids.append(int(entry["file_id"]))
                        try:
                            followed = json.loads(entry["refs_json"] or "{}")
                        except ValueError:
                            followed = {}
                        for table, keys in followed.items():
                            ref = refs.get(table)
                            if not ref:
                                continue
                            _table, key, column = ref
                            conn.executemany(
                                f"UPDATE {table} SET {column}=? WHERE {key}=? AND {column}=?",
                                [(entry["old_path"], k, entry["new_path"]) for k in keys])
                    else:
                        stats["skipped"] += 1
                    conn.execute("UPDATE lib2_path_repoint_journal SET undone=1 WHERE id=?",
                                 (int(entry["id"]),))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        handled += len(entries)
        if progress:
            progress(handled, total)
    with closing(database._get_connection()) as conn:
        conn.execute("UPDATE lib2_path_repoint_runs SET status='undone', "
                     "undone_at=CURRENT_TIMESTAMP, repointed=0 WHERE id=?", (int(run_id),))
        conn.commit()
    if restored_ids:
        try:
            from core.library2.validation import notify_changes
            notify_changes(restored_ids)
        except Exception as exc:  # noqa: BLE001
            logger.debug("undo change notice failed: %s", exc)
    return stats


__all__ = [
    "BATCH", "OUTCOMES", "Verdict", "ensure_schema", "judge", "list_runs", "preview",
    "repoint_roots", "repoint_scanned", "undo_run", "walk",
]
