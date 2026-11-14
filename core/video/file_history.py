"""what was done to the library's files by hand, and putting it back.

every rename, move, delete and re-file a person does (from the library
manager, a title's files tab, a maintenance fix) leaves one row in
video_file_changes. undo reads the row and reverses it:

  rename / move  the file goes back to where it was, sidecars with it
  delete         the file comes out of the recycle folder and its library
                 row is put back, so it is owned again without a scan
  reassign       the file is filed under the episode it came from and
                 renamed back

an undo is refused, in plain words, when the world moved on: the file is no
longer where the change left it, something else sits at the old path, or the
recycle folder was emptied.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import sqlite3
import uuid
from typing import Any, Dict, Iterator, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.file_history")

ACTIONS = ("rename", "move", "delete", "reassign")

_quiet: contextvars.ContextVar = contextvars.ContextVar("video_file_history_quiet", default=False)
_batch: contextvars.ContextVar = contextvars.ContextVar("video_file_history_batch", default=None)
_action: contextvars.ContextVar = contextvars.ContextVar("video_file_history_action", default=None)


@contextlib.contextmanager
def quiet() -> Iterator[None]:
    """nothing inside is recorded (an undo, or a step of a larger change that
    records itself)"""
    token = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(token)


@contextlib.contextmanager
def batch(action: Optional[str] = None) -> Iterator[str]:
    """everything recorded inside shares one batch id, so a bulk run is undone
    together. ``action`` names the changes inside (a move is a rename to
    another folder, and should read as a move)."""
    batch_id = uuid.uuid4().hex[:12]
    t1, t2 = _batch.set(batch_id), _action.set(action)
    try:
        yield batch_id
    finally:
        _batch.reset(t1)
        _action.reset(t2)


def owner_of(db, file_id) -> Dict[str, Any]:
    """the title a stored file belongs to, and a short label for the file.
    read before a delete drops the row."""
    blank = {"kind": None, "item_id": None, "title": None, "label": ""}
    try:
        conn = db._get_connection()
        try:
            r = conn.execute(
                "SELECT f.relative_path, f.movie_id, m.title AS movie_title, s.id AS show_id, "
                "s.title AS show_title, e.season_number, e.episode_number FROM media_files f "
                "LEFT JOIN movies m ON m.id = f.movie_id "
                "LEFT JOIN episodes e ON e.id = f.episode_id "
                "LEFT JOIN shows s ON s.id = e.show_id WHERE f.id = ?", (int(file_id),)).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, TypeError, ValueError):
        return blank
    if not r:
        return blank
    if r["movie_id"] is not None:
        return {"kind": "movie", "item_id": r["movie_id"], "title": r["movie_title"],
                "label": os.path.basename(str(r["relative_path"] or "").replace("\\", "/"))}
    if r["show_id"] is None:
        return blank
    label = ""
    if r["season_number"] is not None and r["episode_number"] is not None:
        label = "S%02dE%02d" % (int(r["season_number"]), int(r["episode_number"]))
    return {"kind": "show", "item_id": r["show_id"], "title": r["show_title"], "label": label}


def record(db, action: str, *, file_id=None, owner: Optional[dict] = None, from_path=None,
           to_path=None, from_stored=None, to_stored=None, trash_path=None,
           from_episode_id=None, to_episode_id=None,
           snapshot: Optional[dict] = None) -> Optional[int]:
    """one change. never raises: a missing history row is not a failed rename.
    ``owner``: what owner_of read, when the file's row is already gone."""
    if _quiet.get():
        return None
    action = _action.get() or action
    if action not in ACTIONS:
        return None
    who = owner or owner_of(db, file_id)
    try:
        conn = db._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO video_file_changes (action, kind, item_id, title, label, file_id, "
                "from_path, to_path, from_stored, to_stored, trash_path, from_episode_id, "
                "to_episode_id, snapshot, batch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (action, who["kind"], who["item_id"], who["title"], who["label"], file_id,
                 from_path, to_path, from_stored, to_stored, trash_path, from_episode_id,
                 to_episode_id,
                 json.dumps(snapshot) if snapshot else None, _batch.get()))
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()
    except Exception:   # noqa: BLE001
        logger.debug("file change not recorded (%s)", action, exc_info=True)
        return None


