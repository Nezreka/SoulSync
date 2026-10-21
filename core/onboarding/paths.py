"""check a folder the setup flow is about to save.

answers the questions people get wrong on day one: does it exist, can
soulsync write there, how much room is left, and is it a windows path typed
into a linux container. nothing is created here, a missing folder only
reports whether it could be.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from typing import Callable, Optional

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _nearest_existing_parent(path: str, isdir: Callable[[str], bool]) -> Optional[str]:
    current = os.path.dirname(os.path.abspath(path))
    while current:
        if isdir(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def _probe_writable(folder: str) -> bool:
    """os.access lies on network shares and some docker mounts, so write a
    throwaway file instead"""
    try:
        fd, name = tempfile.mkstemp(prefix=".soulsync-write-test-", dir=folder)
        os.close(fd)
        os.remove(name)
        return True
    except OSError:
        return False


def check_path(path: str, *, docker: bool,
               isdir: Callable[[str], bool] = os.path.isdir,
               exists: Callable[[str], bool] = os.path.exists,
               writable: Callable[[str], bool] = _probe_writable,
               disk_usage: Callable[[str], object] = shutil.disk_usage) -> dict:
    """{state, message, free_bytes, resolved}. state is ok, create (missing but
    its parent is writable, soulsync makes it on first use) or problem"""
    raw = (path or "").strip()
    out = {"path": raw, "resolved": "", "state": "problem", "message": "", "free_bytes": None}
    if not raw:
        out["message"] = "Pick a folder"
        return out
    if "\x00" in raw:
        out["message"] = "That isn't a valid folder path"
        return out
    if docker and _WINDOWS_DRIVE.match(raw):
        out["message"] = ("That's a Windows path. Inside Docker use the container path "
                          "from your compose file, like /app/downloads")
        return out

    resolved = os.path.abspath(raw)
    out["resolved"] = resolved

    def free(folder):
        try:
            return int(disk_usage(folder).free)
        except OSError:
            return None

    if exists(resolved) and not isdir(resolved):
        out["message"] = "That's a file, not a folder"
        return out

    if isdir(resolved):
        out["free_bytes"] = free(resolved)
        if not writable(resolved):
            out["message"] = ("SoulSync can't write here. Check the folder's owner, "
                              "or PUID and PGID in Docker")
            return out
        out["state"] = "ok"
        return out

    parent = _nearest_existing_parent(resolved, isdir)
    if parent is None or not writable(parent):
        out["message"] = ("This folder doesn't exist and SoulSync can't create it. "
                          + ("Is the volume mapped in your compose file?" if docker
                             else "Create it first, or pick another"))
        return out
    out["free_bytes"] = free(parent)
    out["state"] = "create"
    out["message"] = "Doesn't exist yet. SoulSync creates it on first use"
    return out


def check_paths(paths: dict, *, docker: bool, **kwargs) -> dict:
    """check several at once and flag two rows pointing at the same folder,
    a download folder that is also the library is how half-finished albums
    end up in plex"""
    results = {key: check_path(value, docker=docker, **kwargs)
               for key, value in (paths or {}).items()}
    seen = {}
    for key, result in results.items():
        resolved = result.get("resolved")
        if not resolved:
            continue
        norm = os.path.normcase(os.path.normpath(resolved))
        if norm in seen and result["state"] != "problem":
            result["state"] = "problem"
            result["message"] = "Same folder as another row. Each one needs its own"
        seen.setdefault(norm, key)
    return results
