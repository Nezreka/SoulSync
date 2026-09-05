"""Bulk track-load used by M3U export path resolution.

M3U export used to resolve each track with a per-artist search_tracks() loop,
which could block for a long time behind the enrichment/scan writers (the
"Export M3U hangs forever" report). It now bulk-loads (artist, title, file_path)
in one WAL-concurrent read; this pins that method's contract.
"""

from __future__ import annotations

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_library_track


def _db_with_track(tmp_path, *, title, artist, file_path, server='jellyfin'):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as c:
        seed_library_track(c, artist=artist, album='Album', title=title,
                           file_path=file_path, server_source=server)
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
        # one with a file, one without — only the first should come back.
        seed_library_track(c, artist='A', album='Al', title='Has Path',
                           track_server_id='t1', file_path='/m/a.flac',
                           server_source='jellyfin')
        seed_library_track(c, artist='A', album='Al', title='No Path',
                           track_server_id='t2', server_source='jellyfin')
        c.commit()
    rows = db.get_tracks_for_m3u_resolution()
    titles = {r['title'] for r in rows}
    assert titles == {'Has Path'}


def test_empty_db_safe(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    assert db.get_tracks_for_m3u_resolution() == []
