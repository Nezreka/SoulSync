"""File-level operations on owned video: where a file should be named, renaming
it there, and deleting it to the recycle bin.

One home for this so the Naming Conformance job and the library tab on a
title's page can't drift: both render the same expected path and move files
the same way.

The stored path is the media SERVER's view of the file. Nothing here acts on
a file SoulSync can't see locally: a path that doesn't resolve is reported as
unreachable and left alone, never guessed at.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Callable, Dict, List, Optional

from core.video.library_paths import file_quality
from utils.logging_config import get_logger

logger = get_logger("video.file_ops")

_SIDECAR_SUFFIXES = (".nfo", "-thumb.jpg", "-thumb.jpeg", "-thumb.png", "-thumb.webp",
                     ".srt", ".ass", ".sub", ".idx", ".jpg")


def naming_fields(row: dict) -> dict:
    """the naming template's fields for a library file row (the shape of
    repair_library_files / library_title_files)."""
    return {"title": row.get("title"), "series": row.get("series"),
            "year": row.get("year"), "season": row.get("season"),
            "episode": row.get("episode"), "episode_title": row.get("episode_title"),
            "quality": row.get("quality"), "resolution": row.get("resolution"),
            "codec": row.get("video_codec"), "tmdbid": row.get("tmdb_id"),
            "imdbid": row.get("imdb_id"), "tvdbid": row.get("tvdb_id")}


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def expected_path(row: dict, real: str, roots: List[str], settings: dict) -> Optional[str]:
    """where the current naming templates put this file, on the library root
    it already sits under, or None when it sits under no configured root."""
    from core.video import organization
    from core.video.library_roots import containing_root
    root = containing_root(real, roots or [])
    if not root:
        return None
    fields = naming_fields(row)
    fields["quality"] = file_quality(fields.get("quality"), real)
    ext = os.path.splitext(real)[1]
    return organization.render_path(row.get("scope"), root, fields, settings, ext)["path"]


def move_sidecars(old_path: str, new_path: str) -> int:
    """bring same-stem sidecars (nfo/thumb/subs incl. language-tagged .srt)
    along with the video. best-effort; returns how many moved."""
    old_stem = os.path.splitext(old_path)[0]
    new_stem = os.path.splitext(new_path)[0]
    moved = 0
    try:
        names = os.listdir(os.path.dirname(old_path) or ".")
    except OSError:
        return 0
    base = os.path.basename(old_stem)
    for n in names:
        full = os.path.join(os.path.dirname(old_path), n)
        if full == old_path or not n.startswith(base):
            continue
        rest = n[len(base):]
        # sidecars share the stem plus "." or "-thumb" and a known suffix --
        # language subs like ".en.srt" pass via the .srt suffix; anything else
        # sharing the stem (an unrelated file) is left alone
        if not (rest.startswith(".") or rest.startswith("-thumb")):
            continue
        if not any(rest.endswith(sfx) for sfx in _SIDECAR_SUFFIXES):
            continue
        try:
            shutil.move(full, new_stem + rest)
            moved += 1
        except OSError:   # noqa: PERF203 - per-sidecar resilience
            continue
    return moved


def update_stored_path(db, file_id, old_local: str, new_local: str) -> None:
    """mirror a rename into the stored (server-view) path, so lookups by that
    path find the file before the server rescans."""
    try:
        from core.video.mass_rename import _inverse_reroot
        stored = db.media_file_stored_path(int(file_id))
        if stored is None:
            return
        db.set_media_file_stored_path(int(file_id), _inverse_reroot(new_local, old_local, stored))
    except Exception:   # noqa: BLE001 - the next server scan re-adopts the truth anyway
        logger.debug("stored-path update failed for file %s", file_id, exc_info=True)


def move_into_place(db, file_id, cur: str, want: str) -> Dict[str, Any]:
    """rename ``cur`` to ``want`` with its sidecars, mirror the stored path and
    tidy the emptied folder. never overwrites."""
    if not os.path.isfile(cur):
        return {"success": False, "error": "the file moved since the scan — re-run it"}
    if os.path.exists(want):
        return {"success": False, "error": "destination already exists — nothing overwritten"}
    try:
        os.makedirs(os.path.dirname(want) or ".", exist_ok=True)
        shutil.move(cur, want)
    except OSError as e:
        return {"success": False, "error": f"rename failed: {e}"}
    moved = move_sidecars(cur, want)
    # the library's stored path must follow, or every lookup by that path
    # (upgrades, a new episode finding its show's folder) misses the file
    # until the media server rescans -- the same mirror Mass Rename keeps
    update_stored_path(db, file_id, cur, want)
    # tidy an emptied folder (only ever the file's own former dir)
    try:
        old_dir = os.path.dirname(cur)
        if old_dir and not os.listdir(old_dir):
            os.rmdir(old_dir)
    except OSError:
        pass
    extra = f" (+{moved} sidecar{'s' if moved != 1 else ''})" if moved else ""
    return {"success": True, "action": "renamed", "sidecars": moved,
            "message": f"Renamed to {os.path.basename(want)}{extra}"}


def _locate(db, row: dict, resolve: Optional[Callable] = None) -> Optional[str]:
    from core.video.path_resolver import resolve_video_file_path, video_base_dirs
    resolve = resolve or resolve_video_file_path
    return resolve(row.get("relative_path"), video_base_dirs(db),
                   size_bytes=row.get("size_bytes"))


def rename_plan(db, file_id, *, resolve: Optional[Callable] = None) -> Dict[str, Any]:
    """{current, expected, conforms} for one stored file, or {error}. the
    preview the library tab shows before anything moves."""
    from core.video import organization
    from core.video.library_roots import library_roots
    row = db.library_file_row(file_id)
    if not row:
        return {"error": "That file isn't in the library any more."}
    real = _locate(db, row, resolve)
    if not real:
        return {"error": "SoulSync can't reach this file from here — only your media server can see it."}
    roots = library_roots(db, row.get("scope"))
    if not roots:
        return {"error": "No library folder is configured for this type."}
    want = expected_path(row, real, roots, organization.load(db))
    if not want:
        return {"error": "This file sits outside your library folders, so it has no template path."}
    return {"current": real, "expected": want, "conforms": same_path(real, want)}


def rename_to_template(db, file_id, *, resolve: Optional[Callable] = None) -> Dict[str, Any]:
    """rename one stored file to where the current templates put it."""
    plan = rename_plan(db, file_id, resolve=resolve)
    if plan.get("error"):
        return {"success": False, "error": plan["error"]}
    if plan["conforms"]:
        return {"success": True, "action": "unchanged", "message": "Already named by your template"}
    res = move_into_place(db, file_id, plan["current"], plan["expected"])
    if res.get("success"):
        res["path"] = plan["expected"]
    return res


def delete_to_recycle(db, file_id, *, resolve: Optional[Callable] = None,
                      discard: Optional[Callable] = None) -> Dict[str, Any]:
    """delete one stored file through the recycle bin, then forget it.

    a user's delete button never falls back to a permanent delete: a file that
    sits under no library folder has nowhere to be recycled to, so it's
    refused and left in place. same for a file SoulSync can't reach."""
    from core.video import organization, recycle
    row = db.library_file_row(file_id)
    if not row:
        return {"success": False, "error": "That file isn't in the library any more."}
    real = _locate(db, row, resolve)
    if not real:
        return {"success": False,
                "error": "SoulSync can't reach this file from here — delete it on your media server."}
    settings = organization.load(db)
    if settings.get("recycle_deletes", True) and not recycle.trash_dir_for(real, settings, db):
        return {"success": False,
                "error": "This file sits outside your library folders, so there's no recycle bin "
                         "for it. Nothing was deleted."}
    discard = discard or (lambda p: recycle.discard(p, settings, db, reason="deleted from the library tab"))
    res = discard(real)
    if not res.get("ok"):
        return {"success": False, "error": "Couldn't move the file — it's still where it was."}
    dropped = db.library_drop_file(file_id) or {}
    return {"success": True, "action": "deleted", "recycled": bool(res.get("recycled")),
            "trash_path": res.get("trash_path"), "remaining": dropped.get("remaining"),
            "kind": dropped.get("kind"), "item_id": dropped.get("item_id")}


__all__ = ["naming_fields", "same_path", "expected_path", "move_sidecars",
           "update_stored_path", "move_into_place", "rename_plan",
           "rename_to_template", "delete_to_recycle"]
