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
from contextlib import closing
from typing import Any, Dict, Iterable, List, Optional, Set

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


# wanted reasons that only restate what the upgrade imported: a file that was
# there, a flag of unknown origin. none of them is a person asking for upgrades
_IMPORTED_BASELINE_REASONS = frozenset({
    "track_rule:file_import", "track_rule:legacy_import",
    "album_rule:legacy_import", "artist_rule:legacy_import", "default_unmonitored",
})


def upgrade_opted_in(conn, track_id: int, profile_id: int = 1) -> bool:
    """Whether anything beyond the upgrade's own baseline wants this track:
    the track, its album or its artist monitored on purpose, a wishlist
    item, a new-release rule. dev never queued an upgrade or an album's
    missing tracks on its own. False only when the projection positively
    says the import is the only reason; a track with no projection row is
    left to the caller as before."""
    try:
        row = conn.execute(
            "SELECT reason FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=? AND wanted=1",
            (int(track_id), int(profile_id))).fetchone()
    except Exception as exc:  # noqa: BLE001 - no projection table: nothing to judge by
        logger.debug("upgrade opt-in lookup failed for track %s: %s", track_id, exc)
        return True
    return not (row and str(row[0] or "") in _IMPORTED_BASELINE_REASONS)


def intent_profile_ids(conn, *, track_ids: Iterable[int] = (),
                       album_ids: Iterable[int] = (),
                       artist_ids: Iterable[int] = ()) -> Set[int]:
    """Every profile with a wish or a rule on these rows.

    Each profile's wishlist holds its own row for a track, so taking rows out
    of the catalogue has to withdraw them for each of these profiles, not only
    for whoever clicked. The ids go in as one json parameter, an artist with a
    big catalogue would pass SQLite's variable limit otherwise.
    """
    groups = (("track", [int(i) for i in track_ids]),
              ("album", [int(i) for i in album_ids]),
              ("artist", [int(i) for i in artist_ids]))
    profiles: Set[int] = set()
    for entity_type, ids in groups:
        if not ids:
            continue
        payload = json.dumps(ids)
        rows = conn.execute(
            """SELECT profile_id FROM lib2_monitor_rules
                WHERE entity_type=? AND entity_id IN (SELECT value FROM json_each(?))""",
            (entity_type, payload)).fetchall()
        if entity_type == "track":
            rows += conn.execute(
                """SELECT profile_id FROM lib2_wanted_tracks
                    WHERE track_id IN (SELECT value FROM json_each(?))""",
                (payload,)).fetchall()
        profiles.update(int(r[0]) for r in rows if r[0] is not None)
    return profiles


def enqueue_tracks(conn, track_ids: List[int], monitored: bool, *,
                   profile_id: int = 1, user_initiated: bool = False) -> List[int]:
    """Queue wishlist add/remove mirrors for lib2 tracks.

    Runs on the CALLER's connection so the outbox rows commit atomically with
    the monitor-flag change. Returns the created outbox row ids.
    """
    from core.library2.wishlist_mirror import track_wishlist_payload
    from core.library_scope import library_scope, library_scope_for_profile

    outbox_ids: List[int] = []
    for tid in track_ids:
        # Payload construction is part of the authoritative monitor mutation's
        # transaction boundary.  Propagate failures so the caller can roll back
        # instead of committing a flag change with no retryable outbox intent.
        # Judged in the library this profile's wishlist fills (#1199).
        with library_scope(library_scope_for_profile(profile_id)):
            payload = track_wishlist_payload(conn, tid)
        if not payload:
            continue
        stype = "single" if payload.pop("_album_type", "") == "single" else "album"
        should_queue = bool(payload.pop("_should_queue", False))
        payload.pop("_source_album_id", "")
        source_info = payload.pop("_source_info", {})
        has_file = bool(payload.pop("_has_file", None))
        if (monitored and should_queue and not user_initiated
                and not upgrade_opted_in(conn, tid, profile_id)):
            # nothing goes on the wishlist by itself unless someone asked for
            # it: the track, its album or its artist monitored on purpose, a
            # wishlist item, a new-release rule. the upgrade's own baseline
            # isn't asking. without this it queued every mp3 in a 300k library
            # for an upgrade, then every missing track of every album it had
            # fetched a tracklist for (280k). the branch below takes off one
            # an older run already added
            should_queue = False
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


