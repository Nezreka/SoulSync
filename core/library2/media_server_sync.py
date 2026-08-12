"""Writing what a media server reports into the Library v2 catalogue.

The scan is the one path on which rows come into existence, and until now it
wrote the legacy tables. It writes the catalogue now, keyed by the server's own
id (``server_source`` + ``server_id``, §50.4.4.25).

Three rules run through every upsert here:

**Match by the server id, fall back to identity, then stamp.** A rating key is
not stable — a library rescan hands out new ones, which is why the legacy code
carried a whole "ratingKey migrated" dance that copied enrichment onto a new
row. A catalogue row has its own id, so a changed rating key is nothing but a
new stamp on the row we already had.

**The server is authoritative for what the server knows** (title, track number,
duration, path) **and for nothing else.** Everything a provider enriched —
artwork, genres, bios, provider ids — is only ever filled in, never cleared, by
a scan: ``COALESCE`` on the way in, and no write at all when the server sent
nothing.

**A file is a row** (ADR-03). The server's path/size/bitrate land on
``lib2_track_files``, not on the track.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.media_server_sync")


def _name_key(name: Any) -> str:
    from core.library2.importer import normalize_name

    return normalize_name(str(name or ""))


def _genres_json(obj: Any) -> Optional[str]:
    """The server's genre list as v2 stores it, or None when it sent none."""
    genres = []
    raw = getattr(obj, "genres", None)
    if raw:
        genres = [g.tag if hasattr(g, "tag") else str(g) for g in raw]
    genres = [str(g).strip() for g in genres if str(g).strip()]
    return json.dumps(genres) if genres else None


def upsert_artist(cursor, *, server_source: str, server_id: str, name: str,
                  image_url: Optional[str] = None,
                  genres_json: Optional[str] = None) -> int:
    """The catalogue id for an artist the server reported."""
    row = cursor.execute(
        "SELECT id FROM lib2_artists WHERE server_source=? AND server_id=?",
        (server_source, str(server_id)),
    ).fetchone()
    if row is None:
        # A rescan re-keyed the artist, or the row came from an import/download.
        # Same artist either way — take it over and stamp the new id on it.
        row = cursor.execute(
            "SELECT id FROM lib2_artists "
            " WHERE name_key=? AND canonical_artist_id IS NULL "
            "   AND (server_id IS NULL OR server_source=?) "
            " ORDER BY (server_id IS NOT NULL) DESC, id LIMIT 1",
            (_name_key(name), server_source),
        ).fetchone()
    if row is None:
        return int(cursor.execute(
            "INSERT INTO lib2_artists(name, name_key, sort_name, image_url, genres,"
            "                         server_source, server_id, monitored)"
            " VALUES(?,?,?,?,COALESCE(?, '[]'),?,?,0)",
            (name, _name_key(name), name, image_url, genres_json,
             server_source, str(server_id)),
        ).lastrowid)
    artist_id = int(row[0])
    cursor.execute(
        "UPDATE lib2_artists"
        "   SET name=?, name_key=?,"
        "       image_url=COALESCE(NULLIF(?, ''), image_url),"
        "       genres=COALESCE(?, genres),"
        "       server_source=?, server_id=?, updated_at=CURRENT_TIMESTAMP"
        " WHERE id=?",
        (name, _name_key(name), image_url, genres_json, server_source,
         str(server_id), artist_id),
    )
    return artist_id


def resolve_artist(cursor, server_source: str, server_id: Any) -> Optional[int]:
    row = cursor.execute(
        "SELECT id FROM lib2_artists WHERE server_source=? AND server_id=?",
        (server_source, str(server_id)),
    ).fetchone()
    return int(row[0]) if row else None


def resolve_album(cursor, server_source: str, server_id: Any) -> Optional[int]:
    row = cursor.execute(
        "SELECT id FROM lib2_albums WHERE server_source=? AND server_id=?",
        (server_source, str(server_id)),
    ).fetchone()
    return int(row[0]) if row else None


