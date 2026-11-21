"""Folder overrides: "the media server's /data/music is /music here".

Most setups never need one. The path resolver suffix-matches a media server's
paths against SoulSync's library folders, and the scan learns the two prefixes
on its own (``core.library.server_paths``). An override is for the setups where
that guessing fails: two mounts that share no folder names, or a tail that
matches more than one place.

An override only renames. It never gives SoulSync access to a folder: the drive
still has to be mounted into SoulSync.

One table for every media type. Music reads it today through
``local_path_for``; the video side passes ``media_type='video'`` when it plugs
in. Rules:

* the longest matching server prefix wins (``/music/lossless`` beats ``/music``)
* a prefix only matches on a folder boundary (``/music`` never claims
  ``/musicvideos``)
* Windows and UNC prefixes compare without case, posix ones with case
* an override that names a missing file falls through to the next one, and then
  to the inferred learning, so a stale override never hides a file
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import closing
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library.path_mappings")

MEDIA_TYPES = ("music", "video")
_KIND = re.compile(r"^[a-z0-9_]{0,32}$")
_DRIVE = re.compile(r"^[A-Za-z]:")

DDL = """
CREATE TABLE IF NOT EXISTS media_path_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL DEFAULT 'music',   -- 'music' | 'video'
    server_kind TEXT NOT NULL DEFAULT '',       -- plex | jellyfin | navidrome, '' = any server
    server_prefix TEXT NOT NULL,                -- the folder as the media server names it
    local_prefix TEXT NOT NULL,                 -- the same folder as SoulSync sees it
    enabled INTEGER NOT NULL DEFAULT 1,
    verified_at TIMESTAMP,                      -- last verify run
    verify_json TEXT NOT NULL DEFAULT '{}',     -- {sampled, readable, reasons}
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(media_type, server_kind, server_prefix)
)
"""


def ensure_schema(cursor: Any) -> None:
    """the override table. idempotent, runs every start"""
    cursor.execute(DDL)


# ---------------------------------------------------------------- prefixes
def windows_style(path: str) -> bool:
    """a drive letter or a UNC share: compared without case"""
    text = str(path or "")
    return bool(_DRIVE.match(text)) or text.startswith(("\\\\", "//"))


def normalize_prefix(prefix: Any) -> str:
    """forward slashes, no doubled or trailing slash, the root kept as '/'.
    'C:\\Music\\' is 'C:/Music', '\\\\nas\\share' is '//nas/share'."""
    text = str(prefix or "").strip()
    if not text:
        return ""
    unc = text.startswith(("\\\\", "//"))
    text = re.sub(r"/{2,}", "/", text.replace("\\", "/"))
    if unc:
        text = "/" + text if not text.startswith("//") else text
    stripped = text.rstrip("/")
    if not stripped:
        return "/"
    return stripped


def _fold(text: str, windows: bool) -> str:
    return text.casefold() if windows else text


def relative_to(path: Any, prefix: Any) -> Optional[str]:
    """``path`` below ``prefix`` as 'a/b/c' ('' for the folder itself), or None
    when it isn't under it. Matches on a folder boundary only."""
    norm_prefix = normalize_prefix(prefix)
    if not norm_prefix or not isinstance(path, str) or not path.strip():
        return None
    norm_path = normalize_prefix(path)
    windows = windows_style(norm_prefix) or windows_style(norm_path)
    p, f = _fold(norm_path, windows), _fold(norm_prefix, windows)
    if f == "/":
        return norm_path[1:] if norm_path.startswith("/") and not norm_path.startswith("//") else None
    if p == f:
        return ""
    if p.startswith(f + "/"):
        return norm_path[len(norm_prefix) + 1:]
    return None


