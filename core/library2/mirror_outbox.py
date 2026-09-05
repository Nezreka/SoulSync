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


def _legacy_wishlist_row_exists(conn, wishlist_id: Any, profile_id: int,
                                album_id: Any = None) -> bool:
    """Whether the legacy Wishlist currently holds this release (dd28-41).

    SYNC-02 trigger A: the probe asked for the bare track id while the insert
    had stored ``<track>::<album>``, so it never found the row and the mirror
    skipped withdrawing a wish the library had already satisfied — leaving the
    processor downloading a track that was fine.

    A bare-keyed row still counts as this release under the same rule the
    removal uses: it names this album, or it names none at all (pre-composite
    rows, and rows written by writers outside Library v2).

    Best-effort: an install without the table (or mid-migration) reports False,
    which only means "nothing to withdraw".
    """
    try:
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(wishlist_tracks)")
        }
        if not columns:
            return False
        from core.wishlist.identity import wishlist_row_key

        bare = str(wishlist_id or "").strip()
        album = str(album_id or "").strip()
        release_key = wishlist_row_key(bare, album)
        candidates = [release_key] if release_key == bare else [release_key, bare]
        marks = ",".join("?" for _ in candidates)
        has_data = "spotify_data" in columns
        select = "spotify_track_id, spotify_data" if has_data else "spotify_track_id, NULL"
        if "profile_id" in columns:
            rows = conn.execute(
                f"SELECT {select} FROM wishlist_tracks "
                f"WHERE spotify_track_id IN ({marks}) AND profile_id=?",
                (*candidates, profile_id),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {select} FROM wishlist_tracks "
                f"WHERE spotify_track_id IN ({marks})",
                tuple(candidates),
            ).fetchall()
        for row in rows:
            key = str(row[0] or "")
            if key == release_key:
                return True
            try:
                stored = json.loads(row[1] or "{}")
                stored_album = str((stored.get("album") or {}).get("id") or "").strip()
            except Exception:  # noqa: BLE001
                stored_album = ""
            if stored_album in ("", album):
                return True
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("wishlist presence check skipped for %s: %s", wishlist_id, exc)
        return False


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
        # SYNC-02/03: the wishlist stores a RELEASE of a recording. Derive that
        # key once, here, so the add, the presence probe, the remove and the
        # outbox's supersession all speak about the same row instead of three
        # of them falling back to the bare track id.
        from core.wishlist.identity import wishlist_key_from_payload
        release_key = wishlist_key_from_payload(payload) or payload["id"]
        if monitored and should_queue:
            op = "wishlist_add"
            data = {"payload": payload, "source_type": stype,
                    "source_info": source_info,
                    "key": release_key,
                    "quality_profile_id": payload.get("quality_profile_id")}
        elif monitored:
            # dd28-41: a monitored track with nothing to acquire right now (it
            # already has a satisfying file) used to enqueue NOTHING. If it was
            # ALREADY on the Wishlist — a file appeared outside SoulSync, or the
            # profile's cutoff was lowered so the existing file now satisfies it
            # — nothing ever took it off again and the wishlist processor kept
            # trying to download a track that was fine. Withdrawing is safe: the
            # moment it becomes an upgrade candidate again the projection re-adds
            # it. Only emitted when a row actually exists, so the steady state
            # does not fill the outbox with no-op removes.
            _album = payload.get("album")
            if not _legacy_wishlist_row_exists(
                conn, payload["id"], profile_id,
                _album.get("id") if isinstance(_album, dict) else None,
            ):
                continue
            op = "wishlist_remove"
            data = _remove_payload(payload, release_key)
        else:
            op = "wishlist_remove"
            data = _remove_payload(payload, release_key)
        cur = conn.execute(
            "INSERT INTO lib2_mirror_outbox(op, payload, profile_id, user_initiated) "
            "VALUES(?,?,?,?)",
            (op, json.dumps(data), profile_id, 1 if user_initiated else 0))
        outbox_ids.append(cur.lastrowid)
    return outbox_ids


def _remove_payload(payload: Dict[str, Any], release_key: str) -> Dict[str, Any]:
    """The withdrawal op for exactly the release this payload describes.

    ``id`` stays the bare track id so an older drain still understands the row;
    ``album_id`` is what narrows the removal to this release, and ``key`` is
    what supersession compares (SYNC-02/03).
    """
    album = payload.get("album")
    album_id = album.get("id") if isinstance(album, dict) else None
    return {"id": payload["id"], "album_id": album_id, "key": release_key}


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
    if monitored:
        # Split-doc contract (branch-split/LIBRARY_OVERHAUL.md "Library v2
        # integration after rebase"): the native Watchlist add takes one
        # explicit quality_profile_id. Push the artist's current effective
        # catalog profile (Track→Album→Artist→Global, guide §2.3) at the
        # moment monitoring turns on. This is a one-time push, not a live
        # link — a later catalog profile edit does not retroactively change
        # an already-monitored artist's Watchlist assignment.
        from core.library2.profile_lookup import effective_quality_profile
        data["quality_profile_id"] = effective_quality_profile(
            conn, "artists", artist_id)["id"]
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
        # SYNC-02: rows queued with an album withdraw exactly that release.
        # Rows without one (queued by an older build, or a track whose release
        # has no identity) keep the original recording-wide removal.
        if data.get("album_id"):
            db.remove_release_from_wishlist(
                data.get("id"), data.get("album_id"), profile_id,
                raise_on_error=True)
        else:
            db.remove_from_wishlist(data.get("id"), profile_id,
                                    raise_on_error=True)
    elif op == "watchlist_add":
        db.add_artist_to_watchlist(data.get("ext"), data.get("name"),
                                   profile_id, data.get("source"),
                                   quality_profile_id=data.get("quality_profile_id"),
                                   raise_on_error=True)
    elif op == "watchlist_remove":
        db.remove_artist_from_watchlist(data.get("ext"), profile_id,
                                        raise_on_error=True)
    else:
        raise ValueError(f"Unknown mirror op: {op!r}")


