"""the play log tables. they live in the main soulsync database (the one that
holds listening_history and profiles): a play spans music, video and books, so
it belongs to neither the video db nor the audiobook db.

  server_plays              one row per play
  server_activity_minutes   per minute peaks: streams, transcodes, bandwidth
  server_geo_cache          ip -> city, so a viewer is looked up once a month
  server_activity_meta      small markers (plex history import)

writes are tiny and come from one writer thread, never from a request.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from typing import Any, Callable, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("server_activity")

PLAY_COLUMNS = (
    "play_key", "group_key", "source", "server", "server_name", "session_key", "kind",
    "rating_key", "parent_key", "grandparent_key", "title", "parent_title", "grandparent_title",
    "subtitle", "year", "season", "episode", "track", "thumb",
    "user", "user_id", "user_avatar",
    "player_product", "player_version", "player_platform", "player_device", "player_name",
    "ip", "remote", "location",
    "started_at", "stopped_at", "last_seen",
    "duration_ms", "position_ms", "max_position_ms", "percent",
    "played_ms", "paused_ms", "buffering_ms", "pause_count", "buffer_count",
    "method", "reasons", "hw", "quality", "change", "lossless", "detail", "segments",
    "sent_kb", "peak_kbps", "book_percent", "book_left_ms", "chapter", "link",
)

DDL = [
    """CREATE TABLE IF NOT EXISTS server_plays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        play_key TEXT NOT NULL UNIQUE,
        group_key TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'live',
        server TEXT NOT NULL,
        server_name TEXT,
        session_key TEXT,
        kind TEXT NOT NULL,
        rating_key TEXT,
        parent_key TEXT,
        grandparent_key TEXT,
        title TEXT NOT NULL DEFAULT '',
        parent_title TEXT,
        grandparent_title TEXT,
        subtitle TEXT,
        year INTEGER,
        season INTEGER,
        episode INTEGER,
        track INTEGER,
        thumb TEXT,
        user TEXT NOT NULL DEFAULT '',
        user_id TEXT,
        user_avatar TEXT,
        player_product TEXT,
        player_version TEXT,
        player_platform TEXT,
        player_device TEXT,
        player_name TEXT,
        ip TEXT,
        remote INTEGER NOT NULL DEFAULT 0,
        location TEXT,
        started_at REAL NOT NULL,
        stopped_at REAL,
        last_seen REAL NOT NULL,
        duration_ms INTEGER,
        position_ms INTEGER,
        max_position_ms INTEGER,
        percent INTEGER,
        played_ms INTEGER NOT NULL DEFAULT 0,
        paused_ms INTEGER NOT NULL DEFAULT 0,
        buffering_ms INTEGER NOT NULL DEFAULT 0,
        pause_count INTEGER NOT NULL DEFAULT 0,
        buffer_count INTEGER NOT NULL DEFAULT 0,
        method TEXT,
        reasons TEXT,
        hw INTEGER,
        quality TEXT,
        change TEXT,
        lossless INTEGER,
        detail TEXT,
        segments TEXT,
        sent_kb REAL NOT NULL DEFAULT 0,
        peak_kbps INTEGER,
        book_percent INTEGER,
        book_left_ms INTEGER,
        chapter TEXT,
        link TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_server_plays_started ON server_plays(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_server_plays_group ON server_plays(group_key)",
    "CREATE INDEX IF NOT EXISTS idx_server_plays_open ON server_plays(stopped_at) WHERE stopped_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_server_plays_user ON server_plays(user, started_at)",
    # history pages: newest group first, read from the index alone
    "CREATE INDEX IF NOT EXISTS idx_server_plays_group_started ON server_plays(group_key, started_at)",
    # when the log began (the first live play) and the plex history import marker
    "CREATE INDEX IF NOT EXISTS idx_server_plays_source ON server_plays(source, started_at)",
    # history's people menu (with each one's picture) and servers menu, from the index alone
    "CREATE INDEX IF NOT EXISTS idx_server_plays_user_avatar ON server_plays(user, started_at, user_avatar)",
    "CREATE INDEX IF NOT EXISTS idx_server_plays_server ON server_plays(server, started_at)",
    """CREATE TABLE IF NOT EXISTS server_activity_minutes (
        minute INTEGER PRIMARY KEY,
        streams INTEGER NOT NULL DEFAULT 0,
        transcodes INTEGER NOT NULL DEFAULT 0,
        total_kbps INTEGER NOT NULL DEFAULT 0,
        remote_kbps INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS server_geo_cache (
        ip TEXT PRIMARY KEY,
        data TEXT,
        ok INTEGER NOT NULL DEFAULT 0,
        fetched_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS server_activity_meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )""",
]