def join_local(local_prefix: str, rest: str) -> str:
    """the local folder plus the part below the server folder, in the local
    folder's own separator"""
    base = str(local_prefix or "").strip()
    if not rest:
        return base.rstrip("/\\") or base
    sep = "\\" if "\\" in base or (windows_style(base) and "/" not in base) else "/"
    parts = [p for p in rest.split("/") if p]
    return base.rstrip("/\\") + sep + sep.join(parts)


def translate(path: Any, mappings: List[dict], *, server_kind: Optional[str] = None) -> List[str]:
    """every local path the overrides give for ``path``, longest server prefix
    first. ``server_kind`` limits it to that server's overrides plus the ones
    for any server; a server's own override wins a tie."""
    out: List[str] = []
    ranked = []
    for m in mappings or []:
        if not m.get("enabled", 1):
            continue
        kind = str(m.get("server_kind") or "")
        if server_kind and kind and kind != server_kind:
            continue
        rest = relative_to(path, m.get("server_prefix"))
        if rest is None:
            continue
        ranked.append((len(normalize_prefix(m.get("server_prefix"))), 1 if kind else 0,
                       join_local(m.get("local_prefix") or "", rest)))
    ranked.sort(key=lambda r: (-r[0], -r[1]))
    for _length, _own, local in ranked:
        if local and local not in out:
            out.append(local)
    return out


# ---------------------------------------------------------------- storage
def _row(r) -> dict:
    keys = ("id", "media_type", "server_kind", "server_prefix", "local_prefix",
            "enabled", "verified_at", "verify_json", "created_at", "updated_at")
    out = dict(zip(keys, tuple(r), strict=True))
    try:
        out["verify"] = json.loads(out.pop("verify_json") or "{}")
    except (TypeError, ValueError):
        out["verify"] = {}
    out["enabled"] = bool(out["enabled"])
    return out


def list_mappings(conn: Any, media_type: Optional[str] = None,
                  *, enabled_only: bool = False) -> List[dict]:
    sql = ("SELECT id, media_type, server_kind, server_prefix, local_prefix, enabled,"
           " verified_at, verify_json, created_at, updated_at FROM media_path_mappings")
    where, args = [], []
    if media_type:
        where.append("media_type = ?")
        args.append(media_type)
    if enabled_only:
        where.append("enabled = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY media_type, length(server_prefix) DESC, id"
    try:
        return [_row(r) for r in conn.execute(sql, args).fetchall()]
    except Exception as exc:  # noqa: BLE001 - no table yet reads as no overrides
        logger.debug("folder overrides unreadable: %s", exc)
        return []


class MappingError(ValueError):
    """a save that can't be stored, in words for the person saving"""


def clean_mapping(data: dict) -> dict:
    media_type = str(data.get("media_type") or "music").strip().lower()
    if media_type not in MEDIA_TYPES:
        raise MappingError("media_type must be music or video")
    kind = str(data.get("server_kind") or "").strip().lower()
    if not _KIND.match(kind):
        raise MappingError("server_kind must be a server name like plex")
    server_prefix = normalize_prefix(data.get("server_prefix"))
    if not server_prefix:
        raise MappingError("Say which folder the media server uses")
    local = str(data.get("local_prefix") or "").strip()
    if not local:
        raise MappingError("Say which folder SoulSync sees")
    if not (local.startswith(("/", "\\\\")) or windows_style(local)):
        raise MappingError("The SoulSync folder has to be a full path, like /music")
    local = local.rstrip("/\\") or local[:1]
    return {"media_type": media_type, "server_kind": kind, "server_prefix": server_prefix,
            "local_prefix": local, "enabled": 0 if data.get("enabled") is False else 1}


def save_mapping(conn: Any, data: dict, mapping_id: Optional[int] = None) -> int:
    """insert or update one override, returns its id. Raises MappingError."""
    row = clean_mapping(data)
    if mapping_id:
        cur = conn.execute(
            "UPDATE media_path_mappings SET media_type=?, server_kind=?, server_prefix=?,"
            " local_prefix=?, enabled=?, verified_at=NULL, verify_json='{}',"
            " updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (row["media_type"], row["server_kind"], row["server_prefix"],
             row["local_prefix"], row["enabled"], int(mapping_id)))
        if not cur.rowcount:
            raise MappingError("That override is gone")
        new_id = int(mapping_id)
    else:
        conn.execute(
            "INSERT INTO media_path_mappings(media_type, server_kind, server_prefix,"
            " local_prefix, enabled) VALUES(?,?,?,?,?)"
            " ON CONFLICT(media_type, server_kind, server_prefix) DO UPDATE SET"
            " local_prefix=excluded.local_prefix, enabled=excluded.enabled,"
            " verified_at=NULL, verify_json='{}', updated_at=CURRENT_TIMESTAMP",
            (row["media_type"], row["server_kind"], row["server_prefix"],
             row["local_prefix"], row["enabled"]))
        new_id = int(conn.execute(
            "SELECT id FROM media_path_mappings WHERE media_type=? AND server_kind=?"
            " AND server_prefix=?",
            (row["media_type"], row["server_kind"], row["server_prefix"])).fetchone()[0])
    conn.commit()
    invalidate()
    return new_id


