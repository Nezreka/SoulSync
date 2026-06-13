"""Bulk track-load used by M3U export path resolution.

M3U export used to resolve each track with a per-artist search_tracks() loop,
which could block for a long time behind the enrichment/scan writers (the
"Export M3U hangs forever" report). It now bulk-loads (artist, title, file_path)
in one WAL-concurrent read; this pins that method's contract.
"""

from __future__ import annotations

from database.music_database import MusicDatabase


def _db_with_track(tmp_path, *, title, artist, file_path, server='jellyfin'):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as c:
        c.execute("INSERT INTO artists (id, name) VALUES (1, ?)", (artist,))
        c.execute("INSERT INTO albums (id, title, artist_id) VALUES (1, 'Album', 1)")
        c.execute(
            "INSERT INTO tracks (id, title, artist_id, album_id, file_path, server_source) "
            "VALUES (1, ?, 1, 1, ?, ?)",
            (title, file_path, server),
        )
        c.commit()
    return db


def test_returns_artist_title_path(tmp_path):
    db = _db_with_track(tmp_path, title='How You Remind Me', artist='Nickelback',
                        file_path='/music/nb/how.flac')
    rows = db.get_tracks_for_m3u_resolution(server_source='jellyfin')
    assert rows == [{'title': 'How You Remind Me', 'artist': 'Nickelback',
                     'file_path': '/music/nb/how.flac'}]


def test_filters_by_server_source(tmp_path):
    db = _db_with_track(tmp_path, title='X', artist='Y', file_path='/m/x.flac', server='jellyfin')
    assert db.get_tracks_for_m3u_resolution(server_source='jellyfin')  # match
    assert db.get_tracks_for_m3u_resolution(server_source='plex') == []  # other server


def test_excludes_rows_without_file_path(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as c:
        c.execute("INSERT INTO artists (id, name) VALUES (1, 'A')")
        c.execute("INSERT INTO albums (id, title, artist_id) VALUES (1, 'Al', 1)")
        # one with a path, one without — only the first should come back.
        c.execute("INSERT INTO tracks (id, title, artist_id, album_id, file_path, server_source) "
                  "VALUES (1, 'Has Path', 1, 1, '/m/a.flac', 'jellyfin')")
        c.execute("INSERT INTO tracks (id, title, artist_id, album_id, file_path, server_source) "
                  "VALUES (2, 'No Path', 1, 1, NULL, 'jellyfin')")
        c.commit()
    rows = db.get_tracks_for_m3u_resolution()
    titles = {r['title'] for r in rows}
    assert titles == {'Has Path'}


def test_empty_db_safe(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    assert db.get_tracks_for_m3u_resolution() == []
