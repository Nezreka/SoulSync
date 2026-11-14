"""Acting on what the "Your library" tab reports.

The tab could say a movie has two copies, a show is split across five
folders and a file is filed under the wrong episode, and then offered nothing
to do about any of it. These are the fixes:

* extra copies: keep the best copy of each movie or episode, recycle the rest
* bulk: rename or delete a chosen set of files in one go
* reassign: hand a file to the episode it really is
* consolidate: bring a show's files into one of its folders

Every one of them goes through ``file_ops``: nothing is overwritten, a delete
is the recycle bin, and a file SoulSync can't reach is left alone and named.
"""

from __future__ import annotations

import os
import shutil
import threading
from typing import Any, Callable, Dict, List, Optional

from core.video import file_ops
from core.video.library_manage import _Disk, _best, _res_label, show_folder_of
from utils.logging_config import get_logger

logger = get_logger("video.library_actions")


def _label(row: dict) -> str:
    if row.get("season") is None:
        return row.get("title") or ""
    return "S%02dE%02d" % (int(row.get("season") or 0), int(row.get("episode") or 0))


def _name(row: dict) -> str:
    return os.path.basename(str(row.get("relative_path") or "").replace("\\", "/"))


# ── extra copies ─────────────────────────────────────────────────────────────

def extra_copies_plan(db, kind: str, item_id: int, *, disk: Optional[_Disk] = None) -> Dict[str, Any]:
    """what "keep the best copy" would do: for each movie or episode with more
    than one file, the one that stays and the ones that go. the best is the
    highest resolution, then the biggest: the same one the tab marks."""
    rows = db.library_title_files(kind, item_id)
    disk = disk or _Disk(db)
    by_item: Dict[Any, List[dict]] = {}
    for r in rows:
        by_item.setdefault(r["item_id"], []).append(r)
    groups, files, size, blocked = [], 0, 0, 0
    for group in by_item.values():
        if len(group) < 2:
            continue
        keep = _best(group)
        remove = []
        for r in group:
            if r is keep:
                continue
            reachable = bool(disk.locate(r))
            remove.append({"file_id": r["file_id"], "filename": _name(r),
                           "size_bytes": r.get("size_bytes") or 0,
                           "resolution_label": _res_label(r.get("resolution")),
                           "reachable": reachable})
            if reachable:
                files += 1
                size += r.get("size_bytes") or 0
            else:
                blocked += 1
        groups.append({"label": _label(keep),
                       "keep": {"file_id": keep["file_id"], "filename": _name(keep),
                                "size_bytes": keep.get("size_bytes") or 0,
                                "resolution_label": _res_label(keep.get("resolution"))},
                       "remove": remove})
    return {"groups": groups, "files": files, "size_bytes": size, "unreachable": blocked}


def remove_extra_copies(db, kind: str, item_id: int, *, disk: Optional[_Disk] = None,
                        delete: Optional[Callable] = None) -> Dict[str, Any]:
    """recycle every copy but the best of each movie or episode. the plan is
    worked out again here, so a file that arrived or left since the preview
    is judged as it is now. the kept file is never touched."""
    plan = extra_copies_plan(db, kind, item_id, disk=disk)
    delete = delete or (lambda fid: file_ops.delete_to_recycle(db, fid))
    removed, failed, size, errors = 0, 0, 0, []
    for group in plan["groups"]:
        for f in group["remove"]:
            if not f["reachable"]:
                continue
            res = delete(f["file_id"])
            if res.get("success"):
                removed += 1
                size += f["size_bytes"]
            else:
                failed += 1
                errors.append({"filename": f["filename"], "error": res.get("error") or "Couldn't delete"})
    return {"success": True, "removed": removed, "failed": failed, "size_bytes": size,
            "unreachable": plan["unreachable"], "errors": errors[:20]}


# ── bulk ─────────────────────────────────────────────────────────────────────

_BULK = {"rename": file_ops.rename_to_template, "delete": file_ops.delete_to_recycle}


