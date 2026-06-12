"""Materialize a playlist as a folder of links into the real music library.

A playlist folder is a **view**, not storage. Every entry points at the one real
file that already lives in the library (``Artist/Album/track.ext``); a track is
never stored twice no matter how many playlists it's in. Two modes:

- ``symlink`` (default): a *relative* symlink to the real file — ~zero disk, and
  relative so the tree stays valid if the parent folder is moved.
- ``copy``: a real duplicate of the file — for filesystems/players that can't
  follow symlinks (FAT USB sticks, some DAPs) or when a self-contained,
  portable folder is wanted.

Symlinks silently fail or are unsupported on a lot of real setups (Windows
without the privilege, SMB/CIFS shares, FAT/exFAT). So when a symlink can't be
created we **fall back to a copy automatically** — the folder is always fully
populated, never left with dangling links.

This module is pure filesystem mechanics: no DB, no app state. Given a list of
real file paths, a playlists root, a playlist name and a mode, it (re)builds the
folder to match. That makes it the single unit-tested source of truth for "how a
playlist folder looks on disk", and means the folder is a *derived view* that can
be rebuilt from scratch at any time. Filesystem ops are injectable so the
behaviour — including the symlink→copy fallback — is testable without depending
on the host filesystem's symlink support.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from core.imports.paths import sanitize_filename

MATERIALIZE_MODES = ("symlink", "copy")
DEFAULT_MODE = "symlink"


def normalize_mode(mode: Optional[str]) -> str:
    """Coerce a config value to a valid mode (``symlink`` default)."""
    m = (mode or "").strip().lower()
    return m if m in MATERIALIZE_MODES else DEFAULT_MODE


@dataclass
class RebuildSummary:
    """Outcome of rebuilding one playlist folder. ``copied`` may be non-zero even
    in symlink mode when the fallback kicked in (flagged by ``fellback``)."""
    playlist_dir: str = ""
    linked: int = 0
    copied: int = 0
    unchanged: int = 0
    removed_stale: int = 0
    missing_source: int = 0
    failed: int = 0
    fellback: bool = False
    mode_requested: str = DEFAULT_MODE
    errors: List[str] = field(default_factory=list)


def playlist_dir_for(playlists_root: str, playlist_name: str) -> str:
    """Absolute path of one playlist's folder, sanitized and guaranteed to stay
    directly under ``playlists_root`` (defends against ``..`` / separators in a
    playlist name)."""
    root = os.path.abspath(playlists_root)
    safe_name = sanitize_filename(playlist_name or "").strip() or "Unnamed Playlist"
    candidate = os.path.abspath(os.path.join(root, safe_name))
    if os.path.dirname(candidate) != root:
        safe_name = sanitize_filename(os.path.basename(candidate)) or "Unnamed Playlist"
        candidate = os.path.join(root, safe_name)
    return candidate


def _desired_entries(playlist_dir: str, real_paths: Sequence[str]) -> "list[tuple[str, str]]":
    """Map each real file to a flat destination inside ``playlist_dir``, preserving
    the source filename. On a basename collision between two *different* sources,
    disambiguate with a numeric suffix rather than silently overwriting."""
    entries: list[tuple[str, str]] = []
    used: dict[str, str] = {}  # dest basename -> source real path
    for real in real_paths:
        if not real:
            continue
        base = os.path.basename(real)
        name = base
        stem, ext = os.path.splitext(base)
        counter = 1
        while name in used and used[name] != os.path.abspath(real):
            counter += 1
            name = f"{stem} ({counter}){ext}"
        used[name] = os.path.abspath(real)
        entries.append((os.path.abspath(real), os.path.join(playlist_dir, name)))
    return entries


def _symlink_is_current(dest: str, rel_target: str) -> bool:
    try:
        return os.path.islink(dest) and os.readlink(dest) == rel_target
    except OSError:
        return False


def _remove_entry(path: str) -> None:
    """Remove an existing file/symlink at ``path`` (incl. a broken symlink)."""
    if os.path.islink(path) or os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def materialize_one(
    real_path: str,
    dest_path: str,
    mode: str = DEFAULT_MODE,
    *,
    symlink_fn: Callable[[str, str], None] = os.symlink,
    copy_fn: Callable[[str, str], object] = shutil.copy2,
) -> str:
    """Create one playlist entry at ``dest_path`` pointing at ``real_path``.

    Idempotent: a correct existing entry is left alone. In ``symlink`` mode a
    relative link is used; if it can't be created (unsupported FS, no privilege)
    it falls back to a copy so the entry is never left broken. Returns one of:
    ``'linked'``, ``'copied'``, ``'unchanged'``, ``'fellback'`` (symlink
    requested but copied), ``'missing'`` (source gone)."""
    if not real_path or not os.path.exists(real_path):
        return "missing"

    dest_dir = os.path.dirname(dest_path)
    os.makedirs(dest_dir, exist_ok=True)
    rel_target = os.path.relpath(os.path.abspath(real_path), start=dest_dir)

    if mode == "copy":
        if os.path.isfile(dest_path) and not os.path.islink(dest_path):
            return "unchanged"
        _remove_entry(dest_path)
        copy_fn(real_path, dest_path)
        return "copied"

    # symlink mode
    if _symlink_is_current(dest_path, rel_target):
        return "unchanged"
    _remove_entry(dest_path)
    try:
        symlink_fn(rel_target, dest_path)
        return "linked"
    except (OSError, NotImplementedError):
        copy_fn(real_path, dest_path)
        return "fellback"


def rebuild_playlist_folder(
    playlists_root: str,
    playlist_name: str,
    real_paths: Sequence[str],
    mode: str = DEFAULT_MODE,
    *,
    prune_stale: bool = True,
    symlink_fn: Callable[[str, str], None] = os.symlink,
    copy_fn: Callable[[str, str], object] = shutil.copy2,
) -> RebuildSummary:
    """(Re)build ``playlists_root/<playlist_name>/`` so it contains exactly one
    entry per real file in ``real_paths`` — adding missing entries, leaving correct
    ones untouched, and (when ``prune_stale``) removing entries no longer present.
    Idempotent and safe to re-run any time. Filesystem ops are injectable."""
    mode = normalize_mode(mode)
    pdir = playlist_dir_for(playlists_root, playlist_name)
    summary = RebuildSummary(playlist_dir=pdir, mode_requested=mode)
    os.makedirs(pdir, exist_ok=True)

    entries = _desired_entries(pdir, real_paths)
    keep = {dest for _real, dest in entries}

    for real, dest in entries:
        try:
            outcome = materialize_one(real, dest, mode, symlink_fn=symlink_fn, copy_fn=copy_fn)
        except OSError as e:
            summary.failed += 1
            summary.errors.append(f"{os.path.basename(dest)}: {e}")
            continue
        if outcome == "linked":
            summary.linked += 1
        elif outcome == "copied":
            summary.copied += 1
        elif outcome == "fellback":
            summary.copied += 1
            summary.fellback = True
        elif outcome == "unchanged":
            summary.unchanged += 1
        elif outcome == "missing":
            summary.missing_source += 1

    if prune_stale and os.path.isdir(pdir):
        for name in os.listdir(pdir):
            full = os.path.join(pdir, name)
            if full in keep:
                continue
            if os.path.islink(full) or os.path.isfile(full):
                _remove_entry(full)
                summary.removed_stale += 1

    return summary


__all__ = [
    "MATERIALIZE_MODES",
    "DEFAULT_MODE",
    "normalize_mode",
    "RebuildSummary",
    "playlist_dir_for",
    "materialize_one",
    "rebuild_playlist_folder",
]
