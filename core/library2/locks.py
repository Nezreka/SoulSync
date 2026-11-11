"""Reviewed and locked: an album or artist nothing automated may change (#2046).

A person goes through an album, gets the tags, files and match right, and
locks it. From then on every automated job leaves it alone: scans, enrichment,
repair jobs, retags, reorganize, upgrades. What the person does by hand on its
page still works. The lock doubles as "I reviewed this".

A lock is a set of scopes, all on by default:

    tags      tags inside the files
    artwork   the cover, in the library and in the files
    metadata  the library's own fields: title, year, genres, type, ids
    files     file names and folders
    match     which release and edition it is matched to
    upgrades  replacing a file with a better copy

One rule decides who is a person: code running inside a web request is, and
so is anything inside ``user_action()``. Everything else is automation: a
worker thread, the scheduler, a scan. A queue that carries out one thing a
person asked for wraps its work in ``user_action()``.

An artist lock covers the artist's own fields. Its albums are locked one by
one (the artist page offers "and all current albums"), so a new release still
gets tagged.
"""

from __future__ import annotations

import contextvars
import hashlib
import threading
import time
from contextlib import closing, contextmanager
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.locks")

SCOPES: Tuple[str, ...] = ("tags", "artwork", "metadata", "files", "match", "upgrades")
SCOPE_LABELS: Dict[str, str] = {
    "tags": "Tags in the files",
    "artwork": "Artwork",
    "metadata": "Library details",
    "files": "File names and folders",
    "match": "Release match",
    "upgrades": "Quality upgrades",
}
ENTITY_TYPES = ("album", "artist")
_TABLES = {"album": "lib2_albums", "artist": "lib2_artists"}

LIB2_ENTITY_LOCKS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_entity_locks (
    entity_type TEXT NOT NULL,            -- 'album' | 'artist'
    entity_id INTEGER NOT NULL,
    scopes TEXT NOT NULL,                 -- comma list out of locks.SCOPES
    locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    locked_by_profile_id INTEGER,
    snapshot TEXT,                        -- what the files looked like when it was reviewed
    PRIMARY KEY (entity_type, entity_id)
)
"""

LIB2_LOCK_SKIPS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_lock_skips (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    scope TEXT NOT NULL,
    what TEXT NOT NULL,                   -- "Genre cleanup", "Spotify enrichment"
    first_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    times INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (entity_type, entity_id, scope, what)
)
"""

# a deleted or merged-away row takes its lock with it
_CLEANUP_DDL = tuple(
    f"""
CREATE TRIGGER IF NOT EXISTS lib2_{entity}_lock_cleanup AFTER DELETE ON lib2_{entity}s
BEGIN
    DELETE FROM lib2_entity_locks WHERE entity_type='{entity}' AND entity_id=OLD.id;
    DELETE FROM lib2_lock_skips WHERE entity_type='{entity}' AND entity_id=OLD.id;
END
""" for entity in ("album", "artist"))

DDL = (LIB2_ENTITY_LOCKS_DDL, LIB2_LOCK_SKIPS_DDL, *_CLEANUP_DDL)


class LockError(ValueError):
    """A lock request that names something that is not there."""


# ── who is acting ────────────────────────────────────────────────────────────

_ACTOR: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lib2_lock_actor", default=None)


@contextmanager
def user_action():
    """Work a person asked for on one thing, carried out off the request."""
    token = _ACTOR.set("user")
    try:
        yield
    finally:
        _ACTOR.reset(token)


@contextmanager
def automation():
    """Library-wide work started from a request: a bulk job is still a job."""
    token = _ACTOR.set("automation")
    try:
        yield
    finally:
        _ACTOR.reset(token)


def carry(fn):
    """A thread target that keeps who started it. A thread has no request
    and would count as automation, so a fetch a person kicked off from a page
    would be turned away from the very thing they asked about."""
    as_user = acting_as_user()

    def run(*args, **kwargs):
        with (user_action() if as_user else automation()):
            return fn(*args, **kwargs)

    return run


def acting_as_user() -> bool:
    actor = _ACTOR.get()
    if actor:
        return actor == "user"
    try:
        from flask import has_request_context
        return bool(has_request_context())
    except Exception:  # noqa: BLE001 - no flask, no request: automation
        return False


# ── reading locks ────────────────────────────────────────────────────────────

def _entity(entity_type: Any) -> str:
    text = str(entity_type or "").strip().lower().rstrip("s")
    if text not in ENTITY_TYPES:
        raise LockError(f"Unknown entity type: {entity_type!r}")
    return text


