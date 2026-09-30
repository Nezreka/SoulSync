"""Daily Mix ranks your library's top genres (#daily-mix, boulder sept 30).

tracks has no genres column on real installs, the genres live on artists.
get_top_genres_from_library only ever looked at tracks, so it fell back to
artist NAMES, and the daily mix generator then asked the discovery pool for
a genre called "Louis Armstrong". every Daily Mix came back empty and Run now
on it looked like it did nothing.
"""

import sqlite3
from contextlib import contextmanager

from core.personalized_playlists import PersonalizedPlaylistsService, rank_library_genres


def test_rank_library_genres_weights_and_merges_case():
    rows = [
        ('["House", "Techno"]', 10),
        ('["house"]', 5),
        ('Pop, Rock', 2),
        ('', 99),
        (None, 99),
        ('[]', 99),
    ]
    assert rank_library_genres(rows) == [
        ('House', 15),
        ('Techno', 10),
        ('Pop', 2),
        ('Rock', 2),
    ]


class _Db:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def _get_connection(self):
        yield self.conn


def _library(artist_genres):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT, genres TEXT)')
    conn.execute('CREATE TABLE tracks (id INTEGER PRIMARY KEY, artist_id INTEGER, title TEXT)')
    tid = 1
    for aid, (name, genres, n_tracks) in enumerate(artist_genres, start=1):
        conn.execute('INSERT INTO artists VALUES (?, ?, ?)', (aid, name, genres))
        for _ in range(n_tracks):
            conn.execute('INSERT INTO tracks VALUES (?, ?, ?)', (tid, aid, f't{tid}'))
            tid += 1
    service = PersonalizedPlaylistsService.__new__(PersonalizedPlaylistsService)
    service.database = _Db(conn)
    return service


def test_top_genres_come_from_artists_not_artist_names():
    # louis armstrong has the most tracks. before the fix he became "genre" #1
    service = _library([
        ('Louis Armstrong', '["Jazz"]', 9),
        ('Dua Lipa', '["Pop", "Dance"]', 6),
        ('Calvin Harris', '["Dance", "Electro"]', 5),
        ('Nobody Tagged', None, 20),
    ])
    top = service.get_top_genres_from_library(limit=3)
    names = [g for g, _ in top]
    assert names == ['Dance', 'Jazz', 'Pop']
    assert 'Louis Armstrong' not in names
    assert 'Nobody Tagged' not in names


def test_falls_back_to_artist_names_only_with_no_genre_data_at_all():
    service = _library([('Louis Armstrong', None, 3), ('Dua Lipa', '[]', 1)])
    assert service.get_top_genres_from_library(limit=2) == [
        ('Louis Armstrong', 3),
        ('Dua Lipa', 1),
    ]
