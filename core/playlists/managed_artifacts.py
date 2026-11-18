"""Conservative ownership records for generated playlist files.

The manifest lives beside playlist folders, never among the user's audio/M3Us.
An absent, invalid, or outdated ownership record grants no permission to replace
or prune a file. Atomic replacement keeps the previous complete manifest intact
if a write is interrupted.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading

MANIFEST_DIR = ".soulsync-playlist-manifests"
MATERIALIZE_LOCK = threading.RLock()


def safe_output_dir(root: str, folder: str, *, create: bool = True) -> str:
    root = os.path.abspath(root)
    folder = os.path.abspath(folder)
    if os.path.dirname(folder) != root or os.path.basename(folder) == MANIFEST_DIR:
        raise OSError("Playlist output must be a direct child of the output root")
    if os.path.islink(folder):
        raise OSError("Refusing a symlinked playlist output directory")
    if create:
        os.makedirs(folder, exist_ok=True)
    if os.path.dirname(os.path.realpath(folder)) != os.path.realpath(root):
        raise OSError("Playlist output resolves outside its output root")
    return folder


def artifact_fingerprint(path: str):
    """Describe the directory entry itself, never its symlink target.

    Only what survives a remount goes in (#2137): device, inode and ctime
    change when a FAT/exFAT drive or a network share comes back, and fat
    mount options can shift permission bits and mtimes too. A copy is its
    size and sha256 (the digest is what catches an edit, ctime never did on
    windows), a symlink is where it points. Symlinks never read their
    target's bytes.
    """
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return {"kind": "symlink", "target": os.readlink(path)}
        if not stat.S_ISREG(st.st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            # same call, same mount: these still prove the open hit the entry we lstat'd.
            if (opened.st_dev, opened.st_ino, opened.st_mode) != (st.st_dev, st.st_ino, st.st_mode):
                return None
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            after = os.fstat(stream.fileno())
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                return None
        return {"kind": "file", "size": opened.st_size, "sha256": digest.hexdigest()}
    except OSError:
        return None


def stable_fingerprint(record):
    """A stored fingerprint in today's shape.

    manifests before #2137 also kept device, inode, mode, mtime and ctime.
    their stable part (sha256 + size, or the link target) is still exact, so
    an upgrade keeps owning every entry instead of calling it changed.
    anything else grants nothing."""
    if not isinstance(record, dict):
        return None
    kind = record.get("kind")
    if kind is None and isinstance(record.get("mode"), int):
        kind = "symlink" if stat.S_ISLNK(record["mode"]) else "file"
    if kind == "symlink" and isinstance(record.get("target"), str):
        return {"kind": "symlink", "target": record["target"]}
    if (kind == "file" and isinstance(record.get("sha256"), str)
            and isinstance(record.get("size"), int)):
        return {"kind": "file", "size": record["size"], "sha256": record["sha256"]}
    return None


def entry_is_owned(path: str, record) -> bool:
    if not isinstance(record, dict):
        return False
    expected = stable_fingerprint(record.get("fingerprint"))
    return expected is not None and artifact_fingerprint(path) == expected


def manifest_path(root: str, folder: str, *, create: bool = True) -> str:
    directory = os.path.join(os.path.abspath(root), MANIFEST_DIR)
    if os.path.islink(directory):
        raise OSError("Refusing a symlinked playlist manifest directory")
    if create:
        os.makedirs(directory, exist_ok=True)
    key = hashlib.sha256(os.path.basename(folder).encode("utf-8")).hexdigest()
    path = os.path.join(directory, key + ".json")
    if os.path.islink(path):
        raise OSError("Refusing a symlinked playlist manifest")
    return path


def load_manifest(root: str, folder: str) -> dict:
    path = manifest_path(root, folder)
    empty = {"version": 1, "folder": os.path.basename(folder), "entries": {}}
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except (FileNotFoundError, ValueError, UnicodeError):
        return empty
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("folder") != empty["folder"]:
        return empty
    entries = value.get("entries")
    if not isinstance(entries, dict):
        return empty
    value["entries"] = {
        name: record for name, record in entries.items()
        if isinstance(name, str) and name not in ("", ".", "..")
        and "/" not in name and "\\" not in name and isinstance(record, dict)
    }
    for record in value["entries"].values():
        # pre #2137 fingerprints move to the remount-safe shape on load, so the
        # next save writes them back without device/inode/ctime.
        if "fingerprint" in record:
            record["fingerprint"] = stable_fingerprint(record["fingerprint"])
    return value


def save_manifest(root: str, folder: str, manifest: dict) -> None:
    path = manifest_path(root, folder)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, sort_keys=True, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Persist the rename too on filesystems which support directory fsync.
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
