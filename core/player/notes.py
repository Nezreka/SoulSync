"""what people say about what they watch and listen to, in player.db:

  * a comment: about a title as a whole (a movie, a show, an album). seen
    by everyone on the server who can see that title
  * a mark: a moment in a video or a song, with a note if you like (a
    bookmark: "the scene", "this solo"). yours alone unless you share it.
    marks show on the seek bar; a hover reads them, a click goes there

one table, one shape: a mark is a note with a time. whether a viewer may
see the title at all is the caller's call (the same rules as watching now).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

TABLE = "player_notes"
KINDS = ("movie", "show", "episode", "track", "album", "audiobook", "youtube", "podcast_episode")
MAX_BODY = 1000
MAX_PER_TITLE = 500

_ready: set = set()


class NoteError(ValueError):
    pass


def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                at_seconds REAL,
                body TEXT NOT NULL DEFAULT '',
                shared INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                edited_at REAL
            )""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_item ON {TABLE} (item_type, item_id, created_at)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_profile ON {TABLE} (profile_id)")
        conn.commit()
    _ready.add(key)


def _kind(item_type: Any, item_id: Any) -> tuple:
    kind = str(item_type or "")
    ident = str(item_id or "").strip()[:200]
    if kind not in KINDS or not ident:
        raise NoteError("that isn't something notes can be on")
    return kind, ident


def _body(v: Any, *, need: bool) -> str:
    text = " ".join(str(v or "").split()) if v is not None else ""
    if len(text) > MAX_BODY:
        raise NoteError(f"keep it under {MAX_BODY} characters")
    if need and not text:
        raise NoteError("say something")
    return text


def _at(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v < 0 or v > 1e6:
        raise NoteError("a mark needs a time in seconds")
    return round(float(v), 2)


def _row(r, viewer_id: int) -> Dict[str, Any]:
    return {"id": r[0], "profile_id": r[1], "item_type": r[2], "item_id": r[3], "at": r[4],
            "body": r[5], "shared": bool(r[6]), "created_at": r[7], "edited_at": r[8],
            "mine": int(r[1]) == int(viewer_id)}


COLS = "id, profile_id, item_type, item_id, at_seconds, body, shared, created_at, edited_at"


def list_for(db: Any, viewer_id: int, item_type: Any, item_id: Any) -> List[Dict[str, Any]]:
    """a title's notes the viewer may read: its own, and others' shared
    ones. comments newest first, then marks in time order"""
    kind, ident = _kind(item_type, item_id)
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT {COLS} FROM {TABLE} WHERE item_type = ? AND item_id = ? "
            "AND (profile_id = ? OR shared = 1) ORDER BY created_at DESC LIMIT ?",
            (kind, ident, int(viewer_id), MAX_PER_TITLE)).fetchall()
    notes = [_row(r, viewer_id) for r in rows]
    comments = [n for n in notes if n["at"] is None]
    marks = sorted((n for n in notes if n["at"] is not None), key=lambda n: n["at"])
    return comments + marks


def add(db: Any, profile_id: int, item_type: Any, item_id: Any, body: Any = None,
        at: Any = None, shared: Any = None, *, now: Optional[float] = None) -> Dict[str, Any]:
    """a comment (no time: shared, and it has to say something) or a mark
    (a time: a note is optional, yours alone unless shared)"""
    kind, ident = _kind(item_type, item_id)
    when = _at(at)
    text = _body(body, need=when is None)
    share = True if when is None else bool(shared)
    ensure_schema(db)
    t = time.time() if now is None else now
    with db._get_connection() as conn:
        count = conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE profile_id = ? AND item_type = ? "
                             "AND item_id = ?", (int(profile_id), kind, ident)).fetchone()[0]
        if count >= MAX_PER_TITLE:
            raise NoteError("that's a lot of notes on one title: take some out first")
        cur = conn.execute(
            f"INSERT INTO {TABLE} (profile_id, item_type, item_id, at_seconds, body, shared, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (int(profile_id), kind, ident, when, text, int(share), t))
        conn.commit()
        row = conn.execute(f"SELECT {COLS} FROM {TABLE} WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row(row, profile_id)


def _get(conn, note_id: Any):
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return None
    return conn.execute(f"SELECT {COLS} FROM {TABLE} WHERE id = ?", (nid,)).fetchone()


def edit(db: Any, profile_id: int, note_id: Any, body: Any = None, shared: Any = None,
         *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """change your own note; None: no such note of yours"""
    ensure_schema(db)
    with db._get_connection() as conn:
        r = _get(conn, note_id)
        if not r or int(r[1]) != int(profile_id):
            return None
        is_mark = r[4] is not None
        text = r[5] if body is None else _body(body, need=not is_mark)
        share = bool(r[6]) if shared is None or not is_mark else bool(shared)
        conn.execute(f"UPDATE {TABLE} SET body = ?, shared = ?, edited_at = ? WHERE id = ?",
                     (text, int(share), time.time() if now is None else now, r[0]))
        conn.commit()
        return _row(_get(conn, r[0]), profile_id)


def remove(db: Any, profile_id: int, note_id: Any, *, moderator: bool = False) -> bool:
    """take a note down: your own, or anyone's for a moderator (a shared one)"""
    ensure_schema(db)
    with db._get_connection() as conn:
        r = _get(conn, note_id)
        if not r:
            return False
        if int(r[1]) != int(profile_id) and not (moderator and r[6]):
            return False
        conn.execute(f"DELETE FROM {TABLE} WHERE id = ?", (r[0],))
        conn.commit()
        return True