def _shared_library_member_profile(db, profile_id: int) -> bool:
    """a non-admin profile reading the shared library (its wishes are its own)"""
    try:
        if int(profile_id) == 1:
            return False
        from core.library_scope import library_scope_for_profile
        from core.profile_context import is_admin_profile
        return (library_scope_for_profile(int(profile_id)) == "shared"
                and not is_admin_profile(int(profile_id), database=db))
    except Exception:  # noqa: BLE001 - unknown: treat as a member, never auto-approve
        return True


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


def profile_asks_first(db, profile_id) -> bool:
    """Is this a profile whose wishlist rows are requests (no download rights,
    upstream core/requests/music.py)? Profile 1 never is."""
    from core.permissions import profile_can_download
    try:
        if not profile_id or int(profile_id) == 1:
            return False
        return not profile_can_download(db.get_profile(int(profile_id)))
    except Exception:  # noqa: BLE001 - unreadable: treat the row as an ordinary add
        return False


def _execute_op(db, op: str, data: Dict[str, Any], profile_id: int,
                user_initiated: bool) -> None:
    """Replay one mirror op against the legacy tables. Raises on failure.

    A False return from add_to_wishlist is a legitimate terminal outcome
    (duplicate upserted in place, ignore-listed, blocklisted) — only
    exceptions count as failures to retry.
    """
    if op == "wishlist_add":
        # A profile without download rights keeps a wishlist of requests the
        # scheduled run leaves alone until an admin approves them (upstream
        # core/requests/music.py). What this mirror writes is never such an
        # ask: that profile cannot monitor (E-13, wishes_in_own_library), so
        # the intent is the admin's -- made in its library -- and lands
        # approved instead of waiting for an approval nobody is asked for.
        payload = data.get("payload") or {}
        # ...except a household member in the SHARED library: they monitor
        # on their own watchlist and wishlist now (X1), and what they ask for
        # is a request an admin approves, as in 3.6.1. auto-approving it let
        # an ask-first member's Library click download without approval
        approved = profile_asks_first(db, profile_id) and not _shared_library_member_profile(db, profile_id)
        # Why the row is there, not the default "Download failed": nothing was
        # tried yet when the catalogue asks for a missing track or an upgrade.
        evaluation = (data.get("source_info") or {}).get("quality_evaluation")
        reason = {"upgrade_candidate": "Quality upgrade wanted",
                  "unknown": "Checking for a quality upgrade"}.get(evaluation, "Missing from library")
        db.add_to_wishlist(payload,
                           failure_reason=reason,
                           source_type=data.get("source_type", "album"),
                           source_info=data.get("source_info") or {},
                           user_initiated=user_initiated,
                           profile_id=profile_id,
                           quality_profile_id=data.get("quality_profile_id"),
                           raise_on_error=True,
                           **({"request_approved": True} if approved else {}))
        if approved:
            from core.wishlist.identity import wishlist_key_from_payload
            row_key = wishlist_key_from_payload(payload) or payload.get("id")
            if row_key:
                db.approve_request_rows(profile_id, [row_key])
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


def _row_key(row) -> Optional[tuple]:
    """``_entity_key`` for one outbox row, per profile: one profile's newer op
    must never cancel another profile's wish or watch on the same entity (an
    admin's remove dropped a member's pending add)."""
    key = _entity_key(row["op"], json.loads(row["payload"] or "{}"))
    if key is None:
        return None
    try:
        profile = int(row["profile_id"] or 1)
    except (IndexError, KeyError, TypeError, ValueError):
        profile = 1
    return key + (profile,)


