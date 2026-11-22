"""Path rules for the folder watcher, with no I/O of their own.

Which names a watcher ignores (temp and partial files, dotfiles, system
folders), which folder a change belongs to (the album folder, with a CD1/Disc 2
folder counted as its album), and whether a folder can be watched live or has
to be checked on a timer (network shares, where the system's change
notifications are not reliable).

Windows paths (``C:\\Music``, ``\\\\nas\\music``) are judged by their shape on any
platform, so the rules are testable from Linux and right on Windows.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple

AUDIO_EXTS = frozenset({
    ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".m4b", ".aac", ".alac",
    ".wav", ".wma", ".aiff", ".aif", ".ape", ".wv", ".dsf", ".dff", ".mka",
})

#: endings of files still being written: browsers, download clients, copy
#: tools, SoulSync's own atomic writes
TEMP_SUFFIXES = (
    ".part", ".partial", ".tmp", ".temp", ".crdownload", ".download", ".dlpart",
    ".!qb", ".!ut", ".aria2", ".filepart", ".sstmp", ".ssync-tmp", ".swp", "~",
)

#: folders no music lives in: NAS indexes, recycle bins, OS bookkeeping
IGNORED_DIRS = frozenset({
    "@eadir", "#recycle", "#snapshot", "$recycle.bin", "system volume information",
    "lost+found", "@recycle", ".trash", "@tmp",
})

#: a disc folder belongs to its album: "CD1", "Disc 2", "Disk 01", "CD 3"
_DISC_FOLDER = re.compile(r"^\s*(cd|disc|disk|dvd|side)\s*[-_.]?\s*\d{1,2}\b", re.IGNORECASE)

#: filesystems whose change notifications don't reach this machine reliably:
#: network shares, and the host-folder bridges of WSL and Docker Desktop
NETWORK_FS = frozenset({
    "cifs", "smb", "smb2", "smb3", "smbfs", "nfs", "nfs4", "9p", "drvfs",
    "afs", "ncpfs", "davfs", "fuse.davfs2", "fuse.sshfs", "sshfs", "fuse.rclone",
    "glusterfs", "fuse.glusterfs", "ceph", "fuse.ceph", "fuse.s3fs", "fuse.gcsfuse",
    "fuse.grpcfuse", "grpcfuse", "fakeowner", "fuse.juicefs", "lustre", "beegfs",
})

LIVE = "live"
POLLING = "polling"
OFF = "off"
AUTO = "auto"
MODES = (AUTO, LIVE, POLLING, OFF)

#: GetDriveTypeW's answer for a mapped network drive
DRIVE_REMOTE = 4

_WIN_DRIVE = re.compile(r"^[A-Za-z]:([\\/]|$)")


def is_unc(path: str) -> bool:
    """``\\\\host\\share`` or ``//host/share``, Windows' network path."""
    text = str(path or "")
    return (text.startswith("\\\\") or text.startswith("//")) and len(text) > 2 \
        and text[2] not in "\\/?."


def is_windows_path(path: str) -> bool:
    text = str(path or "")
    return bool(_WIN_DRIVE.match(text)) or is_unc(text)


def _mod(path: str):
    return ntpath if is_windows_path(path) else posixpath


def norm(path: str) -> str:
    """The folder as one spelling: normalised, no trailing separator (a
    drive or share root keeps its own)."""
    text = str(path or "").strip()
    if not text:
        return ""
    mod = _mod(text)
    out = mod.normpath(text)
    if mod is ntpath:
        drive, rest = ntpath.splitdrive(out)
        if rest in ("", "\\"):
            return drive + "\\"
        return out.rstrip("\\")
    return out if out == "/" else out.rstrip("/")


def parent(path: str) -> str:
    return norm(_mod(path).dirname(norm(path)))


def basename(path: str) -> str:
    return _mod(path).basename(norm(path))


def is_under(path: str, folder: str) -> bool:
    """``path`` is ``folder`` or inside it. Case folded for Windows paths."""
    a, b = norm(path), norm(folder)
    if not a or not b:
        return False
    if is_windows_path(b):
        a, b = a.replace("/", "\\").lower(), b.replace("/", "\\").lower()
        sep = "\\"
    else:
        sep = "/"
    if a == b:
        return True
    return a.startswith(b if b.endswith(sep) else b + sep)


def relative_parts(path: str, root: str) -> List[str]:
    """The components of ``path`` below ``root`` (empty for the root)."""
    if not is_under(path, root):
        return []
    a, b = norm(path), norm(root)
    rest = a[len(b):]
    return [part for part in re.split(r"[\\/]+", rest) if part]


def is_temp_name(name: str) -> bool:
    """A file still being written, or never meant to stay: dotfiles,
    ``~$`` office locks, partial downloads, copy temps."""
    low = str(name or "").lower()
    if not low or low.startswith(".") or low.startswith("~$"):
        return True
    return low.endswith(TEMP_SUFFIXES)


def is_audio_name(name: str) -> bool:
    low = str(name or "").lower()
    dot = low.rfind(".")
    return dot > 0 and low[dot:] in AUDIO_EXTS and not is_temp_name(low)


def ignored_dir_name(name: str) -> bool:
    low = str(name or "").lower()
    return low.startswith(".") or low in IGNORED_DIRS


def is_ignored(path: str, root: str, *, is_dir: bool = False,
               excluded: Iterable[str] = ()) -> bool:
    """Whether a change at ``path`` is none of the watcher's business: outside
    the root, inside a dot or system folder, inside an excluded folder (the
    Import or download folder kept inside the library), or a file that is not
    audio or is still being written."""
    if not is_under(path, root):
        return True
    for folder in excluded:
        if folder and is_under(path, folder):
            return True
    parts = relative_parts(path, root)
    dirs = parts if is_dir else parts[:-1]
    if any(ignored_dir_name(part) for part in dirs):
        return True
    if is_dir:
        return False
    return not parts or not is_audio_name(parts[-1])


