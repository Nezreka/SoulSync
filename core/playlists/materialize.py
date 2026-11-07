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
import json
import tempfile
from dataclasses import dataclass, field
from typing import Callable, List, Mapping, Optional, Sequence

from core.imports.paths import sanitize_filename
from core.playlists.managed_artifacts import (
    MANIFEST_DIR, MATERIALIZE_LOCK, artifact_fingerprint, entry_is_owned,
    load_manifest, safe_output_dir, save_manifest,
)

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


def _desired_entries(
    playlist_dir: str,
    real_paths: Sequence[str],
    dest_names: Optional[Sequence[Optional[str]]] = None,
) -> "list[tuple[str, str]]":
    """Map each real file to a flat destination inside ``playlist_dir``.

    By default the source filename is preserved. ``dest_names`` (parallel to
    ``real_paths``) lets a caller override the name per entry — e.g. a custom
    playlist file-naming template; a falsy override falls back to the source
    basename. On a name collision between two *different* sources, disambiguate
    with a numeric suffix rather than silently overwriting."""
    entries: list[tuple[str, str]] = []
    used: dict[str, str] = {}  # dest basename -> source real path
    for i, real in enumerate(real_paths):
        if not real:
            continue
        override = dest_names[i] if (dest_names is not None and i < len(dest_names)) else None
        base = sanitize_filename(override or os.path.basename(real)).strip()
        if base in ("", ".", ".."):
            base = "Unnamed Track"
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


_MTIME_SLACK_NS = 2_000_000_000


def _copy_is_current(real_path: str, dest_path: str) -> bool:
    """True when an existing copied entry still matches the source.

    The default ``copy_fn`` (``shutil.copy2``) preserves mtime, so size +
    mtime is a cheap reliable freshness check: a retagged or replaced
    library file at the same path changes at least one of them. A custom
    copy_fn that does NOT preserve mtime degrades to "always re-copy"
    (safe, just less idempotent) — never to a stale "unchanged".
    """
    try:
        src = os.stat(real_path)
        dst = os.stat(dest_path)
    except OSError:
        return False
    # within the coarsest common timestamp step: FAT keeps 2 s, exFAT 10 ms,
    # some network shares and WSL's drvfs whole seconds, so copy2's exact
    # time comes back rounded there and an exact match never held: every
    # rebuild re-copied every file. a retag or a replaced file moves the time
    # by far more than this (or changes the size)
    return (src.st_size == dst.st_size
            and abs(src.st_mtime_ns - dst.st_mtime_ns) <= _MTIME_SLACK_NS)


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
    replace_owned: bool = False,
    expected_fingerprint: Optional[dict] = None,
) -> str:
    """Create one playlist entry at ``dest_path`` pointing at ``real_path``.

    Idempotent: a correct existing entry is left alone. In ``symlink`` mode a
    relative link is used; if it can't be created (unsupported FS, no privilege)
    it falls back to a copy so the entry is never left broken. In ``copy``
    mode an existing copy is verified against the source (size + mtime) before
    being reported ``'unchanged'`` — a retagged/replaced library file at the
    same path is re-copied, never left stale. Returns one of:
    ``'linked'``, ``'copied'``, ``'unchanged'``, ``'fellback'`` (symlink
    requested but copied), ``'missing'`` (source gone)."""
    if not real_path or not os.path.exists(real_path):
        return "missing"

    # Exact paths and hardlink aliases are originals, even in symlink mode.
    # A generated symlink may safely change mode after ownership is verified.
    if os.path.abspath(real_path) == os.path.abspath(dest_path):
        return "unchanged"
    try:
        if os.path.samefile(real_path, dest_path) and not os.path.islink(dest_path):
            return "unchanged"
    except OSError:
        pass

    dest_dir = os.path.dirname(dest_path)
    os.makedirs(dest_dir, exist_ok=True)
    rel_target = os.path.relpath(os.path.abspath(real_path), start=dest_dir)

    if mode == "copy":
        if os.path.isfile(dest_path) and not os.path.islink(dest_path):
            if _copy_is_current(real_path, dest_path):
                return "unchanged"
    elif _symlink_is_current(dest_path, rel_target):
        return "unchanged"

    if os.path.lexists(dest_path) and not replace_owned:
        raise FileExistsError(f"Unmanaged playlist destination already exists: {dest_path}")

    # Prepare replacement before touching an existing owned artifact. A failed
    # copy must neither destroy it nor leave a partially written managed file.
    fd, temporary = tempfile.mkstemp(prefix=".soulsync-entry-", dir=dest_dir)
    os.close(fd)
    os.unlink(temporary)
    try:
        if mode == "copy":
            copy_fn(real_path, temporary)
            outcome = "copied"
        else:
            try:
                symlink_fn(rel_target, temporary)
                outcome = "linked"
            except (OSError, NotImplementedError):
                _remove_entry(temporary)
                copy_fn(real_path, temporary)
                outcome = "fellback"
        if replace_owned:
            if expected_fingerprint is not None and artifact_fingerprint(dest_path) != expected_fingerprint:
                raise FileExistsError("Managed playlist destination changed while preparing its replacement")
            os.replace(temporary, dest_path)
        else:
            # Exclusive publication cannot replace a file created after the
            # initial collision check. Symlinks do not require hardlink support.
            if os.path.islink(temporary):
                os.symlink(os.readlink(temporary), dest_path)
            else:
                with open(temporary, "rb") as src, open(dest_path, "xb") as dst:
                    shutil.copyfileobj(src, dst)
                shutil.copystat(temporary, dest_path, follow_symlinks=False)
        return outcome
    finally:
        _remove_entry(temporary)