def upsert_album(cursor, *, server_source: str, server_id: str, artist_id: int,
                 title: str, year=None, image_url: Optional[str] = None,
                 genres_json: Optional[str] = None,
                 track_count=None) -> int:
    """The catalogue id for a release the server reported.

    ``origin='library'`` is the point of the whole row: the server has the
    files, so the user owns it — as opposed to the discography rows v2 keeps
    for artists it merely follows.
    """
    row = cursor.execute(
        "SELECT id FROM lib2_albums WHERE server_source=? AND server_id=?",
        (server_source, str(server_id)),
    ).fetchone()
    if row is None:
        row = cursor.execute(
            "SELECT id FROM lib2_albums"
            " WHERE primary_artist_id=? AND LOWER(title)=LOWER(?)"
            "   AND (server_id IS NULL OR server_source=?)"
            " ORDER BY (server_id IS NOT NULL) DESC, id LIMIT 1",
            (artist_id, title, server_source),
        ).fetchone()
    if row is None:
        album_id = int(cursor.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, year, image_url,"
            "                        genres, track_count, origin, server_source,"
            "                        server_id)"
            " VALUES(?,?,?,?,COALESCE(?, '[]'),?, 'library', ?, ?)",
            (artist_id, title, year, image_url, genres_json, track_count,
             server_source, str(server_id)),
        ).lastrowid)
    else:
        album_id = int(row[0])
        cursor.execute(
            "UPDATE lib2_albums"
            "   SET primary_artist_id=?, title=?, year=COALESCE(?, year),"
            "       image_url=COALESCE(NULLIF(?, ''), image_url),"
            "       genres=COALESCE(?, genres),"
            "       track_count=COALESCE(?, track_count),"
            "       origin='library', server_source=?, server_id=?,"
            "       updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?",
            (artist_id, title, year, image_url, genres_json, track_count,
             server_source, str(server_id), album_id),
        )
    cursor.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
        " VALUES(?,?,'primary')", (album_id, artist_id))
    return album_id


def upsert_track(cursor, *, server_source: str, server_id: str, album_id: int,
                 artist_id: int, title: str, track_number=None, disc_number=1,
                 duration=None, track_artist: Optional[str] = None,
                 musicbrainz_id: Optional[str] = None,
                 file_path: Optional[str] = None, file_size=None,
                 bitrate=None) -> int:
    """The catalogue id for a track the server reported, plus its file row."""
    row = cursor.execute(
        "SELECT id FROM lib2_tracks WHERE server_source=? AND server_id=?",
        (server_source, str(server_id)),
    ).fetchone()
    if row is None:
        row = cursor.execute(
            "SELECT id FROM lib2_tracks"
            " WHERE album_id=? AND LOWER(title)=LOWER(?)"
            "   AND (server_id IS NULL OR server_source=?)"
            " ORDER BY (server_id IS NOT NULL) DESC, id LIMIT 1",
            (album_id, title, server_source),
        ).fetchone()
    if row is None:
        track_id = int(cursor.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,"
            "                        duration, track_artist, musicbrainz_id,"
            "                        server_source, server_id)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (album_id, title, track_number, disc_number, duration, track_artist,
             musicbrainz_id, server_source, str(server_id)),
        ).lastrowid)
    else:
        track_id = int(row[0])
        cursor.execute(
            "UPDATE lib2_tracks"
            "   SET album_id=?, title=?, track_number=?, disc_number=?,"
            "       duration=COALESCE(?, duration),"
            "       track_artist=COALESCE(?, track_artist),"
            "       musicbrainz_id=COALESCE(?, musicbrainz_id),"
            "       server_source=?, server_id=?, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?",
            (album_id, title, track_number, disc_number, duration, track_artist,
             musicbrainz_id, server_source, str(server_id), track_id),
        )
    cursor.execute(
        "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)"
        " VALUES(?,?,'primary',0)", (track_id, artist_id))
    if file_path:
        _upsert_file(cursor, track_id, file_path, file_size, bitrate)
    return track_id


def _upsert_file(cursor, track_id: int, path: str, size, bitrate) -> None:
    """The track's file row. One primary per track (ADR-03)."""
    row = cursor.execute(
        "SELECT id FROM lib2_track_files WHERE track_id=? AND path=?",
        (track_id, path)).fetchone()
    fmt = path.rsplit('.', 1)[-1].lower() if '.' in path else None
    if row:
        cursor.execute(
            "UPDATE lib2_track_files"
            "   SET size=COALESCE(?, size), bitrate=COALESCE(?, bitrate),"
            "       format=COALESCE(format, ?), file_state='active',"
            "       is_primary=1, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?",
            (size, bitrate, fmt, int(row[0])))
        primary_id = int(row[0])
    else:
        primary_id = int(cursor.execute(
            "INSERT INTO lib2_track_files(track_id, path, size, bitrate, format,"
            "                             is_primary, file_state, import_status)"
            " VALUES(?,?,?,?,?,1,'active','imported')",
            (track_id, path, size, bitrate, fmt)).lastrowid)
    cursor.execute(
        "UPDATE lib2_track_files SET is_primary=0 WHERE track_id=? AND id<>?",
        (track_id, primary_id))


__all__ = [
    "resolve_album", "resolve_artist", "upsert_album", "upsert_artist",
    "upsert_track", "_genres_json",
]
