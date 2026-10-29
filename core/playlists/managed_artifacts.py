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

    Metadata detects replacements cheaply. Copies also retain a content digest:
    Windows/filesystem ctime semantics may not expose an in-place edit when the
    writer restores size and mtime. Symlinks never read their target's bytes.
    """
    try:
        st = os.lstat(path)
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
            return None
        result = {"device": st.st_dev, "inode": st.st_ino, "mode": st.st_mode,
                  "size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}
        if stat.S_ISLNK(st.st_mode):
            result["target"] = os.readlink(path)
        else:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_mode) != (st.st_dev, st.st_ino, st.st_mode):
                    return None
                digest = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                        opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                    return None
                result["sha256"] = digest.hexdigest()
        return result
    except OSError:
        return None


def entry_is_owned(path: str, record) -> bool:
    return isinstance(record, dict) and record.get("fingerprint") is not None and (
        artifact_fingerprint(path) == record["fingerprint"])


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
