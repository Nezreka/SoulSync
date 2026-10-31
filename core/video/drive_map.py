"""Your library's folders: where SoulSync reaches each folder the server reads.

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

The folders come from the server itself: every folder the libraries SoulSync
scans read (Plex section locations, Jellyfin virtual folders), each a Movies
or TV folder. A folder SoulSync already reaches (your output folders, an
extra folder) is recognised and ticked; the rest each get one box. A filled
in folder is a full library folder of its kind (library_roots): found,
renamed to the templates, recycled on delete. When the server can't be asked
the drives are worked out from the library's own paths instead.
Stored in video settings as ``drive_map``: [{"server", "local", "kind"}].
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
        kind = e.get("kind") if e.get("kind") in ("movie", "episode") else None
        out.append({"server": server, "local": local.rstrip("/\\") or local, "kind": kind})
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


_FOLDERS_TTL = 600.0
_folders_cache: Dict[str, Any] = {"at": 0.0, "folders": None}


def _server_folders_cached(fetch: Optional[Callable[[], Any]] = None):
    """the server's library folders, asked at most every 10 minutes"""
    now = time.monotonic()
    if fetch is None and _folders_cache["at"] and now - _folders_cache["at"] < _FOLDERS_TTL:
        return _folders_cache["folders"]
    if fetch is None:
        from core.video.sources import server_library_folders as fetch
    try:
        folders = fetch()
    except Exception:   # noqa: BLE001 - an unreachable server: fall back to paths
        folders = None
    _folders_cache.update(at=now, folders=folders)
    return folders


def _configured_roles(db) -> List[tuple]:
    """(local folder, what it is) for every folder set in Settings"""
    from core.video.library_roots import additional_paths, four_k_path
    roles = []
    for key, label in (("movies_path", "your Movies folder"), ("tv_path", "your TV folder"),
                       ("anime_path", "your anime folder")):
        v = str(db.get_setting(key) or "").strip()
        if v:
            roles.append((v, label))
    for kind, label in (("movie", "your 4K movies folder"), ("episode", "your 4K TV folder")):
        v = four_k_path(db, kind)
        if v:
            roles.append((v, label))
    for key, label in (("movies_additional_paths", "an extra movie folder"),
                       ("tv_additional_paths", "an extra TV folder")):
        for v in additional_paths(db, key):
            roles.append((v, label))
    return roles


def _same_dir(a: str, b: str) -> bool:
    na = os.path.normcase(os.path.normpath(str(a or "")))
    nb = os.path.normcase(os.path.normpath(str(b or "")))
    return bool(na) and na == nb


def _recognise(db, folder: str, sample_paths: List[str], roles: List[tuple]) -> Optional[Dict[str, str]]:
    """a folder SoulSync already reaches through a folder set in Settings:
    its local path and what it is, worked out from a few of its files"""
    from core.video.path_resolver import resolve_video_file_path
    bases = [r[0] for r in roles]
    if not bases:
        return None
    for p in sample_paths[:5]:
        real = resolve_video_file_path(p, bases, drive_map=[])
        if not real:
            continue
        rest = _norm_server(p)[len(folder):].strip("/")
        tail = rest.replace("/", os.sep)
        local = real[:-len(tail)].rstrip("/\\") if tail and real.replace("\\", "/").endswith(rest) else None
        if not local:
            continue
        for path, label in roles:
            if _same_dir(local, path):
                return {"local": path, "role": label}
        return {"local": local, "role": "a folder SoulSync already reaches"}
    return None


def server_folders(db, *, fetch: Optional[Callable[[], Any]] = None) -> List[Dict[str, Any]]:
    """every folder the server's libraries read, Movies or TV, biggest first,
    with how many library files are in it and where SoulSync reaches it"""
    listed = _server_folders_cached(fetch)
    by_server: Dict[str, Dict[str, Any]] = {}
    if listed:
        for f in listed:
            server = _norm_server(f.get("server"))
            if server and server not in by_server:
                by_server[server] = {"server": server, "kind": f.get("kind"),
                                     "library": f.get("library")}
    # the library's own files: counts, and the drives when the server can't say
    paths = db.media_file_paths()
    if not by_server:
        for p in paths:
            root = drive_root(p)
            if root and root not in by_server:
                by_server[root] = {"server": root, "kind": None, "library": None}
    order = sorted(by_server, key=len, reverse=True)
    counts: Dict[str, int] = {k: 0 for k in by_server}
    samples: Dict[str, List[str]] = {k: [] for k in by_server}
    for p in paths:
        n = _norm_server(p)
        for k in order:
            if n == k or n.startswith(k.rstrip("/") + "/"):
                counts[k] += 1
                if len(samples[k]) < 5:
                    samples[k].append(p)
                break
    saved = {e["server"]: e for e in load(db)}
    roles = _configured_roles(db)
    out = []
    for server, f in by_server.items():
        row = {**f, "files": counts[server], "local": "", "auto": False, "role": None}
        if server in saved:
            row["local"] = saved[server]["local"]
            row["kind"] = row["kind"] or saved[server].get("kind")
        else:
            known = _recognise(db, server, samples[server], roles)
            if known:
                row.update(local=known["local"], auto=True, role=known["role"])
        out.append(row)
    # a folder saved earlier that the server no longer lists stays visible
    for server, e in saved.items():
        if server not in by_server:
            out.append({"server": server, "kind": e.get("kind"), "library": None, "files": 0,
                        "local": e["local"], "auto": False, "role": None})
    out.sort(key=lambda d: (-d["files"], d["server"]))
    return out


def server_drives(db) -> List[Dict[str, Any]]:
    """kept for callers of the first drive-level version"""
    return server_folders(db)


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