def rebuild_playlist_folder(
    playlists_root: str,
    playlist_name: str,
    real_paths: Sequence[str],
    mode: str = DEFAULT_MODE,
    *,
    dest_names: Optional[Sequence[Optional[str]]] = None,
    prune_stale: bool = True,
    symlink_fn: Callable[[str, str], None] = os.symlink,
    copy_fn: Callable[[str, str], object] = shutil.copy2,
    playlist_identity: Optional[dict] = None,
) -> RebuildSummary:
    """(Re)build ``playlists_root/<playlist_name>/`` so it contains exactly one
    entry per real file in ``real_paths`` — adding missing entries, leaving correct
    ones untouched, and (when ``prune_stale``) removing entries no longer present.
    ``dest_names`` (parallel to ``real_paths``) optionally overrides each entry's
    filename (custom playlist naming); falsy entries keep the source basename.
    Idempotent and safe to re-run any time. Filesystem ops are injectable."""
    with MATERIALIZE_LOCK:
        return _rebuild_playlist_folder(
            playlists_root, playlist_name, real_paths, mode, dest_names=dest_names,
            prune_stale=prune_stale, symlink_fn=symlink_fn, copy_fn=copy_fn,
            playlist_identity=playlist_identity)


_IDENTITY_KEYS = ("profile_id", "source", "identity_kind", "identity_ref")


def _same_playlist(a: Optional[dict], b: Optional[dict]) -> bool:
    return bool(a and b) and all(str(a.get(k)) == str(b.get(k)) for k in _IDENTITY_KEYS)


def _folder_for_identity(playlists_root: str, identity: dict) -> Optional[str]:
    """The folder a manifest already records for this playlist, if any."""
    directory = os.path.join(os.path.abspath(playlists_root), MANIFEST_DIR)
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as stream:
                value = json.load(stream)
        except (OSError, ValueError, UnicodeError):
            continue
        if isinstance(value, dict) and _same_playlist(value.get("playlist"), identity):
            folder = os.path.join(os.path.abspath(playlists_root), str(value.get("folder") or ""))
            if value.get("folder") and os.path.isdir(folder):
                return folder
    return None