def _entity_key(op: str, data: Dict[str, Any]) -> Optional[tuple]:
    """The mirrored entity a row asserts state for, or ``None`` if unknown.

    Ops are absolute assertions about one entity ("this track is wishlisted
    with this payload" / "it is not"), never deltas — so for a given entity
    only the newest row describes the intended end state (dd28-13).
    """
    if op in ("wishlist_add", "wishlist_remove"):
        # SYNC-03: two wanted releases of one recording are two independent
        # intents. Keying both on the bare track id made the second album look
        # like a newer assertion about the first, and its add was dropped
        # before it was ever persisted. ``key`` is the release key both ops now
        # carry; the bare-id fallback keeps rows queued by an older build (they
        # survive a restart in the outbox) working exactly as before.
        target = data.get("key")
        if target is None:
            target = (data.get("payload") or {}).get("id") if op == "wishlist_add" \
                else data.get("id")
        return ("wishlist", target) if target is not None else None
    if op in ("watchlist_add", "watchlist_remove"):
        target = data.get("ext")
        return ("watchlist", target) if target is not None else None
    return None


def _superseded_ids(conn, rows: List[Dict[str, Any]]) -> set:
    """Pending rows that a LATER row for the same entity already overrides.

    dd28-13: ``drain`` iterates by id but does not stop at the first failure.
    If row 100 (``wishlist_add`` for track T) failed transiently while row 101
    (``wishlist_remove`` for T) succeeded, row 100 stayed pending and the next
    drain replayed it — resurrecting a wishlist entry the user had just
    removed. ``retry_failed`` made it worse by resetting arbitrarily old failed
    rows with no ordering check at all. Rather than serializing the whole
    drain behind one stuck entity, drop the rows that are provably obsolete.
    """
    pending: list[tuple[int, tuple]] = []
    families: set[str] = set()
    for row in rows:
        try:
            key = _entity_key(row["op"], json.loads(row["payload"] or "{}"))
        except (TypeError, ValueError):
            key = None
        if key is None:
            continue
        pending.append((row["id"], key))
        families.add(key[0])
    if not pending:
        return set()

    # One pass over the candidate rows instead of a query per pending row: the
    # newest id per entity is all that matters, and a drain batch is small.
    lowest_pending = min(row_id for row_id, _ in pending)
    newest_by_key: Dict[tuple, int] = {}
    ops = sorted({op for family in families for op in _SIBLING_OPS[family]})
    marks = ",".join("?" for _ in ops)
    for candidate in conn.execute(
        f"""SELECT id, op, payload FROM lib2_mirror_outbox
             WHERE id > ? AND op IN ({marks}) ORDER BY id""",
        (lowest_pending, *ops),
    ):
        try:
            key = _entity_key(candidate["op"], json.loads(candidate["payload"] or "{}"))
        except (TypeError, ValueError):
            continue
        if key is None:
            continue
        newest_by_key[key] = max(newest_by_key.get(key, 0), int(candidate["id"]))

    return {
        row_id for row_id, key in pending
        if newest_by_key.get(key, 0) > row_id
    }


_SIBLING_OPS = {
    "wishlist": ("wishlist_add", "wishlist_remove"),
    "watchlist": ("watchlist_add", "watchlist_remove"),
}


