"""Delete a whole title -- a show or a movie -- and keep it deleted.

Taking the files away is the easy part. What brings a title straight back is
everything that still wants it: a follow (an airing show in the library is
followed by default), wishlist rows on any profile, a download still running
(a failed one puts itself back on the wishlist), a pending request someone
could approve, and repair findings that would grab it again when approved.
So the delete runs in this order:

  1. stop acquisition, for EVERY profile: mute the follow, cancel the
     title's downloads, clear its wishlist rows, deny pending requests (each
     requester is told), close its pending repair findings, and hide it from
     Discover
  2. the files: the title's own folder goes into the recycle bin as one
     entry (core.video.recycle.discard_folder), so putting it back is one
     restore; a file that shares a folder with something else goes in on
     its own
  3. ask the media server to rescan, so it lets go of the title
  4. forget the library rows

Torrents the client is still seeding are left alone on purpose: the library
copy is a COPY, and pulling a torrent before its seeding goals are met can
cost ratio on a private tracker. The seeding sweep, or the user, lets go of
them. Both confirmation steps say so.

``plan`` is the read-only preview the first confirmation shows; ``run`` does
it, and only when the caller typed the title exactly (the second
confirmation). Every outside effect is injectable, so the tests touch no
media server, download client or profile store.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.delete_title")

KINDS = ("movie", "show")
DENY_RESPONSE = "Removed from the library"
FINDING_ACTION = "Title deleted"


# ── helpers ──────────────────────────────────────────────────────────────────

def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _title_folder(kind: str, path: str) -> str:
    """the folder a title's file belongs to: its own folder, or for an
    episode in a season folder, the show folder above it."""
    from core.video.library_manage import _SEASON_DIR
    d = os.path.dirname(path)
    if kind == "show" and _SEASON_DIR.search(os.path.basename(d) or ""):
        d = os.path.dirname(d)
    return d


def _foreign_media(folder: str, ours: set) -> bool:
    """True when ``folder`` holds a video file that isn't one of the title's,
    i.e. it is NOT the title's own folder and must not go as a whole."""
    from core.video.importer import VIDEO_EXTS
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "ss_recycle"]
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTS and \
                    _norm(os.path.join(base, name)) not in ours:
                return True
    return False


def _folder_size(folder: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                continue
    return total


def _all_profile_ids() -> List[int]:
    """every profile, so a mute covers the household, not only the admin."""
    try:
        from core.media_accounts import profile_names
        names, _admin = profile_names()
        return sorted(names)
    except Exception:   # noqa: BLE001 - fall back to the profiles we can see
        logger.debug("listing profiles for a title delete failed", exc_info=True)
        return []


def _default_cancel(db, dl: Dict[str, Any]) -> None:
    """cancel one in-flight download the way the Downloads page's Cancel does:
    a client job is removed with its unfinished files, a soulseek transfer is
    cancelled, and the row is marked cancelled (never failed -- a failed row
    puts itself back on the wishlist)."""
    if dl.get("source") in ("torrent", "usenet"):
        from core.video.client_grab import remove as remove_client_job
        remove_client_job(dl.get("source"), dl.get("client_ref"), delete_files=True)
    else:
        from core.video.slskd_download import cancel_download
        cancel_download(dl.get("username"), dl.get("filename"))
    db.update_video_download(dl["id"], status="cancelled", error="Cancelled: title deleted",
                             completed_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))


def _default_refresh(kind: str) -> Dict[str, Any]:
    from core.video.sources import refresh_video_server_sections
    return refresh_video_server_sections("movie" if kind == "movie" else "show")


# ── the preview ──────────────────────────────────────────────────────────────

