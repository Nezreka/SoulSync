"""What a Cover Art finding is about, read live when it is opened (#2169).

The finding stores what the scan saw. This says what is there now, in each
of the places a cover lives, and what Apply Art would do in each one with
the user's settings, so the finding can show it instead of three yes/no rows.
"""

from __future__ import annotations

import os
from contextlib import closing
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("repair_job.cover_art_state")

PLACES = ("library", "files", "folder")


def _album_id(details: Dict[str, Any]) -> Optional[int]:
    raw = str(details.get("album_id") or "")
    if raw.startswith("lib2:"):
        raw = raw[5:]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _setting(config, key: str) -> bool:
    try:
        return config.get("metadata_enhancement." + key, True) is not False
    except Exception:  # noqa: BLE001 - an unreadable setting keeps the default
        return True


def _folder_cover(folders) -> Optional[str]:
    """the name of the cover file sitting in the album's folders, if any"""
    from core.library2.artwork import _ALBUM_FOLDER_STEMS, _FOLDER_IMAGE_EXTS
    for folder in folders:
        try:
            names = {name.lower(): name for name in os.listdir(folder)}
        except OSError:
            continue
        for stem in _ALBUM_FOLDER_STEMS:
            for ext in _FOLDER_IMAGE_EXTS:
                found = names.get(f"{stem}.{ext}")
                if found:
                    return found
    return None


def _file_counts(conn, album_id: int, owner) -> Dict[str, int]:
    """files of the album with and without a cover inside, from the tag cache
    the scan keeps. one indexed query for one album"""
    from core.library2.status import _coerce_tags
    rows = conn.execute(
        """SELECT f.tags_json, f.owner_profile_id FROM lib2_tracks t
           JOIN lib2_track_files f ON f.track_id = t.id
           WHERE t.album_id = ? AND COALESCE(f.file_state, 'active') = 'active'""",
        (album_id,)).fetchall()
    counts = {"with": 0, "without": 0, "unknown": 0}
    for row in rows:
        if owner is not None and int(row[1] or 1) != int(owner):
            continue
        tags = _coerce_tags(row[0])
        if "has_cover_art" not in tags:
            counts["unknown"] += 1
        elif tags.get("has_cover_art"):
            counts["with"] += 1
        else:
            counts["without"] += 1
    return counts


def apply_plan(state: Dict[str, Any]) -> list:
    """What Apply Art does in each place, the same choices the fix makes:
    a saved or picked library cover goes to the files and the folder, else
    the found one; a cover that is there is never replaced."""
    library, files, folder, found = (state["library"], state["files"],
                                     state["folder"], state["found"])
    settings = state["settings"]
    if library["locked"]:
        uses = "chosen"
    elif library["has"]:
        uses = "library"
    elif found.get("url"):
        uses = "found"
    else:
        uses = None

    plan = []
    if library["locked"]:
        plan.append({"place": "library", "action": "keep", "why": "chosen"})
    elif library["has"]:
        plan.append({"place": "library", "action": "keep", "why": "has"})
    elif uses == "found":
        plan.append({"place": "library", "action": "write", "why": "found"})
    else:
        plan.append({"place": "library", "action": "skip", "why": "nothing_found"})

    will_embed = False
    if not settings["embed"]:
        plan.append({"place": "files", "action": "skip", "why": "setting_off"})
    elif not (files["with"] or files["without"]):
        plan.append({"place": "files", "action": "skip", "why": "unknown"})
    elif not files["without"]:
        plan.append({"place": "files", "action": "keep", "why": "has"})
    elif uses:
        will_embed = True
        plan.append({"place": "files", "action": "write", "why": uses,
                     "count": files["without"]})
    else:
        plan.append({"place": "files", "action": "skip", "why": "nothing_found"})

    if not settings["sidecar"]:
        plan.append({"place": "folder", "action": "skip", "why": "setting_off"})
    elif folder["file"]:
        plan.append({"place": "folder", "action": "keep", "why": "has", "file": folder["file"]})
    elif not folder["exists"]:
        plan.append({"place": "folder", "action": "skip", "why": "unreachable"})
    elif files["with"] or will_embed:
        plan.append({"place": "folder", "action": "write", "why": "files"})
    elif uses:
        plan.append({"place": "folder", "action": "write", "why": uses})
    else:
        plan.append({"place": "folder", "action": "skip", "why": "nothing_found"})
    return plan


def cover_state(database, config_manager, finding_id: int, details: Dict[str, Any]) -> Optional[dict]:
    """the album's cover in the library, the files and the folder now, the
    found one, and what Apply Art would do. None for a finding with no album"""
    album_id = _album_id(details)
    if album_id is None:
        return None
    from core.library2.artwork import _album_folders
    owner = details.get("library_owner_id")
    with closing(database._get_connection()) as conn:
        import sqlite3
        conn.row_factory = sqlite3.Row
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lib2_albums)")}
        row = conn.execute(
            "SELECT image_url, %s FROM lib2_albums WHERE id = ?"
            % ("COALESCE(art_locked, 0)" if "art_locked" in cols else "0"),
            (album_id,)).fetchone()
        if row is None:
            return None
        files = _file_counts(conn, album_id, owner)
        folders = _album_folders(conn, config_manager, album_id)
    has = bool(str(row[0] or "").strip())
    image = f"/api/repair/findings/{int(finding_id)}/cover-image?place="
    folder_file = _folder_cover(folders)
    state = {
        "album_id": album_id,
        "library": {"has": has, "locked": bool(row[1]) and has,
                    "url": image + "library" if has else None},
        "files": {**files, "url": image + "files" if files["with"] else None},
        "folder": {"file": folder_file, "exists": bool(folders),
                   "url": image + "folder" if folder_file else None},
        "found": {"url": details.get("found_artwork_url") or None,
                  "source": (details.get("artwork_source")
                             if details.get("found_artwork_url") else None)},
        "settings": {"embed": _setting(config_manager, "embed_album_art"),
                     "sidecar": _setting(config_manager, "cover_art_download")},
    }
    state["plan"] = apply_plan(state)
    return state


def cover_image(database, config_manager, details: Dict[str, Any], place: str) -> Optional[bytes]:
    """the picture in one place, for the finding's tiles"""
    album_id = _album_id(details)
    if album_id is None or place not in PLACES:
        return None
    from core.library2 import artwork
    with closing(database._get_connection()) as conn:
        import sqlite3
        conn.row_factory = sqlite3.Row
        if place == "files":
            return artwork._embedded_art_for_album(conn, config_manager, album_id)
        if place == "folder":
            return artwork._folder_art_for_album(conn, config_manager, album_id)
        row = conn.execute("SELECT image_url FROM lib2_albums WHERE id = ?", (album_id,)).fetchone()
        if not row or not str(row[0] or "").strip():
            return None
        # what the library shows, from its own artwork cache
        path = artwork.build_artwork(database, conn, config_manager, "album", album_id)
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        return handle.read()