def _resolve_playlist_dir(playlists_root: str, playlist_name: str,
                          identity: Optional[dict]) -> str:
    """Where this playlist's folder is: its name, like 3.6.1.

    With a stable identity the folder a manifest already records for it wins
    (a renamed playlist keeps its folder), and a hashed folder from before is
    renamed to the name when that name is free. A name another playlist
    already holds gets a short suffix from the identity."""
    desired = playlist_dir_for(playlists_root, playlist_name)
    if not identity or identity.get("identity_kind") == "name":
        return desired
    existing = _folder_for_identity(playlists_root, identity)
    if existing and os.path.abspath(existing) != os.path.abspath(desired):
        if not os.path.lexists(desired) and os.path.basename(existing).startswith("profile-"):
            try:
                old_manifest = load_manifest(playlists_root, existing)
                os.rename(existing, desired)
                old_manifest["folder"] = os.path.basename(desired)
                save_manifest(playlists_root, desired, old_manifest)
                try:
                    from core.playlists.managed_artifacts import manifest_path
                    os.unlink(manifest_path(playlists_root, existing, create=False))
                except OSError:
                    pass
                return desired
            except OSError:
                return existing
        return existing
    if existing:
        return existing
    held = load_manifest(playlists_root, desired).get("playlist") if os.path.isdir(desired) else None
    if held and not _same_playlist(held, identity):
        suffix = str(identity.get("identity_digest") or "")[:6] or "2"
        return playlist_dir_for(playlists_root, f"{playlist_name} ({suffix})")
    return desired


def _adoptable(real: str, dest: str, mode: str) -> bool:
    """A 3.6.1 entry that is exactly what this rebuild would make: a link to
    the source, or (copy mode) a current copy. Never an original file."""
    try:
        if os.path.islink(dest):
            return os.path.realpath(dest) == os.path.realpath(real)
        if mode != "copy" or os.path.samefile(real, dest):
            return False
    except OSError:
        return False
    return _copy_is_current(real, dest)


def _adopt_legacy_folder(pdir: str, owned: dict, keep: set, mode: str) -> None:
    """A folder 3.6.1 built has no manifest. 3.6.1 relinked a broken link,
    re-copied a stale copy and pruned what left the playlist; v2 only manages
    what its manifest owns, so those entries failed every rebuild or stayed for
    good. Once, the first time v2 sees such a folder: every symlink is adopted
    (a link is never an original; 3.6.1 only wrote links and copies here), and
    in copy mode a regular file carrying a current member's name. Anything
    else stays the user's."""
    try:
        names = os.listdir(pdir)
    except OSError:
        return
    for name in names:
        full = os.path.join(pdir, name)
        if name in owned:
            continue
        try:
            if os.path.islink(full):
                owned[name] = {"source": os.path.realpath(full),
                               "fingerprint": artifact_fingerprint(full)}
            elif mode == "copy" and full in keep and os.path.isfile(full):
                owned[name] = {"source": None, "fingerprint": artifact_fingerprint(full)}
        except OSError:
            continue


