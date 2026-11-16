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


def fingerprint_matches(current, stored) -> bool:
    """True when a fresh fingerprint proves the stored record's entry.

    Only the stable keys are compared: device, inode and ctime do not survive
    a remount (FAT/exFAT and many network shares have no persistent inode
    numbers, and a drive can return with a different device number), so stored
    records carrying those legacy keys are matched on the stable keys alone.
    Mode must agree on file type, and the proof key must be present in both
    and equal: copies are proved by their sha256 content digest plus size,
    symlinks by their exact target alone (a link's size is just its target's
    length, and some drivers report it differently). A record missing its
    proof key is never owned, so the entry is preserved.
    Of mode, only the file-type bits are compared: the permission and
    setuid/setgid/sticky bits do not survive remount mount options or a
    chmod, and they add no proof beyond the digest or target (a copy
    carries its source's permissions anyway).
    mtime_ns is stored but never compared: a network-share client can
    cache the exact time just set while the server keeps a rounded one
    (100ns on NTFS-backed shares, 2s on FAT-backed), FAT timezone/DST
    mount options shift it by hours, and it adds no proof anyway - copy2
    preserves the source's mtime on copies, and a symlink's proof is its
    target.
    Malformed stored values (including out-of-range modes from a corrupted
    manifest) yield False, never an exception: the type bits are masked in
    pure Python rather than via stat.S_IFMT, whose C conversion raises on
    negative or huge ints.
    """
    if not isinstance(current, dict) or not isinstance(stored, dict):
        return False
    mode = current.get("mode")
    stored_mode = stored.get("mode")
    if type(mode) is not int or type(stored_mode) is not int:
        return False
    # 0o177777 is the largest legitimate st_mode: file-type bits plus
    # setuid/setgid/sticky plus the rwx permission bits. The stored mode
    # must lie in it (a corrupted manifest can carry any int); the current
    # mode comes from our own lstat, so it is masked into range instead -
    # extra high bits on an exotic platform must not fail every entry.
    if not (0 <= stored_mode <= 0o177777):
        return False
    mode &= 0o177777
    type_bits = mode & 0o170000
    if type_bits not in (stat.S_IFREG, stat.S_IFLNK):
        return False
    if type_bits != (stored_mode & 0o170000):
        return False
    extra = "target" if type_bits == stat.S_IFLNK else "sha256"
    if not isinstance(current.get(extra), str) or not isinstance(stored.get(extra), str):
        return False
    if current[extra] != stored[extra]:
        return False
    if extra == "sha256":
        if type(current.get("size")) is not int or type(stored.get("size")) is not int:
            return False
        if current["size"] != stored["size"]:
            return False
    return True


def artifact_fingerprint(path: str):
    """Describe the directory entry itself, never its symlink target.

    Mode, size and mtime are stored, plus a content digest for copies
    (Windows/filesystem ctime semantics may not expose an in-place edit
    when the writer restores size and mtime) or the exact target for
    symlinks (which never read their target's bytes). Only remount-stable
    fields are compared (see fingerprint_matches): mode's file type and
    the digest or target, plus size for copies. Device, inode and ctime
    are not stored in new fingerprints: they change across remounts, so a
    fingerprint carrying them fails every entry after one (old records
    keep those keys; the comparison ignores them). mtime is recorded for
    diagnostics only - it is not stable across remounts and proves
    nothing the digest or target does not.
    Residual risk: a copy is byte-identical to its source by construction,
    so the library source itself is a byte-identical match for our
    copy, at any mtime. Moving or copying (mv, cp, cp -p, rsync -a) that
    source onto our copy therefore makes it indistinguishable from our
    copy (neither a permission nor an mtime difference distinguishes it
    either), so if that track later leaves the playlist, prune deletes
    the file. A symlink recreated to the same target likewise counts as
    ours: its target is all it holds.
    """
    try:
        st = os.lstat(path)
        if not (stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode)):
            return None
        result = {"mode": st.st_mode,
                  "size": st.st_size, "mtime_ns": st.st_mtime_ns}
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
        fingerprint_matches(artifact_fingerprint(path), record["fingerprint"]))


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
