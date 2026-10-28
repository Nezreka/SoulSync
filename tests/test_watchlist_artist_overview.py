"""the watchlist panel's overview: what you own of an artist and how much you
play them. the real db, a temp file."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.settings import config_manager
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, 'get_active_media_server', lambda: 'plex')
    return MusicDatabase(str(tmp_path / 'wao.db'))


def _exec(db, sql, params=()):
    conn = db._get_connection()
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _artist(db, artist_id, name, server='plex', **ids):
    cols = ['id', 'name', 'server_source', *ids]
    _exec(db, f"INSERT INTO artists ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
          (artist_id, name, server, *ids.values()))


def _album(db, album_id, artist_id, title, created_at, year=None):
    _exec(db, "INSERT INTO albums (id, artist_id, title, year, created_at) VALUES (?, ?, ?, ?, ?)",
          (album_id, artist_id, title, year, created_at))


def _track(db, track_id, album_id, artist_id, title='t'):
    _exec(db, "INSERT INTO tracks (id, album_id, artist_id, title) VALUES (?, ?, ?, ?)",
          (track_id, album_id, artist_id, title))


def _play(db, title, artist, days_ago=0, owner=1):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime('%Y-%m-%d %H:%M:%S')
    _exec(db, "INSERT INTO listening_history (track_id, title, artist, played_at, profile_id) "
              "VALUES (?, ?, ?, ?, ?)",
          (f'{title}-{when}-{days_ago}', title, artist, when, owner))


def test_library_finds_the_artist_by_provider_id(db):
    _artist(db, 1, 'Boards of Canada', spotify_artist_id='sp1')
    _album(db, 10, 1, 'Geogaddi', '2026-01-01 00:00:00', 2002)
    _album(db, 11, 1, 'Tomorrow', '2026-03-01 00:00:00', 2013)
    for i in range(3):
        _track(db, 100 + i, 10, 1)
    _track(db, 110, 11, 1)

    out = db.get_watchlist_artist_overview('Some Other Name', {'spotify': 'sp1'})

    lib = out['library']
    assert str(lib['artist_id']) == '1'
    assert (lib['album_count'], lib['track_count']) == (2, 4)
    # newest landing first, with its own track count
    assert [a['title'] for a in lib['recent_albums']] == ['Tomorrow', 'Geogaddi']
    assert lib['recent_albums'][1]['track_count'] == 3


def test_library_falls_back_to_the_name_when_no_id_lands(db):
    _artist(db, 1, 'Björk')
    _album(db, 10, 1, 'Homogenic', '2026-01-01 00:00:00')
    _track(db, 100, 10, 1)

    out = db.get_watchlist_artist_overview('bjork', {'spotify': 'nope'})

    assert out['library']['album_count'] == 1


def test_library_ignores_another_servers_rows(db):
    _artist(db, 1, 'Air', server='jellyfin', spotify_artist_id='sp1')
    _album(db, 10, 1, 'Moon Safari', '2026-01-01 00:00:00')
    _track(db, 100, 10, 1)

    assert db.get_watchlist_artist_overview('Air', {'spotify': 'sp1'})['library'] is None


def test_listening_counts_top_tracks_and_the_month(db):
    for _ in range(3):
        _play(db, 'Roygbiv', 'Boards of Canada', days_ago=2)
    _play(db, 'Dayvan Cowboy', 'Boards of Canada', days_ago=60)
    _play(db, 'Unrelated', 'Someone Else', days_ago=1)

    lis = db.get_watchlist_artist_overview('Boards of Canada')['listening']

    assert lis['play_count'] == 4
    assert lis['plays_30d'] == 3
    assert [t['title'] for t in lis['top_tracks']] == ['Roygbiv', 'Dayvan Cowboy']
    assert lis['top_tracks'][0]['play_count'] == 3
    assert lis['last_played'].startswith(
        (datetime.now(timezone.utc) - timedelta(days=2)).strftime('%Y-%m-%d'))


def test_listening_counts_plays_under_the_library_name_too(db):
    # the watchlist spells it one way, the server another
    _artist(db, 1, 'AC/DC', spotify_artist_id='sp1')
    _album(db, 10, 1, 'Back in Black', '2026-01-01 00:00:00')
    _track(db, 100, 10, 1)
    _play(db, 'Hells Bells', 'AC/DC')
    _play(db, 'Shoot to Thrill', 'ACDC')

    lis = db.get_watchlist_artist_overview('ACDC', {'spotify': 'sp1'})['listening']

    assert lis['play_count'] == 2


def test_listening_reads_only_the_callers_pile(db):
    _play(db, 'Roygbiv', 'Boards of Canada', owner=5)

    # no profile means the shared pile, never someone else's
    assert db.get_watchlist_artist_overview('Boards of Canada')['listening'] is None


def test_nothing_known_is_two_nones(db):
    assert db.get_watchlist_artist_overview('Nobody', {}) == {'library': None, 'listening': None}


def test_listening_gives_twelve_months_oldest_first(db):
    _play(db, 'Roygbiv', 'Boards of Canada', days_ago=0)
    _play(db, 'Roygbiv', 'Boards of Canada', days_ago=0)
    _play(db, 'Roygbiv', 'Boards of Canada', days_ago=500)

    months = db.get_watchlist_artist_overview('Boards of Canada')['listening']['months']

    assert len(months) == 12
    assert months[-1] == {'month': datetime.now().strftime('%Y-%m'), 'plays': 2}
    # the old play is outside the window
    assert sum(m['plays'] for m in months) == 2
    assert [m['month'] for m in months] == sorted(m['month'] for m in months)


def test_top_tracks_carry_their_library_cover(db):
    _artist(db, 1, 'Boards of Canada')
    _album(db, 10, 1, 'Geogaddi', '2026-01-01 00:00:00')
    _exec(db, "UPDATE albums SET thumb_url = 'geo.jpg' WHERE id = 10")
    _track(db, 100, 10, 1, 'Dawn Chorus')
    _play(db, 'Dawn Chorus', 'Boards of Canada')
    # no linked track, found by its album title instead
    _exec(db, "INSERT INTO listening_history (track_id, title, artist, album, played_at, profile_id) "
              "VALUES ('x', 'Gyroscope', 'Boards of Canada', 'geogaddi', '2026-01-01 00:00:00', 1)")
    _exec(db, "UPDATE listening_history SET db_track_id = 100 WHERE title = 'Dawn Chorus'")

    top = db.get_watchlist_artist_overview('Boards of Canada')['listening']['top_tracks']

    assert {t['title']: t['thumb_url'] for t in top} == {'Dawn Chorus': 'geo.jpg', 'Gyroscope': 'geo.jpg'}


def test_top_track_cover_comes_from_the_active_server(db):
    # one artist on two servers; plex is active
    _artist(db, 1, 'bbno$')
    _artist(db, 2, 'bbno$', server='jellyfin')
    _album(db, 10, 1, 'two', '2026-01-01 00:00:00')
    _album(db, 20, 2, 'two', '2026-01-01 00:00:00')
    _exec(db, "UPDATE albums SET thumb_url = '/library/metadata/10/thumb' WHERE id = 10")
    _exec(db, "UPDATE albums SET thumb_url = '/Items/abc/Images/Primary' WHERE id = 20")
    _track(db, 100, 10, 1, 'it boy')
    _track(db, 200, 20, 2, 'it boy')
    # the play's linked track is the jellyfin row
    _play(db, 'it boy', 'bbno$')
    _exec(db, "UPDATE listening_history SET db_track_id = 200, album = 'it boy'")

    top = db.get_watchlist_artist_overview('bbno$')['listening']['top_tracks']

    assert top[0]['thumb_url'] == '/library/metadata/10/thumb'


def test_top_track_cover_survives_a_stale_linked_track(db):
    # a rescan gave the song a new id, the play still has the old one
    _artist(db, 1, 'bbno$')
    _album(db, 10, 1, 'bbno$', '2026-01-01 00:00:00')
    _exec(db, "UPDATE albums SET thumb_url = 'self.jpg' WHERE id = 10")
    _track(db, 100, 10, 1, 'yezzir')
    _play(db, 'yezzir', 'bbno$')
    _exec(db, "UPDATE listening_history SET db_track_id = 999, album = 'yezzir'")

    top = db.get_watchlist_artist_overview('bbno$')['listening']['top_tracks']

    assert top[0]['thumb_url'] == 'self.jpg'
