"""Relocate an AcoustID-mismatched file into Staging for clean re-import (#704).

The existing 'retag' fix corrects a mismatched file's tags + DB record but leaves
the file in the WRONG artist/album folder on disk — so the library shows the right
title while the file sits under the previous track's artist/album. AcoustID only
yields a title + artist (not a reliable album), so an *in-place* move has no
trustworthy target.

Instead: retag the file, move it into the staging folder, and drop the stale
``tracks`` row. The auto-import worker (which watches staging) then re-identifies
the file with full metadata and files it in the correct artist/album/track path —
reusing the battle-tested import pipeline rather than guessing a destination here.

Side effects are injected so the orchestration is a pure, unit-testable seam.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional


# Sidecar a relocate leaves next to the staged file so the auto-import worker
# can route the file back into the owning profile's own library (#1504).
# Appended to the FULL staged filename (never the bare stem) so it cannot
# collide with the audio file or its " (1)"-suffixed variants, and the .json
# extension keeps it out of every audio-candidate scan — it is never treated
# as importable media.
PROFILE_SIDECAR_SUFFIX = ".soulsync-profile.json"


def profile_sidecar_path(staged_path: str) -> str:
    """Path of the own-library routing sidecar for a staged file."""
    return f"{staged_path}{PROFILE_SIDECAR_SUFFIX}"


def profile_sidecar_payload(profile_id: int, source_path: str) -> Dict[str, Any]:
    """Sidecar payload for a file about to be staged.

    Carries a size+mtime fingerprint of the source file so the auto-import
    worker can tell a stale sidecar (staged file deleted by hand, a different
    file later landing at the same name) from a live one — a stale sidecar
    would silently route an unrelated file into the wrong profile's library.
    Fingerprint keys are best-effort: a file that can't be stat'ed still gets
    a sidecar, and the reader treats a missing fingerprint as "trust"."""
    payload: Dict[str, Any] = {'profile_id': int(profile_id)}
    try:
        st = os.stat(source_path)
        payload['size'] = st.st_size
        payload['mtime'] = st.st_mtime
    except OSError:
        pass
    return payload


def write_profile_sidecar(staged_path: str, profile_id: int, source_path: str,
                          write_file: Callable[[str, str], Any] = None) -> bool:
    """Write the own-library routing sidecar next to a staged file.

    ``source_path`` is fingerprinted into the payload (see
    ``profile_sidecar_payload``). Best-effort — returns False instead of
    raising; a missing sidecar just means the file re-imports into the shared
    folder (today's behavior)."""
    import json
    try:
        content = json.dumps(profile_sidecar_payload(profile_id, source_path))
        if write_file is not None:
            write_file(profile_sidecar_path(staged_path), content)
        else:
            with open(profile_sidecar_path(staged_path), 'w', encoding='utf-8') as f:
                f.write(content)
        return True
    except Exception:  # noqa: BLE001 - fail-open: shared-folder re-import
        return False


def staging_destination(staging_dir: str, filename: str,
                        exists: Callable[[str], bool]) -> str:
    """A non-colliding path for ``filename`` inside ``staging_dir``.

    If the name is already taken, suffix it ``' (1)'``, ``' (2)'``, … before the
    extension — never overwrite an unrelated file already waiting in staging.
    """
    base, ext = os.path.splitext(filename)
    dest = os.path.join(staging_dir, filename)
    n = 1
    while exists(dest):
        dest = os.path.join(staging_dir, f"{base} ({n}){ext}")
        n += 1
    return dest


def relocate_mismatch_to_staging(
    resolved_path: str,
    staging_dir: str,
    tag_updates: Optional[Dict[str, Any]],
    *,
    write_tags: Callable[[str, Dict[str, Any]], Any],
    move_file: Callable[[str, str], Any],
    drop_db_row: Callable[[], Any],
    exists: Callable[[str], bool],
    profile_id: Optional[int] = None,
    write_file: Callable[[str, str], Any] = None,
) -> str:
    """Retag (best-effort) → move into staging → drop the stale DB row.

    Returns the staging destination path. Order matters: the DB row is dropped
    only AFTER a successful move, so a failed move (which raises) leaves the
    library entry intact rather than orphaning it.

    ``profile_id``: the own-library profile that owns the file (#1504). Its
    routing sidecar is written BEFORE the move — a poll landing between the
    write and the move finds no staged file yet, so it can't import without
    the profile; writing after the move would leave the reverse window."""
    if tag_updates:
        try:
            write_tags(resolved_path, tag_updates)
        except Exception:  # noqa: S110 — tags are best-effort; re-import re-derives them
            # The relocation itself is the point, so don't abort over a tag write.
            pass

    dest = staging_destination(staging_dir, os.path.basename(resolved_path), exists)
    if profile_id:
        write_profile_sidecar(dest, profile_id, resolved_path, write_file=write_file)
    move_file(resolved_path, dest)   # may raise → row NOT dropped (intentional)
    drop_db_row()
    return dest


__all__ = ["PROFILE_SIDECAR_SUFFIX", "profile_sidecar_path",
           "profile_sidecar_payload", "write_profile_sidecar",
           "staging_destination", "relocate_mismatch_to_staging"]
