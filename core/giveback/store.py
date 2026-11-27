"""what soulsync has given back, so nothing is ever sent twice.

``giveback_submissions`` holds one row per (kind, subject, mbid):

- kind 'acoustid': subject is ``file:<lib2_track_files.id>``, a file's
  fingerprint sent for that recording.
- kind 'isrc': subject is the isrc, sent for that recording.

status is 'submitted' (sent and taken), 'present' (the other side already had
it), 'skipped' (not sure enough to send; ``detail`` says why) or 'rejected'
(the other side said no). a row in any status means "don't try this again".
a network failure writes nothing, so the next round retries it.

``giveback_cursor`` keeps where each kind's walk through the library got to,
so a round doesn't re-read the rows the last one already looked at.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

SUBMITTED = 'submitted'
PRESENT = 'present'
SKIPPED = 'skipped'
REJECTED = 'rejected'
STATUSES = (SUBMITTED, PRESENT, SKIPPED, REJECTED)

DDL = (
    """CREATE TABLE IF NOT EXISTS giveback_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        mbid TEXT NOT NULL,
        status TEXT NOT NULL,
        detail TEXT,
        remote_id TEXT,
        track_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (kind, subject, mbid)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_giveback_kind_status ON giveback_submissions (kind, status)",
    """CREATE TABLE IF NOT EXISTS giveback_cursor (
        kind TEXT PRIMARY KEY,
        last_id INTEGER NOT NULL DEFAULT 0,
        wrapped_at TIMESTAMP
    )""",
)


def ensure_schema(cursor: Any) -> None:
    """idempotent; music_database runs it at every start"""
    for statement in DDL:
        cursor.execute(statement)


def file_subject(file_id: int) -> str:
    return f'file:{int(file_id)}'


def seen(conn: Any, kind: str, subject: str, mbid: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM giveback_submissions WHERE kind=? AND subject=? AND mbid=?",
        (kind, subject, mbid)).fetchone()
    return row is not None


def record(conn: Any, kind: str, subject: str, mbid: str, status: str, *,
           detail: Optional[str] = None, remote_id: Optional[str] = None,
           track_id: Optional[int] = None) -> bool:
    """write the outcome once. answers False when it was already there (a
    second writer lost the race), which is fine: it is never sent twice."""
    if status not in STATUSES:
        raise ValueError(f'unknown give back status: {status}')
    cur = conn.execute(
        "INSERT OR IGNORE INTO giveback_submissions "
        "(kind, subject, mbid, status, detail, remote_id, track_id) VALUES (?,?,?,?,?,?,?)",
        (kind, subject, mbid, status, detail, remote_id, track_id))
    return bool(cur.rowcount)


def record_many(conn: Any, rows: Iterable[Dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        n += record(conn, r['kind'], r['subject'], r['mbid'], r['status'],
                    detail=r.get('detail'), remote_id=r.get('remote_id'),
                    track_id=r.get('track_id'))
    return n


def counts(conn: Any) -> Dict[str, Dict[str, int]]:
    """{'acoustid': {'submitted': n, ...}, 'isrc': {...}}"""
    out: Dict[str, Dict[str, int]] = {
        'acoustid': {s: 0 for s in STATUSES},
        'isrc': {s: 0 for s in STATUSES},
    }
    for kind, status, n in conn.execute(
            "SELECT kind, status, COUNT(*) FROM giveback_submissions GROUP BY kind, status"):
        out.setdefault(kind, {s: 0 for s in STATUSES})[status] = int(n)
    return out


def get_cursor(conn: Any, kind: str) -> int:
    row = conn.execute("SELECT last_id FROM giveback_cursor WHERE kind=?", (kind,)).fetchone()
    return int(row[0]) if row else 0


def set_cursor(conn: Any, kind: str, last_id: int, *, wrapped: bool = False) -> None:
    conn.execute(
        "INSERT INTO giveback_cursor (kind, last_id, wrapped_at) VALUES (?, ?, "
        "CASE WHEN ? THEN CURRENT_TIMESTAMP END) "
        "ON CONFLICT(kind) DO UPDATE SET last_id=excluded.last_id, "
        "wrapped_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE giveback_cursor.wrapped_at END",
        (kind, int(last_id), 1 if wrapped else 0, 1 if wrapped else 0))


# a walk that reached the end of the library rests this long before it
# starts again from the top
REST_HOURS = 24


def resting(conn: Any, kind: str) -> bool:
    """true while a finished walk is resting"""
    row = conn.execute(
        "SELECT last_id, wrapped_at FROM giveback_cursor WHERE kind=? "
        f"AND wrapped_at > datetime('now', '-{int(REST_HOURS)} hours')", (kind,)).fetchone()
    return bool(row) and int(row[0]) == 0
