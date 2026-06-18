"""#889 Phase 4/5: apply a re-identify — stage the library file + write the hint.

When the user confirms a release in the Re-identify modal, we:
  1. COPY (never move) the track's library file into the auto-import staging folder,
     so the original is untouched until the re-import succeeds,
  2. fingerprint the staged copy (rename-proof binding), and
  3. write a single-use hint carrying the chosen release's IDs (+ ``replace_track_id``
     when 'replace original' is ticked).

The auto-import worker then picks the staged file up, finds the hint, and re-imports
it against the user-chosen release (Phase 2). The pieces here are split so the
naming + hint construction are pure/unit-tested and the actual copy is injectable.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Callable, Dict, Optional

from core.imports.paths import sanitize_filename
from core.imports.rematch_hints import RematchHint, quick_file_signature


def staged_destination(staging_dir: str, real_path: str, library_track_id: Any) -> str:
    """Where the staged copy lands: a single loose file in the staging ROOT (so the
    worker treats it as a single-track candidate), named to keep the extension and
    be unique + traceable to the track it re-identifies. The filename is cosmetic —
    matching is driven by the hint, not the name."""
    base = os.path.basename(real_path)
    stem, ext = os.path.splitext(base)
    safe_stem = sanitize_filename(stem).strip() or "track"
    name = f"{safe_stem} [reid-{library_track_id}]{ext}"
    return os.path.join(staging_dir, name)


def stage_file_for_reidentify(
    real_path: str,
    staging_dir: str,
    library_track_id: Any,
    *,
    copy_fn: Callable[[str, str], object] = shutil.copy2,
    signature_fn: Callable[[str], Optional[str]] = quick_file_signature,
) -> Dict[str, Any]:
    """Copy the library file into staging and fingerprint the copy. Returns
    ``{staged_path, content_hash}``. Raises ``FileNotFoundError`` if the source is
    gone (caller surfaces a clear error rather than writing a dangling hint)."""
    if not real_path or not os.path.isfile(real_path):
        raise FileNotFoundError(real_path or "(empty path)")
    os.makedirs(staging_dir, exist_ok=True)
    dest = staged_destination(staging_dir, real_path, library_track_id)
    copy_fn(real_path, dest)
    return {"staged_path": dest, "content_hash": signature_fn(dest)}


def build_reidentify_hint(
    library_track_id: Any,
    hint_fields: Dict[str, Any],
    staged_path: str,
    content_hash: Optional[str],
    *,
    replace: bool,
) -> RematchHint:
    """Pure: assemble the RematchHint from the resolved release fields + staging
    info. ``replace_track_id`` is the library row to delete on success, but only
    when 'replace original' was ticked. ``exempt_dedup`` is always True — a
    re-identify is explicit and must bypass dedup-skip."""
    return RematchHint(
        staged_path=staged_path,
        content_hash=content_hash,
        source=hint_fields.get("source") or "",
        isrc=hint_fields.get("isrc"),
        track_id=hint_fields.get("track_id"),
        album_id=hint_fields.get("album_id"),
        artist_id=hint_fields.get("artist_id"),
        track_title=hint_fields.get("track_title"),
        album_name=hint_fields.get("album_name"),
        artist_name=hint_fields.get("artist_name"),
        album_type=hint_fields.get("album_type"),
        track_number=hint_fields.get("track_number"),
        disc_number=hint_fields.get("disc_number"),
        replace_track_id=(int(library_track_id) if replace and str(library_track_id).isdigit() else
                          (library_track_id if replace else None)),
        exempt_dedup=True,
    )


__all__ = [
    "staged_destination",
    "stage_file_for_reidentify",
    "build_reidentify_hint",
]
