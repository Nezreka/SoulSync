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
) -> str:
    """Retag (best-effort) → move into staging → drop the stale DB row.

    Returns the staging destination path. Order matters: the DB row is dropped
    only AFTER a successful move, so a failed move (which raises) leaves the
    library entry intact rather than orphaning it.
    """
    if tag_updates:
        try:
            write_tags(resolved_path, tag_updates)
        except Exception:  # noqa: S110 — tags are best-effort; re-import re-derives them
            # The relocation itself is the point, so don't abort over a tag write.
            pass

    dest = staging_destination(staging_dir, os.path.basename(resolved_path), exists)
    move_file(resolved_path, dest)   # may raise → row NOT dropped (intentional)
    drop_db_row()
    return dest


__all__ = ["staging_destination", "relocate_mismatch_to_staging"]
