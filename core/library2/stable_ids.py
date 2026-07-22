"""Stable provider-less IDs for Library-v2 entities (audit P1-12).

Wishlist mirroring needs an ID for tracks/albums that have no Spotify ID.
Using the SQLite rowid (``lib2-track:<rowid>``) is not migration-stable: a
library reset deletes lib2 rows but keeps the wishlist, and re-imported
rows get NEW rowids — old wishlist items orphan, new ones duplicate, and a
reused rowid can silently point a wishlist row at the wrong track.

The fix is a persisted ``stable_id`` per row, minted ONCE from the entity's
natural identity (artist/album/title/position). Deterministic minting means
a reset + reimport of the same library reproduces the same IDs, so existing
wishlist rows keep matching; persisting the value means later metadata
edits don't silently change an ID that wishlist rows already reference.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.stable_ids")


def _norm(value: Optional[Any]) -> str:
    """Normalize one identity component: NFC, casefolded, whitespace-collapsed.

    Deliberately conservative — this must be reproducible forever, so no
    clever fuzzy cleanup that might change between releases."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return " ".join(text.split()).casefold()


def _digest(kind: str, parts: Iterable[str]) -> str:
    payload = "\x1f".join([kind, *parts]).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def compute_album_stable_id(artist_name: Optional[str], title: Optional[str],
                            album_type: Optional[str]) -> str:
    """Deterministic identity for an album: primary artist + title + type.

    ``album_type`` keeps an album and its same-named single/EP apart."""
    return _digest("album", (_norm(artist_name), _norm(title), _norm(album_type)))


def compute_track_stable_id(album_stable_id: str, title: Optional[str],
                            disc_number: Optional[Any],
                            track_number: Optional[Any]) -> str:
    """Deterministic identity for a track within its album's identity."""
    return _digest("track", (album_stable_id, _norm(title),
                             _norm(disc_number), _norm(track_number)))


def ensure_album_stable_id(conn, album_id: int) -> Optional[str]:
    """The album's persisted stable_id, minting and storing it when absent."""
    row = conn.execute(
        """SELECT al.stable_id, al.title, al.album_type, ar.name AS artist_name
             FROM lib2_albums al
             JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE al.id = ?""", (album_id,)).fetchone()
    if row is None:
        return None
    if row["stable_id"]:
        return row["stable_id"]
    stable_id = compute_album_stable_id(row["artist_name"], row["title"],
                                        row["album_type"])
    conn.execute("UPDATE lib2_albums SET stable_id=? WHERE id=? AND stable_id IS NULL",
                 (stable_id, album_id))
    return stable_id


def ensure_track_stable_id(conn, track_id: int) -> Optional[str]:
    """The track's persisted stable_id, minting and storing it when absent."""
    row = conn.execute(
        """SELECT t.stable_id, t.title, t.disc_number, t.track_number, t.album_id
             FROM lib2_tracks t WHERE t.id = ?""", (track_id,)).fetchone()
    if row is None:
        return None
    if row["stable_id"]:
        return row["stable_id"]
    album_sid = ensure_album_stable_id(conn, row["album_id"]) or ""
    stable_id = compute_track_stable_id(album_sid, row["title"],
                                        row["disc_number"], row["track_number"])
    conn.execute("UPDATE lib2_tracks SET stable_id=? WHERE id=? AND stable_id IS NULL",
                 (stable_id, track_id))
    return stable_id


def backfill_stable_ids(cursor) -> int:
    """Mint stable_ids for every lib2 album/track that lacks one.

    Called from the schema-ensure step so existing installs converge once;
    afterwards rows are filled lazily on first use. Returns how many rows
    were filled."""
    filled = 0
    # Positional access — works for plain tuples and sqlite3.Row alike.
    albums = cursor.execute(
        """SELECT al.id, ar.name, al.title, al.album_type
             FROM lib2_albums al
             JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE al.stable_id IS NULL""").fetchall()
    for album_id, artist_name, title, album_type in albums:
        cursor.execute(
            "UPDATE lib2_albums SET stable_id=? WHERE id=? AND stable_id IS NULL",
            (compute_album_stable_id(artist_name, title, album_type), album_id))
        filled += cursor.rowcount
    tracks = cursor.execute(
        """SELECT t.id, al.stable_id, t.title, t.disc_number, t.track_number
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id = t.album_id
            WHERE t.stable_id IS NULL""").fetchall()
    for track_id, album_sid, title, disc_number, track_number in tracks:
        cursor.execute(
            "UPDATE lib2_tracks SET stable_id=? WHERE id=? AND stable_id IS NULL",
            (compute_track_stable_id(album_sid or "", title,
                                     disc_number, track_number), track_id))
        filled += cursor.rowcount
    if filled:
        logger.info("Backfilled %d Library-v2 stable_ids", filled)
    return filled


__all__ = [
    "backfill_stable_ids",
    "compute_album_stable_id",
    "compute_track_stable_id",
    "ensure_album_stable_id",
    "ensure_track_stable_id",
]
