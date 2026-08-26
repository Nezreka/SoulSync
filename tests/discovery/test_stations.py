"""recommended stations: owned top artists as one-click radio cards."""

import pytest

from core.discovery.stations import build_stations
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    d = MusicDatabase(str(tmp_path / 'm.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists (id, name, name_key, image_url, spotify_id) "
                "VALUES (1, 'Daft Punk', 'daft punk', 'http://dp.jpg', 'sp1')")
    cur.execute("INSERT INTO lib2_artists (id, name, name_key, spotify_id) "
                "VALUES (2, 'Justice', 'justice', 'sp2')")
    cur.execute("INSERT INTO lib2_albums (id, title, primary_artist_id) VALUES (10, 'A', 1)")
    cur.execute("INSERT INTO lib2_albums (id, title, primary_artist_id) VALUES (20, 'B', 2)")

    def _owned(title, album_id, path):
        cur.execute("INSERT INTO lib2_tracks (title, album_id) VALUES (?, ?)",
                    (title, album_id))
        cur.execute(
            "INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
            "VALUES (?, ?, 1, 'active')", (cur.lastrowid, path))

    for i in range(5):
        _owned(f'DP {i}', 10, f'/m/dp{i}.flac')
    # justice owns only TWO playable tracks - below the station floor
    for i in range(2):
        _owned(f'J {i}', 20, f'/m/j{i}.flac')
    for artist, n in (('Daft Punk', 30), ('Justice', 20), ('Unowned Star', 50)):
        for i in range(n):
            cur.execute("INSERT INTO listening_history (title, artist, played_at) "
                        "VALUES ('x', ?, datetime('now', ?))", (artist, f'-{i} hours'))
    cur.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                "similarity_rank, profile_id) VALUES ('sp1', 'Justice', 1, 1)")
    cur.execute("INSERT INTO similar_artists (source_artist_id, similar_artist_name, "
                "similarity_rank, profile_id) VALUES ('sp1', 'SebastiAn', 2, 1)")
    conn.commit()
    conn.close()
    return d


def test_stations_are_owned_playable_artists_with_companions(db):
    stations = build_stations(db)
    names = [s['name'] for s in stations]
    # unowned star is heard most but cannot be a station; justice has too few
    # playable tracks to hold one
    assert names == ['Daft Punk']
    dp = stations[0]
    # ids stay EXACTLY as the catalogue stores them (TEXT after the
    # artists_new migration; jellyfin installs hold GUIDs) - the #1185 lesson
    assert str(dp['artist_id']) == '1'
    # thumbs now pass through normalize_image_url - external urls become
    # cache-proxied (/api/image-cache/<key>), media-server-relative ones
    # become loadable. either way: browser-safe, never raw and never empty.
    assert dp['image_url'].startswith(('/api/image-cache/', 'http://dp.jpg'))
    assert dp['with'] == ['Justice', 'SebastiAn']


def test_empty_history_means_no_stations(tmp_path):
    d = MusicDatabase(str(tmp_path / 'empty.db'))
    assert build_stations(d) == []