def clean_scopes(scopes: Optional[Iterable[Any]]) -> FrozenSet[str]:
    """The scopes asked for; every scope when none are named."""
    if scopes is None:
        return frozenset(SCOPES)
    wanted = {str(s or "").strip().lower() for s in scopes}
    unknown = wanted - set(SCOPES) - {""}
    if unknown:
        raise LockError(f"Unknown lock scope(s): {sorted(unknown)}")
    return frozenset(wanted & set(SCOPES))


def _split(scopes: Any) -> FrozenSet[str]:
    return frozenset(s for s in str(scopes or "").split(",") if s in SCOPES)


def _join(scopes: Iterable[str]) -> str:
    wanted = set(scopes)
    return ",".join(s for s in SCOPES if s in wanted)


# every check on a hot path (a tag save, a provider write) reads this map, not
# the table. it is small: one entry per locked album or artist
_CACHE: Dict[str, Tuple[float, int, Dict[Tuple[str, int], FrozenSet[str]]]] = {}
_CACHE_TTL = 20.0
_GENERATION = 0
_CACHE_GUARD = threading.Lock()


def _db_key(conn) -> str:
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        return str(row[2] or id(conn))
    except Exception:  # noqa: BLE001
        return str(id(conn))


def _open():
    from database.music_database import get_database
    return get_database()._get_connection()


def invalidate() -> None:
    global _GENERATION
    with _CACHE_GUARD:
        _GENERATION += 1
        _CACHE.clear()
        _FILES.clear()


def lock_map(conn=None) -> Dict[Tuple[str, int], FrozenSet[str]]:
    """Every lock: {('album', 12): {'tags', ...}}. Cached for a few seconds."""
    owned = conn is None
    try:
        conn = _open() if owned else conn
    except Exception as exc:  # noqa: BLE001 - no database, nothing is locked
        logger.debug("locks: no database to read: %s", exc)
        return {}
    try:
        key = _db_key(conn)
        now = time.monotonic()
        hit = _CACHE.get(key)
        if hit and hit[1] == _GENERATION and now - hit[0] < _CACHE_TTL:
            return hit[2]
        try:
            rows = conn.execute(
                "SELECT entity_type, entity_id, scopes FROM lib2_entity_locks").fetchall()
        except Exception:  # noqa: BLE001 - an older database without the table
            rows = []
        found = {(str(r[0]), int(r[1])): _split(r[2]) for r in rows}
        with _CACHE_GUARD:
            _CACHE[key] = (now, _GENERATION, found)
        return found
    finally:
        if owned:
            conn.close()


def scopes_for(entity_type: Any, entity_id: Any, conn=None) -> FrozenSet[str]:
    try:
        return lock_map(conn).get((_entity(entity_type), int(entity_id)), frozenset())
    except (TypeError, ValueError):
        return frozenset()


def is_locked(entity_type: Any, entity_id: Any, scope: Optional[str] = None, conn=None) -> bool:
    """Whether it is locked at all, or for one scope. Says nothing about who asks."""
    held = scopes_for(entity_type, entity_id, conn)
    return bool(held) if scope is None else scope in held