def plan(db, kind: str, item_id, *, resolve: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    """everything one delete would do, read-only. None for an unknown title.

    ``blocked`` carries the reason when it can't run (something would have
    to be deleted for good with recycling on); the dialog shows it instead
    of the delete button."""
    from core.video import organization, recycle
    from core.video.file_ops import _locate
    if kind not in KINDS:
        return None
    rows = db.title_delete_rows(kind, item_id)
    ident = db.title_identity(kind, item_id) if rows else None
    if not ident:
        return None
    tmdb_id = ident.get("tmdb_id")
    settings = organization.load(db)
    recycling = bool(settings.get("recycle_deletes", True))

    files: List[Dict[str, Any]] = []
    for rid in rows:
        files.extend(db.library_title_files(kind, rid))
    located, missing = [], 0
    for f in files:
        real = _locate(db, f, resolve)
        if real:
            located.append((f, real))
        else:
            missing += 1
    ours = {_norm(real) for _f, real in located}

    roots = recycle._library_roots(db)
    groups: Dict[str, List] = {}
    for f, real in located:
        groups.setdefault(_title_folder(kind, real), []).append((f, real))
    folders, loose = [], []
    for folder, members in sorted(groups.items()):
        own = (os.path.isdir(folder) and not os.path.islink(folder)
               and recycle._root_for(folder, roots) is not None
               and _norm(folder) not in {_norm(r) for r in roots}
               and not _foreign_media(folder, ours))
        if own:
            folders.append({"path": folder, "files": len(members), "size_bytes": _folder_size(folder)})
        else:
            loose.extend({"path": real, "size_bytes": f.get("size_bytes") or 0} for f, real in members)

    blocked = None
    if recycling:
        for p in [x["path"] for x in folders] + [x["path"] for x in loose]:
            if not recycle.trash_dir_for(p, settings, db):
                blocked = ("Some of this title's files sit outside your library folders, so there's "
                           "no recycle bin for them. Nothing was deleted.")
                break

    episode_ids = db.title_delete_episode_ids(rows) if kind == "show" else []
    file_ids = [f.get("file_id") for f in files if f.get("file_id") is not None]
    return {
        "kind": kind,
        "item_id": int(item_id),
        "title": ident.get("title") or "",
        "year": ident.get("year"),
        "tmdb_id": tmdb_id,
        "row_ids": rows,
        "episode_ids": episode_ids,
        "file_ids": file_ids,
        "files": len(files),
        "files_missing": missing,
        "folders": folders,
        "loose_files": loose,
        "size_bytes": sum(x["size_bytes"] for x in folders) + sum(x["size_bytes"] for x in loose),
        "recycling": recycling,
        "keep_days": int(settings.get("recycle_keep_days") or 7),
        "followers": db.title_watchlist_followers(kind, tmdb_id),
        "wishlist": db.title_wishlist_count(kind, tmdb_id),
        "downloads": len(db.title_active_downloads(kind, tmdb_id, rows)),
        "requests": len(db.title_pending_requests(kind, tmdb_id)),
        "findings": db.title_pending_findings(kind, rows, episode_ids, file_ids),
        "seeding": db.title_seeding_torrents(kind, tmdb_id, rows),
        "blocked": blocked,
    }


# ── the delete ───────────────────────────────────────────────────────────────

def run(db, kind: str, item_id, *, typed_title: str, resolved_by: int,
        profile_ids: Optional[Iterable[int]] = None,
        notify: Optional[Callable[[int, str, str], None]] = None,
        cancel: Optional[Callable] = None,
        refresh: Optional[Callable[[str], Dict[str, Any]]] = None,
        resolve: Optional[Callable] = None) -> Dict[str, Any]:
    """delete the title in the order the module docstring gives. Refuses
    unless ``typed_title`` is the title exactly (surrounding spaces aside)."""
    from core.video import organization, recycle
    p = plan(db, kind, item_id, resolve=resolve)
    if not p:
        return {"success": False, "error": "That title isn't in the library any more."}
    if str(typed_title or "").strip() != p["title"].strip() or not p["title"].strip():
        return {"success": False, "error": "The title you typed doesn't match. Nothing was deleted."}
    if p["blocked"]:
        return {"success": False, "error": p["blocked"]}

    tmdb_id, rows = p["tmdb_id"], p["row_ids"]
    cancel = cancel or _default_cancel
    refresh = refresh or _default_refresh
    done: Dict[str, Any] = {"muted": 0, "downloads_cancelled": 0, "wishlist_removed": 0,
                            "requests_denied": 0, "findings_resolved": 0, "ignored": False,
                            "folders_recycled": 0, "files_recycled": 0, "recycled": False,
                            "server_refresh": "skipped", "rows_dropped": 0,
                            "seeding_left": p["seeding"]}

    # 1. stop acquisition, for every profile
    if kind == "show" and tmdb_id:
        pids = set(_all_profile_ids() if profile_ids is None else profile_ids)
        pids |= set(p["followers"])
        pids.add(int(resolved_by))
        for pid in sorted(pids):
            if db.remove_from_watchlist("show", int(tmdb_id), profile_id=int(pid)):
                done["muted"] += 1
    for dl in db.title_active_downloads(kind, tmdb_id, rows):
        try:
            cancel(db, dl)
            done["downloads_cancelled"] += 1
        except Exception:   # noqa: BLE001 - one stuck client must not stop the delete
            logger.exception("title delete: cancelling download %s failed", dl.get("id"))
    if tmdb_id:
        done["wishlist_removed"] = db.remove_from_wishlist(kind, tmdb_id=int(tmdb_id), profile_id=None)
        denied = db.deny_title_requests(kind, tmdb_id, resolved_by=resolved_by,
                                        admin_response=DENY_RESPONSE)
        done["requests_denied"] = len(denied)
        for r in denied:
            if notify:
                try:
                    notify(r["profile_id"], f"{r.get('title') or p['title']} was declined: "
                                            f"{DENY_RESPONSE}", "warning")
                except Exception:   # noqa: BLE001 - a notice never stops the delete
                    logger.debug("title delete: request notice failed", exc_info=True)
        done["ignored"] = bool(db.add_ignored(kind, tmdb_id, title=p["title"], year=p["year"]))
    done["findings_resolved"] = db.resolve_title_findings(
        kind, rows, p["episode_ids"], p["file_ids"], action=FINDING_ACTION)

    # 2. the files
    settings = organization.load(db)
    reason = f"deleted: {p['title']}"
    for folder in p["folders"]:
        res = recycle.discard_folder(folder["path"], settings, db, reason=reason)
        if not res.get("ok"):
            return _stopped(p, done, folder["path"], res.get("error"))
        done["folders_recycled"] += 1
        done["recycled"] = done["recycled"] or bool(res.get("recycled"))
    for f in p["loose_files"]:
        res = recycle.discard(f["path"], settings, db, reason=reason)
        if not res.get("ok"):
            return _stopped(p, done, f["path"], "couldn't move the file")
        done["files_recycled"] += 1
        done["recycled"] = done["recycled"] or bool(res.get("recycled"))

    # a download that failed while we were cancelling it can put itself back
    # on the wishlist in that window -- sweep once more now the files are gone
    if tmdb_id:
        done["wishlist_removed"] += db.remove_from_wishlist(kind, tmdb_id=int(tmdb_id), profile_id=None)

    # 3. the media server lets go of it
    try:
        res = refresh(kind) or {}
        done["server_refresh"] = "ok" if res.get("ok") else "failed"
    except Exception:   # noqa: BLE001 - the server's own scan catches up anyway
        logger.exception("title delete: server refresh failed")
        done["server_refresh"] = "failed"

    # 4. forget it
    done["rows_dropped"] = db.drop_title_rows(kind, rows)
    logger.info("deleted %s %s (%s): %s", kind, p["item_id"], p["title"], done)
    return {"success": True, "title": p["title"], "kind": kind, **done}


def _stopped(p: Dict[str, Any], done: Dict[str, Any], path: str, why) -> Dict[str, Any]:
    """the files step failed part-way. acquisition is already stopped (that
    part is safe to keep), the rows stay so the delete can be run again."""
    logger.warning("title delete stopped at %s: %s", path, why)
    return {"success": False, "partial": True, "title": p["title"], **done,
            "error": f"Stopped at {os.path.basename(path) or path}: {why or 'could not be moved'}. "
                     "Nothing will grab it again; the rest is still in place, so you can try again."}