def ensure_schema(cursor) -> None:
    """idempotent; called from the main database's init."""
    for stmt in DDL:
        cursor.execute(stmt)


# ── connections ─────────────────────────────────────────────────────────────

def _default_factory():
    from database.music_database import get_database
    return get_database()._get_connection()


_factory: Callable[[], Any] = _default_factory
_schema_ready = False
_schema_lock = threading.Lock()


def use_connection_factory(factory: Optional[Callable[[], Any]]) -> None:
    """tests point the store at a temp file."""
    global _factory, _schema_ready
    _factory = factory or _default_factory
    _schema_ready = False
    bump_generation()


def connect():
    """a connection with the tables there. the main db creates them at start;
    the check here covers a db that predates them without a restart."""
    global _schema_ready
    conn = _factory()
    if not _schema_ready:
        with _schema_lock:
            if not _schema_ready:
                cur = conn.cursor()
                ensure_schema(cur)
                conn.commit()
                _schema_ready = True
    return conn


def _row(r) -> Dict[str, Any]:
    if isinstance(r, sqlite3.Row):
        return {k: r[k] for k in r.keys()}
    return dict(r)


def query(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with closing(connect()) as conn:
        conn.row_factory = sqlite3.Row
        return [_row(r) for r in conn.execute(sql, tuple(params)).fetchall()]


# ── writes ──────────────────────────────────────────────────────────────────
#
# every write that changes the plays moves the generation on; history and
# stats keep their answers while it stands still.

_generation = [0]
_generation_lock = threading.Lock()


def generation() -> int:
    return _generation[0]


def bump_generation() -> None:
    with _generation_lock:
        _generation[0] += 1


def _jsonish(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    if isinstance(v, bool):
        return int(v)
    return v


def apply_ops(ops: List[tuple]) -> None:
    """('play', row) upserts by play_key, ('drop', play_key) deletes,
    ('minute', minute, streams, transcodes, total, remote) keeps the peaks."""
    if not ops:
        return
    cols = ", ".join(PLAY_COLUMNS)
    marks = ", ".join("?" for _ in PLAY_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in PLAY_COLUMNS if c != "play_key")
    play_sql = (f"INSERT INTO server_plays ({cols}) VALUES ({marks}) "
                f"ON CONFLICT(play_key) DO UPDATE SET {updates}")
    minute_sql = ("INSERT INTO server_activity_minutes (minute, streams, transcodes, total_kbps, remote_kbps) "
                  "VALUES (?, ?, ?, ?, ?) ON CONFLICT(minute) DO UPDATE SET "
                  "streams=MAX(streams, excluded.streams), transcodes=MAX(transcodes, excluded.transcodes), "
                  "total_kbps=MAX(total_kbps, excluded.total_kbps), remote_kbps=MAX(remote_kbps, excluded.remote_kbps)")
    with closing(connect()) as conn:
        for op in ops:
            if op[0] == "play":
                row = op[1]
                conn.execute(play_sql, tuple(_jsonish(row.get(c)) for c in PLAY_COLUMNS))
            elif op[0] == "drop":
                conn.execute("DELETE FROM server_plays WHERE play_key = ?", (op[1],))
            elif op[0] == "minute":
                conn.execute(minute_sql, tuple(op[1:6]))
        conn.commit()
    if any(op[0] in ("play", "drop") for op in ops):
        bump_generation()


# ── the staged swap (plex history repair) ───────────────────────────────────
#
# a plain side table, not a temp one (the connection pool drops temp tables
# between connections). a leftover from an interrupted run is thrown away.

STAGE = "server_plays_import"


def stage_reset() -> None:
    cols = ", ".join(PLAY_COLUMNS)
    with closing(connect()) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {STAGE}")
        conn.execute(f"CREATE TABLE {STAGE} AS SELECT {cols} FROM server_plays WHERE 0")
        conn.commit()


def stage_rows(rows: List[Dict[str, Any]]) -> None:
    """one batch into the side table; history doesn't see it yet."""
    if not rows:
        return
    cols = ", ".join(PLAY_COLUMNS)
    marks = ", ".join("?" for _ in PLAY_COLUMNS)
    with closing(connect()) as conn:
        conn.executemany(f"INSERT INTO {STAGE} ({cols}) VALUES ({marks})",
                         [tuple(_jsonish(r.get(c)) for c in PLAY_COLUMNS) for r in rows])
        conn.commit()


def swap_staged(marker_key: str, marker_value: str) -> int:
    """the staged plays replace the imported ones they match, and the marker
    is written, in one transaction. live rows are never touched; play_key is
    unique, so nothing doubles. returns the plays now in from the stage."""
    cols = ", ".join(PLAY_COLUMNS)
    with closing(connect()) as conn:
        try:
            # explicit, so an autocommit connection is atomic here too
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"DELETE FROM server_plays WHERE source = 'plex_history' "
                         f"AND play_key IN (SELECT play_key FROM {STAGE})")
            n = conn.execute(f"INSERT OR IGNORE INTO server_plays ({cols}) "
                             f"SELECT {cols} FROM {STAGE} WHERE source = 'plex_history'").rowcount
            conn.execute("INSERT INTO server_activity_meta (key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (marker_key, marker_value))
            conn.execute(f"DROP TABLE {STAGE}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    bump_generation()
    return n or 0


def load_open_plays() -> List[Dict[str, Any]]:
    return query("SELECT * FROM server_plays WHERE stopped_at IS NULL")


def recent_closed(since: float) -> List[Dict[str, Any]]:
    """plays that ended lately, so a binge run keeps its group across a restart."""
    return query("SELECT play_key, group_key, server, user, kind, parent_key, grandparent_key, stopped_at "
                 "FROM server_plays WHERE stopped_at IS NOT NULL AND stopped_at >= ? AND source = 'live'",
                 (since,))


def prune(retention_days: int, now: float) -> int:
    """drop plays older than the retention. 0 keeps everything."""
    if not retention_days or retention_days <= 0:
        return 0
    cutoff = now - retention_days * 86400
    with closing(connect()) as conn:
        n = conn.execute("DELETE FROM server_plays WHERE started_at < ? AND stopped_at IS NOT NULL",
                         (cutoff,)).rowcount
        conn.execute("DELETE FROM server_activity_minutes WHERE minute < ?", (int(cutoff // 60),))
        conn.commit()
    if n:
        bump_generation()
    return n or 0


def clear_play_log() -> Dict[str, Any]:
    """the clear button: every finished play and every minute peak. plays still
    running are kept so they close normally. plex's history isn't imported
    again afterwards."""
    with closing(connect()) as conn:
        n = conn.execute("DELETE FROM server_plays WHERE stopped_at IS NOT NULL").rowcount
        conn.execute("DELETE FROM server_activity_minutes")
        conn.commit()
    bump_generation()
    try:
        from core.server_activity import stats
        stats.invalidate()
    except Exception:   # noqa: BLE001
        logger.debug("stats cache reset failed", exc_info=True)
    return {"ok": True, "removed": n or 0}


def meta_get(key: str) -> Optional[str]:
    rows = query("SELECT value FROM server_activity_meta WHERE key = ?", (key,))
    return rows[0]["value"] if rows else None


def meta_set(key: str, value: str) -> None:
    with closing(connect()) as conn:
        conn.execute("INSERT INTO server_activity_meta (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()


def geo_get(ip: str) -> Optional[Dict[str, Any]]:
    rows = query("SELECT data, ok, fetched_at FROM server_geo_cache WHERE ip = ?", (ip,))
    if not rows:
        return None
    r = rows[0]
    try:
        data = json.loads(r["data"]) if r["data"] else None
    except ValueError:
        data = None
    return {"data": data, "ok": bool(r["ok"]), "fetched_at": float(r["fetched_at"])}


def geo_put(ip: str, data: Optional[Dict[str, Any]], ok: bool, now: float) -> None:
    with closing(connect()) as conn:
        conn.execute("INSERT INTO server_geo_cache (ip, data, ok, fetched_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(ip) DO UPDATE SET data=excluded.data, ok=excluded.ok, "
                     "fetched_at=excluded.fetched_at",
                     (ip, json.dumps(data) if data else None, 1 if ok else 0, now))
        conn.commit()