def _superseded_ids(conn, rows: List[Dict[str, Any]]) -> set:
    """Pending rows that a LATER row for the same entity already overrides.

    dd28-13: ``drain`` iterates by id but does not stop at the first failure.
    If row 100 (``wishlist_add`` for track T) failed transiently while row 101
    (``wishlist_remove`` for T) succeeded, row 100 stayed pending and the next
    drain replayed it — resurrecting a wishlist entry the user had just
    removed. ``retry_failed`` made it worse by resetting arbitrarily old failed
    rows with no ordering check at all. Rather than serializing the whole
    drain behind one stuck entity, drop the rows that are provably obsolete.

    Each row carries its ``entity_key`` (schema trigger), so this is one
    indexed lookup per batch. It used to json-parse every row newer than the
    oldest pending one, which with a backlog was the whole backlog, per drain.
    A row without a key (a build before the column, before the backfill ran)
    falls back to parsing just that row.
    """
    keyed: Dict[int, str] = {}
    for row in rows:
        key = row.get("entity_key")
        if key is None:
            try:
                parts = _row_key(row)
            except (TypeError, ValueError):
                parts = None
            if parts is None:
                continue
            key = "|".join(str(p) for p in parts)
        keyed[int(row["id"])] = str(key)
    if not keyed:
        return set()
    newest = {
        str(r[0]): int(r[1]) for r in conn.execute(
            "SELECT entity_key, MAX(id) FROM lib2_mirror_outbox "
            "WHERE entity_key IN (SELECT value FROM json_each(?)) GROUP BY entity_key",
            (json.dumps(sorted(set(keyed.values()))),))
    }
    return {row_id for row_id, key in keyed.items() if newest.get(key, 0) > row_id}


_SIBLING_OPS = {
    "wishlist": ("wishlist_add", "wishlist_remove"),
    "watchlist": ("watchlist_add", "watchlist_remove"),
}


# a row that failed waits before its next try: 30s, doubling, an hour at most.
# without it the background drainer took the same failing row first in every
# batch, 0.1s apart, so a transient error ("database is locked") spent all
# MAX_ATTEMPTS in about a second and the wishlist write was failed for good.
# a drain of the caller's own rows (ids=) is not held back.
_RETRY_DUE = ("(attempts = 0 OR processed_at IS NULL OR processed_at <= "
              "datetime('now', '-' || MIN(30 * (1 << MIN(attempts - 1, 7)), 3600) || ' seconds'))")


def drain(db, *, limit: int = 500, ids: Optional[Iterable[int]] = None) -> Dict[str, int]:
    """Process pending outbox rows. Returns ``{"done": n, "failed": m}``.

    Safe to call from anywhere (request handlers, jobs): idempotent ops,
    per-row commits, serialized per process.

    ``ids``: only these rows (the ones the caller just queued), then the rest
    of the queue moves on in the background. A request that drained without
    them worked through the OLDEST pending rows first, so behind a backlog a
    click waited on hundreds of other wishlist writes, and every other request
    that drained queued up behind it on the lock.
    """
    done = failed = superseded = 0
    wanted_ids = None if ids is None else sorted({int(i) for i in ids})
    if wanted_ids is not None and not wanted_ids:
        return {"done": 0, "failed": 0, "superseded": 0}
    with _drain_lock:
        with closing(db._get_connection()) as conn:
            columns = "id, op, payload, profile_id, user_initiated, attempts"
            if _has_entity_key(conn):
                columns += ", entity_key"
            if wanted_ids is None:
                rows = [dict(r) for r in conn.execute(
                    f"SELECT {columns} FROM lib2_mirror_outbox "
                    f"WHERE status='pending' AND {_RETRY_DUE} ORDER BY id LIMIT ?", (limit,))]
            else:
                rows = [dict(r) for r in conn.execute(
                    f"SELECT {columns} FROM lib2_mirror_outbox "
                    "WHERE status='pending' AND id IN (SELECT value FROM json_each(?)) "
                    "ORDER BY id", (json.dumps(wanted_ids),))]
            obsolete = _superseded_ids(conn, rows)
        if obsolete:
            with closing(db._get_connection()) as conn:
                marks = ",".join("?" for _ in obsolete)
                conn.execute(
                    f"""UPDATE lib2_mirror_outbox
                           SET status='superseded', last_error=NULL,
                               processed_at=CURRENT_TIMESTAMP
                         WHERE id IN ({marks})""",
                    tuple(obsolete),
                )
                conn.commit()
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
            with closing(db._get_connection()) as conn:
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

    if wanted_ids is not None:
        drain_soon(db)
    return {"done": done, "failed": failed, "superseded": superseded}


_key_column: Dict[str, bool] = {}


