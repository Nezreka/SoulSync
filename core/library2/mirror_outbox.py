"""Transactional outbox for lib2 → legacy wishlist/watchlist mirroring.

Audit P0-04 / ADR-02 (option 3): the lib2 monitor-flag change and the intent
to mirror it are committed in ONE transaction — the caller enqueues outbox
rows on its own connection before committing. A worker (``drain``) then
replays the rows against the legacy tables on separate connections. A mirror
failure keeps its row pending (with the error recorded) instead of being
swallowed, so the UI can show it and any later drain retries it.

The payload is fully resolved at enqueue time (from the same transaction's
snapshot), so a drain never needs the lib2 row to still exist — deletes can
enqueue their un-mirrors before removing the rows.

Ops are idempotent end to end: ``add_to_wishlist`` upserts (P1-09/P1-10),
removals are naturally idempotent, so a crash between "executed" and
"marked done" only causes a harmless replay.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.mirror_outbox")

# After this many failed attempts a row flips to 'failed' — still visible and
# manually retryable, but no longer hammered by every opportunistic drain.
MAX_ATTEMPTS = 10

# One drain at a time per process; ops are idempotent so this is about noise
# and SQLite write pressure, not correctness.
_drain_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Enqueue (caller's connection, caller's transaction — NO commit here)
# ---------------------------------------------------------------------------


def enqueue_tracks(conn, track_ids: List[int], monitored: bool, *,
                   profile_id: int = 1, user_initiated: bool = False) -> List[int]:
    """Queue wishlist add/remove mirrors for lib2 tracks.

    Runs on the CALLER's connection so the outbox rows commit atomically with
    the monitor-flag change. Returns the created outbox row ids.
    """
    from core.library2.wishlist_mirror import track_wishlist_payload

    outbox_ids: List[int] = []
    for tid in track_ids:
        # Payload construction is part of the authoritative monitor mutation's
        # transaction boundary.  Propagate failures so the caller can roll back
        # instead of committing a flag change with no retryable outbox intent.
        payload = track_wishlist_payload(conn, tid)
        if not payload:
            continue
        stype = "single" if payload.pop("_album_type", "") == "single" else "album"
        should_queue = bool(payload.pop("_should_queue", False))
        payload.pop("_source_album_id", "")
        source_info = payload.pop("_source_info", {})
        payload.pop("_has_file", None)
        if monitored:
            if not should_queue:
                continue
            op = "wishlist_add"
            data = {"payload": payload, "source_type": stype,
                    "source_info": source_info,
                    "quality_profile_id": payload.get("quality_profile_id")}
        else:
            op = "wishlist_remove"
            data = {"id": payload["id"]}
        cur = conn.execute(
            "INSERT INTO lib2_mirror_outbox(op, payload, profile_id, user_initiated) "
            "VALUES(?,?,?,?)",
            (op, json.dumps(data), profile_id, 1 if user_initiated else 0))
        outbox_ids.append(cur.lastrowid)
    return outbox_ids


def enqueue_projected_tracks(
    conn,
    track_ids: List[int],
    *,
    profile_id: int = 1,
    user_initiated: bool = False,
) -> List[int]:
    """Queue mirrors from authoritative wanted states, never flag guesses."""
    from core.library2.wanted import track_wanted_states
    states = track_wanted_states(conn, track_ids, profile_id=profile_id)
    outbox_ids: List[int] = []
    for wanted in (False, True):
        selected = [track_id for track_id, state in states.items() if state is wanted]
        if selected:
            outbox_ids.extend(enqueue_tracks(
                conn,
                selected,
                wanted,
                profile_id=profile_id,
                user_initiated=user_initiated,
            ))
    return outbox_ids


def enqueue_artist_watchlist(conn, artist_id: int, monitored: bool, *,
                             profile_id: int = 1) -> List[int]:
    """Queue a watchlist add/remove mirror for a lib2 artist (same-transaction)."""
    row = conn.execute(
        "SELECT name, spotify_id, musicbrainz_id, external_ids "
        "FROM lib2_artists WHERE id=?",
        (artist_id,)).fetchone()
    if not row:
        return []
    from core.library2.provider_ids import (
        preferred_provider_identity,
        source_ids_from_values,
    )
    from core.metadata.registry import get_primary_source, get_source_priority
    source, ext = preferred_provider_identity(
        source_ids_from_values(
            spotify_id=row["spotify_id"],
            musicbrainz_id=row["musicbrainz_id"],
            external_ids=row["external_ids"],
        ),
        get_source_priority(get_primary_source()),
    )
    if not ext:
        return []  # no external id → stays lib2-local only
    op = "watchlist_add" if monitored else "watchlist_remove"
    data = {"ext": ext, "name": row["name"], "source": source}
    cur = conn.execute(
        "INSERT INTO lib2_mirror_outbox(op, payload, profile_id) VALUES(?,?,?)",
        (op, json.dumps(data), profile_id))
    return [cur.lastrowid]


# ---------------------------------------------------------------------------
# Drain (worker: own connections, small per-row transactions)
# ---------------------------------------------------------------------------


def _execute_op(db, op: str, data: Dict[str, Any], profile_id: int,
                user_initiated: bool) -> None:
    """Replay one mirror op against the legacy tables. Raises on failure.

    A False return from add_to_wishlist is a legitimate terminal outcome
    (duplicate upserted in place, ignore-listed, blocklisted) — only
    exceptions count as failures to retry.
    """
    if op == "wishlist_add":
        db.add_to_wishlist(data.get("payload") or {},
                           source_type=data.get("source_type", "album"),
                           source_info=data.get("source_info") or {},
                           user_initiated=user_initiated,
                           profile_id=profile_id,
                           quality_profile_id=data.get("quality_profile_id"),
                           raise_on_error=True)
    elif op == "wishlist_remove":
        db.remove_from_wishlist(data.get("id"), profile_id,
                                raise_on_error=True)
    elif op == "watchlist_add":
        db.add_artist_to_watchlist(data.get("ext"), data.get("name"),
                                   profile_id, data.get("source"),
                                   raise_on_error=True)
    elif op == "watchlist_remove":
        db.remove_artist_from_watchlist(data.get("ext"), profile_id,
                                        raise_on_error=True)
    else:
        raise ValueError(f"Unknown mirror op: {op!r}")


def drain(db, *, limit: int = 500) -> Dict[str, int]:
    """Process pending outbox rows. Returns ``{"done": n, "failed": m}``.

    Safe to call from anywhere (request handlers, jobs): idempotent ops,
    per-row commits, serialized per process.
    """
    done = failed = 0
    with _drain_lock:
        conn = db._get_connection()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, op, payload, profile_id, user_initiated, attempts "
                "FROM lib2_mirror_outbox WHERE status='pending' ORDER BY id LIMIT ?",
                (limit,))]
        finally:
            conn.close()
        for row in rows:
            try:
                data = json.loads(row["payload"] or "{}")
                _execute_op(db, row["op"], data, row["profile_id"],
                            bool(row["user_initiated"]))
                error: Optional[str] = None
            except Exception as e:  # noqa: BLE001
                error = str(e) or e.__class__.__name__
            conn = db._get_connection()
            try:
                if error is None:
                    conn.execute(
                        "UPDATE lib2_mirror_outbox SET status='done', "
                        "attempts=attempts+1, last_error=NULL, "
                        "processed_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
                    done += 1
                else:
                    next_status = ("failed" if row["attempts"] + 1 >= MAX_ATTEMPTS
                                   else "pending")
                    conn.execute(
                        "UPDATE lib2_mirror_outbox SET status=?, attempts=attempts+1, "
                        "last_error=?, processed_at=CURRENT_TIMESTAMP WHERE id=?",
                        (next_status, error, row["id"]))
                    failed += 1
                    logger.warning("mirror outbox op %s (row %s) failed (attempt %d): %s",
                                   row["op"], row["id"], row["attempts"] + 1, error)
                conn.commit()
            finally:
                conn.close()
    return {"done": done, "failed": failed}


# ---------------------------------------------------------------------------
# Status / retry (UI visibility — the point of the whole exercise)
# ---------------------------------------------------------------------------


def outbox_status(conn) -> Dict[str, Any]:
    counts = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM lib2_mirror_outbox GROUP BY status")}
    errors = [dict(r) for r in conn.execute(
        "SELECT id, op, attempts, last_error, created_at FROM lib2_mirror_outbox "
        "WHERE status='failed' OR (status='pending' AND last_error IS NOT NULL) "
        "ORDER BY id DESC LIMIT 20")]
    return {
        "pending": counts.get("pending", 0),
        "failed": counts.get("failed", 0),
        "done": counts.get("done", 0),
        "recent_errors": errors,
    }


def retry_failed(conn) -> int:
    """Flip 'failed' rows back to 'pending' (manual retry). Caller commits."""
    cur = conn.execute(
        "UPDATE lib2_mirror_outbox SET status='pending', attempts=0 "
        "WHERE status='failed'")
    return cur.rowcount


def prune_done(conn, *, keep: int = 500) -> int:
    """Trim old completed rows so the table can't grow unbounded. Caller commits."""
    cur = conn.execute(
        "DELETE FROM lib2_mirror_outbox WHERE status='done' AND id NOT IN ("
        "SELECT id FROM lib2_mirror_outbox WHERE status='done' ORDER BY id DESC LIMIT ?)",
        (keep,))
    return cur.rowcount


__all__ = [
    "enqueue_tracks", "enqueue_projected_tracks", "enqueue_artist_watchlist", "drain",
    "outbox_status", "retry_failed", "prune_done", "MAX_ATTEMPTS",
]