def _rebuild_playlist_folder(
    playlists_root, playlist_name, real_paths, mode, *, dest_names, prune_stale,
    symlink_fn, copy_fn, playlist_identity,
):
    mode = normalize_mode(mode)
    pdir = _resolve_playlist_dir(playlists_root, playlist_name, playlist_identity)
    summary = RebuildSummary(playlist_dir=pdir, mode_requested=mode)
    safe_output_dir(playlists_root, pdir)
    manifest = load_manifest(playlists_root, pdir)
    owned = manifest["entries"]
    if playlist_identity is not None:
        manifest["playlist"] = dict(playlist_identity)

    entries = _desired_entries(pdir, real_paths, dest_names)
    keep = {dest for _real, dest in entries}
    sources = {real for real, _dest in entries}
    if not owned and not manifest.get("legacy_adopted") and os.path.isdir(pdir):
        _adopt_legacy_folder(pdir, owned, keep, mode)
        manifest["legacy_adopted"] = True

    for real, dest in entries:
        name = os.path.basename(dest)
        record = owned.get(name)
        verified = entry_is_owned(dest, record)
        try:
            if (verified and playlist_identity and playlist_identity.get("identity_kind") == "name"
                    and record.get("source") != real):
                raise FileExistsError("Playlist has no stable identity; preserving another source's entry")
            if os.path.lexists(dest) and record and not verified:
                owned.pop(name, None)
                raise FileExistsError("Managed playlist destination was changed; preserving it")
            outcome = materialize_one(real, dest, mode, symlink_fn=symlink_fn, copy_fn=copy_fn,
                                      replace_owned=verified,
                                      expected_fingerprint=record["fingerprint"] if verified else None)
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
        if outcome in ("linked", "copied", "fellback"):
            owned[name] = {"source": real, "fingerprint": artifact_fingerprint(dest)}
        elif outcome == "unchanged" and verified:
            # Keep the fingerprint verified before the freshness check; re-reading
            # it here would adopt an edit made in between.
            owned[name] = {**record, "source": real}
        elif outcome == "unchanged" and not record and _adoptable(real, dest, mode):
            # a 3.6.1 folder's entry: what this rebuild would have written, so
            # it is recorded and managed from now on (replaced when its track
            # moves, pruned when it leaves the playlist) instead of failing
            owned[name] = {"source": real, "fingerprint": artifact_fingerprint(dest)}

    if prune_stale:
        # Only a complete, unchanged ownership record authorizes deletion.
        # Historical originals, M3Us, foreign files and edited copies survive.
        for name, record in list(owned.items()):
            full = os.path.join(pdir, name)
            if full in keep or full in sources:
                continue
            if entry_is_owned(full, record):
                try:
                    os.unlink(full)
                    summary.removed_stale += 1
                except OSError as error:
                    summary.failed += 1
                    summary.errors.append(f"{name}: {error}")
                    continue
            owned.pop(name, None)

    save_manifest(playlists_root, pdir, manifest)
    return summary


def update_materialized_paths(playlists_root: str, moves: Mapping[str, str]) -> int:
    """Repair unchanged managed links after exact, successful physical moves.

    Copies keep their independent bytes; their source provenance is updated.
    Unowned or user-edited entries and arbitrary M3Us are never touched.
    """
    normalized = {os.path.abspath(old): os.path.abspath(new) for old, new in moves.items()
                  if old and new and os.path.isfile(new)}
    root = os.path.abspath(playlists_root)
    directory = os.path.join(root, MANIFEST_DIR)
    if not normalized or not os.path.isdir(directory) or os.path.islink(directory):
        return 0
    repaired = 0
    with MATERIALIZE_LOCK:
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if not name.endswith(".json") or os.path.islink(path):
                continue
            try:
                with open(path, encoding="utf-8") as stream:
                    header = json.load(stream)
                folder = header.get("folder") if isinstance(header, dict) else None
                if not isinstance(folder, str) or os.path.basename(folder) != folder:
                    continue
                pdir = safe_output_dir(root, os.path.join(root, folder), create=False)
                manifest = load_manifest(root, pdir)
                changed = False
                for entry, record in manifest["entries"].items():
                    new = normalized.get(record.get("source"))
                    dest = os.path.join(pdir, entry)
                    if not new or not entry_is_owned(dest, record):
                        continue
                    if os.path.islink(dest):
                        materialize_one(new, dest, "symlink", replace_owned=True,
                                        expected_fingerprint=record["fingerprint"])
                        record["fingerprint"] = artifact_fingerprint(dest)
                        repaired += 1
                    record["source"] = new
                    changed = True
                if changed:
                    save_manifest(root, pdir, manifest)
            except (OSError, ValueError, UnicodeError):
                continue
    return repaired


__all__ = [
    "MATERIALIZE_MODES",
    "DEFAULT_MODE",
    "normalize_mode",
    "RebuildSummary",
    "playlist_dir_for",
    "materialize_one",
    "rebuild_playlist_folder",
    "update_materialized_paths",
]
