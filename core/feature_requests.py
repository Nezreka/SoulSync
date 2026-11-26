"""asking for a feature (#2304).

a member's account says what else there is on this server that isn't
switched on for them. this is the way to ask for it without leaving the app:
one press makes a request, an admin approves or declines it, and approving
switches that one permission on for that person through the same call the
People editor uses (so the server's policy still has the last word).

what can be asked for is the list of plain on/off features a member might
look for (the same list their account shows). limits, ratings and who gets
to download are an admin's decisions and are not on it.

kept in the music database. the columns say requester_id / resolved_by, not
profile_id, so the sweep that removes a deleted profile's rows leaves the
history of what was asked and answered.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

TABLE = "feature_requests"
MAX_PENDING = 12

# permission key -> what a member calls it
FEATURES: Dict[str, str] = {
    "podcasts.access": "Podcasts",
    "books.access": "Audiobooks",
    "movies.access": "Movies",
    "tv.access": "TV shows",
    "music.discover": "Discover",
    "music.playlist_sync": "Playlist sync",
    "music.explorer": "Playlist Explorer",
    "music.import": "Import",
    "music.stats": "Listening stats",
    "music.watchlist": "The music watchlist",
    "music.videos": "Music videos",
    "music.sample_studio": "Sample Studio",
    "music.apps": "Music apps",
    "video.discover": "Movie and TV discover",
    "video.calendar": "The calendar",
    "video.youtube": "YouTube channels",
    "chat.use": "Chat & Arcade",
    "account.connect": "Connecting your own accounts",
    "automations.personal": "Your own automations",
}


class FeatureRequestError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def ensure_schema(conn: Any) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id INTEGER NOT NULL,
            requester_name TEXT,
            feature TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            resolved_by INTEGER,
            admin_response TEXT
        )""")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_status ON {TABLE} (status, requester_id)")


def _rows(db: Any, where: str = "", args: tuple = ()) -> List[Dict[str, Any]]:
    import sqlite3
    cols = ("id", "requester_id", "requester_name", "feature", "status", "created_at",
            "resolved_at", "resolved_by", "admin_response")
    # a read never makes the table (a GET must not write): no table yet is
    # nothing asked yet. the first ask makes it.
    try:
        with db._get_connection() as conn:
            cur = conn.execute(
                f"SELECT id, requester_id, requester_name, feature, status, created_at, resolved_at, "
                f"resolved_by, admin_response FROM {TABLE} {where} ORDER BY id DESC", args)
            out = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e).lower():
            raise
        out = []
    for r in out:
        r["label"] = FEATURES.get(r["feature"], r["feature"])
    return out


def requestable(effective: Mapping[str, Any]) -> List[Dict[str, str]]:
    """the features this person doesn't have and could ask for"""
    return [{"key": k, "label": label} for k, label in FEATURES.items() if effective.get(k) is False]


def mine(db: Any, requester_id: int, effective: Mapping[str, Any]) -> Dict[str, Any]:
    """{requestable, pending: [feature], answered: the last few answers}"""
    rows = _rows(db, "WHERE requester_id = ?", (int(requester_id),))
    pending = [r["feature"] for r in rows if r["status"] == "pending"]
    answered = [{"feature": r["feature"], "label": r["label"], "status": r["status"],
                 "admin_response": r["admin_response"], "resolved_at": r["resolved_at"]}
                for r in rows if r["status"] != "pending"][:6]
    return {"requestable": requestable(effective), "pending": pending, "answered": answered}


def ask(db: Any, requester: Mapping[str, Any], feature: Any, effective: Mapping[str, Any]) -> Dict[str, Any]:
    """one request; asking twice for the same thing is the same request"""
    key = str(feature or "")
    if key not in FEATURES:
        raise FeatureRequestError("That isn't something that can be asked for.")
    if effective.get(key) is not False:
        raise FeatureRequestError("You already have that.", 409)
    rid = int(requester["id"])
    existing = _rows(db, "WHERE requester_id = ? AND status = 'pending'", (rid,))
    for r in existing:
        if r["feature"] == key:
            return r
    if len(existing) >= MAX_PENDING:
        raise FeatureRequestError("You have a lot waiting already. Give your admin a chance to answer.", 429)
    with db._get_connection() as conn:
        ensure_schema(conn)
        cur = conn.execute(
            f"INSERT INTO {TABLE} (requester_id, requester_name, feature) VALUES (?, ?, ?)",
            (rid, str(requester.get("name") or ""), key))
        conn.commit()
        new_id = int(cur.lastrowid)
    return _rows(db, "WHERE id = ?", (new_id,))[0]


def pending(db: Any) -> List[Dict[str, Any]]:
    return list(reversed(_rows(db, "WHERE status = 'pending'")))


def get(db: Any, request_id: int) -> Optional[Dict[str, Any]]:
    rows = _rows(db, "WHERE id = ?", (int(request_id),))
    return rows[0] if rows else None


def resolve(db: Any, request_id: int, *, approve: bool, by: Optional[int], response: Any = None,
            grant=None) -> Dict[str, Any]:
    """answer a waiting request. ``grant(requester_id, feature)`` switches the
    permission on and raises if the server's policy won't allow it; it runs
    BEFORE the request is marked approved, so a request is never recorded as
    approved for a permission that wasn't given."""
    row = get(db, request_id)
    if not row or row["status"] != "pending":
        raise FeatureRequestError("That request isn't waiting any more.", 409)
    if approve and grant is not None:
        grant(row["requester_id"], row["feature"])
    text = (str(response or "").strip()[:500]) or None
    with db._get_connection() as conn:
        ensure_schema(conn)
        conn.execute(
            f"UPDATE {TABLE} SET status = ?, resolved_at = CURRENT_TIMESTAMP, resolved_by = ?, "
            "admin_response = ? WHERE id = ? AND status = 'pending'",
            ("approved" if approve else "declined", by, text, int(request_id)))
        conn.commit()
    return get(db, request_id) or row


__all__ = ["FEATURES", "FeatureRequestError", "requestable", "mine", "ask", "pending", "get", "resolve"]