def snapshot_file(db, file_id) -> Optional[dict]:
    """a media_files row as it stands, to put back after a delete is undone"""
    try:
        conn = db._get_connection()
        try:
            r = conn.execute("SELECT * FROM media_files WHERE id = ?", (int(file_id),)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()
    except (sqlite3.Error, TypeError, ValueError):
        return None


def _public(r: Any) -> Dict[str, Any]:
    d = dict(r)
    d.pop("snapshot", None)
    d["undone"] = bool(d.get("undone_at"))
    d["from_name"] = os.path.basename(str(d.get("from_path") or "").replace("\\", "/"))
    d["to_name"] = os.path.basename(str(d.get("to_path") or "").replace("\\", "/"))
    return d


def changes(db, *, kind: Optional[str] = None, item_id=None, limit: int = 200,
            offset: int = 0) -> Dict[str, Any]:
    """newest first, for one title or the whole library"""
    where, args = [], []
    if kind in ("movie", "show") and item_id is not None:
        where += ["kind = ?", "item_id = ?"]
        args += [kind, int(item_id)]
    sql = " WHERE " + " AND ".join(where) if where else ""
    limit, offset = max(1, min(int(limit or 200), 500)), max(0, int(offset or 0))
    conn = db._get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM video_file_changes" + sql, args).fetchone()[0]
        rows = conn.execute("SELECT * FROM video_file_changes" + sql +
                            " ORDER BY id DESC LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
    except sqlite3.Error:
        return {"items": [], "total": 0}
    finally:
        conn.close()
    return {"items": [_public(r) for r in rows], "total": int(total)}


def _get(db, change_id) -> Optional[dict]:
    conn = db._get_connection()
    try:
        r = conn.execute("SELECT * FROM video_file_changes WHERE id = ?",
                         (int(change_id),)).fetchone()
        return dict(r) if r else None
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()


def _mark_undone(db, change_id: int) -> None:
    conn = db._get_connection()
    try:
        conn.execute("UPDATE video_file_changes SET undone_at = datetime('now') WHERE id = ?",
                     (change_id,))
        conn.commit()
    finally:
        conn.close()


_GONE = ("The library no longer lists this file where the change left it, so it can't be put "
         "back from here.")


def _file_now(db, ch: dict) -> Optional[int]:
    """the library row of the file a change left behind, as it is TODAY.

    a scan deletes and re-adds a title's file rows, so the id written down at
    the time of the change may be gone, or worse, belong to another file. the
    row is found by the path the change left the file at; the old id is only
    trusted while it still sits at that path."""
    stored = ch.get("to_stored")
    conn = db._get_connection()
    try:
        if stored:
            rows = conn.execute("SELECT id FROM media_files WHERE relative_path = ?",
                                (stored,)).fetchall()
            ids = [int(r[0]) for r in rows]
            if ch.get("file_id") in ids:
                return int(ch["file_id"])
            return ids[0] if len(ids) == 1 else None
        # a change from before to_stored was kept: the id, only if its file
        # still carries the name the change gave it
        row = conn.execute("SELECT relative_path FROM media_files WHERE id = ?",
                           (ch.get("file_id"),)).fetchone()
        name = os.path.basename(str(ch.get("to_path") or "").replace("\\", "/"))
        if row and name and os.path.basename(str(row[0] or "").replace("\\", "/")) == name:
            return int(ch["file_id"])
        return None
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()


def _unfile_from_library(db, ch: dict) -> None:
    """a move to another library filed the title there. going back, the title
    stops being filed there, or its next download would land in the library
    it just left."""
    try:
        from core.video import libraries
        kind = ch.get("kind")
        if kind not in ("movie", "show") or not ch.get("item_id"):
            return
        tmdb = (db.title_identity(kind, ch["item_id"]) or {}).get("tmdb_id")
        there = libraries.library_for_path(db, ch.get("to_path"), kind)
        home = libraries.library_for_path(db, ch.get("from_path"), kind)
        if tmdb and there and (not home or home["id"] != there["id"]) \
                and libraries.title_assignment(db, kind, tmdb) == there["id"]:
            libraries.assign_title(db, kind, tmdb, None)
        libraries.attribute_files(db)
    except Exception:   # noqa: BLE001 - the file is back; the next scan re-files the title
        logger.debug("un-filing after an undone move failed", exc_info=True)


def _undo_move(db, ch: dict) -> Dict[str, Any]:
    from core.video import file_ops
    cur, back = ch.get("to_path"), ch.get("from_path")
    if not cur or not back:
        return {"success": False, "error": "This change didn't record where the file was."}
    if not os.path.isfile(cur):
        return {"success": False,
                "error": "The file isn't where this change left it any more, so it can't be put back."}
    file_id = _file_now(db, ch)
    if file_id is None:
        return {"success": False, "error": _GONE}
    res = file_ops.move_into_place(db, file_id, cur, back,
                                   stored=ch.get("from_stored") or None)
    if not res.get("success"):
        return {"success": False, "error": res.get("error") or "The file couldn't be moved back."}
    if ch.get("action") == "move":
        _unfile_from_library(db, ch)
    return {"success": True, "message": "Put back as %s" % os.path.basename(back)}


def _restore_row(db, snap: dict) -> bool:
    """put a dropped media_files row back, under its old id when that is
    still free, and mark its movie or episode owned again"""
    conn = db._get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(media_files)")}
        snap = {k: v for k, v in snap.items() if k in cols}
        owner = ("movies", snap.get("movie_id")) if snap.get("movie_id") is not None \
            else ("episodes", snap.get("episode_id"))
        if owner[1] is None or not conn.execute(
                f"SELECT 1 FROM {owner[0]} WHERE id = ?", (owner[1],)).fetchone():
            return False
        if conn.execute("SELECT 1 FROM media_files WHERE id = ?", (snap.get("id"),)).fetchone():
            snap.pop("id", None)
        names = list(snap)
        conn.execute("INSERT INTO media_files (%s) VALUES (%s)"
                     % (", ".join(names), ", ".join("?" * len(names))), [snap[n] for n in names])
        conn.execute(f"UPDATE {owner[0]} SET has_file = 1 WHERE id = ?", (owner[1],))
        conn.commit()
        return True
    except sqlite3.Error:
        logger.exception("could not put the library row back for a restored file")
        return False
    finally:
        conn.close()


def _undo_delete(db, ch: dict) -> Dict[str, Any]:
    from core.video import organization, recycle
    trash = ch.get("trash_path")
    if not trash:
        return {"success": False,
                "error": "This file was deleted for good (the recycle folder was off), so it can't come back."}
    if not os.path.exists(trash):
        return {"success": False,
                "error": "The recycle folder no longer holds this file. It was emptied or restored already."}
    res = recycle.restore_entry(os.path.dirname(trash), os.path.basename(trash),
                                organization.load(db), db)
    if not res.get("success"):
        return {"success": False, "error": res.get("error") or "The file couldn't be restored."}
    back = False
    try:
        snap = json.loads(ch.get("snapshot") or "null")
        back = bool(snap) and _restore_row(db, snap)
    except ValueError:
        back = False
    return {"success": True, "rescan": not back,
            "message": ("Restored and back in the library" if back
                        else "Restored. The next scan adds it back to the library")}


def _rename_back(db, file_id) -> str:
    """rename a re-filed file to its episode's name. '' when it is named
    right, else why not."""
    from core.video import file_ops
    try:
        res = file_ops.rename_to_template(db, file_id) or {}
        return "" if res.get("success") else (res.get("error") or "the rename failed")
    except Exception:   # noqa: BLE001 - the re-file is done; the name is the follow-up
        logger.exception("rename after undoing a reassign failed")
        return "the rename failed"


def _undo_reassign(db, ch: dict) -> Dict[str, Any]:
    file_id = _file_now(db, ch)
    if file_id is None:
        return {"success": False, "error": _GONE}
    moved = db.library_move_file(file_id, ch.get("from_episode_id"))
    if not moved:
        return {"success": False,
                "error": "The file or the episode it came from isn't in the library any more."}
    note = _rename_back(db, file_id)
    out = {"success": True, "message": "Filed back under the episode it came from"
           + (" (not renamed: %s)" % note if note else "")}
    if note:
        out["rename_pending"] = file_id     # a batch tries again once its neighbours moved
    return out


def undo(db, change_id) -> Dict[str, Any]:
    """reverse one change. {success, message} or {success: False, error}."""
    ch = _get(db, change_id)
    if not ch:
        return {"success": False, "error": "That change isn't in the history any more."}
    if ch.get("undone_at"):
        return {"success": False, "error": "That change was already undone."}
    handler = {"rename": _undo_move, "move": _undo_move, "delete": _undo_delete,
               "reassign": _undo_reassign}.get(ch["action"])
    if not handler:
        return {"success": False, "error": "This kind of change can't be undone."}
    with quiet():
        try:
            res = handler(db, ch)
        except Exception:   # noqa: BLE001
            logger.exception("undo failed for change %s", change_id)
            return {"success": False, "error": "Undo failed. See app.log."}
    if res.get("success"):
        _mark_undone(db, int(ch["id"]))
        res.update(kind=ch.get("kind"), item_id=ch.get("item_id"))
    return res


def undo_batch(db, batch_id: str) -> Dict[str, Any]:
    """every change of one bulk run, newest first"""
    conn = db._get_connection()
    try:
        ids = [int(r[0]) for r in conn.execute(
            "SELECT id FROM video_file_changes WHERE batch = ? AND undone_at IS NULL "
            "ORDER BY id DESC", (str(batch_id),))]
    except sqlite3.Error:
        ids = []
    finally:
        conn.close()
    done, failed, titles, unnamed = 0, [], set(), []
    # twice: in a chain (E01 to E02, E02 to E03) one file's old name is free
    # only after the next one has gone back
    for _ in range(2):
        again, failed = [], []
        for cid in ids:
            res = undo(db, cid)
            if res.get("success"):
                done += 1
                titles.add((res.get("kind"), res.get("item_id")))
                if res.get("rename_pending"):
                    unnamed.append(res["rename_pending"])
            else:
                again.append(cid)
                failed.append({"id": cid, "error": res.get("error")})
        ids = again
        if not ids:
            break
    # re-filed files whose old name was still taken: again, while any gets through
    with quiet():
        while unnamed:
            still = [f for f in unnamed if _rename_back(db, f)]
            if len(still) == len(unnamed):
                break
            unnamed = still
    return {"success": done > 0 and not failed, "undone": done, "failed": failed,
            "not_renamed": len(unnamed),
            "titles": [{"kind": k, "item_id": i} for k, i in titles if k and i]}




__all__ = ["ACTIONS", "quiet", "batch", "owner_of", "record", "snapshot_file", "changes", "undo", "undo_batch"]
