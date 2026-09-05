"""Map media-server identities onto an existing Library-v2 catalogue.

After the upgrade, a server scan is not an ownership/import path. Catalogue and
file rows come from the import/download pipeline; a server only stamps its own
IDs and technical observations onto those existing rows. ``allow_create`` is
reserved for that pipeline's shared helpers.

Three rules run through every upsert here:

**Match by the server id, fall back to identity, then stamp.** A rating key is
not stable — a library rescan hands out new ones, which is why the legacy code
carried a whole "ratingKey migrated" dance that copied enrichment onto a new
row. A catalogue row has its own id, so a changed rating key is nothing but a
new stamp on the row we already had.

**The server is only an identity/technical observer.** It may stamp its own id
and refresh track/disc number, duration, size and bitrate.  It does not create
catalogue or file ownership and does not replace provider/import metadata.

**A file is a row** (ADR-03). The server's path/size/bitrate land on
``lib2_track_files``, not on the track.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from utils.logging_config import get_logger
from core.library2.media_mappings import (
    is_media_server_source,
    resolve_mapping,
    upsert_mapping,
)
from core.library2.track_files import elect_primary_file

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
                  genres_json: Optional[str] = None,
                  overwrite: bool = True,
                  allow_create: bool = False) -> Optional[int]:
    """The catalogue id for an artist the server reported.

    ``allow_create`` is exclusively for the successful import pipeline.  A
    normal media-server scan only stamps an existing row.
    """
    mapped_id = resolve_mapping(cursor, "artist", server_source, server_id)
    row = ((mapped_id,) if mapped_id is not None else cursor.execute(
        "SELECT id FROM lib2_artists WHERE server_source=? AND server_id=? AND (? OR"
        " EXISTS (SELECT 1 FROM lib2_albums al JOIN lib2_tracks t ON t.album_id=al.id"
        " JOIN lib2_track_files f ON f.track_id=t.id WHERE al.primary_artist_id=lib2_artists.id"
        " AND f.file_state='active' AND TRIM(f.path)<>''))",
        (server_source, str(server_id), allow_create),
    ).fetchone())
    if row is None:
        # A rescan re-keyed the artist, or the row came from an import/download.
        # Same artist either way — take it over and stamp the new id on it.
        candidates = cursor.execute(
            "SELECT id FROM lib2_artists "
            " WHERE name_key=? AND canonical_artist_id IS NULL "
            "   AND EXISTS (SELECT 1 FROM lib2_albums al JOIN lib2_tracks t"
            "                ON t.album_id=al.id JOIN lib2_track_files f ON f.track_id=t.id"
            "                WHERE al.primary_artist_id=lib2_artists.id"
            "                  AND f.file_state='active' AND TRIM(f.path)<>'') "
            " ORDER BY id LIMIT 2",
            (_name_key(name),),
        ).fetchall()
        # A name-only match is safe only when it identifies one owned artist.
        row = candidates[0] if len(candidates) == 1 else None
    if row is None:
        if not allow_create:
            return None
        artist_id = int(cursor.execute(
            "INSERT INTO lib2_artists(name, name_key, sort_name, image_url, genres,"
            "                         server_source, server_id, monitored)"
            " VALUES(?,?,?,?,COALESCE(?, '[]'),?,?,0)",
            (name, _name_key(name), name, image_url, genres_json,
             server_source, str(server_id)),
        ).lastrowid)
        if is_media_server_source(server_source):
            upsert_mapping(cursor, "artist", artist_id, server_source, server_id)
        return artist_id
    artist_id = int(row[0])
    if not allow_create:
        upsert_mapping(cursor, "artist", artist_id, server_source, server_id)
        return artist_id
    image_set = (
        "image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url "
        "WHEN image_url IS NULL OR image_url='' THEN COALESCE(?, image_url) "
        "ELSE image_url END"
        if not overwrite else
        "image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url "
        "ELSE COALESCE(NULLIF(?, ''), image_url) END"
    )
    genres_set = ("genres=CASE WHEN genres IS NULL OR genres IN ('', '[]') "
                  "THEN COALESCE(?, genres) ELSE genres END"
                  if not overwrite else "genres=COALESCE(?, genres)")
    cursor.execute(
        "UPDATE lib2_artists"
        "   SET name=?, name_key=?,"
        f"       {image_set},"
        f"       {genres_set},"
        "       server_source=?, server_id=?, updated_at=CURRENT_TIMESTAMP"
        " WHERE id=?",
        (name, _name_key(name), image_url, genres_json, server_source,
         str(server_id), artist_id),
    )
    if is_media_server_source(server_source):
        upsert_mapping(cursor, "artist", artist_id, server_source, server_id)
    return artist_id


def resolve_artist(cursor, server_source: str, server_id: Any) -> Optional[int]:
    mapped = resolve_mapping(cursor, "artist", server_source, server_id)
    if mapped is not None:
        return mapped
    row = cursor.execute(
        "SELECT id FROM lib2_artists WHERE server_source=? AND server_id=?",
        (server_source, str(server_id)),
    ).fetchone()
    return int(row[0]) if row else None


def resolve_album(cursor, server_source: str, server_id: Any) -> Optional[int]:
    mapped = resolve_mapping(cursor, "album", server_source, server_id)
    if mapped is not None:
        return mapped
    row = cursor.execute(
        "SELECT id FROM lib2_albums WHERE server_source=? AND server_id=?",
        (server_source, str(server_id)),
    ).fetchone()
    return int(row[0]) if row else None


def resolve_track(cursor, server_source: str, server_id: Any) -> Optional[int]:
    mapped = resolve_mapping(cursor, "track", server_source, server_id)
    if mapped is not None:
        return mapped
    row = cursor.execute(
        "SELECT id FROM lib2_tracks WHERE server_source=? AND server_id=?",
        (server_source, str(server_id)),
    ).fetchone()
    return int(row[0]) if row else None


def upsert_album(cursor, *, server_source: str, server_id: str, artist_id: int,
                 title: str, year=None, image_url: Optional[str] = None,
                 genres_json: Optional[str] = None,
                 track_count=None, duration=None,
                 allow_create: bool = False) -> Optional[int]:
    """Map a server release; imports alone may create/own the row."""
    mapped_id = resolve_mapping(cursor, "album", server_source, server_id)
    row = ((mapped_id,) if mapped_id is not None else cursor.execute(
        "SELECT id FROM lib2_albums WHERE server_source=? AND server_id=? AND (? OR"
        " EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id"
        " WHERE t.album_id=lib2_albums.id AND f.file_state='active' AND TRIM(f.path)<>''))",
        (server_source, str(server_id), allow_create),
    ).fetchone())
    if row is None:
        candidates = cursor.execute(
            "SELECT id FROM lib2_albums"
            " WHERE primary_artist_id=? AND LOWER(title)=LOWER(?)"
            "   AND EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f"
            "                ON f.track_id=t.id WHERE t.album_id=lib2_albums.id"
            "                  AND f.file_state='active' AND TRIM(f.path)<>'')"
            " ORDER BY id LIMIT 2",
            (artist_id, title),
        ).fetchall()
        row = candidates[0] if len(candidates) == 1 else None
    if row is None:
        if not allow_create:
            return None
        album_id = int(cursor.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title, year, image_url,"
            "                        genres, track_count, duration, origin,"
            "                        server_source, server_id)"
            " VALUES(?,?,?,?,COALESCE(?, '[]'),?,?, 'library', ?, ?)",
            (artist_id, title, year, image_url, genres_json, track_count, duration,
             server_source, str(server_id)),
        ).lastrowid)
    else:
        album_id = int(row[0])
        if not allow_create:
            cursor.execute(
                "UPDATE lib2_albums SET track_count=COALESCE(?,track_count),"
                "duration=COALESCE(?,duration),"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (track_count, duration, album_id))
            upsert_mapping(cursor, "album", album_id, server_source, server_id)
            return album_id
        cursor.execute(
            "UPDATE lib2_albums"
            "   SET primary_artist_id=?, title=?, year=COALESCE(?, year),"
            "       image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url "
            "                      ELSE COALESCE(NULLIF(?, ''), image_url) END,"
            "       genres=COALESCE(?, genres),"
            "       track_count=COALESCE(?, track_count),"
            "       duration=COALESCE(?, duration),"
            "       origin='library', server_source=?, server_id=?,"
            "       updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?",
            (artist_id, title, year, image_url, genres_json, track_count, duration,
             server_source, str(server_id), album_id),
        )
    if is_media_server_source(server_source):
        upsert_mapping(cursor, "album", album_id, server_source, server_id)
    cursor.execute(
        "DELETE FROM lib2_album_artists WHERE album_id=? AND role='primary' "
        "AND artist_id<>?", (album_id, artist_id))
    cursor.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role)"
        " VALUES(?,?,'primary') ON CONFLICT(album_id, artist_id)"
        " DO UPDATE SET role='primary'", (album_id, artist_id))
    return album_id


def upsert_track(cursor, *, server_source: str, server_id: str, album_id: int,
                 artist_id: int, title: str, track_number=None, disc_number=None,
                 duration=None, track_artist: Optional[str] = None,
                 musicbrainz_id: Optional[str] = None,
                 file_path: Optional[str] = None, file_size=None,
                 bitrate=None, allow_create: bool = False,
                 file_source: Optional[str] = None) -> Optional[int]:
    """The catalogue id for a track the server reported, plus its file row."""
    mapped_id = resolve_mapping(cursor, "track", server_source, server_id)
    row = ((mapped_id,) if mapped_id is not None else cursor.execute(
        "SELECT id FROM lib2_tracks WHERE server_source=? AND server_id=? AND (? OR"
        " EXISTS (SELECT 1 FROM lib2_track_files f WHERE f.track_id=lib2_tracks.id"
        " AND f.file_state='active' AND TRIM(f.path)<>''))",
        (server_source, str(server_id), allow_create),
    ).fetchone())
    if row is None:
        row = cursor.execute(
            "SELECT t.id FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id"
            " WHERE f.path=? AND f.file_state='active' LIMIT 1", (file_path,)
        ).fetchone() if file_path else None
    if row is None:
        candidates = cursor.execute(
            "SELECT id FROM lib2_tracks"
            " WHERE album_id=? AND LOWER(title)=LOWER(?)"
            "   AND ((NULLIF(?, '') IS NOT NULL AND musicbrainz_id=?)"
            "     OR (? IS NOT NULL AND track_number=?"
            "         AND COALESCE(disc_number,1)=COALESCE(?,1)))"
            "   AND EXISTS (SELECT 1 FROM lib2_track_files f"
            "                WHERE f.track_id=lib2_tracks.id AND f.file_state='active'"
            "                  AND TRIM(f.path)<>'')"
            " ORDER BY id LIMIT 2",
            (album_id, title, musicbrainz_id, musicbrainz_id,
             track_number, track_number, disc_number),
        ).fetchall()
        row = candidates[0] if len(candidates) == 1 else None
    if row is None:
        if not allow_create:
            return None
        track_id = int(cursor.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,"
            "                        duration, track_artist, musicbrainz_id,"
            "                        server_source, server_id)"
            " VALUES(?,?,COALESCE(?,1),COALESCE(?,1),?,?,?,?,?)",
            (album_id, title, track_number, disc_number, duration, track_artist,
             musicbrainz_id, server_source, str(server_id)),
        ).lastrowid)
    else:
        track_id = int(row[0])
        if not allow_create:
            cursor.execute(
                "UPDATE lib2_tracks SET track_number=COALESCE(?,track_number),"
                "disc_number=COALESCE(?,disc_number),duration=COALESCE(?,duration),"
                "track_artist=COALESCE(?,track_artist),"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (track_number, disc_number, duration, track_artist,
                 track_id))
            upsert_mapping(cursor, "track", track_id, server_source, server_id)
            if file_path:
                cursor.execute(
                    "UPDATE lib2_track_files SET size=COALESCE(?,size),"
                    "bitrate=COALESCE(?,bitrate),server_source=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE track_id=? AND path=?"
                    " AND file_state='active'",
                    (file_size, bitrate, server_source, track_id, file_path))
            return track_id
        # Ownership may correct an existing row, never blank it: a caller that
        # does not know the disc (the importer never passes one) must not stamp
        # its default over the disc the catalogue already holds.
        cursor.execute(
            "UPDATE lib2_tracks"
            "   SET album_id=?, title=?,"
            "       track_number=COALESCE(?, track_number),"
            "       disc_number=COALESCE(?, disc_number),"
            "       duration=COALESCE(?, duration),"
            "       track_artist=COALESCE(?, track_artist),"
            "       musicbrainz_id=COALESCE(?, musicbrainz_id),"
            "       server_source=?, server_id=?, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?",
            (album_id, title, track_number, disc_number, duration, track_artist,
             musicbrainz_id, server_source, str(server_id), track_id),
        )
    if is_media_server_source(server_source):
        upsert_mapping(cursor, "track", track_id, server_source, server_id)
    cursor.execute(
        "DELETE FROM lib2_track_artists WHERE track_id=? AND role='primary' "
        "AND artist_id<>?", (track_id, artist_id))
    cursor.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position)"
        " VALUES(?,?,'primary',0) ON CONFLICT(track_id, artist_id)"
        " DO UPDATE SET role='primary', position=0", (track_id, artist_id))
    if file_path and allow_create:
        _upsert_file(cursor, track_id, file_path, file_size, bitrate,
                     server_source=server_source, source=file_source)
    return track_id


def _upsert_file(cursor, track_id: int, path: str, size, bitrate,
                 server_source=None, source=None) -> None:
    """Refresh a file row, then apply ADR-03's shared primary election.

    A media-server observation must not silently override a manual selection
    or promote a lower-quality derivative merely because it was observed
    last.  Inserts/updates already participate through schema triggers; the
    explicit election here also converges databases created before them.
    """
    row = cursor.execute(
        "SELECT id FROM lib2_track_files WHERE track_id=? AND path=?",
        (track_id, path)).fetchone()
    fmt = path.rsplit('.', 1)[-1].lower() if '.' in path else None
    if row:
        cursor.execute(
            "UPDATE lib2_track_files"
            "   SET size=COALESCE(?, size), bitrate=COALESCE(?, bitrate),"
            "       format=COALESCE(format, ?), file_state='active',"
            "       server_source=COALESCE(?, server_source),"
            "       source=COALESCE(source, ?),"
            "       updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?",
            (size, bitrate, fmt, server_source, source, int(row[0])))
        observed_id = int(row[0])
    else:
        observed_id = int(cursor.execute(
            "INSERT INTO lib2_track_files(track_id, path, size, bitrate, format,"
            " server_source, source, file_state, import_status)"
            " VALUES(?,?,?,?,?,?,?,'active','imported')",
            (track_id, path, size, bitrate, fmt, server_source, source)).lastrowid)
    cursor.execute(
        "UPDATE lib2_track_files SET file_state=CASE WHEN source IS NULL "
        "AND legacy_track_id IS NULL THEN 'deleted' ELSE file_state END,"
        " server_source=NULL, updated_at=CURRENT_TIMESTAMP"
        " WHERE track_id=? AND id<>? AND server_source=?",
        (track_id, observed_id, server_source))
    elect_primary_file(cursor, track_id)


__all__ = [
    "resolve_album", "resolve_artist", "resolve_track", "upsert_album", "upsert_artist",
    "upsert_track", "_genres_json",
]
