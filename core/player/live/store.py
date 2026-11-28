"""Live TV storage in player.db: sources, channels, and EPG programs.

Sources are admin-configured:
  - 'hdhomerun': an OTA network tuner (auto-discovered or manual IP)
  - 'm3u': a user-supplied M3U playlist URL or local file
  - 'xmltv': a user-supplied XMLTV guide URL or local file

DRM-tagged channels from HDHomeRun are skipped on ingest (we never decrypt DRM).
User edits on a channel (enabled, display name, channel number, manual epg_id)
survive refreshes.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

SOURCES = "live_sources"
CHANNELS = "live_channels"
PROGRAMS = "live_programs"

SOURCE_KINDS = ("hdhomerun", "m3u", "xmltv")

_ready: set = set()


class LiveStoreError(ValueError):
    pass


def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {SOURCES} (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                tuner_count INTEGER NOT NULL DEFAULT 0,
                device_id TEXT,
                device_auth TEXT,
                user_agent TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_refresh_at REAL,
                last_error TEXT,
                meta_json TEXT NOT NULL DEFAULT '{{}}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {CHANNELS} (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                guide_number TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                custom_name TEXT,
                stream_url TEXT NOT NULL,
                logo_url TEXT,
                group_title TEXT,
                tvg_id TEXT,
                epg_id TEXT,
                headers_json TEXT NOT NULL DEFAULT '{{}}',
                enabled INTEGER NOT NULL DEFAULT 1,
                is_drm INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                FOREIGN KEY (source_id) REFERENCES {SOURCES}(id) ON DELETE CASCADE
            )""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{CHANNELS}_source ON {CHANNELS} (source_id)")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {PROGRAMS} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                epg_channel_id TEXT NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                title TEXT NOT NULL,
                subtitle TEXT,
                description TEXT,
                category TEXT,
                icon_url TEXT,
                episode_num TEXT,
                is_new INTEGER NOT NULL DEFAULT 0,
                is_live INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (source_id) REFERENCES {SOURCES}(id) ON DELETE CASCADE
            )""")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{PROGRAMS}_chan_time "
                     f"ON {PROGRAMS} (epg_channel_id, start_ts, end_ts)")
        conn.commit()
    _ready.add(key)


def _source_row(row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["meta"] = json.loads(d.pop("meta_json", "{}") or "{}")
    except (TypeError, ValueError):
        d["meta"] = {}
    d["enabled"] = bool(d.get("enabled", 1))
    return d


def _channel_row(row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["headers"] = json.loads(d.pop("headers_json", "{}") or "{}")
    except (TypeError, ValueError):
        d["headers"] = {}
    d["enabled"] = bool(d.get("enabled", 1))
    d["is_drm"] = bool(d.get("is_drm", 0))
    d["display_name"] = d.get("custom_name") or d.get("name") or ""
    d["effective_epg_id"] = d.get("epg_id") or d.get("tvg_id") or ""
    return d


def add_source(
    db: Any,
    *,
    kind: str,
    name: str,
    url: str,
    tuner_count: int = 0,
    device_id: Optional[str] = None,
    device_auth: Optional[str] = None,
    user_agent: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_schema(db)
    k = str(kind or "").strip().lower()
    if k not in SOURCE_KINDS:
        raise LiveStoreError(f"source kind must be one of {', '.join(SOURCE_KINDS)}")
    nm = str(name or "").strip()[:80]
    u = str(url or "").strip()
    if not nm:
        raise LiveStoreError("source name is required")
    if not u:
        raise LiveStoreError("source url or path is required")
    now = time.time()
    sid = f"src_{uuid.uuid4().hex[:12]}"
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO {SOURCES} "
            "(id, kind, name, url, tuner_count, device_id, device_auth, user_agent, "
            " enabled, meta_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (sid, k, nm, u, max(0, int(tuner_count or 0)),
             device_id, device_auth, user_agent,
             json.dumps(meta or {}), now, now),
        )
        conn.commit()
    return get_source(db, sid)


def list_sources(db: Any) -> List[Dict[str, Any]]:
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT s.*, "
            f"(SELECT COUNT(*) FROM {CHANNELS} c WHERE c.source_id = s.id AND c.is_drm = 0) AS channel_count "
            f"FROM {SOURCES} s ORDER BY s.created_at ASC"
        ).fetchall()
    return [_source_row(r) for r in rows]


