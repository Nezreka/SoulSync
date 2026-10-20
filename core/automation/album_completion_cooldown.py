"""Persistent, atomic cooldown for automatic album-completion attempts."""

import sqlite3
import time
import uuid

COOLDOWN_SECONDS = 24 * 60 * 60


def claim_attempt(db, profile_id, source, release_id, *, now=None):
    """Atomically reserve a release; return token or None if cooling down."""
    if profile_id is None or not source or not release_id:
        raise ValueError("Incomplete cooldown identity")

    timestamp = time.time() if now is None else float(now)
    token = str(uuid.uuid4())
    conn = db._get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS album_completion_attempts (
                profile_id TEXT NOT NULL,
                canonical_source TEXT NOT NULL,
                canonical_album_id TEXT NOT NULL,
                claimed_at REAL NOT NULL,
                token TEXT NOT NULL,
                PRIMARY KEY (
                    profile_id, canonical_source, canonical_album_id
                )
            )
        """)
        result = conn.execute("""
            INSERT INTO album_completion_attempts
                (profile_id, canonical_source, canonical_album_id,
                 claimed_at, token)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (
                profile_id, canonical_source, canonical_album_id
            ) DO UPDATE SET
                claimed_at = excluded.claimed_at,
                token = excluded.token
            WHERE album_completion_attempts.claimed_at <= ?
        """, (
            str(profile_id), str(source).casefold(), str(release_id),
            timestamp, token, timestamp - COOLDOWN_SECONDS,
        ))
        conn.commit()
        return token if result.rowcount == 1 else None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_attempt(db, profile_id, source, release_id, token):
    """Undo only our own reservation when submission fails."""
    if not token:
        return
    conn = db._get_connection()
    try:
        conn.execute("""
            DELETE FROM album_completion_attempts
            WHERE profile_id = ?
              AND canonical_source = ?
              AND canonical_album_id = ?
              AND token = ?
        """, (
            str(profile_id), str(source).casefold(),
            str(release_id), token,
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def is_cooling_down(db, profile_id, source, release_id, *, now=None):
    """Read-only peek so the handler can skip a release without fetching it.

    claim_attempt is still the real gate, this just saves the api call.
    """
    if profile_id is None or not source or not release_id:
        return False
    timestamp = time.time() if now is None else float(now)
    conn = db._get_connection()
    try:
        row = conn.execute("""
            SELECT claimed_at FROM album_completion_attempts
            WHERE profile_id = ?
              AND canonical_source = ?
              AND canonical_album_id = ?
        """, (
            str(profile_id), str(source).casefold(), str(release_id),
        )).fetchone()
    except sqlite3.OperationalError:
        # no claims yet, table not created
        return False
    finally:
        conn.close()
    return row is not None and row[0] > timestamp - COOLDOWN_SECONDS