def delete_mapping(conn: Any, mapping_id: int) -> bool:
    cur = conn.execute("DELETE FROM media_path_mappings WHERE id=?", (int(mapping_id),))
    conn.commit()
    invalidate()
    return bool(cur.rowcount)


def record_verify(conn: Any, mapping_id: int, stats: dict) -> None:
    conn.execute(
        "UPDATE media_path_mappings SET verified_at=CURRENT_TIMESTAMP, verify_json=? WHERE id=?",
        (json.dumps(stats or {}), int(mapping_id)))
    conn.commit()


# ---------------------------------------------------------------- the lookup
# the resolver runs per track during a scan, so the enabled overrides are
# kept in memory and re-read after a save or every half minute
_CACHE_SECONDS = 30.0
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {}
_connect: Optional[Callable[[], Any]] = None


def configure(connect: Optional[Callable[[], Any]]) -> None:
    """where the overrides are read from (a zero-arg connection factory).
    web_server sets the music database at start; unset, there are none."""
    global _connect
    _connect = connect
    invalidate()


def invalidate() -> None:
    with _cache_lock:
        _cache.clear()


def _load(media_type: str) -> List[dict]:
    # unconfigured (a tool, a test) means no overrides: the resolver must not
    # open a database of its own just to find there are none
    if _connect is None:
        return []
    with closing(_connect()) as conn:
        return list_mappings(conn, media_type, enabled_only=True)


def active_mappings(media_type: str = "music") -> List[dict]:
    """the enabled overrides for one media type. Never raises."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(media_type)
        if hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
    try:
        rows = _load(media_type)
    except Exception as exc:  # noqa: BLE001 - no overrides is the safe answer
        logger.debug("folder overrides could not be read: %s", exc)
        rows = []
    with _cache_lock:
        _cache[media_type] = (now, rows)
    return rows


def local_path(server_path: Any, *, media_type: str = "music", server_kind: Optional[str] = None,
               exists: Callable[[str], bool] = None) -> Optional[str]:
    """the first override translation of ``server_path`` that exists here, or
    None (no override covers it, or none of them finds the file)"""
    import os
    if not isinstance(server_path, str) or not server_path:
        return None
    mappings = active_mappings(media_type)
    if not mappings:
        return None
    check = exists or os.path.exists
    for candidate in translate(server_path, mappings, server_kind=server_kind):
        try:
            if check(candidate):
                return candidate
        except OSError:
            continue
    return None


__all__ = [
    "DDL", "MEDIA_TYPES", "MappingError", "active_mappings", "clean_mapping", "configure",
    "delete_mapping", "ensure_schema", "invalidate", "join_local", "list_mappings",
    "local_path", "normalize_prefix", "record_verify", "relative_to", "save_mapping",
    "translate", "windows_style",
]