def get_source(db: Any, source_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema(db)
    with db._get_connection() as conn:
        row = conn.execute(
            f"SELECT s.*, "
            f"(SELECT COUNT(*) FROM {CHANNELS} c WHERE c.source_id = s.id AND c.is_drm = 0) AS channel_count "
            f"FROM {SOURCES} s WHERE s.id = ?",
            (source_id,),
        ).fetchone()
    return _source_row(row) if row else None


def update_source_status(
    db: Any,
    source_id: str,
    *,
    last_error: Optional[str] = None,
    device_auth: Optional[str] = None,
    tuner_count: Optional[int] = None,
) -> None:
    ensure_schema(db)
    now = time.time()
    sets = ["last_refresh_at = ?", "last_error = ?", "updated_at = ?"]
    params: List[Any] = [now, last_error, now]
    if device_auth is not None:
        sets.append("device_auth = ?")
        params.append(device_auth)
    if tuner_count is not None:
        sets.append("tuner_count = ?")
        params.append(max(0, int(tuner_count)))
    params.append(source_id)
    with db._get_connection() as conn:
        conn.execute(f"UPDATE {SOURCES} SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()


def delete_source(db: Any, source_id: str) -> bool:
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {PROGRAMS} WHERE source_id = ?", (source_id,))
        conn.execute(f"DELETE FROM {CHANNELS} WHERE source_id = ?", (source_id,))
        cur = conn.execute(f"DELETE FROM {SOURCES} WHERE id = ?", (source_id,))
        conn.commit()
        return cur.rowcount > 0


def upsert_channels(db: Any, source_id: str, channels: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Sync a source's channel list while preserving admin overrides (enabled,
    custom_name, manual epg_id). DRM-tagged channels are stored with is_drm=1
    and hidden from viewers."""
    ensure_schema(db)
    now = time.time()
    incoming = list(channels)
    with db._get_connection() as conn:
        existing = {
            r["id"]: dict(r)
            for r in conn.execute(f"SELECT * FROM {CHANNELS} WHERE source_id = ?", (source_id,)).fetchall()
        }
        seen_ids = set()
        added = 0
        updated = 0
        drm_hidden = 0
        for idx, ch in enumerate(incoming):
            is_drm = 1 if ch.get("is_drm") else 0
            if is_drm:
                drm_hidden += 1
            num = str(ch.get("guide_number") or "").strip()
            name = str(ch.get("name") or "").strip() or num or f"Channel {idx + 1}"
            stream_url = str(ch.get("stream_url") or "").strip()
            if not stream_url:
                continue
            cid = str(ch.get("id") or f"live_{source_id}_{num or idx}")
            seen_ids.add(cid)
            prev = existing.get(cid)
            headers_json = json.dumps(ch.get("headers") or {})
            if prev:
                conn.execute(
                    f"UPDATE {CHANNELS} SET guide_number=?, name=?, stream_url=?, logo_url=?, "
                    "group_title=?, tvg_id=?, headers_json=?, is_drm=?, sort_order=?, updated_at=? "
                    "WHERE id=?",
                    (num, name, stream_url, ch.get("logo_url"), ch.get("group_title"),
                     ch.get("tvg_id"), headers_json, is_drm, idx, now, cid),
                )
                updated += 1
            else:
                conn.execute(
                    f"INSERT INTO {CHANNELS} "
                    "(id, source_id, guide_number, name, stream_url, logo_url, group_title, "
                    " tvg_id, headers_json, enabled, is_drm, sort_order, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, source_id, num, name, stream_url, ch.get("logo_url"),
                     ch.get("group_title"), ch.get("tvg_id"), headers_json,
                     0 if is_drm else 1, is_drm, idx, now),
                )
                added += 1
        # prune channels that disappeared from the tuner/playlist
        stale = [cid for cid in existing if cid not in seen_ids]
        if stale:
            ph = ",".join("?" * len(stale))
            conn.execute(f"DELETE FROM {CHANNELS} WHERE id IN ({ph})", stale)
        conn.commit()
    return {"added": added, "updated": updated, "removed": len(stale), "drm_hidden": drm_hidden}


def list_channels(db: Any, *, include_disabled: bool = False) -> List[Dict[str, Any]]:
    ensure_schema(db)
    where = ["c.is_drm = 0"]
    if not include_disabled:
        where.append("c.enabled = 1")
        where.append("s.enabled = 1")
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT c.*, s.name AS source_name, s.kind AS source_kind, s.tuner_count "
            f"FROM {CHANNELS} c JOIN {SOURCES} s ON s.id = c.source_id "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY c.sort_order ASC, c.guide_number ASC, c.name ASC"
        ).fetchall()
    return [_channel_row(r) for r in rows]


def get_channel(db: Any, channel_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema(db)
    with db._get_connection() as conn:
        row = conn.execute(
            f"SELECT c.*, s.name AS source_name, s.kind AS source_kind, s.tuner_count "
            f"FROM {CHANNELS} c JOIN {SOURCES} s ON s.id = c.source_id "
            f"WHERE c.id = ? AND c.is_drm = 0",
            (channel_id,),
        ).fetchone()
    return _channel_row(row) if row else None


def update_channel(db: Any, channel_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ensure_schema(db)
    sets = []
    params: List[Any] = []
    if "enabled" in patch:
        sets.append("enabled = ?")
        params.append(1 if patch["enabled"] else 0)
    if "custom_name" in patch:
        val = str(patch["custom_name"] or "").strip()[:80]
        sets.append("custom_name = ?")
        params.append(val or None)
    if "epg_id" in patch:
        val = str(patch["epg_id"] or "").strip()[:120]
        sets.append("epg_id = ?")
        params.append(val or None)
    if "guide_number" in patch:
        sets.append("guide_number = ?")
        params.append(str(patch["guide_number"] or "").strip()[:20])
    if not sets:
        return get_channel(db, channel_id)
    sets.append("updated_at = ?")
    params.append(time.time())
    params.append(channel_id)
    with db._get_connection() as conn:
        conn.execute(f"UPDATE {CHANNELS} SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    return get_channel(db, channel_id)


def replace_programs(db: Any, source_id: str, programs: Iterable[Dict[str, Any]]) -> int:
    """Replace a source's EPG programs and purge expired entries (>6h old)."""
    ensure_schema(db)
    cutoff = time.time() - 6 * 3600
    batch = []
    for p in programs:
        end_ts = float(p.get("end_ts") or 0)
        start_ts = float(p.get("start_ts") or 0)
        if end_ts <= cutoff or end_ts <= start_ts:
            continue
        cid = str(p.get("epg_channel_id") or "").strip()
        title = str(p.get("title") or "").strip()
        if not cid or not title:
            continue
        batch.append((
            source_id, cid, start_ts, end_ts, title,
            p.get("subtitle"), p.get("description"), p.get("category"),
            p.get("icon_url"), p.get("episode_num"),
            1 if p.get("is_new") else 0, 1 if p.get("is_live") else 0,
        ))
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {PROGRAMS} WHERE source_id = ?", (source_id,))
        conn.execute(f"DELETE FROM {PROGRAMS} WHERE end_ts < ?", (cutoff,))
        if batch:
            conn.executemany(
                f"INSERT INTO {PROGRAMS} "
                "(source_id, epg_channel_id, start_ts, end_ts, title, subtitle, "
                " description, category, icon_url, episode_num, is_new, is_live) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
        conn.commit()
    return len(batch)


def programs_window(
    db: Any,
    start_ts: float,
    end_ts: float,
    channel_ids: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Programs overlapping [start_ts, end_ts] keyed by live channel id.
    Matches each channel's effective_epg_id (manual epg_id > tvg_id > guide_number > name).
    Channels with no guide data get clean 1-hour placeholder blocks so the grid
    is never blank."""
    ensure_schema(db)
    chans = list_channels(db)
    if channel_ids is not None:
        want = set(channel_ids)
        chans = [c for c in chans if c["id"] in want]
    if not chans:
        return {}
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT p.*, s.kind AS source_kind FROM {PROGRAMS} p "
            f"JOIN {SOURCES} s ON s.id = p.source_id "
            f"WHERE p.end_ts > ? AND p.start_ts < ? "
            f"ORDER BY CASE WHEN s.kind = 'xmltv' THEN 0 ELSE 1 END, p.start_ts ASC",
            (float(start_ts), float(end_ts)),
        ).fetchall()
    by_epg: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        key = str(r["epg_channel_id"]).strip().lower()
        by_epg.setdefault(key, []).append(dict(r))

    out: Dict[str, List[Dict[str, Any]]] = {}
    for ch in chans:
        candidates = [
            ch.get("epg_id"),
            ch.get("tvg_id"),
            ch.get("guide_number"),
            ch.get("display_name"),
        ]
        found: List[Dict[str, Any]] = []
        for cand in candidates:
            k = str(cand or "").strip().lower()
            if k and k in by_epg:
                found = by_epg[k]
                break
        if found:
            # deduplicate overlapping entries (user XMLTV wins over HDHomeRun XMLTV via ORDER BY)
            merged: List[Dict[str, Any]] = []
            for p in found:
                if merged and p["start_ts"] < merged[-1]["end_ts"]:
                    continue
                merged.append(p)
            out[ch["id"]] = merged
        else:
            # 1-hour placeholder blocks when no EPG exists
            t = int(start_ts // 3600) * 3600
            blocks = []
            while t < end_ts:
                blocks.append({
                    "epg_channel_id": ch["id"],
                    "start_ts": float(t),
                    "end_ts": float(t + 3600),
                    "title": ch["display_name"],
                    "subtitle": None,
                    "description": "Live broadcast (no guide data)",
                    "category": "Live",
                    "icon_url": ch.get("logo_url"),
                    "episode_num": None,
                    "is_new": 0,
                    "is_live": 1,
                    "placeholder": True,
                })
                t += 3600
            out[ch["id"]] = blocks
    return out
