"""the conversation around an issue, one implementation for music and video.

an issue used to hold a single admin_response the admin overwrote, and the
reporter never learned anything happened. now every issue carries a thread
(comments plus status changes as timeline events), a list of followers (people
who hit the same problem and said so instead of filing a duplicate) and an
unread flag for the reporter.

each side hands in its connection factory and table names; the schema is
created here, idempotent, so neither db needs to know the details. the
columns are named author_id / follower_id, never profile_id, on purpose: the
profile-delete sweep removes every row with a profile_id column, and a thread
should outlive one of its participants.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable, Dict, List, Optional

BODY_MAX = 2000


class IssueThreadStore:
    def __init__(self, connect: Callable[[], sqlite3.Connection], issues_table: str,
                 comments_table: str, followers_table: str):
        self._connect = connect
        self.t_issues = issues_table
        self.t_comments = comments_table
        self.t_followers = followers_table
        self._ready = False

    # ── schema ────────────────────────────────────────────────────────────
    def ensure_schema(self, conn) -> None:
        if self._ready:
            return
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.t_comments} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id INTEGER NOT NULL,
                author_id INTEGER,
                author_name TEXT,
                kind TEXT NOT NULL DEFAULT 'comment',   -- comment | event
                body TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{self.t_comments}_issue ON {self.t_comments} (issue_id)")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.t_followers} (
                issue_id INTEGER NOT NULL,
                follower_id INTEGER NOT NULL,
                follower_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (issue_id, follower_id)
            )""")
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({self.t_issues})").fetchall()}
        if cols and "reporter_unread" not in cols:
            conn.execute(f"ALTER TABLE {self.t_issues} ADD COLUMN reporter_unread INTEGER DEFAULT 0")
        if cols and "reporter_label" not in cols:
            conn.execute(f"ALTER TABLE {self.t_issues} ADD COLUMN reporter_label TEXT")
        conn.commit()
        self._ready = True

    def _run(self, fn):
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            self.ensure_schema(conn)
            return fn(conn)
        finally:
            conn.close()

    # ── thread ────────────────────────────────────────────────────────────
    def add_comment(self, issue_id: int, author_id: Optional[int], author_name: str, body: str,
                    kind: str = "comment") -> Optional[int]:
        body = str(body or "").strip()[:BODY_MAX]
        if not body:
            return None

        def go(conn):
            cur = conn.execute(
                f"INSERT INTO {self.t_comments} (issue_id, author_id, author_name, kind, body) "
                f"VALUES (?, ?, ?, ?, ?)",
                (int(issue_id), author_id, author_name or None, kind if kind in ("comment", "event") else "comment",
                 body))
            conn.execute(f"UPDATE {self.t_issues} SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (int(issue_id),))
            conn.commit()
            return cur.lastrowid
        return self._run(go)

    def list_comments(self, issue_id: int) -> List[Dict[str, Any]]:
        return self._run(lambda conn: [dict(r) for r in conn.execute(
            f"SELECT * FROM {self.t_comments} WHERE issue_id = ? ORDER BY id", (int(issue_id),)).fetchall()])

    def delete_thread(self, issue_id: int) -> None:
        def go(conn):
            conn.execute(f"DELETE FROM {self.t_comments} WHERE issue_id = ?", (int(issue_id),))
            conn.execute(f"DELETE FROM {self.t_followers} WHERE issue_id = ?", (int(issue_id),))
            conn.commit()
        self._run(go)

    # ── followers + duplicates ───────────────────────────────────────────
    def find_open_duplicate(self, entity_type: str, entity_id: str, category: str) -> Optional[Dict[str, Any]]:
        """an issue still open on the same item for the same reason."""
        def go(conn):
            row = conn.execute(
                f"SELECT * FROM {self.t_issues} WHERE entity_type = ? AND entity_id = ? AND category = ? "
                f"AND status IN ('open', 'in_progress') ORDER BY id LIMIT 1",
                (entity_type, str(entity_id), category)).fetchone()
            return dict(row) if row else None
        return self._run(go)

    def follow(self, issue_id: int, follower_id: int, follower_name: str = "") -> bool:
        def go(conn):
            cur = conn.execute(
                f"INSERT OR IGNORE INTO {self.t_followers} (issue_id, follower_id, follower_name) VALUES (?, ?, ?)",
                (int(issue_id), int(follower_id), follower_name or None))
            conn.commit()
            return cur.rowcount > 0
        return self._run(go)

    def follower_ids(self, issue_id: int) -> List[int]:
        return self._run(lambda conn: [int(r[0]) for r in conn.execute(
            f"SELECT follower_id FROM {self.t_followers} WHERE issue_id = ?", (int(issue_id),)).fetchall()])

    def followers(self, issue_id: int) -> List[Dict[str, Any]]:
        return self._run(lambda conn: [dict(r) for r in conn.execute(
            f"SELECT follower_id, follower_name, created_at FROM {self.t_followers} WHERE issue_id = ? "
            f"ORDER BY created_at", (int(issue_id),)).fetchall()])

    def followed_issue_ids(self, profile_id: int) -> List[int]:
        return self._run(lambda conn: [int(r[0]) for r in conn.execute(
            f"SELECT issue_id FROM {self.t_followers} WHERE follower_id = ?", (int(profile_id),)).fetchall()])

    # ── the reporter's unread flag ───────────────────────────────────────
    def set_unread(self, issue_id: int, unread: bool) -> None:
        def go(conn):
            conn.execute(f"UPDATE {self.t_issues} SET reporter_unread = ? WHERE id = ?",
                         (1 if unread else 0, int(issue_id)))
            conn.commit()
        self._run(go)

    def unread_count(self, profile_id: int) -> int:
        return self._run(lambda conn: int(conn.execute(
            f"SELECT COUNT(*) FROM {self.t_issues} WHERE profile_id = ? AND reporter_unread = 1",
            (int(profile_id),)).fetchone()[0]))

    # ── a profile being deleted ──────────────────────────────────────────
    def hand_open_issues_to_admin(self, conn, profile_id: int, name: str) -> int:
        """runs INSIDE the delete's transaction: open reports are real
        problems with the library, so they stay in the admin's queue with the
        reporter's name kept as a label, instead of vanishing with them."""
        self.ensure_schema(conn)
        cur = conn.execute(
            f"UPDATE {self.t_issues} SET profile_id = 1, reporter_label = COALESCE(reporter_label, ?) "
            f"WHERE profile_id = ? AND status IN ('open', 'in_progress')",
            (name or None, int(profile_id)))
        conn.execute(f"DELETE FROM {self.t_followers} WHERE follower_id = ?", (int(profile_id),))
        return cur.rowcount


__all__ = ["IssueThreadStore", "BODY_MAX"]
