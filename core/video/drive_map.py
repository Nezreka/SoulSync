"""The drive map: where SoulSync reaches each of the media server's drives.

Plex and Jellyfin report a file by the path THEY see it under
(``/mnt/easystore3/TV SHOWS/...``). SoulSync usually sees the same file
somewhere else: a network share (``\\\\nas\\easystore3\\TV SHOWS\\...``), a
different Docker mount, another drive letter. The path resolver used to guess,
re-rooting the path's last few folders onto the folders set in Settings,
which only finds files on a drive that IS one of those folders. A library
spread over a dozen drives was mostly out of reach: nothing could rename,
probe, verify or replace those files.

Here each server drive is paired with its local path once, and every path is
translated exactly (longest server prefix wins). The resolver tries the map
first and keeps its old guess as the fallback, so an unmapped drive behaves
the way it always did.

The drives are found in the library itself, from the paths the server already
reported, so this works for Plex and Jellyfin without asking either.
Stored in video settings as ``drive_map``: [{"server": ..., "local": ...}].
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

SETTING = "drive_map"
_CACHE_TTL = 20.0
_cache: Dict[str, Any] = {"at": 0.0, "map": []}


def _norm_server(path: Any) -> str:
    """a server path prefix, compared the way paths are written: forward
    slashes, no trailing slash, case kept (a linux server is case-sensitive)"""
    p = str(path or "").strip().replace("\\", "/")
    p = re.sub(r"/{2,}", "/", p) if not p.startswith("//") else "//" + re.sub(r"/{2,}", "/", p[2:])
    return p.rstrip("/") or ("/" if p.startswith("/") else "")


def normalize(entries: Any) -> List[Dict[str, str]]:
    """a list of {server, local} pairs, cleaned. raises ValueError on a bad
    shape or one server drive mapped twice."""
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise ValueError("The drive map must be a list")
    out, seen = [], set()
    for e in entries:
        if not isinstance(e, dict):
            raise ValueError("Each drive needs a server path and a local path")
        server = _norm_server(e.get("server"))
        local = str(e.get("local") or "").strip()
        if not server:
            raise ValueError("A drive is missing its server path")
        if "\x00" in server or "\x00" in local:
            raise ValueError("Paths cannot contain null characters")
        if not local:
            continue           # left blank: not mapped (yet)
        key = server.casefold()
        if key in seen:
            raise ValueError(f"{server} is mapped twice")
        seen.add(key)
        out.append({"server": server, "local": local.rstrip("/\\") or local})
    return out


def load(db) -> List[Dict[str, str]]:
    try:
        raw = db.get_setting(SETTING) or "[]"
        return normalize(json.loads(raw) if isinstance(raw, str) else raw)
    except (ValueError, TypeError):
        return []


def save(db, entries: Any) -> List[Dict[str, str]]:
    cleaned = normalize(entries)
    db.set_setting(SETTING, json.dumps(cleaned))
    forget()
    return cleaned


def forget() -> None:
    """drop the cached map so a save shows at once"""
    _cache["at"] = 0.0


def current() -> List[Dict[str, str]]:
    """the saved map, read at most every 20s (the resolver runs per file)"""
    now = time.monotonic()
    if _cache["at"] and now - _cache["at"] < _CACHE_TTL:
        return _cache["map"]
    try:
        from api.video import get_video_db
        entries = load(get_video_db())
    except Exception:   # noqa: BLE001 - no db (a test, early boot): nothing mapped
        entries = []
    _cache.update(at=now, map=entries)
    return entries


def map_path(entries: List[Dict[str, str]], server_path: Any) -> Optional[str]:
    """the local path a server path maps to, or None when no drive covers it.
    the longest server prefix wins, so /mnt/a/TV can map apart from /mnt/a."""
    p = _norm_server(server_path)
    if not p:
        return None
    best = None
    for e in entries or []:
        server = e["server"]
        if p == server or p.startswith(server.rstrip("/") + "/"):
            if best is None or len(server) > len(best["server"]):
                best = e
    if best is None:
        return None
    rest = p[len(best["server"]):].lstrip("/")
    local = best["local"]
    if not rest:
        return local
    # join with the local side's own separator (a share keeps backslashes)
    sep = "\\" if ("\\" in local or re.match(r"^[A-Za-z]:", local)) else "/"
    return local.rstrip("/\\") + sep + rest.replace("/", sep)


def drive_root(server_path: Any) -> Optional[str]:
    """the drive a server path lives on: '/mnt/easystore3', 'D:', '//nas/share'.
    one mapping per drive is what people set up"""
    p = _norm_server(server_path)
    if not p:
        return None
    if p.startswith("//"):
        parts = [x for x in p[2:].split("/") if x]
        return "//" + "/".join(parts[:2]) if len(parts) >= 2 else None
    m = re.match(r"^([A-Za-z]:)(/|$)", p)
    if m:
        return m.group(1)
    parts = [x for x in p.split("/") if x]
    if len(parts) < 2:
        return None
    # /mnt/<drive>, /media/<drive>, /data/... : the first two levels
    return "/" + "/".join(parts[:2])


def server_drives(db) -> List[Dict[str, Any]]:
    """every drive the library's files sit on, biggest first, with how many
    files and the mapping saved for it"""
    counts: Dict[str, int] = {}
    for path in db.media_file_paths():
        root = drive_root(path)
        if root:
            counts[root] = counts.get(root, 0) + 1
    saved = {e["server"]: e["local"] for e in load(db)}
    out = [{"server": root, "files": n, "local": saved.get(root, "")}
           for root, n in counts.items()]
    # a mapping saved for a drive no file is on any more stays visible
    for server, local in saved.items():
        if server not in counts:
            out.append({"server": server, "files": 0, "local": local})
    out.sort(key=lambda d: (-d["files"], d["server"]))
    return out


def check(db, server: Any, local: Any, *, sample: int = 40,
          exists: Callable[[str], bool] = os.path.exists,
          isdir: Callable[[str], bool] = os.path.isdir) -> Dict[str, Any]:
    """does mapping ``server`` to ``local`` reach the right files? a sample of
    the library's files on that drive, translated and looked for."""
    server = _norm_server(server)
    local = str(local or "").strip()
    if not server or not local:
        return {"ok": False, "message": "Set the folder SoulSync reaches this drive at"}
    if not isdir(local):
        return {"ok": False, "message": "SoulSync can't see that folder"}
    entries = [{"server": server, "local": local}]
    paths = db.media_file_paths(prefix=server, limit=sample)
    if not paths:
        return {"ok": True, "message": "Folder found (no library files on this drive to test)",
                "sampled": 0, "found": 0}
    found, missing = 0, []
    for p in paths:
        cand = map_path(entries, p)
        if cand and exists(cand):
            found += 1
        elif len(missing) < 3:
            missing.append(cand or p)
    ok = found >= max(1, int(len(paths) * 0.8))
    return {"ok": ok, "sampled": len(paths), "found": found, "missing_examples": missing,
            "message": (f"{found} of {len(paths)} sample files found" if found
                        else "None of the sample files are there: check the folder")}