def album_of_track(conn, track_id: Any) -> Optional[int]:
    try:
        row = conn.execute("SELECT album_id FROM lib2_tracks WHERE id=?",
                           (int(track_id),)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return int(row[0]) if row and row[0] is not None else None


def _tail(path: str) -> str:
    """"album folder/file name": what a server path and a local path of the
    same file still have in common."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    return "/".join(parts[-2:]).casefold()


_FILES: Dict[str, Tuple[float, int, Dict[str, int], Dict[str, Optional[int]]]] = {}
_FILES_TTL = 120.0


def _locked_files(conn) -> Tuple[Dict[str, int], Dict[str, Optional[int]]]:
    """The files of every locked album: by stored path, and by folder and
    file name. Only locked albums are read, so this stays small."""
    key = _db_key(conn)
    now = time.monotonic()
    hit = _FILES.get(key)
    if hit and hit[1] == _GENERATION and now - hit[0] < _FILES_TTL:
        return hit[2], hit[3]
    by_path: Dict[str, int] = {}
    by_tail: Dict[str, Optional[int]] = {}
    try:
        rows = conn.execute(
            "SELECT f.path, t.album_id FROM lib2_entity_locks lk "
            "JOIN lib2_tracks t ON t.album_id=lk.entity_id "
            "JOIN lib2_track_files f ON f.track_id=t.id "
            "WHERE lk.entity_type='album' AND f.path IS NOT NULL AND f.path<>'' "
            "AND COALESCE(f.file_state,'active')<>'deleted'").fetchall()
    except Exception:  # noqa: BLE001
        rows = []
    for path, album_id in rows:
        by_path[str(path)] = int(album_id)
        tail = _tail(path)
        # the same folder and file name under two locked albums names neither
        by_tail[tail] = int(album_id) if by_tail.get(tail, int(album_id)) == int(album_id) else None
    with _CACHE_GUARD:
        _FILES[key] = (now, _GENERATION, by_path, by_tail)
    return by_path, by_tail


def album_of_file(conn, file_path: Any) -> Optional[int]:
    """The LOCKED album a path on disk belongs to. The stored path first; a
    media server and SoulSync often disagree about the front of the same
    path, so then the album folder and file name."""
    path = str(file_path or "")
    if not path:
        return None
    by_path, by_tail = _locked_files(conn)
    if path in by_path:
        return by_path[path]
    return by_tail.get(_tail(path))


def unlocked_sql(entity_type: str, alias: str, scope: str) -> str:
    """SQL that is true for a row no lock of this scope covers. A track is
    covered by its album's lock."""
    if scope not in SCOPES:
        raise LockError(f"Unknown lock scope: {scope!r}")
    entity = str(entity_type).rstrip("s")
    if entity == "track":
        entity, column = "album", f"{alias}.album_id"
    elif entity in ENTITY_TYPES:
        column = f"{alias}.id"
    else:
        raise LockError(f"Unknown entity type: {entity_type!r}")
    return (f"NOT EXISTS (SELECT 1 FROM lib2_entity_locks lk WHERE lk.entity_type='{entity}' "
            f"AND lk.entity_id={column} AND instr(',' || lk.scopes || ',', ',{scope},') > 0)")


# ── the one question every automated writer asks ─────────────────────────────

_DOING: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lib2_lock_doing", default=None)


_JOB_SCOPE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lib2_lock_job_scope", default=None)


@contextmanager
def doing(what: str, scope: Optional[str] = None):
    """Name the work under way ("Genre cleanup"), for the skip log. A writer
    deep in the stack only knows it is "a tag write".

    ``scope`` is the part of an album the work changes. With it set, the
    lists of files and albums a repair job reads leave locked ones out
    (``drop_locked_subjects``), so the job never looks at them."""
    token = _DOING.set(str(what or "").strip() or None)
    scope_token = _JOB_SCOPE.set(scope if scope in SCOPES else None)
    try:
        yield
    finally:
        _JOB_SCOPE.reset(scope_token)
        _DOING.reset(token)


def drop_locked_subjects(conn, subjects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The subjects a running job may work on: every one, unless the job
    named a scope and the subject's album holds a lock on it."""
    scope = _JOB_SCOPE.get()
    if not scope or not subjects or acting_as_user():
        return subjects
    held = lock_map(conn)
    if not held:
        return subjects
    kept = []
    for subject in subjects:
        album_id = subject.get("album_id")
        try:
            locked = album_id is not None and scope in held.get(("album", int(album_id)), ())
        except (TypeError, ValueError):
            locked = False
        if locked:
            _remember_skip("album", int(album_id), scope, _DOING.get() or "Automation")
        else:
            kept.append(subject)
    return kept


def blocks(entity_type: Any, entity_id: Any, scope: str, what: str, *, conn=None,
           known=None) -> bool:
    """True when automation must leave this alone. A person is never blocked.

    ``what`` names the work in the words the page shows: "Genre cleanup",
    "Spotify enrichment". A job that named itself with ``doing()`` is used
    instead. A blocked attempt is remembered so the album can show what the
    lock kept away. ``known`` is a ``lock_map()`` the caller already read."""
    if entity_id in (None, "") or acting_as_user():
        return False
    try:
        entity = _entity(entity_type)
        entity_id = int(entity_id)
    except (LockError, TypeError, ValueError):
        return False
    held = (known.get((entity, entity_id), frozenset()) if known is not None
            else scopes_for(entity, entity_id, conn))
    if scope not in held:
        return False
    _remember_skip(entity, entity_id, scope, _DOING.get() or what)
    return True


def blocks_entity(entity_type: Any, entity_id: Any, scope: str, what: str, *, conn=None) -> bool:
    """``blocks`` for an artist, album or track id, whichever a writer holds."""
    kind = str(entity_type or "").strip().lower().rstrip("s")
    if kind == "track":
        return blocks_track(entity_id, scope, what, conn=conn)
    if kind in ("release_group", "album"):
        return blocks("album", entity_id, scope, what, conn=conn)
    return blocks(kind, entity_id, scope, what, conn=conn)


def blocks_track(track_id: Any, scope: str, what: str, *, conn=None) -> bool:
    """``blocks`` for a track: its album's lock covers it."""
    if track_id in (None, "") or acting_as_user() or not lock_map(conn):
        return False
    owned = conn is None
    try:
        conn = _open() if owned else conn
    except Exception:  # noqa: BLE001
        return False
    try:
        album_id = album_of_track(conn, track_id)
        return bool(album_id) and blocks("album", album_id, scope, what, conn=conn)
    finally:
        if owned:
            conn.close()


def blocks_file(file_path: Any, scope: str, what: str, *, conn=None) -> bool:
    """``blocks`` for a file on disk. A file the library does not hold (a
    download still in staging) is nobody's locked album."""
    if not file_path or acting_as_user() or not lock_map(conn):
        return False
    owned = conn is None
    try:
        conn = _open() if owned else conn
    except Exception:  # noqa: BLE001
        return False
    try:
        album_id = album_of_file(conn, file_path)
        return bool(album_id) and blocks("album", album_id, scope, what, conn=conn)
    finally:
        if owned:
            conn.close()


def finding_subjects(conn, entity_type: Any, entity_id: Any, file_path: Any = None,
                     details: Any = None) -> List[Tuple[str, int]]:
    """The albums and artists a repair finding is about, as lock subjects.
    Read off its ``lib2:<id>`` subject, the ids its details carry, then its
    file. A finding about nothing in the library has none."""
    found: List[Tuple[str, int]] = []

    def add(kind: str, value: Any) -> None:
        try:
            item = (kind, int(value))
        except (TypeError, ValueError):
            return
        if item[1] > 0 and item not in found:
            found.append(item)

    text = str(entity_id or "")
    native = text[5:] if text.startswith("lib2:") else None
    kind = str(entity_type or "").strip().lower()
    if native and native.isdigit():
        if kind == "track":
            add("album", album_of_track(conn, native))
        elif kind in ("album", "release_group"):
            add("album", native)
        elif kind == "artist":
            add("artist", native)
    linked = (details or {}).get("library_v2") if isinstance(details, dict) else None
    if isinstance(linked, dict):
        for album_id in linked.get("album_ids") or []:
            add("album", album_id)
        if not found:
            for track_id in linked.get("track_ids") or []:
                add("album", album_of_track(conn, track_id))
    if not found and file_path and lock_map(conn):
        add("album", album_of_file(conn, file_path))
    return found


def finding_lock(conn, job_scope: str, entity_type: Any, entity_id: Any,
                 file_path: Any = None, details: Any = None) -> Optional[Tuple[str, int, str]]:
    """The lock that covers a finding, as ``(entity_type, id, scope)``, or
    None. A job with a scope is covered by a lock on that scope. A 'report'
    job is covered by any lock at all. Says nothing about who asks."""
    if job_scope == "none" or not lock_map(conn):
        return None
    held = lock_map(conn)
    for subject in finding_subjects(conn, entity_type, entity_id, file_path, details):
        scopes = held.get(subject)
        if not scopes:
            continue
        if job_scope in scopes:
            return subject[0], subject[1], job_scope
        if job_scope not in SCOPES:
            return subject[0], subject[1], next(s for s in SCOPES if s in scopes)
    return None


# ── what the lock kept away ──────────────────────────────────────────────────

_PENDING: Dict[Tuple[str, int, str, str], int] = {}
_SEEN: Dict[Tuple[str, int, str, str], float] = {}
_SKIP_GUARD = threading.Lock()
_SKIP_QUIET = 600.0           # the same job on the same album, once per ten minutes
_FLUSHER: Optional[threading.Thread] = None
_FLUSH_EVERY = 15.0


def _remember_skip(entity: str, entity_id: int, scope: str, what: str) -> None:
    """Note a blocked attempt in memory. It is written later, from a thread
    that holds no one else's transaction: a check runs in the middle of other
    people's reads and writes and must never take the write lock there."""
    global _FLUSHER
    key = (entity, entity_id, scope, str(what or "Automation")[:120])
    now = time.monotonic()
    with _SKIP_GUARD:
        if now - _SEEN.get(key, -_SKIP_QUIET) < _SKIP_QUIET:
            return
        _SEEN[key] = now
        _PENDING[key] = _PENDING.get(key, 0) + 1
        if _FLUSHER is None or not _FLUSHER.is_alive():
            _FLUSHER = threading.Thread(target=_flush_loop, name="lib2-lock-skips", daemon=True)
            _FLUSHER.start()


def _flush_loop() -> None:
    while True:
        time.sleep(_FLUSH_EVERY)
        with _SKIP_GUARD:
            if not _PENDING:
                continue
        try:
            with closing(_open()) as conn:
                flush_skips(conn)
        except Exception as exc:  # noqa: BLE001 - a log line, never a failure
            logger.debug("locks: skip log not written: %s", exc)


def flush_skips(conn) -> int:
    """Write the remembered skips. Commits."""
    with _SKIP_GUARD:
        pending = dict(_PENDING)
        _PENDING.clear()
    if not pending:
        return 0
    try:
        for (entity, entity_id, scope, what), times in pending.items():
            conn.execute(
                "INSERT INTO lib2_lock_skips(entity_type, entity_id, scope, what, times) "
                "VALUES(?,?,?,?,?) ON CONFLICT(entity_type, entity_id, scope, what) DO UPDATE SET "
                "last_at=CURRENT_TIMESTAMP, times=times+excluded.times",
                (entity, entity_id, scope, what, times))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("locks: skip log not written: %s", exc)
        return 0
    return len(pending)


def skips(conn, entity_type: Any, entity_id: Any, limit: int = 20) -> List[Dict[str, Any]]:
    """What automation was kept from doing here, newest first."""
    flush_skips(conn)
    rows = conn.execute(
        "SELECT scope, what, first_at, last_at, times FROM lib2_lock_skips "
        "WHERE entity_type=? AND entity_id=? ORDER BY last_at DESC, what LIMIT ?",
        (_entity(entity_type), int(entity_id), int(limit))).fetchall()
    return [{"scope": r[0], "what": r[1], "first_at": r[2], "last_at": r[3],
             "times": int(r[4] or 1)} for r in rows]


def reset_for_tests() -> None:
    with _SKIP_GUARD:
        _PENDING.clear()
        _SEEN.clear()
    invalidate()


# ── reviewed: what the files looked like ─────────────────────────────────────

def album_fingerprint(conn, album_id: Any) -> str:
    """The album's files as one value: which files, where, how big. A file
    replaced, renamed, added or removed changes it."""
    rows = conn.execute(
        "SELECT f.path, COALESCE(f.size, 0) FROM lib2_track_files f "
        "JOIN lib2_tracks t ON t.id=f.track_id "
        "WHERE t.album_id=? AND COALESCE(f.file_state,'active')='active' "
        "ORDER BY f.path", (int(album_id),)).fetchall()
    digest = hashlib.sha1()
    for path, size in rows:
        digest.update(f"{path}\x00{int(size or 0)}\x01".encode("utf-8", "replace"))
    return f"{len(rows)}:{digest.hexdigest()[:16]}"


def _snapshot(conn, entity: str, entity_id: int) -> Optional[str]:
    return album_fingerprint(conn, entity_id) if entity == "album" else None


def _describe(conn, row) -> Dict[str, Any]:
    entity, entity_id = str(row[0]), int(row[1])
    held = _split(row[2])
    changed = False
    if entity == "album" and row[5]:
        changed = album_fingerprint(conn, entity_id) != row[5]
    return {
        "entity_type": entity, "entity_id": entity_id, "locked": True,
        "scopes": [s for s in SCOPES if s in held],
        "locked_at": row[3], "locked_by_profile_id": row[4],
        "changed_since_review": changed,
    }


_SELECT = ("SELECT entity_type, entity_id, scopes, locked_at, locked_by_profile_id, snapshot "
           "FROM lib2_entity_locks")


def get_lock(conn, entity_type: Any, entity_id: Any) -> Optional[Dict[str, Any]]:
    row = conn.execute(_SELECT + " WHERE entity_type=? AND entity_id=?",
                       (_entity(entity_type), int(entity_id))).fetchone()
    return _describe(conn, row) if row else None


def unlocked_state(entity_type: Any, entity_id: Any) -> Dict[str, Any]:
    return {"entity_type": _entity(entity_type), "entity_id": int(entity_id), "locked": False,
            "scopes": [], "locked_at": None, "locked_by_profile_id": None,
            "changed_since_review": False}


def set_lock(conn, entity_type: Any, entity_id: Any, scopes: Optional[Iterable[Any]] = None,
             *, profile_id: Optional[int] = None, review: bool = True) -> Dict[str, Any]:
    """Lock it, or change which scopes an existing lock holds. No scopes left
    means unlocked. ``review=False`` keeps the review date and snapshot of a
    lock that is only having its switches changed. The caller commits."""
    entity = _entity(entity_type)
    entity_id = int(entity_id)
    if conn.execute(f"SELECT 1 FROM {_TABLES[entity]} WHERE id=?", (entity_id,)).fetchone() is None:
        raise LockError(f"{entity.title()} not found")
    wanted = clean_scopes(scopes)
    if not wanted:
        clear_lock(conn, entity, entity_id)
        return unlocked_state(entity, entity_id)
    existing = conn.execute(
        "SELECT 1 FROM lib2_entity_locks WHERE entity_type=? AND entity_id=?",
        (entity, entity_id)).fetchone()
    if existing and not review:
        conn.execute("UPDATE lib2_entity_locks SET scopes=? WHERE entity_type=? AND entity_id=?",
                     (_join(wanted), entity, entity_id))
    else:
        conn.execute(
            "INSERT INTO lib2_entity_locks(entity_type, entity_id, scopes, locked_by_profile_id, snapshot) "
            "VALUES(?,?,?,?,?) ON CONFLICT(entity_type, entity_id) DO UPDATE SET "
            "scopes=excluded.scopes, locked_at=CURRENT_TIMESTAMP, "
            "locked_by_profile_id=excluded.locked_by_profile_id, snapshot=excluded.snapshot",
            (entity, entity_id, _join(wanted), profile_id, _snapshot(conn, entity, entity_id)))
    invalidate()
    return get_lock(conn, entity, entity_id) or unlocked_state(entity, entity_id)


def clear_lock(conn, entity_type: Any, entity_id: Any) -> bool:
    """Unlock. What the lock kept away is forgotten with it. The caller commits."""
    entity = _entity(entity_type)
    removed = conn.execute(
        "DELETE FROM lib2_entity_locks WHERE entity_type=? AND entity_id=?",
        (entity, int(entity_id))).rowcount
    conn.execute("DELETE FROM lib2_lock_skips WHERE entity_type=? AND entity_id=?",
                 (entity, int(entity_id)))
    invalidate()
    return bool(removed)


def set_many(conn, entity_type: Any, entity_ids: Iterable[Any], scopes=None, *,
             locked: bool = True, profile_id: Optional[int] = None) -> int:
    """Lock or unlock a selection. Ids that are not there are passed over."""
    done = 0
    for entity_id in entity_ids:
        try:
            if locked:
                set_lock(conn, entity_type, entity_id, scopes, profile_id=profile_id)
            else:
                clear_lock(conn, entity_type, entity_id)
            done += 1
        except (LockError, TypeError, ValueError):
            continue
    return done


def forget_entity(conn, entity_type: Any, entity_id: Any) -> None:
    """The row is gone (deleted, merged away): so is its lock."""
    try:
        clear_lock(conn, entity_type, entity_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("locks: stale lock not cleared: %s", exc)


def artist_review_counts(conn, artist_id: Any) -> Dict[str, int]:
    """How many of an artist's owned albums are reviewed."""
    row = conn.execute(
        """SELECT COUNT(*), SUM(CASE WHEN lk.entity_id IS NOT NULL THEN 1 ELSE 0 END)
             FROM lib2_albums al
             LEFT JOIN lib2_entity_locks lk ON lk.entity_type='album' AND lk.entity_id=al.id
            WHERE al.primary_artist_id=?
              AND EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id
                           WHERE t.album_id=al.id
                             AND COALESCE(f.file_state,'active')='active')""",
        (int(artist_id),)).fetchone()
    return {"albums": int(row[0] or 0), "reviewed": int(row[1] or 0)}


__all__ = [
    "DDL", "ENTITY_TYPES", "LockError", "SCOPES", "SCOPE_LABELS",
    "acting_as_user", "album_fingerprint", "album_of_file", "album_of_track",
    "artist_review_counts", "automation", "blocks", "carry", "blocks_entity", "blocks_file",
    "blocks_track",
    "clean_scopes", "clear_lock", "doing", "drop_locked_subjects", "finding_lock", "finding_subjects", "flush_skips", "forget_entity", "get_lock",
    "invalidate", "is_locked", "lock_map", "scopes_for", "set_lock", "set_many",
    "skips", "unlocked_sql", "unlocked_state", "user_action",
]