def drain(db, *, limit: int = 500) -> Dict[str, int]:
    """Process pending outbox rows. Returns ``{"done": n, "failed": m}``.

    Safe to call from anywhere (request handlers, jobs): idempotent ops,
    per-row commits, serialized per process.
    """
    done = failed = superseded = 0
    with _drain_lock:
        conn = db._get_connection()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, op, payload, profile_id, user_initiated, attempts "
                "FROM lib2_mirror_outbox WHERE status='pending' ORDER BY id LIMIT ?",
                (limit,))]
            obsolete = _superseded_ids(conn, rows)
        finally:
            conn.close()
        if obsolete:
            conn = db._get_connection()
            try:
                marks = ",".join("?" for _ in obsolete)
                conn.execute(
                    f"""UPDATE lib2_mirror_outbox
                           SET status='superseded', last_error=NULL,
                               processed_at=CURRENT_TIMESTAMP
                         WHERE id IN ({marks})""",
                    tuple(obsolete),
                )
                conn.commit()
            finally:
                conn.close()
            superseded = len(obsolete)
            logger.info(
                "mirror outbox: skipped %d row(s) overridden by a newer op",
                superseded,
            )
            rows = [r for r in rows if r["id"] not in obsolete]
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

    # iss29-D11: prune here, on the path that actually runs.
    #
    # `prune_done` was only ever called from the monitoring-list reconcile job
    # and from the manual retry endpoint, so on a normal install the completed
    # rows were never trimmed — the production database already holds 771
    # `done` rows against `keep=500`. That history is not inert: `_superseded_ids`
    # scans it on every drain, so the cost of a drain grows with every mirror
    # op ever performed. Trimming after a pass that actually did something
    # keeps the table bounded without adding a write to a no-op drain.
    if done or failed or superseded:
        conn = db._get_connection()
        try:
            pruned = prune_done(conn)
            conn.commit()
            if pruned:
                logger.debug("mirror outbox: pruned %d completed row(s)", pruned)
        except Exception as prune_err:  # noqa: BLE001 — housekeeping, never fatal
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001, S110 - a rollback that itself fails
                pass           # adds nothing; prune_err is logged below.
            logger.debug("mirror outbox prune skipped: %s", prune_err)
        finally:
            conn.close()

    return {"done": done, "failed": failed, "superseded": superseded}


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
    """Flip current failed rows back to pending; retire obsolete ones first."""
    failed = [dict(row) for row in conn.execute(
        "SELECT id, op, payload FROM lib2_mirror_outbox "
        "WHERE status='failed' ORDER BY id")]
    obsolete = _superseded_ids(conn, failed)
    if obsolete:
        marks = ",".join("?" for _ in obsolete)
        conn.execute(
            f"UPDATE lib2_mirror_outbox SET status='superseded', last_error=NULL, "
            f"processed_at=CURRENT_TIMESTAMP WHERE id IN ({marks})",
            tuple(obsolete),
        )
    cur = conn.execute(
        "UPDATE lib2_mirror_outbox SET status='pending', attempts=0 "
        "WHERE status='failed'")
    return cur.rowcount


def prune_done(conn, *, keep: int = 500) -> int:
    """Trim old completed rows so the table can't grow unbounded. Caller commits.

    A ``done`` row is never pruned while a NON-TERMINAL row (``pending`` /
    ``failed``) with an older id exists for the same entity. ``_superseded_ids``
    decides "this stuck row is obsolete" by finding a later row for the same
    entity key -- including a ``done`` one -- so pruning that later row destroys
    the only evidence of the supersede. Row 100 = wishlist_add(T) failed; row
    101 = wishlist_remove(T) succeeded; prune 101, hit "Retry failed", and the
    drain replays 100 and resurrects the entry the user removed. That is exactly
    the dd28-13 failure the supersede logic exists to prevent.
    """
    # The entity key lives inside the JSON payload, so the "is this row still
    # load-bearing?" question is answered in Python rather than in the DELETE.
    protected = _ids_superseding_stuck_rows(conn)
    exclude = ""
    params: list = [keep]
    if protected:
        exclude = f" AND id NOT IN ({','.join('?' for _ in protected)})"
        params.extend(sorted(protected))
    cur = conn.execute(
        "DELETE FROM lib2_mirror_outbox WHERE status='done' AND id NOT IN ("
        "SELECT id FROM lib2_mirror_outbox WHERE status='done' "
        "ORDER BY id DESC LIMIT ?)" + exclude,
        params)
    return cur.rowcount


def _ids_superseding_stuck_rows(conn) -> set:
    """Ids of rows that are the reason some older non-terminal row is obsolete.

    Deleting one of these silently re-arms it: the next ``drain`` re-derives
    "is row X superseded?" from history, finds nothing later for that entity,
    and executes it.
    """
    stuck: Dict[tuple, int] = {}
    for row in conn.execute(
        "SELECT id, op, payload FROM lib2_mirror_outbox "
        "WHERE status IN ('pending','failed')"
    ):
        try:
            key = _entity_key(row["op"], json.loads(row["payload"] or "{}"))
        except (TypeError, ValueError):
            continue
        if key is None:
            continue
        stuck[key] = min(stuck.get(key, int(row["id"])), int(row["id"]))
    if not stuck:
        return set()

    protected: set = set()
    for row in conn.execute(
        "SELECT id, op, payload FROM lib2_mirror_outbox WHERE id > ?",
        (min(stuck.values()),),
    ):
        try:
            key = _entity_key(row["op"], json.loads(row["payload"] or "{}"))
        except (TypeError, ValueError):
            continue
        if key is not None and key in stuck and int(row["id"]) > stuck[key]:
            protected.add(int(row["id"]))
    return protected


__all__ = [
    "enqueue_tracks", "enqueue_projected_tracks", "enqueue_artist_watchlist", "drain",
    "outbox_status", "retry_failed", "prune_done", "MAX_ATTEMPTS",
]