def is_disc_folder(name: str) -> bool:
    return bool(_DISC_FOLDER.match(str(name or "")))


def album_unit(path: str, root: str, *, is_dir: bool = False) -> Optional[Tuple[str, bool]]:
    """The folder one change is about, as ``(folder, recursive)``.

    A file's unit is its album folder: the folder it is in, or that folder's
    parent when it is a disc folder (CD1, Disc 2). A folder that appeared,
    vanished or moved is its own unit, read with everything in it (an album
    copied in, an artist folder deleted). A file directly in the root is the
    root, read without its subfolders. Never above the root; ``None`` for the
    root folder itself (its health is checked on its own)."""
    root_n = norm(root)
    target = norm(path)
    if not is_under(target, root_n):
        return None
    if is_dir:
        if not relative_parts(target, root_n):
            return None
        folder = target
        if is_disc_folder(basename(folder)) and len(relative_parts(folder, root_n)) > 1:
            folder = parent(folder)
        return folder, True
    folder = parent(target)
    if not relative_parts(folder, root_n):
        return root_n, False
    if is_disc_folder(basename(folder)) and len(relative_parts(folder, root_n)) > 1:
        folder = parent(folder)
    return folder, True


def collapse(units: Dict[str, bool]) -> Dict[str, bool]:
    """Drop every unit a recursive unit above it already covers."""
    recursive = sorted((f for f, r in units.items() if r), key=len)
    kept: Dict[str, bool] = {}
    for folder, rec in sorted(units.items(), key=lambda item: len(item[0])):
        if any(top != folder and is_under(folder, top) for top in recursive if top in kept):
            continue
        kept[folder] = rec or kept.get(folder, False)
    return kept


def coarsen(units: Dict[str, bool], root: str, cap: int) -> Dict[str, bool]:
    """Keep at most ``cap`` units by lifting the deepest ones a level at a
    time: a copy of 5,000 albums becomes a few hundred artist folders, and
    only a flood of artists reaches the root."""
    out = collapse(units)
    root_n = norm(root)
    while len(out) > max(1, cap):
        depth = {f: len(relative_parts(f, root_n)) for f in out}
        deepest = max(depth.values())
        if deepest == 0:
            break
        lifted: Dict[str, bool] = {}
        for folder, rec in out.items():
            if depth[folder] == deepest:
                up = parent(folder) if deepest > 1 else root_n
                lifted[up] = True
            else:
                lifted[folder] = lifted.get(folder, False) or rec
        out = collapse(lifted)
    return out


# ── live or polling ──────────────────────────────────────────────────────


def parse_mounts(text: str) -> List[Tuple[str, str]]:
    """``[(mount point, fs type)]`` from /proc/mounts (or /proc/self/mountinfo)."""
    out: List[Tuple[str, str]] = []
    for line in str(text or "").splitlines():
        fields = line.split()
        if not fields:
            continue
        if " - " in line:  # mountinfo: "... mountpoint ... - fstype source opts"
            left, _, right = line.partition(" - ")
            lf, rf = left.split(), right.split()
            if len(lf) >= 5 and rf:
                out.append((_unescape(lf[4]), rf[0]))
            continue
        if len(fields) >= 3:
            out.append((_unescape(fields[1]), fields[2]))
    return out


def _unescape(text: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def fs_type_for(path: str, mounts: List[Tuple[str, str]]) -> str:
    """The filesystem type of the longest mount point holding ``path``."""
    best, kind = "", ""
    for point, fstype in mounts:
        if is_under(path, point) and len(point) >= len(best):
            best, kind = point, fstype
    return kind


def classify(path: str, *, platform: str, mounts: Optional[List[Tuple[str, str]]] = None,
             drive_type: Optional[Callable[[str], int]] = None) -> Tuple[str, str]:
    """``(LIVE or POLLING, why)`` for one folder, by where it lives.

    Network shares and the host-folder bridges (WSL's drvfs, Docker Desktop's
    9p) don't pass change notifications through reliably, so those are checked
    on a timer. ``platform`` is ``sys.platform``; ``mounts`` the parsed mount
    table (Linux); ``drive_type`` Windows' GetDriveTypeW for a drive root."""
    text = str(path or "")
    if is_unc(text):
        return POLLING, "network share"
    if platform.startswith("win") or _WIN_DRIVE.match(text):
        if _WIN_DRIVE.match(text) and drive_type is not None:
            remote = False
            try:
                remote = int(drive_type(text[:2] + "\\")) == DRIVE_REMOTE
            except Exception:  # noqa: BLE001 - unknown drive: watch it live
                remote = False
            if remote:
                return POLLING, "network drive"
        return LIVE, "local disk"
    if mounts:
        fstype = fs_type_for(text, mounts).lower()
        if fstype in NETWORK_FS or fstype.startswith("nfs") or fstype.startswith("smb"):
            return POLLING, f"network share ({fstype})"
        if fstype:
            return LIVE, f"local disk ({fstype})"
    return LIVE, "local disk"


__all__ = [
    "AUDIO_EXTS", "AUTO", "IGNORED_DIRS", "LIVE", "MODES", "NETWORK_FS", "OFF", "POLLING",
    "album_unit", "basename", "classify", "coarsen", "collapse", "fs_type_for",
    "ignored_dir_name", "is_audio_name", "is_disc_folder", "is_ignored", "is_temp_name",
    "is_under", "is_unc", "is_windows_path", "norm", "parent", "parse_mounts",
    "relative_parts",
]
