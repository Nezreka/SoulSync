"""Map media-server identities onto the Library-v2 catalogue.

A server scan stamps its own IDs and technical observations onto the rows the
catalogue already has. A file it lists that the catalogue lacks (a ripped CD,
Lidarr, a copy into the music folder) is not written here either:
``adopt_scanned_file`` hands it to the import writer, like a finished
download. ``allow_create`` is reserved for that pipeline's shared helpers.

Three rules run through every upsert here:

**Match by the server id, fall back to identity, then stamp.** A rating key is
not stable — a library rescan hands out new ones, which is why the legacy code
carried a whole "ratingKey migrated" dance that copied enrichment onto a new
row. A catalogue row has its own id, so a changed rating key is nothing but a
new stamp on the row we already had.

**The server is only an identity/technical observer.** It may stamp its own id
and refresh track/disc number, duration, size and bitrate.  It does not create
catalogue or file ownership itself and does not replace provider/import metadata.

**A file is a row** (ADR-03). The server's path/size/bitrate land on
``lib2_track_files``, not on the track.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from utils.logging_config import get_logger
from core.library2.media_mappings import (
    is_media_server_source,
    resolve_mapping as _resolve_mapping,
    upsert_mapping as _upsert_mapping,
)
from core.library2.track_files import elect_primary_file

logger = get_logger("library2.media_server_sync")


def scan_library_id(owner_profile_id: Any = None) -> str:
    """The server library a scan reads (#1199): '' for the shared one,
    ``own:<pid>`` for a profile's own. One catalogue row seen by both scans
    keeps both ids side by side instead of each scan overwriting the other's.
    Without an argument: the library the running scan was scoped to."""
    if owner_profile_id is None:
        from core.library_scope import _explicit_scope
        owner_profile_id = _explicit_scope.get()
    if isinstance(owner_profile_id, int) and not isinstance(owner_profile_id, bool):
        return f"own:{owner_profile_id}"
    return ""


def resolve_mapping(cursor: Any, entity_type: str, server_source: Any, server_id: Any):
    return _resolve_mapping(cursor, entity_type, server_source, server_id, scan_library_id())


def upsert_mapping(cursor: Any, entity_type: str, entity_id: int,
                   server_source: Any, server_id: Any, *, server_path: Optional[str] = None) -> None:
    _upsert_mapping(cursor, entity_type, entity_id, server_source, server_id,
                    scan_library_id(), server_path=server_path)


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
    from core.library2.sql_util import owned_sql
    owned_artist = owned_sql('artist', 'lib2_artists')
    mapped_id = resolve_mapping(cursor, "artist", server_source, server_id)
    row = ((mapped_id,) if mapped_id is not None else cursor.execute(
        "SELECT id FROM lib2_artists WHERE server_source=? AND server_id=? AND (? OR "
        + owned_artist + ")",
        (server_source, str(server_id), allow_create),
    ).fetchone())
    if row is None:
        # A rescan re-keyed the artist, or the row came from an import/download.
        # Same artist either way — take it over and stamp the new id on it.
        candidates = cursor.execute(
            "SELECT id FROM lib2_artists "
            " WHERE name_key=? AND canonical_artist_id IS NULL "
            "   AND " + owned_artist +
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


def track_artist_references(track_obj: Any) -> list:
    """Native artist IDs explicitly reported on a song (OpenSubsonic/Jellyfin)."""
    raw = getattr(track_obj, '_data', None)
    if not isinstance(raw, dict):
        return []
    references = raw.get('ArtistItems') or raw.get('artists') or []
    if not isinstance(references, list):
        return []
    return [(str(item.get('Id') or item.get('id')), str(item.get('Name') or item.get('name')))
            for item in references if isinstance(item, dict)
            and (item.get('Id') or item.get('id')) and (item.get('Name') or item.get('name'))]


def recognize_track_artists(cursor: Any, track_id: int, track_obj: Any, server_source: str) -> None:
    """Stamp native IDs only onto the recognized recording's existing credits.

    Navidrome's album-artist listing omits featured-only artists, but song
    responses carry their IDs. Do not create credits or artists from a scan.
    """
    credits = cursor.execute(
        "SELECT a.id,a.name_key FROM lib2_track_artists ta JOIN lib2_artists a ON a.id=ta.artist_id "
        "WHERE ta.track_id=?", (track_id,),
    ).fetchall()
    for server_id, name in track_artist_references(track_obj):
        candidates = {row['id'] for row in credits if row['name_key'] == _name_key(name)}
        if len(candidates) == 1:
            upsert_mapping(cursor, 'artist', candidates.pop(), server_source, server_id)


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
                 allow_create: bool = False,
                 title_fallback: bool = True) -> Optional[int]:
    """Map a server release; imports alone may create/own the row.

    ``title_fallback=False`` skips the (artist, title) match for a caller that
    already did it with more knowledge -- an import that carries a release id
    and has decided this is a different release of the same name (#1299).
    """
    mapped_id = resolve_mapping(cursor, "album", server_source, server_id)
    row = ((mapped_id,) if mapped_id is not None else cursor.execute(
        "SELECT id FROM lib2_albums WHERE server_source=? AND server_id=? AND (? OR"
        " EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id=t.id"
        " WHERE t.album_id=lib2_albums.id AND f.file_state='active' AND TRIM(f.path)<>''))",
        (server_source, str(server_id), allow_create),
    ).fetchone())
    if row is None and title_fallback:
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
                 file_source: Optional[str] = None,
                 server_path: Optional[str] = None) -> Optional[int]:
    """The catalogue id for a track the server reported, plus its file row."""
    duration = duration or None  # a server's 0 is no length
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
        # The file's length only fills a missing one. Written over the
        # catalogue's, a wrong file (the studio cut imported for the slowed
        # track) became the length every later download was checked against.
        if not allow_create:
            cursor.execute(
                "UPDATE lib2_tracks SET track_number=COALESCE(?,track_number),"
                "disc_number=COALESCE(?,disc_number),duration=COALESCE(duration,?),"
                "track_artist=COALESCE(?,track_artist),"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (track_number, disc_number, duration, track_artist,
                 track_id))
            upsert_mapping(cursor, "track", track_id, server_source, server_id,
                           server_path=server_path)
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
            "       duration=COALESCE(duration, ?),"
            "       track_artist=COALESCE(?, track_artist),"
            "       musicbrainz_id=COALESCE(?, musicbrainz_id),"
            "       server_source=?, server_id=?, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?",
            (album_id, title, track_number, disc_number, duration, track_artist,
             musicbrainz_id, server_source, str(server_id), track_id),
        )
    if is_media_server_source(server_source):
        upsert_mapping(cursor, "track", track_id, server_source, server_id,
                       server_path=server_path)
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


def server_track_names(track_obj: Any) -> Dict[str, Any]:
    """What the server says a song is: title, album artist, album, numbers.
    Plex names them on the object, Jellyfin and Navidrome in the raw item."""
    data = getattr(track_obj, "data", None) or getattr(track_obj, "_data", None) or {}
    if not isinstance(data, dict):
        data = {}
    artists = data.get("Artists") or []
    artist = (getattr(track_obj, "grandparentTitle", None) or data.get("AlbumArtist")
              or data.get("albumArtist") or data.get("displayAlbumArtist")
              or data.get("artist") or (artists[0] if artists else None))
    return {
        "name": getattr(track_obj, "title", None),
        "artist": artist,
        "album": getattr(track_obj, "parentTitle", None) or data.get("Album") or data.get("album"),
        "track_number": getattr(track_obj, "trackNumber", None) or getattr(track_obj, "index", None),
        "disc_number": getattr(track_obj, "discNumber", None) or getattr(track_obj, "parentIndex", None),
    }


def _library_folders_visible(roots: Dict[str, int]) -> bool:
    """Whether this process can see any of the library folders at all. When
    it can't, the media server lives somewhere SoulSync has no view of."""
    return any(os.path.isdir(prefix) for prefix in roots)


def adopt_scanned_file(cursor, path: Optional[str], server_path: Optional[str],
                       server_source: str, server_info: Optional[Dict[str, Any]] = None) -> bool:
    """Bring a listed file the catalogue lacks in through the import writer.

    Only a file this process can open under one of the library folders counts:
    ownership stays a property of the file, so a listed file that is gone
    (Navidrome's missing files, a delayed removal) or lies elsewhere stays out.
    The identity comes from the file's tags, album artist first, and the rows
    it creates start unmonitored -- a file found on disk never turns into a
    download or an upgrade. ``server_path`` covers rows stored in the server's
    form of the path (#1573).
    """
    if not path or cursor.execute(
            "SELECT 1 FROM lib2_track_files WHERE path IN (?, ?)"
            " AND COALESCE(file_state,'active')<>'deleted' LIMIT 1",
            (path, server_path or path)).fetchone():
        return False
    from core.library2.library_roots import load_roots
    from core.library2.autolink import link_download_into_library_v2
    roots = load_roots(cursor)
    if os.path.isfile(path):
        # a file the server shares outside SoulSync's own folders is still
        # music you own: the server listing it is how dev's library knew it
        from core.library2 import tag_cache
        tags = tag_cache.read_tag_snapshot(path)
        artist = tags.get("album_artist") or tags.get("artist")
        track_info = {"name": tags.get("title"), "artist": artist, "album": tags.get("album"),
                      "track_number": tags.get("track_number"),
                      "disc_number": tags.get("disc_number")} if artist and tags.get("title") else {}
    elif (server_info and server_info.get("name") and server_info.get("artist")
          and not _library_folders_visible(roots)):
        # the media server is on another machine: SoulSync can't open its
        # files, so the server's word is the identity, the way the upgrade
        # imported every song it already knew. a local library that just
        # lost a file stays out (Navidrome lists missing files for a while)
        track_info = {key: server_info.get(key) for key in
                      ("name", "artist", "album", "track_number", "disc_number")}
    else:
        return False
    # "username" is the file row's source; without track_info the writer
    # derives the identity from the file itself.
    if link_download_into_library_v2({"_final_processed_path": path, "track_info": track_info,
                                      "username": server_source, "_scan_discovered": True}) is None:
        return False
    from core.library2.unmapped_trigger import schedule_unmapped_artist_reconcile
    from core.settings import config_manager
    schedule_unmapped_artist_reconcile(config_manager)
    logger.info("%s scan adopted %s into the catalogue", server_source, path)
    return True


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
        # Whose library the scan is reading (#1199). A scan driven for an
        # own-library profile writes ITS rows; without this the private
        # library comes back empty while its tracks show up in the shared one.
        from core.library_scope import owner_for_new_file
        observed_id = int(cursor.execute(
            "INSERT INTO lib2_track_files(track_id, path, size, bitrate, format,"
            " server_source, source, owner_profile_id, file_state, import_status)"
            " VALUES(?,?,?,?,?,?,?,?,'active','imported')",
            (track_id, path, size, bitrate, fmt, server_source, source,
             owner_for_new_file())).lastrowid)
    from core.library2.sql_util import owner_clause
    cursor.execute(
        "UPDATE lib2_track_files SET file_state=CASE WHEN source IS NULL "
        "AND legacy_track_id IS NULL THEN 'deleted' ELSE file_state END,"
        " server_source=NULL, updated_at=CURRENT_TIMESTAMP"
        " WHERE track_id=? AND id<>? AND server_source=?"
        # same library only: a scan of one directory must not retire the rows
        # another directory holds for the same track
        + owner_clause(column="owner_profile_id"),
        (track_id, observed_id, server_source))
    elect_primary_file(cursor, track_id)


__all__ = [
    "adopt_scanned_file", "resolve_album", "resolve_artist", "resolve_track", "upsert_album",
    "upsert_artist", "upsert_track", "_genres_json",
]
