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
    """the name of the cover file sitting in the album's folders, if any:
    the same check the scan makes (#2202)"""
    from core.metadata.art_apply import folder_cover_file
    for folder in folders:
        found = folder_cover_file(folder)
        if found:
            return os.path.basename(found)
    return None


def library_picture(database, config_manager, album_id: int, image_url) -> bool:
    """the library's saved cover is a picture SoulSync can show, not only a
    url. a placeholder or a link that no longer loads is no cover (#2202: a
    finding said "the library has a cover to copy" over an empty tile, and
    Apply Art had nothing to copy). an error says nothing either way, so the
    saved url is trusted then"""
    url = str(image_url or "").strip()
    if not url:
        return False
    try:
        from core.metadata.artwork import is_placeholder_image_url
        if is_placeholder_image_url(url):
            return False
        from core.library2 import artwork
        cached = artwork.artwork_file(database, "album", album_id)
        if cached.exists() and artwork.is_cached_jpeg(cached):
            return True
        import sqlite3
        with closing(database._get_connection()) as conn:
            conn.row_factory = sqlite3.Row
            path = artwork.build_artwork(database, conn, config_manager, "album", album_id)
        return bool(path) and os.path.isfile(path)
    except Exception as e:  # noqa: BLE001 - unknown is not missing
        logger.debug("library cover check for album %s failed: %s", album_id, e)
        return True


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


#: the covers a finding can apply, best first: the library's own, the
#: folder's, the one inside the files, then one a source found (#2202)
PICKS = ("library", "folder", "files", "found")


def choices(state: Dict[str, Any]) -> list:
    """the tiles that hold a picture, in PICKS order"""
    have = {
        "library": state["library"]["has"],
        "folder": bool(state["folder"].get("file")),
        "files": bool(state["files"].get("with")),
        "found": bool(state["found"].get("url")),
    }
    return [pick for pick in PICKS if have[pick]]


def default_pick(state: Dict[str, Any]) -> Optional[str]:
    found = choices(state)
    return found[0] if found else None


_UNSET = object()


def apply_plan(state: Dict[str, Any], pick=_UNSET) -> list:
    """What Apply Art does in each place with ``pick`` as the cover, the
    same choices the fix makes: the pick fills each place without a cover,
    a cover that is there is kept. picking a found cover over the library's
    own replaces it, as Change cover would. no pick: the default one"""
    library, files, folder = state["library"], state["files"], state["folder"]
    settings = state["settings"]
    if pick is _UNSET:
        pick = default_pick(state)
    if pick is not None and pick not in choices(state):
        pick = None
    # the library's own cover is the one the user picked when it is locked
    uses = "chosen" if pick == "library" and library["locked"] else pick

    plan = []
    if pick == "found":
        plan.append({"place": "library", "action": "write", "why": "found",
                     **({"replace": True} if library["has"] else {}),
                     **({"broken": True} if library.get("broken") else {})})
    elif library["has"]:
        plan.append({"place": "library", "action": "keep",
                     "why": "chosen" if library["locked"] else "has"})
    elif library.get("broken"):
        plan.append({"place": "library", "action": "skip", "why": "broken"})
    elif pick:
        plan.append({"place": "library", "action": "skip", "why": "local"})
    else:
        plan.append({"place": "library", "action": "skip", "why": "nothing_found"})

    if not settings["embed"]:
        plan.append({"place": "files", "action": "skip", "why": "setting_off"})
    elif not (files["with"] or files["without"]):
        plan.append({"place": "files", "action": "skip", "why": "unknown"})
    elif not files["without"]:
        plan.append({"place": "files", "action": "keep", "why": "has"})
    elif uses:
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
    saved = bool(str(row[0] or "").strip())
    has = library_picture(database, config_manager, album_id, row[0])
    image = f"/api/repair/findings/{int(finding_id)}/cover-image?place="
    folder_file = _folder_cover(folders)
    state = {
        "album_id": album_id,
        "library": {"has": has, "locked": bool(row[1]) and has,
                    "broken": saved and not has,
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
    state["choices"] = choices(state)
    state["pick"] = default_pick(state)
    # what Apply Art does for each cover the user can pick, so a pick on the
    # finding shows its own plan without asking the server again
    state["plans"] = {pick: apply_plan(state, pick) for pick in state["choices"]}
    state["plan"] = apply_plan(state, state["pick"])
    return state


def pick_bytes(database, config_manager, album_id: int, pick: str,
               found_url: Optional[str] = None) -> Optional[bytes]:
    """the picture of one tile, for Apply Art to write (#2202). the library's
    is what the library shows, only when it has a saved cover of its own"""
    from core.library2 import artwork
    import sqlite3
    if pick == "found":
        if not found_url:
            return None
        try:
            return artwork._download_remote_artwork(found_url)
        except Exception as e:  # noqa: BLE001 - a dead link is no cover
            logger.debug("found cover for album %s not read: %s", album_id, e)
            return None
    if pick not in PLACES:
        return None
    with closing(database._get_connection()) as conn:
        conn.row_factory = sqlite3.Row
        if pick == "library":
            row = conn.execute("SELECT image_url FROM lib2_albums WHERE id = ?",
                               (album_id,)).fetchone()
            if not row or not str(row[0] or "").strip():
                return None
    if pick == "library":
        if not library_picture(database, config_manager, album_id, row[0]):
            return None
    data = cover_image(database, config_manager, {"album_id": album_id}, pick)
    return data or None


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
