"""Seeding catalogue rows the way the media-server scan leaves them.

Every read path in ``MusicDatabase`` speaks Library v2 now, so a test that used
to write an ``artists``/``albums``/``tracks`` triple writes this instead. The
server's own ids are kept as ``server_id`` — tests address rows by the id they
already used, and the returned catalogue id is there when the assertion needs
the real one.
"""

from __future__ import annotations

from typing import Any, Optional


def _name_key(name: str) -> str:
    from core.library2.importer import normalize_name

    return normalize_name(name)


def seed_artist(conn, *, server_id: str, name: str, server_source: str = 'plex',
                image_url: Optional[str] = None) -> int:
    row = conn.execute(
        "SELECT id FROM lib2_artists WHERE server_source=? AND server_id=?",
        (server_source, str(server_id))).fetchone()
    if row:
        return int(row[0])
    return int(conn.execute(
        "INSERT INTO lib2_artists(name, name_key, image_url, server_source, server_id)"
        " VALUES(?,?,?,?,?)",
        (name, _name_key(name), image_url, server_source, str(server_id))).lastrowid)


def seed_album(conn, *, server_id: str, title: str, artist_id: int,
               server_source: str = 'plex', year: Optional[int] = None,
               image_url: Optional[str] = None, track_count: Optional[int] = None,
               origin: str = 'library') -> int:
    row = conn.execute(
        "SELECT id FROM lib2_albums WHERE server_source=? AND server_id=?",
        (server_source, str(server_id))).fetchone()
    if row:
        return int(row[0])
    album_id = int(conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, year, image_url,"
        "                        track_count, origin, server_source, server_id)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (artist_id, title, year, image_url, track_count, origin, server_source,
         str(server_id))).lastrowid)
    conn.execute("INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
                 " VALUES(?,?,'primary')", (album_id, artist_id))
    return album_id


def seed_track(conn, *, server_id: str, title: str, album_id: int,
               artist_id: Optional[int] = None, server_source: str = 'plex',
               track_number: Optional[int] = None, disc_number: int = 1,
               duration: Optional[int] = None, file_path: Optional[str] = None,
               file_size: Optional[int] = None, bitrate: Optional[int] = None,
               track_artist: Optional[str] = None,
               musicbrainz_id: Optional[str] = None) -> int:
    track_id = int(conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, duration,"
        "                        track_artist, musicbrainz_id, server_source, server_id)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (album_id, title, track_number, disc_number, duration, track_artist,
         musicbrainz_id, server_source, str(server_id))).lastrowid)
    if artist_id is not None:
        conn.execute(
            "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)"
            " VALUES(?,?,'primary',0)", (track_id, artist_id))
    if file_path is not None:
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, size, bitrate, server_source, is_primary,"
            "                             file_state, import_status)"
            " VALUES(?,?,?,?,?,1,'active','imported')",
            (track_id, file_path, file_size, bitrate, server_source))
    return track_id


def seed_library_track(conn, *, artist: str, album: str, title: str,
                       artist_server_id: str = 'ar1', album_server_id: str = 'al1',
                       track_server_id: str = 'tr1', **track_kwargs: Any) -> int:
    """Artist + album + track in one call, for tests that only need a track."""
    server_source = track_kwargs.pop('server_source', 'plex')
    artist_id = seed_artist(conn, server_id=artist_server_id, name=artist,
                            server_source=server_source)
    album_id = seed_album(conn, server_id=album_server_id, title=album,
                          artist_id=artist_id, server_source=server_source)
    return seed_track(conn, server_id=track_server_id, title=title,
                      album_id=album_id, artist_id=artist_id,
                      server_source=server_source, **track_kwargs)


__all__ = ["seed_album", "seed_artist", "seed_library_track", "seed_track"]