def bulk(db, action: str, file_ids: List[Any]) -> Dict[str, Any]:
    """rename to the template, or recycle, a chosen set of files. one at a
    time, in order: each rename reads the folder the last one changed."""
    op = _BULK.get(action)
    if op is None:
        return {"success": False, "error": "action must be rename or delete"}
    from core.video import file_history
    done, unchanged, failed, errors = 0, 0, 0, []
    with file_history.batch() as batch_id:
        for fid in file_ids:
            try:
                res = op(db, fid)
            except Exception as exc:   # noqa: BLE001 - one bad file never stops the rest
                logger.exception("bulk %s failed for file %s", action, fid)
                res = {"success": False, "error": str(exc)}
            if not res.get("success"):
                failed += 1
                errors.append({"file_id": fid, "error": res.get("error") or "Failed"})
            elif res.get("action") == "unchanged":
                unchanged += 1
            else:
                done += 1
    return {"success": True, "action": action, "done": done, "unchanged": unchanged,
            "failed": failed, "errors": errors[:20], "batch": batch_id if done else None}


# ── reassign ─────────────────────────────────────────────────────────────────

def reassign_file(db, file_id, episode_id) -> Dict[str, Any]:
    """file one episode's file under another episode of the same show, then
    rename it to that episode's template name where SoulSync can reach it.

    the rename is what makes it stick: the media server reads the episode off
    the file name, so a file left named S01E03 goes back to episode 3 on the
    server's next scan. where the rename can't happen the answer says so."""
    moved = db.library_move_file(file_id, episode_id)
    if not moved:
        return {"success": False,
                "error": "That file can only move to another episode of the same show."}
    if moved["from_episode"] == moved["to_episode"]:
        return {"success": True, "action": "unchanged", "renamed": False,
                "message": "It's already filed under that episode"}
    renamed, note = False, ""
    from core.video import file_history
    try:
        with file_history.quiet():   # one history row for the re-file, not two
            res = file_ops.rename_to_template(db, file_id)
        renamed = bool(res.get("success")) and res.get("action") == "renamed"
        if not res.get("success"):
            note = res.get("error") or ""
    except Exception:   # noqa: BLE001 - the move itself is done; the name is the follow-up
        logger.exception("rename after reassign failed for file %s", file_id)
        note = "the rename failed"
    file_history.record(db, "reassign", file_id=file_id, from_episode_id=moved["from_episode"],
                        to_episode_id=moved["to_episode"],
                        to_stored=db.media_file_stored_path(file_id))
    return {"success": True, "action": "reassigned", "renamed": renamed, "rename_note": note,
            **moved}


# ── renumber ─────────────────────────────────────────────────────────────────

def renumber_plan(db, show_id: int, file_ids: List[Any], season: Any, start_episode: Any) -> Dict[str, Any]:
    """file a run of a show's files under consecutive episodes of one season.

    the fix for a season numbered wrong on disk: absolute numbers on a show
    the library splits into seasons, specials filed as episodes, a run that
    is off by one. the chosen files keep their order; the first lands on
    (season, start_episode), the next episode's files on the one after. two
    copies of one episode stay together. season 0 is Specials.

    {moves: [{file_id, filename, from, to, from_episode_id, to_episode_id}]}
    or {error}. nothing is changed here."""
    try:
        season, start = int(season), int(start_episode)
        wanted = {int(f) for f in file_ids or []}
    except (TypeError, ValueError):
        return {"error": "Pick the files, a season and the episode to start from."}
    if season < 0 or start < 0 or not wanted:
        return {"error": "Pick the files, a season and the episode to start from."}
    rows = [r for r in db.library_title_files("show", show_id) if r["file_id"] in wanted]
    if len(rows) != len(wanted):
        return {"error": "Some of those files are not this show's any more. Reload and try again."}
    rows.sort(key=lambda r: (r.get("season") or 0, r.get("episode") or 0, r["file_id"]))
    conn = db._get_connection()
    try:
        have = {int(r["episode_number"]): int(r["id"]) for r in conn.execute(
            "SELECT id, episode_number FROM episodes WHERE show_id = ? AND season_number = ?",
            (int(show_id), season))}
    finally:
        conn.close()
    # what already sits under each episode and is not part of this run
    others: Dict[Any, int] = {}
    for r in db.library_title_files("show", show_id):
        if r["file_id"] not in wanted:
            others[r["item_id"]] = others.get(r["item_id"], 0) + 1
    moves, step, last = [], -1, None
    for r in rows:
        if r["item_id"] != last:
            step, last = step + 1, r["item_id"]
        number = start + step
        target = have.get(number)
        if target is None:
            where = "Specials" if season == 0 else "Season %d" % season
            return {"error": "%s has no episode %d in the library, so nothing can be filed there. "
                             "Refresh the show's details if it should." % (where, number)}
        if target == r["item_id"]:
            continue
        if others.get(target):
            # filing onto it would leave two files there and read as extra copies
            return {"error": "S%02dE%02d already has a file that isn't ticked. Tick it too, or "
                             "move it first." % (season, number)}
        moves.append({"file_id": r["file_id"], "filename": _name(r), "from": _label(r),
                      "to": "S%02dE%02d" % (season, number),
                      "from_episode_id": r["item_id"], "to_episode_id": target})
    return {"moves": moves, "files": len(moves), "season": season, "start_episode": start}


