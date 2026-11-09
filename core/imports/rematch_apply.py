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
import re
import shutil
from typing import Any, Callable, Dict, Optional

from core.imports.paths import sanitize_filename
from core.imports.rematch_hints import RematchHint, quick_file_signature
from utils.logging_config import get_logger

logger = get_logger("imports.rematch_apply")


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


# the " [reid-<track id>]" tail staged_destination puts on every copy
_REID_MARKER = re.compile(r" \[reid-[^\]]+\]$")


def is_reidentify_copy(path: str) -> bool:
    """True for a file staged by a re-identify. the scanner keeps each one as
    its own candidate: copies from one bad album all share its album tag, so
    grouping by tag bundled them into one candidate and the hint (one file
    only) never got read (#1567)."""
    stem = os.path.splitext(os.path.basename(path or ""))[0]
    return bool(_REID_MARKER.search(stem))


def stage_file_for_reidentify(
    real_path: str,
    staging_dir: str,
    library_track_id: Any,
    *,
    copy_fn: Callable[[str, str], object] = shutil.copy2,
    signature_fn: Callable[[str], Optional[str]] = quick_file_signature,
    hidden: bool = False,
) -> Dict[str, Any]:
    """Copy the library file into staging and fingerprint the copy. Returns
    ``{staged_path, content_hash}``. Raises ``FileNotFoundError`` if the source is
    gone (caller surfaces a clear error rather than writing a dangling hint).

    ``hidden`` copies to ``<staged_path>.part`` instead (auto-import only picks
    up audio extensions) and adds ``temp_path``: the caller commits the hint
    first, then renames the copy into place, so auto-import never sees the
    file without its hint."""
    if not real_path or not os.path.isfile(real_path):
        raise FileNotFoundError(real_path or "(empty path)")
    os.makedirs(staging_dir, exist_ok=True)
    dest = staged_destination(staging_dir, real_path, library_track_id)
    if hidden:
        temp = dest + ".part"
        copy_fn(real_path, temp)
        return {"staged_path": dest, "temp_path": temp, "content_hash": signature_fn(temp)}
    copy_fn(real_path, dest)
    return {"staged_path": dest, "content_hash": signature_fn(dest)}


def build_reidentify_hint(
    library_track_id: Any,
    hint_fields: Dict[str, Any],
    staged_path: str,
    content_hash: Optional[str],
    *,
    replace: bool,
    replace_file_id: Any = None,
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
        # the staged file, so the replace retires that copy and no other
        # library's (review 5 W2-WD-3)
        replace_file_id=(int(replace_file_id) if replace and replace_file_id else None),
    )


def _remove_quietly(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove orphaned staged copy %s: %s", path, exc)


def stage_with_committed_hint(
    get_connection: Callable[[], Any],
    real_path: str,
    staging_dir: str,
    library_track_id: Any,
    make_hint: Callable[[str, Optional[str]], RematchHint],
    *,
    stage_fn: Callable[..., Dict[str, Any]] = None,
) -> tuple:
    """Stage one track for a re-identify the way album reassign does (review 5
    R5-D-3): copy to a hidden ``.part`` name with no transaction open, write
    and COMMIT the hint, then rename the copy into place. Auto-import never
    sees the file without its hint, and the copy never holds a write lock.

    A hint that cannot be written takes the copy back out; a copy that cannot
    be renamed into place takes its hint back out. Returns
    ``(hint_id, hint, staged)``; raises on either failure."""
    from core.imports.rematch_hints import create_hint

    staged = (stage_fn or stage_file_for_reidentify)(
        real_path, staging_dir, library_track_id, hidden=True)
    temp = staged.get("temp_path")
    hint = make_hint(staged["staged_path"], staged.get("content_hash"))
    conn = get_connection()
    try:
        try:
            hint_id = create_hint(conn.cursor(), hint)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception as rollback_exc:            # noqa: BLE001
                logger.debug("re-identify hint rollback failed: %s", rollback_exc)
            _remove_quietly(temp or staged["staged_path"])
            raise
        if temp:
            try:
                os.replace(temp, staged["staged_path"])
            except OSError:
                # a hint with no file never gets consumed: take it back out
                try:
                    conn.execute("DELETE FROM rematch_hints WHERE id = ?", (hint_id,))
                    conn.commit()
                except Exception as hint_exc:            # noqa: BLE001
                    logger.warning("Could not remove hint %s for unstaged file: %s",
                                   hint_id, hint_exc)
                _remove_quietly(temp)
                raise
    finally:
        conn.close()
    return hint_id, hint, staged


__all__ = [
    "staged_destination",
    "is_reidentify_copy",
    "stage_file_for_reidentify",
    "build_reidentify_hint",
    "stage_with_committed_hint",
]
