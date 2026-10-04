"""Test helper: seed a Library-v2 catalogue the way upstream tests seed the
legacy ``artists`` / ``albums`` / ``tracks`` tables.

Upstream's tests insert legacy rows; this branch reads ``lib2_*``. Porting a
fixture is then one call per track instead of three INSERTs against a
different schema. Artists and albums are found or created by name, so a loop
over tracks of one album yields one album row. A track is owned when it has a
live file row, which is what ``owned=True`` (the default) gives it.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional


def row_conn(path: str) -> sqlite3.Connection:
    """A connection to ``path`` whose rows are ``sqlite3.Row``."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


class RowDb:
    """MusicDatabase stand-in: ``_get_connection`` opens ``path`` via ``row_conn``."""

    def __init__(self, path: str):
        self.path = path

    def _get_connection(self) -> sqlite3.Connection:
        return row_conn(self.path)


def artist(conn, name: str, **cols: Any) -> int:
    row = conn.execute("SELECT id FROM lib2_artists WHERE name = ?", (name,)).fetchone()
    if row:
        if cols:
            sets = ", ".join(f"{k} = ?" for k in cols)
            conn.execute(f"UPDATE lib2_artists SET {sets} WHERE id = ?", (*cols.values(), row[0]))
        return int(row[0])
    keys = ["name", "name_key", *cols]
    values = [name, name.strip().lower(), *cols.values()]
    return int(conn.execute(
        f"INSERT INTO lib2_artists ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
        values).lastrowid)


def album(conn, artist_name: str, title: str, **cols: Any) -> int:
    artist_id = artist(conn, artist_name)
    row = conn.execute("SELECT id FROM lib2_albums WHERE primary_artist_id = ? AND title = ?",
                       (artist_id, title)).fetchone()
    if row:
        if cols:
            sets = ", ".join(f"{k} = ?" for k in cols)
            conn.execute(f"UPDATE lib2_albums SET {sets} WHERE id = ?", (*cols.values(), row[0]))
        return int(row[0])
    cols.setdefault("origin", "library")
    keys = ["primary_artist_id", "title", *cols]
    values = [artist_id, title, *cols.values()]
    return int(conn.execute(
        f"INSERT INTO lib2_albums ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
        values).lastrowid)


def track(conn, artist_name: str, album_title: str, title: str, *, owned: bool = True,
          path: Optional[str] = None, credit: Optional[str] = None,
          album_cols: Optional[Dict[str, Any]] = None, **cols: Any) -> int:
    """One ``lib2_tracks`` row on ``artist_name``'s ``album_title``. ``credit``
    adds a primary track credit (a different artist than the album's)."""
    album_id = album(conn, artist_name, album_title, **(album_cols or {}))
    keys = ["album_id", "title", *cols]
    values = [album_id, title, *cols.values()]
    track_id = int(conn.execute(
        f"INSERT INTO lib2_tracks ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
        values).lastrowid)
    if credit:
        conn.execute("INSERT INTO lib2_track_artists (track_id, artist_id) VALUES (?, ?)",
                     (track_id, artist(conn, credit)))
    if owned:
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
            (track_id, path or f"/music/{artist_name}/{album_title}/{track_id}.flac"))
    return track_id


def file_track(conn, track_id: int, album_id: int, title: str, path: str) -> int:
    """A ``lib2_tracks`` row with an explicit id and its primary file -- the
    shape upstream's ``INSERT INTO tracks (id, album_id, ..., file_path)`` had."""
    conn.execute("INSERT INTO lib2_tracks (id, album_id, title) VALUES (?, ?, ?)",
                 (int(track_id), int(album_id), title))
    conn.execute("INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
                 (int(track_id), str(path)))
    return int(track_id)