def renumber(db, show_id: int, file_ids: List[Any], season: Any, start_episode: Any) -> Dict[str, Any]:
    """do what renumber_plan lists: re-file every file first, then rename them,
    so a run shifted by one never trips over the name its neighbour still has."""
    from core.video import file_history
    plan = renumber_plan(db, show_id, file_ids, season, start_episode)
    if plan.get("error"):
        return {"success": False, "error": plan["error"]}
    moved = []
    with file_history.batch() as batch_id:
        for mv in plan["moves"]:
            if db.library_move_file(mv["file_id"], mv["to_episode_id"]):
                moved.append(mv)
        # a run shifted by one: each file wants the name its neighbour still
        # has, so one pass frees one name. go round while any gets through.
        pending, not_renamed = [m["file_id"] for m in moved], []
        with file_history.quiet():
            while pending:
                not_renamed = []
                for file_id in reversed(pending):
                    try:
                        res = file_ops.rename_to_template(db, file_id) or {}
                    except Exception:   # noqa: BLE001 - the re-file is done; the name follows
                        logger.exception("rename after renumber failed for file %s", file_id)
                        res = {"success": False}
                    if not res.get("success"):
                        not_renamed.append(file_id)
                if len(not_renamed) == len(pending):
                    break
                pending = not_renamed
        # written down after the renames, with where each file ended up
        for mv in moved:
            file_history.record(db, "reassign", file_id=mv["file_id"],
                                from_episode_id=mv["from_episode_id"],
                                to_episode_id=mv["to_episode_id"],
                                to_stored=db.media_file_stored_path(mv["file_id"]))
    return {"success": True, "moved": len(moved), "not_renamed": len(not_renamed),
            "batch": batch_id if moved else None}


def show_episodes(db, show_id: int) -> Dict[str, Any]:
    """a show's episodes by season with the files filed under each: what the
    renumber editor lists. {seasons: [{season, episodes: [{id, episode, title,
    air_date, files: [...]}]}]}"""
    conn = db._get_connection()
    try:
        eps = conn.execute(
            "SELECT id, season_number, episode_number, title, air_date FROM episodes "
            "WHERE show_id = ? ORDER BY season_number, episode_number", (int(show_id),)).fetchall()
    finally:
        conn.close()
    files: Dict[int, List[dict]] = {}
    for r in db.library_title_files("show", show_id):
        files.setdefault(r["item_id"], []).append({
            "file_id": r["file_id"], "filename": _name(r), "size_bytes": r.get("size_bytes") or 0,
            "resolution": r.get("resolution"), "quality": r.get("quality")})
    seasons: Dict[int, dict] = {}
    for e in eps:
        s = seasons.setdefault(int(e["season_number"] or 0),
                               {"season": int(e["season_number"] or 0), "episodes": []})
        s["episodes"].append({"id": e["id"], "episode": e["episode_number"], "title": e["title"],
                              "air_date": e["air_date"], "files": files.get(e["id"], [])})
    return {"seasons": [seasons[k] for k in sorted(seasons, key=lambda n: (n == 0, n))]}


