"""Watch Parties (Watch Together) for SoulSync Player.

Supports:
- Instant Theater Lobbies (waiting room with synced music from the SoulSync library,
  Ready checkmarks, live chat & emoji reactions)
- Scheduled Watch Parties (e.g. upcoming movie nights or new episode premieres,
  with countdown timers & RSVPs)
- Synchronized Playback across Desktop Web and Mobile apps (host-only or everyone
  play/pause/seek control, server-anchored drift-free `live_position`)
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Callable, Dict, List, Optional

from core.player.progress import ProgressError, clean_item_id, clean_item_type, clean_meta

PARTIES_TABLE = "player_watch_parties"
MEMBERS_TABLE = "player_watch_party_members"
MESSAGES_TABLE = "player_watch_party_messages"

ALLOWED_ITEM_TYPES = ("movie", "episode", "youtube")
ALLOWED_STATUSES = ("scheduled", "lobby", "playing", "paused", "ended")
ALLOWED_CONTROL_MODES = ("host", "everyone")
ALLOWED_MESSAGE_KINDS = ("chat", "reaction", "system")

MAX_MESSAGES = 80
STALE_LOBBY_SECONDS = 6 * 3600
STALE_PLAYING_SECONDS = 8 * 3600

# Emoji reaction burst rate limit: max 5 reactions per 4 seconds per profile
REACTION_BURST_LIMIT = 5
REACTION_BURST_WINDOW_SEC = 4.0

# Pee-break intermission constants
PEE_BREAK_DURATION_SEC = 5 * 60.0       # 5-minute intermission timer
PEE_BREAK_COOLDOWN_SEC = 10 * 60.0      # each member can call a pee break once every 10 minutes
RESUME_COUNTDOWN_SEC = 5.0              # 5-second synchronized countdown before playback resumes

_ready: set = set()
_pee_cooldowns: Dict[tuple, float] = {}  # (party_id, profile_id) -> last pee_break epoch
_reaction_timestamps: Dict[tuple, List[float]] = {}  # (party_id, profile_id) -> recent reaction epochs


class PartyError(ValueError):
    pass


def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {PARTIES_TABLE} (
                id TEXT PRIMARY KEY,
                host_profile_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                meta_json TEXT,
                show_id INTEGER,
                status TEXT NOT NULL DEFAULT 'lobby',
                scheduled_for REAL,
                control_mode TEXT NOT NULL DEFAULT 'host',
                position REAL NOT NULL DEFAULT 0.0,
                position_at REAL NOT NULL,
                lobby_track_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {PARTIES_TABLE}_active "
            f"ON {PARTIES_TABLE} (status, updated_at DESC)"
        )
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {MEMBERS_TABLE} (
                party_id TEXT NOT NULL,
                profile_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'joined',
                ready INTEGER NOT NULL DEFAULT 0,
                joined_at REAL NOT NULL,
                seen_at REAL NOT NULL,
                PRIMARY KEY (party_id, profile_id)
            )""")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {MESSAGES_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                party_id TEXT NOT NULL,
                profile_id INTEGER NOT NULL,
                kind TEXT NOT NULL DEFAULT 'chat',
                body TEXT NOT NULL,
                created_at REAL NOT NULL
            )""")
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {MESSAGES_TABLE}_party "
            f"ON {MESSAGES_TABLE} (party_id, id DESC)"
        )
        conn.commit()
    _ready.add(key)


def _clean_item(item_type: Any, item_id: Any) -> tuple:
    try:
        kind = clean_item_type(item_type)
        ident = clean_item_id(item_id)
    except ProgressError as e:
        raise PartyError(str(e)) from e
    if kind not in ALLOWED_ITEM_TYPES:
        raise PartyError(f"watch parties support {', '.join(ALLOWED_ITEM_TYPES)}")
    return kind, ident


def _clean_lobby_track(raw: Any, now: float) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    tid = raw.get("track_id")
    if tid is None or str(tid).strip() == "":
        return None
    dur = raw.get("duration_ms")
    dur_ms = int(dur) if isinstance(dur, (int, float)) and not isinstance(dur, bool) and dur > 0 else None
    album_id = raw.get("album_id")
    aid = int(album_id) if isinstance(album_id, int) and not isinstance(album_id, bool) else None
    return {
        "track_id": str(tid)[:64],
        "title": str(raw.get("title") or "Lobby Music")[:160],
        "artist": str(raw.get("artist") or "")[:120],
        "album_id": aid,
        "artwork": str(raw.get("artwork") or "")[:300] or None,
        "duration_ms": dur_ms,
        "started_at": float(raw.get("started_at") or now),
    }


def _decode_json(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else None
    except (TypeError, ValueError):
        return None


def _compute_live_position(status: str, position: float, position_at: float, now: float) -> float:
    if status == "playing":
        # If position_at is in the future (e.g. 5-second resume countdown), hold position until position_at
        if now < float(position_at):
            return max(0.0, float(position))
        return max(0.0, float(position) + max(0.0, now - float(position_at)))
    return max(0.0, float(position))


def _hydrate_party(conn: Any, row: tuple, viewer_id: int, now: float, *, include_messages: bool) -> Dict[str, Any]:
    (
        pid, host_id, title, item_type, item_id, meta_json, show_id,
        status, scheduled_for, control_mode, position, position_at,
        lobby_track_json, created_at, updated_at,
    ) = row

    # Auto-promote scheduled parties into lobby when within 15 minutes of start time
    effective_status = status
    if status == "scheduled" and scheduled_for is not None and now >= (scheduled_for - 900.0):
        effective_status = "lobby"

    members_rows = conn.execute(
        f"SELECT profile_id, status, ready, joined_at, seen_at FROM {MEMBERS_TABLE} "
        "WHERE party_id = ? ORDER BY joined_at ASC",
        (pid,),
    ).fetchall()

    members = [
        {
            "profile_id": int(m[0]),
            "status": m[1],
            "ready": bool(m[2]),
            "joined_at": float(m[3]),
            "seen_at": float(m[4]),
            "is_host": int(m[0]) == int(host_id),
            "you": int(m[0]) == int(viewer_id),
        }
        for m in members_rows
    ]

    messages: List[Dict[str, Any]] = []
    if include_messages:
        msg_rows = conn.execute(
            f"SELECT id, profile_id, kind, body, created_at FROM {MESSAGES_TABLE} "
            "WHERE party_id = ? ORDER BY id DESC LIMIT ?",
            (pid, MAX_MESSAGES),
        ).fetchall()
        messages = [
            {
                "id": int(m[0]),
                "profile_id": int(m[1]),
                "kind": m[2],
                "body": m[3],
                "created_at": float(m[4]),
                "you": int(m[1]) == int(viewer_id),
            }
            for m in reversed(msg_rows)
        ]

    lobby_track = _decode_json(lobby_track_json)
    lobby_pos = 0.0
    if lobby_track and effective_status in ("lobby", "scheduled"):
        started = float(lobby_track.get("started_at") or created_at)
        elapsed = max(0.0, now - started)
        dur_s = (lobby_track.get("duration_ms") or 0) / 1000.0
        lobby_pos = (elapsed % dur_s) if dur_s > 5.0 else elapsed
        lobby_track["live_position"] = round(lobby_pos, 2)

    raw_meta = _decode_json(meta_json) or {}
    pee_break = raw_meta.get("pee_break") if isinstance(raw_meta.get("pee_break"), dict) else None
    resume_at = float(raw_meta["resume_at"]) if isinstance(raw_meta.get("resume_at"), (int, float)) else None
    if resume_at is not None and now >= resume_at:
        resume_at = None

    last_pee = _pee_cooldowns.get((pid, int(viewer_id)), 0.0)
    pee_cooldown_remaining = max(0, int(round(PEE_BREAK_COOLDOWN_SEC - (now - last_pee)))) if last_pee > 0 else 0

    is_member = any(m["profile_id"] == int(viewer_id) for m in members)
    is_host = int(host_id) == int(viewer_id)
    member_count = len(members)
    ready_count = sum(1 for m in members if m["ready"])
    majority_ready = member_count > 0 and (ready_count * 2 > member_count)

    clean_display_meta = {k: v for k, v in raw_meta.items() if k not in ("pee_break", "resume_at")}

    return {
        "id": pid,
        "host_profile_id": int(host_id),
        "is_host": is_host,
        "is_member": is_member,
        "can_control": is_host or control_mode == "everyone" or (effective_status in ("lobby", "scheduled") and majority_ready),
        "majority_ready": majority_ready,
        "title": title,
        "item_type": item_type,
        "item_id": item_id,
        "meta": clean_display_meta,
        "pee_break": pee_break,
        "resume_at": resume_at,
        "pee_cooldown_remaining": pee_cooldown_remaining,
        "show_id": show_id,
        "status": effective_status,
        "scheduled_for": scheduled_for,
        "control_mode": control_mode,
        "position": float(position),
        "position_at": float(position_at),
        "live_position": round(_compute_live_position(effective_status, position, position_at, now), 2),
        "server_now": now,
        "lobby_track": lobby_track,
        "members": members,
        "member_count": member_count,
        "ready_count": ready_count,
        "messages": messages,
        "created_at": float(created_at),
        "updated_at": float(updated_at),
    }


def _sweep_stale(conn: Any, now: float) -> None:
    conn.execute(
        f"UPDATE {PARTIES_TABLE} SET status = 'ended', updated_at = ? "
        "WHERE status = 'lobby' AND updated_at < ?",
        (now, now - STALE_LOBBY_SECONDS),
    )
    conn.execute(
        f"UPDATE {PARTIES_TABLE} SET status = 'ended', updated_at = ? "
        "WHERE status IN ('playing', 'paused') AND updated_at < ?",
        (now, now - STALE_PLAYING_SECONDS),
    )


def create_party(
    db: Any,
    host_profile_id: int,
    body: Any,
    *,
    default_lobby_track: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    ensure_schema(db)
    if not isinstance(body, dict):
        raise PartyError("invalid party payload")
    item_type, item_id = _clean_item(body.get("item_type"), body.get("item_id"))
    meta = clean_meta(body.get("meta")) or {}
    t = time.time() if now is None else float(now)

    raw_title = str(body.get("title") or meta.get("title") or "Watch Party").strip()[:120]
    if not raw_title:
        raw_title = "Watch Party"

    control_mode = str(body.get("control_mode") or "host").strip().lower()
    if control_mode not in ALLOWED_CONTROL_MODES:
        control_mode = "host"

    scheduled_raw = body.get("scheduled_for")
    scheduled_for: Optional[float] = None
    if scheduled_raw is not None:
        if isinstance(scheduled_raw, (int, float)) and not isinstance(scheduled_raw, bool):
            scheduled_for = float(scheduled_raw)
            if scheduled_for > 1e12:
                scheduled_for /= 1000.0  # tolerate epoch ms
        else:
            raise PartyError("scheduled_for must be an epoch timestamp")

    status = "scheduled" if (scheduled_for is not None and scheduled_for > t + 60.0) else "lobby"
    show_id = body.get("show_id")
    show_id_int = int(show_id) if isinstance(show_id, int) and not isinstance(show_id, bool) else None

    lobby_track = _clean_lobby_track(body.get("lobby_track"), t) or _clean_lobby_track(default_lobby_track, t)

    party_id = secrets.token_hex(4)
    with db._get_connection() as conn:
        _sweep_stale(conn, t)
        conn.execute(
            f"INSERT INTO {PARTIES_TABLE} "
            "(id, host_profile_id, title, item_type, item_id, meta_json, show_id, "
            "status, scheduled_for, control_mode, position, position_at, lobby_track_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, ?)",
            (
                party_id, int(host_profile_id), raw_title, item_type, item_id,
                json.dumps(meta), show_id_int, status, scheduled_for, control_mode,
                t, json.dumps(lobby_track) if lobby_track else None, t, t,
            ),
        )
        conn.execute(
            f"INSERT OR REPLACE INTO {MEMBERS_TABLE} (party_id, profile_id, status, ready, joined_at, seen_at) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (party_id, int(host_profile_id), "rsvp" if status == "scheduled" else "joined", t, t),
        )
        conn.execute(
            f"INSERT INTO {MESSAGES_TABLE} (party_id, profile_id, kind, body, created_at) "
            "VALUES (?, ?, 'system', ?, ?)",
            (
                party_id,
                int(host_profile_id),
                "Scheduled a Watch Party" if status == "scheduled" else "Opened the theater lobby",
                t,
            ),
        )
        conn.commit()
        row = conn.execute(
            f"SELECT id, host_profile_id, title, item_type, item_id, meta_json, show_id, "
            "status, scheduled_for, control_mode, position, position_at, lobby_track_json, created_at, updated_at "
            f"FROM {PARTIES_TABLE} WHERE id = ?",
            (party_id,),
        ).fetchone()
        return _hydrate_party(conn, row, int(host_profile_id), t, include_messages=True)


def list_parties(
    db: Any,
    viewer_id: int,
    *,
    may_see: Optional[Callable[[str, str], bool]] = None,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    ensure_schema(db)
    t = time.time() if now is None else float(now)
    with db._get_connection() as conn:
        _sweep_stale(conn, t)
        conn.commit()
        rows = conn.execute(
            f"SELECT id, host_profile_id, title, item_type, item_id, meta_json, show_id, "
            "status, scheduled_for, control_mode, position, position_at, lobby_track_json, created_at, updated_at "
            f"FROM {PARTIES_TABLE} WHERE status != 'ended' "
            "ORDER BY CASE status WHEN 'playing' THEN 0 WHEN 'paused' THEN 1 WHEN 'lobby' THEN 2 ELSE 3 END, "
            "COALESCE(scheduled_for, created_at) ASC LIMIT 40"
        ).fetchall()
        out = []
        for r in rows:
            item_type, item_id = r[3], r[4]
            if may_see and int(r[1]) != int(viewer_id) and not may_see(item_type, item_id):
                continue
            out.append(_hydrate_party(conn, r, int(viewer_id), t, include_messages=False))
        return out


def get_party(
    db: Any,
    party_id: str,
    viewer_id: int,
    *,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    ensure_schema(db)
    t = time.time() if now is None else float(now)
    with db._get_connection() as conn:
        row = conn.execute(
            f"SELECT id, host_profile_id, title, item_type, item_id, meta_json, show_id, "
            "status, scheduled_for, control_mode, position, position_at, lobby_track_json, created_at, updated_at "
            f"FROM {PARTIES_TABLE} WHERE id = ?",
            (str(party_id),),
        ).fetchone()
        if not row:
            return None
        # Refresh seen_at if viewer is already a member
        conn.execute(
            f"UPDATE {MEMBERS_TABLE} SET seen_at = ? WHERE party_id = ? AND profile_id = ?",
            (t, str(party_id), int(viewer_id)),
        )
        conn.commit()
        return _hydrate_party(conn, row, int(viewer_id), t, include_messages=True)


def join_party(
    db: Any,
    party_id: str,
    profile_id: int,
    *,
    ready: Optional[bool] = None,
    rsvp: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    ensure_schema(db)
    t = time.time() if now is None else float(now)
    with db._get_connection() as conn:
        row = conn.execute(
            f"SELECT status FROM {PARTIES_TABLE} WHERE id = ?",
            (str(party_id),),
        ).fetchone()
        if not row or row[0] == "ended":
            raise PartyError("Watch party not found or already ended")
        existing = conn.execute(
            f"SELECT ready, joined_at FROM {MEMBERS_TABLE} WHERE party_id = ? AND profile_id = ?",
            (str(party_id), int(profile_id)),
        ).fetchone()
        new_ready = int(ready) if ready is not None else (int(existing[0]) if existing else 0)
        joined_at = float(existing[1]) if existing else t
        m_status = "rsvp" if rsvp else "joined"
        conn.execute(
            f"INSERT OR REPLACE INTO {MEMBERS_TABLE} (party_id, profile_id, status, ready, joined_at, seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(party_id), int(profile_id), m_status, new_ready, joined_at, t),
        )
        if not existing:
            conn.execute(
                f"INSERT INTO {MESSAGES_TABLE} (party_id, profile_id, kind, body, created_at) "
                "VALUES (?, ?, 'system', ?, ?)",
                (str(party_id), int(profile_id), "RSVP'd to the watch party" if rsvp else "Joined the room", t),
            )
        conn.execute(f"UPDATE {PARTIES_TABLE} SET updated_at = ? WHERE id = ?", (t, str(party_id)))
        conn.commit()
        full = conn.execute(
            f"SELECT id, host_profile_id, title, item_type, item_id, meta_json, show_id, "
            "status, scheduled_for, control_mode, position, position_at, lobby_track_json, created_at, updated_at "
            f"FROM {PARTIES_TABLE} WHERE id = ?",
            (str(party_id),),
        ).fetchone()
        return _hydrate_party(conn, full, int(profile_id), t, include_messages=True)


def leave_party(db: Any, party_id: str, profile_id: int, *, now: Optional[float] = None) -> bool:
    ensure_schema(db)
    t = time.time() if now is None else float(now)
    with db._get_connection() as conn:
        cur = conn.execute(
            f"DELETE FROM {MEMBERS_TABLE} WHERE party_id = ? AND profile_id = ?",
            (str(party_id), int(profile_id)),
        )
        if cur.rowcount > 0:
            conn.execute(
                f"INSERT INTO {MESSAGES_TABLE} (party_id, profile_id, kind, body, created_at) "
                "VALUES (?, ?, 'system', 'Left the room', ?)",
                (str(party_id), int(profile_id), t),
            )
            remaining = conn.execute(
                f"SELECT profile_id FROM {MEMBERS_TABLE} WHERE party_id = ? ORDER BY joined_at ASC",
                (str(party_id),),
            ).fetchall()
            if not remaining:
                conn.execute(
                    f"UPDATE {PARTIES_TABLE} SET status = 'ended', updated_at = ? WHERE id = ?",
                    (t, str(party_id)),
                )
            else:
                p_row = conn.execute(
                    f"SELECT host_profile_id FROM {PARTIES_TABLE} WHERE id = ?",
                    (str(party_id),),
                ).fetchone()
                if p_row and int(p_row[0]) == int(profile_id):
                    new_host_id = int(remaining[0][0])
                    conn.execute(
                        f"UPDATE {PARTIES_TABLE} SET host_profile_id = ?, updated_at = ? WHERE id = ?",
                        (new_host_id, t, str(party_id)),
                    )
                else:
                    conn.execute(f"UPDATE {PARTIES_TABLE} SET updated_at = ? WHERE id = ?", (t, str(party_id)))
        conn.commit()
        return cur.rowcount > 0


def update_state(
    db: Any,
    party_id: str,
    profile_id: int,
    body: Any,
    *,
    is_admin: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    ensure_schema(db)
    if not isinstance(body, dict):
        raise PartyError("invalid state update")
    t = time.time() if now is None else float(now)
    with db._get_connection() as conn:
        row = conn.execute(
            f"SELECT id, host_profile_id, title, item_type, item_id, meta_json, show_id, "
            "status, scheduled_for, control_mode, position, position_at, lobby_track_json, created_at, updated_at "
            f"FROM {PARTIES_TABLE} WHERE id = ?",
            (str(party_id),),
        ).fetchone()
        if not row:
            raise PartyError("Watch party not found")
        host_id = int(row[1])
        cur_status = row[7]
        control_mode = row[9]
        cur_pos = float(row[10])
        cur_pos_at = float(row[11])

        action = str(body.get("action") or "").strip().lower()

        # Check majority ready for starting the stream
        members_rows = conn.execute(
            f"SELECT profile_id, ready FROM {MEMBERS_TABLE} WHERE party_id = ?",
            (str(party_id),),
        ).fetchall()
        member_count = len(members_rows)
        ready_count = sum(1 for m in members_rows if m[1])
        majority_ready = member_count > 0 and (ready_count * 2 > member_count)

        existing_meta = _decode_json(row[5]) or {}
        cur_pee = existing_meta.get("pee_break") if isinstance(existing_meta.get("pee_break"), dict) else None

        is_host_or_admin = (int(profile_id) == host_id) or is_admin
        # Rules:
        # - Anyone in the party can pause or call a pee_break
        # - Majority ready (when >1 member) or host can start from lobby
        # - The person who called a pee_break (or host) can resume from pee_break
        # - Otherwise control_mode == 'everyone' or host controls play/seek
        if action in ("pause", "pee_break"):
            can_ctrl = True
        elif action == "start" and member_count > 1 and majority_ready:
            can_ctrl = True
        elif action in ("play", "resume") and cur_pee and int(cur_pee.get("profile_id") or 0) == int(profile_id):
            can_ctrl = True
        else:
            can_ctrl = is_host_or_admin or (control_mode == "everyone")

        if not can_ctrl:
            raise PartyError("Only the host can control playback in this room")

        new_status = cur_status
        new_pos = _compute_live_position(cur_status, cur_pos, cur_pos_at, t)
        new_pos_at = t
        new_lobby_json = row[12]
        new_item_type, new_item_id, new_title = row[3], row[4], row[2]
        sys_note: Optional[str] = None

        if action == "start":
            new_status = "playing"
            new_pos = float(body.get("position") or 0.0)
            existing_meta.pop("pee_break", None)
            existing_meta.pop("resume_at", None)
            sys_note = "Started the feature presentation!"
        elif action in ("play", "resume"):
            new_status = "playing"
            if body.get("position") is not None:
                new_pos = max(0.0, float(body["position"]))
            # When resuming from a pause or pee break, use a 5-second synchronized countdown
            countdown = RESUME_COUNTDOWN_SEC if (cur_status == "paused" or cur_pee is not None) else 0.0
            new_pos_at = t + countdown
            if countdown > 0:
                existing_meta["resume_at"] = round(new_pos_at, 2)
            else:
                existing_meta.pop("resume_at", None)
            was_pee = existing_meta.pop("pee_break", None)
            if was_pee:
                sys_note = "Returned from pee break — resuming in 5s!"
            elif cur_status == "paused":
                sys_note = "Resuming playback in 5s!"
        elif action == "pause":
            new_status = "paused"
            if body.get("position") is not None:
                new_pos = max(0.0, float(body["position"]))
            existing_meta.pop("resume_at", None)
            sys_note = "Paused playback"
        elif action == "pee_break":
            ck = (str(party_id), int(profile_id))
            last_pee = _pee_cooldowns.get(ck, 0.0)
            if last_pee > 0 and (t - last_pee) < PEE_BREAK_COOLDOWN_SEC:
                rem_min = max(1, int(round((PEE_BREAK_COOLDOWN_SEC - (t - last_pee)) / 60.0)))
                raise PartyError(f"Pee break is on cooldown ({rem_min}m remaining)")
            _pee_cooldowns[ck] = t
            new_status = "paused"
            if body.get("position") is not None:
                new_pos = max(0.0, float(body["position"]))
            existing_meta.pop("resume_at", None)
            existing_meta["pee_break"] = {
                "profile_id": int(profile_id),
                "started_at": round(t, 2),
                "ends_at": round(t + PEE_BREAK_DURATION_SEC, 2),
            }
            sys_note = "🚽 Called a 5-minute pee break! Stream paused until they return."
        elif action == "seek":
            if body.get("position") is None:
                raise PartyError("seek requires position")
            new_pos = max(0.0, float(body["position"]))
            existing_meta.pop("resume_at", None)
        elif action == "lobby":
            new_status = "lobby"
            new_pos = 0.0
            existing_meta.pop("pee_break", None)
            existing_meta.pop("resume_at", None)
            sys_note = "Returned to the theater lobby"
        elif action == "end":
            if not is_host_or_admin:
                raise PartyError("Only the host can end the watch party")
            new_status = "ended"
            existing_meta.pop("pee_break", None)
            existing_meta.pop("resume_at", None)
            sys_note = "Ended the watch party"

        if "lobby_track" in body:
            lt = _clean_lobby_track(body.get("lobby_track"), t)
            new_lobby_json = json.dumps(lt) if lt else None
            if lt:
                sys_note = f"Queued lobby music: {lt['title']}"

        if body.get("item_type") and body.get("item_id"):
            new_item_type, new_item_id = _clean_item(body["item_type"], body["item_id"])
            cleaned = clean_meta(body.get("meta")) or {}
            existing_meta.update(cleaned)
            if cleaned.get("title"):
                new_title = str(cleaned["title"])[:120]
            new_pos = 0.0
            sys_note = f"Picked {new_title}"

        new_meta_json = json.dumps(existing_meta)

        if body.get("control_mode") in ALLOWED_CONTROL_MODES and (int(profile_id) == host_id or is_admin):
            control_mode = body["control_mode"]

        conn.execute(
            f"UPDATE {PARTIES_TABLE} SET status = ?, position = ?, position_at = ?, "
            "lobby_track_json = ?, item_type = ?, item_id = ?, meta_json = ?, title = ?, "
            "control_mode = ?, updated_at = ? WHERE id = ?",
            (
                new_status, new_pos, new_pos_at, new_lobby_json,
                new_item_type, new_item_id, new_meta_json, new_title,
                control_mode, t, str(party_id),
            ),
        )
        if sys_note:
            conn.execute(
                f"INSERT INTO {MESSAGES_TABLE} (party_id, profile_id, kind, body, created_at) "
                "VALUES (?, ?, 'system', ?, ?)",
                (str(party_id), int(profile_id), sys_note, t),
            )
        conn.commit()
        full = conn.execute(
            f"SELECT id, host_profile_id, title, item_type, item_id, meta_json, show_id, "
            "status, scheduled_for, control_mode, position, position_at, lobby_track_json, created_at, updated_at "
            f"FROM {PARTIES_TABLE} WHERE id = ?",
            (str(party_id),),
        ).fetchone()
        return _hydrate_party(conn, full, int(profile_id), t, include_messages=True)


def post_message(
    db: Any,
    party_id: str,
    profile_id: int,
    body: Any,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    ensure_schema(db)
    if not isinstance(body, dict):
        raise PartyError("invalid message")
    kind = str(body.get("kind") or "chat").strip().lower()
    if kind not in ("chat", "reaction"):
        kind = "chat"
    text = str(body.get("body") or "").strip()[:300]
    if not text:
        raise PartyError("message cannot be empty")
    t = time.time() if now is None else float(now)
    if kind == "reaction":
        rk = (str(party_id), int(profile_id))
        recent = [ts for ts in _reaction_timestamps.get(rk, []) if (t - ts) < REACTION_BURST_WINDOW_SEC]
        if len(recent) >= REACTION_BURST_LIMIT:
            raise PartyError("Slow down on the emoji reactions!")
        recent.append(t)
        _reaction_timestamps[rk] = recent
    with db._get_connection() as conn:
        row = conn.execute(f"SELECT status FROM {PARTIES_TABLE} WHERE id = ?", (str(party_id),)).fetchone()
        if not row or row[0] == "ended":
            raise PartyError("Watch party not found or ended")
        cur = conn.execute(
            f"INSERT INTO {MESSAGES_TABLE} (party_id, profile_id, kind, body, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(party_id), int(profile_id), kind, text, t),
        )
        conn.execute(f"UPDATE {PARTIES_TABLE} SET updated_at = ? WHERE id = ?", (t, str(party_id)))
        conn.commit()
        return {
            "id": int(cur.lastrowid),
            "profile_id": int(profile_id),
            "kind": kind,
            "body": text,
            "created_at": t,
            "you": True,
        }


def delete_party(db: Any, party_id: str, profile_id: int, *, is_admin: bool = False) -> bool:
    ensure_schema(db)
    t = time.time()
    with db._get_connection() as conn:
        row = conn.execute(
            f"SELECT host_profile_id FROM {PARTIES_TABLE} WHERE id = ?",
            (str(party_id),),
        ).fetchone()
        if not row:
            return False
        if int(row[0]) != int(profile_id) and not is_admin:
            raise PartyError("Only the host can close this watch party")
        conn.execute(
            f"UPDATE {PARTIES_TABLE} SET status = 'ended', updated_at = ? WHERE id = ?",
            (t, str(party_id)),
        )
        conn.commit()
        return True