def _has_entity_key(conn) -> bool:
    try:
        path = next((str(r[2]) for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "")
    except Exception:  # noqa: BLE001
        path = ""
    if path and path in _key_column:
        return _key_column[path]
    present = "entity_key" in {r[1] for r in conn.execute("PRAGMA table_info(lib2_mirror_outbox)")}
    if path and present:
        _key_column[path] = True
    return present


# The queue's background drainer. Small batches, the lock released between
# them, so a request draining its own rows waits for one small batch at most.
BACKGROUND_BATCH = 50
BACKGROUND_PAUSE = 0.1
_background_lock = threading.Lock()
_background: Dict[str, Any] = {"thread": None}


def queue_mark(conn) -> int:
    """The newest outbox row id now: pass it to ``drain_since`` after a change
    that queues rows through code that doesn't hand their ids back."""
    return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM lib2_mirror_outbox").fetchone()[0])


def drain_since(db, mark: int) -> Dict[str, int]:
    """Drain the pending rows queued after ``mark`` (a delete's own
    withdrawals), not the oldest rows of whatever backlog sits in front."""
    with closing(db._get_connection()) as conn:
        ids = [int(r[0]) for r in conn.execute(
            "SELECT id FROM lib2_mirror_outbox WHERE id > ? AND status='pending'", (int(mark),))]
    return drain(db, ids=ids)


# the test suite turns it off: a thread outliving its test would drain a
# temporary database that is already gone
BACKGROUND_DRAIN = True


def drain_soon(db) -> bool:
    """Work through the pending queue in the background; True when a drainer
    was started. One per process: a drainer already running picks up what
    was queued since. It stops when the queue is empty or a batch got nothing
    done (failing rows keep their spaced-out retries for a later drain)."""
    if not BACKGROUND_DRAIN:
        return False
    with _background_lock:
        thread = _background.get("thread")
        if thread is not None and thread.is_alive():
            return False

        def _run():
            import time
            try:
                while True:
                    result = drain(db, limit=BACKGROUND_BATCH)
                    if not (result.get("done") or result.get("superseded")):
                        break
                    time.sleep(BACKGROUND_PAUSE)
            except Exception as e:  # noqa: BLE001 - the next drain picks it up
                logger.warning("mirror outbox background drain stopped: %s", e)

        thread = threading.Thread(target=_run, daemon=True, name="lib2-mirror-drain")
        _background["thread"] = thread
        thread.start()
        return True


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
    columns = "id, op, payload, profile_id" + (", entity_key" if _has_entity_key(conn) else "")
    failed = [dict(row) for row in conn.execute(
        f"SELECT {columns} FROM lib2_mirror_outbox WHERE status='failed' ORDER BY id")]
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
    if _has_entity_key(conn):
        # keyed rows answer "is this row still load-bearing?" in the DELETE,
        # off the index. the Python pass below parsed every pending and failed
        # row and every newer one, after every drain that did anything: twice
        # the backlog, per drain, behind a backlog
        cur = conn.execute(
            """DELETE FROM lib2_mirror_outbox
                WHERE status='done'
                  AND id NOT IN (SELECT id FROM lib2_mirror_outbox WHERE status='done'
                                  ORDER BY id DESC LIMIT ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM lib2_mirror_outbox stuck
                       WHERE stuck.entity_key = lib2_mirror_outbox.entity_key
                         AND stuck.id < lib2_mirror_outbox.id
                         AND stuck.status IN ('pending', 'failed'))""",
            (keep,))
        return cur.rowcount
    # no key column (a table an older schema made): parse the payloads
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
        "SELECT id, op, payload, profile_id FROM lib2_mirror_outbox "
        "WHERE status IN ('pending','failed')"
    ):
        try:
            key = _row_key(row)
        except (TypeError, ValueError):
            continue
        if key is None:
            continue
        stuck[key] = min(stuck.get(key, int(row["id"])), int(row["id"]))
    if not stuck:
        return set()

    protected: set = set()
    for row in conn.execute(
        "SELECT id, op, payload, profile_id FROM lib2_mirror_outbox WHERE id > ?",
        (min(stuck.values()),),
    ):
        try:
            key = _row_key(row)
        except (TypeError, ValueError):
            continue
        if key is not None and key in stuck and int(row["id"]) > stuck[key]:
            protected.add(int(row["id"]))
    return protected


__all__ = [
    "enqueue_tracks", "enqueue_projected_tracks", "enqueue_artist_watchlist", "drain",
    "drain_soon", "drain_since", "queue_mark",
    "intent_profile_ids",
    "outbox_status", "retry_failed", "prune_done", "MAX_ATTEMPTS",
]