# ── consolidate ──────────────────────────────────────────────────────────────

def _season_dir_name(season: Any) -> str:
    n = int(season or 0)
    return "Specials" if n == 0 else "Season %02d" % n


def _local_show_folder(local: str, stored: str) -> str:
    """the local folder that is ``show_folder_of(stored)``: the local path with
    the same number of trailing segments taken off."""
    tail = str(stored).replace("\\", "/")[len(show_folder_of(stored)):].strip("/")
    out = local
    for _ in [p for p in tail.split("/") if p]:
        out = os.path.dirname(out)
    return out


def _same_drive(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def consolidate_plan(db, show_id: int, target: Optional[str] = None, *,
                     disk: Optional[_Disk] = None,
                     free_bytes: Optional[Callable[[str], int]] = None) -> Dict[str, Any]:
    """what bringing a show into one folder would move.

    ``target`` is one of the show's own folders as the tab lists them (the
    server's view); the fullest one when not given. a file keeps its name and
    lands in that folder's season folder, named the way that folder already
    names the season. nothing here touches the disk beyond looking."""
    rows = db.library_title_files("show", show_id)
    if not rows:
        return {"error": "This show has no files on record."}
    disk = disk or _Disk(db)
    folders: Dict[str, List[dict]] = {}
    for r in rows:
        folders.setdefault(show_folder_of(r.get("relative_path")), []).append(r)
    if len(folders) < 2:
        return {"error": "This show already lives in one folder."}
    if not target:
        target = max(folders, key=lambda k: len(folders[k]))
    target = str(target).replace("\\", "/").rstrip("/")
    if target not in folders:
        return {"error": "Pick one of the folders this show is already in."}
    # the target as this machine sees it, and how it names each season folder
    target_local, season_dirs = None, {}
    # a windows server's paths go back in with its own separators
    windows = any("\\" in str(r.get("relative_path") or "") for r in folders[target])
    for r in folders[target]:
        local = disk.locate(r)
        if not local:
            continue
        target_local = target_local or _local_show_folder(local, r["relative_path"])
        inner = os.path.dirname(str(r["relative_path"]).replace("\\", "/"))
        if os.path.dirname(inner) == target:   # a season folder right under the show
            season_dirs.setdefault(r.get("season"), os.path.basename(inner))
    if not target_local:
        return {"error": "SoulSync can't reach that folder from here, so it can't move files into it."}
    moves, skipped, size, cross = [], [], 0, 0
    for folder, group in folders.items():
        if folder == target:
            continue
        for r in group:
            name = _name(r)
            local = disk.locate(r)
            if not local:
                skipped.append({"file_id": r["file_id"], "filename": name,
                                "reason": "only your media server can see it"})
                continue
            season_dir = season_dirs.get(r.get("season")) or _season_dir_name(r.get("season"))
            want = os.path.join(target_local, season_dir, name)
            if os.path.exists(want):
                skipped.append({"file_id": r["file_id"], "filename": name,
                                "reason": "a file with that name is already there"})
                continue
            other_drive = not _same_drive(local, target_local)
            moves.append({"file_id": r["file_id"], "filename": name, "label": _label(r),
                          "from": local, "to": want,
                          "stored": ("%s/%s/%s" % (target, season_dir, name)).replace(
                              "/", "\\" if windows else "/"),
                          "size_bytes": r.get("size_bytes") or 0, "copies": other_drive})
            size += r.get("size_bytes") or 0
            if other_drive:
                cross += r.get("size_bytes") or 0
    free = None
    try:
        free = (free_bytes or (lambda p: shutil.disk_usage(p).free))(target_local)
    except OSError:
        free = None
    return {"target": target, "target_local": target_local, "moves": moves, "skipped": skipped,
            "files": len(moves), "size_bytes": size, "copy_bytes": cross, "free_bytes": free,
            "enough_room": free is None or free > cross,
            "folders": [{"path": k, "files": len(v)} for k, v in
                        sorted(folders.items(), key=lambda kv: -len(kv[1]))]}


_JOBS: Dict[int, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def consolidate_state(show_id: int) -> Dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(int(show_id))
        if not job:
            return {"running": False, "known": False}
        return {k: v for k, v in job.items() if k != "stop"} | {"known": True}


def consolidate_stop(show_id: int) -> bool:
    with _JOBS_LOCK:
        job = _JOBS.get(int(show_id))
        if not job or not job["running"]:
            return False
        job["stop"].set()
        return True


def run_consolidate(db, show_id: int, plan: Dict[str, Any], job: Dict[str, Any], *,
                    on_done: Optional[Callable[[], None]] = None) -> None:
    """move every planned file, one at a time, keeping ``job`` current. a
    stop request is honoured between files, never inside one."""
    try:
        for move in plan["moves"]:
            if job["stop"].is_set():
                job["stopped"] = True
                break
            job["current"] = move["filename"]
            base = job["bytes_done"]

            def tick(copied, base=base):
                job["bytes_done"] = base + copied

            res = file_ops.move_into_place(db, move["file_id"], move["from"], move["to"],
                                           stored=move["stored"], progress=tick)
            job["bytes_done"] = base + move["size_bytes"]
            job["done"] += 1
            if res.get("success"):
                job["moved"] += 1
            else:
                job["failed"] += 1
                job["errors"].append({"filename": move["filename"],
                                      "error": res.get("error") or "Couldn't move"})
    except Exception as exc:   # noqa: BLE001 - the job reports its own failure
        logger.exception("consolidate failed for show %s", show_id)
        job["error"] = str(exc)
    finally:
        job["current"] = None
        job["running"] = False
        if on_done and job["moved"]:
            try:
                on_done()
            except Exception:   # noqa: BLE001 - the server's own scan catches up
                logger.debug("post-consolidate refresh failed", exc_info=True)


def consolidate_start(db, show_id: int, target: Optional[str] = None, *,
                      on_done: Optional[Callable[[], None]] = None,
                      background: bool = True, **plan_kwargs) -> Dict[str, Any]:
    """start bringing a show into ``target``. one job per show at a time; the
    work runs off the request thread because a move to another drive is a
    copy of the whole file."""
    show_id = int(show_id)
    with _JOBS_LOCK:
        if (_JOBS.get(show_id) or {}).get("running"):
            return {"success": False, "error": "This show is already being moved."}
    plan = consolidate_plan(db, show_id, target, **plan_kwargs)
    if plan.get("error"):
        return {"success": False, "error": plan["error"]}
    if not plan["moves"]:
        return {"success": False, "error": "There's nothing SoulSync can move here."}
    if not plan["enough_room"]:
        return {"success": False,
                "error": "There isn't enough free space in that folder for the files that have to be copied."}
    job = {"running": True, "target": plan["target"], "total": len(plan["moves"]), "done": 0,
           "moved": 0, "failed": 0, "bytes_total": plan["size_bytes"], "bytes_done": 0,
           "current": None, "errors": [], "stopped": False, "error": None,
           "skipped": len(plan["skipped"]), "stop": threading.Event()}
    with _JOBS_LOCK:
        if (_JOBS.get(show_id) or {}).get("running"):
            return {"success": False, "error": "This show is already being moved."}
        _JOBS[show_id] = job
    if background:
        threading.Thread(target=run_consolidate, args=(db, show_id, plan, job),
                         kwargs={"on_done": on_done}, name="video-consolidate-%s" % show_id,
                         daemon=True).start()
    else:
        run_consolidate(db, show_id, plan, job, on_done=on_done)
    return {"success": True, "files": len(plan["moves"]), "size_bytes": plan["size_bytes"],
            "target": plan["target"]}


__all__ = ["extra_copies_plan", "remove_extra_copies", "bulk", "reassign_file",
           "consolidate_plan", "consolidate_start", "consolidate_state", "consolidate_stop"]
