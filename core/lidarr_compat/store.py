"""the artists and albums lidarr clients added, with the ids they hold on to."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

_ARTIST_FIELDS = ("name", "disambiguation", "artist_type", "monitored", "monitor_new_items",
                  "quality_profile_id", "metadata_profile_id", "root_folder", "refreshed_at")
_ALBUM_FIELDS = ("title", "disambiguation", "album_type", "secondary_types", "release_date",
                 "monitored", "any_release_ok", "track_count", "owned_count", "checked_at",
                 "searched_at", "queued_count")


def ensure_schema(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lidarr_compat_artists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mbid TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            disambiguation TEXT NOT NULL DEFAULT '',
            artist_type TEXT NOT NULL DEFAULT '',
            monitored INTEGER NOT NULL DEFAULT 0,
            monitor_new_items TEXT NOT NULL DEFAULT 'none',
            quality_profile_id INTEGER NOT NULL DEFAULT 1,
            metadata_profile_id INTEGER NOT NULL DEFAULT 1,
            root_folder TEXT NOT NULL DEFAULT '',
            added_at REAL,
            refreshed_at REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lidarr_compat_albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mbid TEXT NOT NULL UNIQUE,
            artist_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            disambiguation TEXT NOT NULL DEFAULT '',
            album_type TEXT NOT NULL DEFAULT '',
            secondary_types TEXT NOT NULL DEFAULT '[]',
            release_date TEXT NOT NULL DEFAULT '',
            monitored INTEGER NOT NULL DEFAULT 0,
            any_release_ok INTEGER NOT NULL DEFAULT 1,
            track_count INTEGER,
            owned_count INTEGER NOT NULL DEFAULT 0,
            checked_at REAL,
            searched_at REAL,
            queued_count INTEGER NOT NULL DEFAULT 0,
            added_at REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lidarr_compat_albums_artist "
                   "ON lidarr_compat_albums (artist_id)")


def _artist(row) -> Dict[str, Any]:
    out = dict(row)
    out["monitored"] = bool(out["monitored"])
    return out


def _album(row) -> Dict[str, Any]:
    out = dict(row)
    out["monitored"] = bool(out["monitored"])
    out["any_release_ok"] = bool(out["any_release_ok"])
    try:
        out["secondary_types"] = list(json.loads(out.get("secondary_types") or "[]"))
    except (TypeError, ValueError):
        out["secondary_types"] = []
    return out


class LidarrStore:
    """thin sqlite layer. connect() hands back a new connection each call."""

    def __init__(self, connect, now=time.time):
        self._connect = connect
        self._now = now

    def _rows(self, sql, args=()):
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def _write(self, sql, args=()):
        conn = self._connect()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur
        finally:
            conn.close()

    # artists

    def artists(self) -> List[Dict[str, Any]]:
        return [_artist(r) for r in self._rows("SELECT * FROM lidarr_compat_artists ORDER BY name")]

    def artist(self, artist_id) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM lidarr_compat_artists WHERE id = ?", (int(artist_id),))
        return _artist(rows[0]) if rows else None

    def artist_by_mbid(self, mbid) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM lidarr_compat_artists WHERE mbid = ?", (str(mbid),))
        return _artist(rows[0]) if rows else None

    def add_artist(self, mbid, **fields) -> Dict[str, Any]:
        """insert once. a second add for the same mbid hands back the first."""
        values = {k: fields[k] for k in _ARTIST_FIELDS if k in fields}
        values["monitored"] = int(bool(values.get("monitored", False)))
        cols = ["mbid", "added_at", *values]
        self._write(
            f"INSERT OR IGNORE INTO lidarr_compat_artists ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            (str(mbid), self._now(), *values.values()))
        return self.artist_by_mbid(mbid)

    def update_artist(self, artist_id, **fields) -> Optional[Dict[str, Any]]:
        values = {k: fields[k] for k in _ARTIST_FIELDS if k in fields}
        if "monitored" in values:
            values["monitored"] = int(bool(values["monitored"]))
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self._write(f"UPDATE lidarr_compat_artists SET {sets} WHERE id = ?",
                        (*values.values(), int(artist_id)))
        return self.artist(artist_id)

    def delete_artist(self, artist_id) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM lidarr_compat_albums WHERE artist_id = ?", (int(artist_id),))
            conn.execute("DELETE FROM lidarr_compat_artists WHERE id = ?", (int(artist_id),))
            conn.commit()
        finally:
            conn.close()

    # albums

    def albums(self, *, artist_id=None, mbid=None, ids: Optional[Iterable[int]] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM lidarr_compat_albums", []
        where = []
        if artist_id is not None:
            where.append("artist_id = ?")
            args.append(int(artist_id))
        if mbid is not None:
            where.append("mbid = ?")
            args.append(str(mbid))
        if ids is not None:
            ids = [int(i) for i in ids]
            if not ids:
                return []
            where.append(f"id IN ({', '.join('?' for _ in ids)})")
            args.extend(ids)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [_album(r) for r in self._rows(sql + " ORDER BY release_date DESC, id", args)]

    def album(self, album_id) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM lidarr_compat_albums WHERE id = ?", (int(album_id),))
        return _album(rows[0]) if rows else None

    def album_by_mbid(self, mbid) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM lidarr_compat_albums WHERE mbid = ?", (str(mbid),))
        return _album(rows[0]) if rows else None

    def add_album(self, mbid, artist_id, **fields) -> Dict[str, Any]:
        """insert once. an album already known keeps its row and its id."""
        values = {k: fields[k] for k in _ALBUM_FIELDS if k in fields}
        if "secondary_types" in values:
            values["secondary_types"] = json.dumps(list(values["secondary_types"] or []))
        for flag in ("monitored", "any_release_ok"):
            if flag in values:
                values[flag] = int(bool(values[flag]))
        cols = ["mbid", "artist_id", "added_at", *values]
        self._write(
            f"INSERT OR IGNORE INTO lidarr_compat_albums ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            (str(mbid), int(artist_id), self._now(), *values.values()))
        return self.album_by_mbid(mbid)

    def update_album(self, album_id, **fields) -> Optional[Dict[str, Any]]:
        values = {k: fields[k] for k in _ALBUM_FIELDS if k in fields}
        if "secondary_types" in values:
            values["secondary_types"] = json.dumps(list(values["secondary_types"] or []))
        for flag in ("monitored", "any_release_ok"):
            if flag in values:
                values[flag] = int(bool(values[flag]))
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            self._write(f"UPDATE lidarr_compat_albums SET {sets} WHERE id = ?",
                        (*values.values(), int(album_id)))
        return self.album(album_id)

    def delete_album(self, album_id) -> None:
        self._write("DELETE FROM lidarr_compat_albums WHERE id = ?", (int(album_id),))
