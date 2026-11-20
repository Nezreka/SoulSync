"""Your Year extras: the streak, busiest day, genre and calendar the story
gained, "save your year as a playlist", the soundtrack it plays, and the
season the dashboard announces it in. Same window and pile as the story."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from flask import Flask

import api.stats as stats_routes
from core.stats import year_extras
from database.music_database import MusicDatabase
from tests import lib2_seed

pytestmark = pytest.mark.usefixtures('utc_server')

NOW = datetime(2026, 12, 3, 12, 0, 0)


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'year.db'))


def _play(db, when, *, title='t', artist='A', album='Al', duration_ms=180_000,
          lib2_track_id=None, profile_id=1):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO listening_history (track_id, title, artist, album, played_at, duration_ms,"
        " lib2_track_id, profile_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (f'{title}-{artist}-{when.isoformat()}', title, artist, album,
         when.isoformat(sep=' '), duration_ms, lib2_track_id, profile_id))
    conn.commit()
    conn.close()


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_the_longest_streak_is_consecutive_days_and_ties_keep_the_first():
    days = ['2026-03-05', '2026-03-01', '2026-03-02', '2026-03-03', '2026-03-03',
            '2026-04-10', '2026-04-11', '2026-04-12']
    assert year_extras.longest_streak(days) == {'days': 3, 'start': '2026-03-01', 'end': '2026-03-03'}
    assert year_extras.longest_streak([]) == {'days': 0, 'start': None, 'end': None}
    # across a month and a year boundary
    assert year_extras.longest_streak(['2025-12-31', '2026-01-01'])['days'] == 2


def test_top_genres_share_is_of_the_genre_known_plays():
    top = year_extras.top_genres({'rock': 30, 'pop': 10, 'jazz': 10, '': 99, 'zero': 0})
    assert [g['genre'] for g in top] == ['rock', 'jazz', 'pop']   # names break the tie
    assert top[0] == {'genre': 'rock', 'plays': 30, 'share': 0.6}


@pytest.mark.parametrize('end,year', [('2026-12-03', 2026), ('2027-01-10', 2026), ('2026-06-01', 2026)])
def test_the_story_year_is_last_year_in_january(end, year):
    assert year_extras.story_year(end) == year


@pytest.mark.parametrize('day,year', [
    (date(2026, 11, 30), None), (date(2026, 12, 1), 2026), (date(2027, 1, 15), 2026),
    (date(2027, 1, 16), None), (date(2026, 7, 4), None)])
def test_the_season_runs_december_first_to_january_fifteenth(day, year):
    assert year_extras.season_year(day) == year


def test_the_needle_drops_a_third_in_and_leaves_room_to_play():
    assert year_extras.soundtrack_start_ms(60_000) == 0           # short: from the top
    assert year_extras.soundtrack_start_ms(240_000) == 72_000
    assert year_extras.soundtrack_start_ms(100_000) == 30_000
    assert year_extras.soundtrack_start_ms(None) == 0


# ── the story's new numbers ──────────────────────────────────────────────────

def test_the_year_reports_every_day_the_streak_and_the_busiest_day(db):
    for d in (1, 2, 3):
        _play(db, datetime(2026, 5, d, 10), artist='Streaky')
    for i in range(4):
        _play(db, datetime(2026, 7, 9, 10 + i), artist='Big Day' if i else 'Other', duration_ms=120_000)
    _play(db, datetime(2025, 11, 30, 9), artist='Before')       # outside the window
    year = db.get_year_in_listening(now=NOW)
    assert year['days'][0] == {'date': '2026-05-01', 'plays': 1, 'minutes': 3}
    assert len(year['days']) == 4
    assert year['streak'] == {'days': 3, 'start': '2026-05-01', 'end': '2026-05-03'}
    assert year['peak_day'] == {'date': '2026-07-09', 'plays': 4, 'minutes': 8, 'top_artist': 'Big Day'}


def test_the_top_genre_comes_from_the_library_artists_of_what_was_played(db):
    conn = db._get_connection()
    rock = lib2_seed.track(conn, 'Rocker', 'Loud', 'Song', )
    jazz = lib2_seed.track(conn, 'Cool Cat', 'Smooth', 'Tune')
    conn.execute("UPDATE lib2_artists SET genres = ? WHERE name = 'Rocker'", ('["rock"]',))
    conn.execute("UPDATE lib2_artists SET genres = ? WHERE name = 'Cool Cat'", ('["jazz", "soul"]',))
    conn.commit()
    conn.close()
    for i in range(3):
        _play(db, datetime(2026, 6, 1, 10 + i), title='Song', artist='Rocker', lib2_track_id=rock)
    _play(db, datetime(2026, 6, 2, 10), title='Tune', artist='Cool Cat', lib2_track_id=jazz)
    _play(db, datetime(2026, 6, 2, 11), title='Unknown', artist='Nobody')   # no genre to give
    genres = db.get_year_in_listening(now=NOW)['top_genres']
    assert genres[0] == {'genre': 'rock', 'plays': 3, 'share': 0.6}
    assert {g['genre'] for g in genres} == {'rock', 'jazz', 'soul'}


def test_an_empty_year_still_has_the_new_keys(db):
    year = db.get_year_in_listening(now=NOW)
    assert year['days'] == [] and year['top_genres'] == []
    assert year['streak']['days'] == 0 and year['peak_day']['top_artist'] is None


def test_the_years_top_tracks_are_the_profiles_own_pile(db):
    other = db.create_profile('Other')
    conn = db._get_connection()
    conn.execute("UPDATE profiles SET listenbrainz_token = 'x' WHERE id = ?", (other,))
    conn.commit()
    conn.close()
    for i in range(3):
        _play(db, datetime(2026, 4, 1, 10 + i), title='Mine', artist='A')
    _play(db, datetime(2026, 4, 2, 10), title='Second', artist='B')
    _play(db, datetime(2026, 4, 2, 11), title='Theirs', artist='C', profile_id=other)
    top = db.get_year_top_tracks(limit=10, now=NOW, profile_id=1)
    assert [t['title'] for t in top] == ['Mine', 'Second']
    assert top[0]['plays'] == 3


# ── save your year as a playlist ─────────────────────────────────────────────

def _tracks(*titles):
    return [{'title': t, 'artist': 'A', 'album': 'Al', 'plays': 1} for t in titles]


def test_saving_makes_your_year_once_and_updates_it_after(db):
    first = year_extras.save_year_playlist(db, 1, 2026, _tracks('One', 'Two'))
    assert first['name'] == 'Your 2026' and first['created'] is True and first['track_count'] == 2
    again = year_extras.save_year_playlist(db, 1, 2026, _tracks('Three', 'One', 'Two'))
    assert again['id'] == first['id'] and again['created'] is False and again['track_count'] == 3
    mine = [p for p in db.get_mirrored_playlists(profile_id=1) if p.get('source') == 'soulsync']
    assert len(mine) == 1
    rows = db.get_mirrored_playlist_tracks(first['id'])
    assert [r['track_name'] for r in rows] == ['Three', 'One', 'Two']


def test_a_renamed_year_playlist_keeps_its_name(db):
    saved = year_extras.save_year_playlist(db, 1, 2026, _tracks('One'))
    conn = db._get_connection()
    conn.execute("UPDATE mirrored_playlists SET name = 'my best of' WHERE id = ?", (saved['id'],))
    conn.commit()
    conn.close()
    assert year_extras.save_year_playlist(db, 1, 2026, _tracks('Two'))['name'] == 'my best of'


def test_each_profile_and_each_year_has_its_own(db):
    other = db.create_profile('Other')
    a = year_extras.save_year_playlist(db, 1, 2026, _tracks('One'))
    b = year_extras.save_year_playlist(db, other, 2026, _tracks('One'))
    c = year_extras.save_year_playlist(db, 1, 2025, _tracks('One'))
    assert len({a['id'], b['id'], c['id']}) == 3
    assert c['name'] == 'Your 2025'


def test_nothing_played_is_refused():
    from core.playlists.user_playlists import UserPlaylistError
    with pytest.raises(UserPlaylistError):
        year_extras.save_year_playlist(None, 1, 2026, [])


def test_size_is_fifty_or_a_hundred():
    assert year_extras.clamp_size(100) == 100
    assert year_extras.clamp_size(5000) == 50
    assert year_extras.clamp_size('x') == 50


# ── the soundtrack ───────────────────────────────────────────────────────────

def test_the_soundtrack_is_the_top_songs_you_own_with_where_to_start(db):
    conn = db._get_connection()
    owned = lib2_seed.track(conn, 'A', 'Al', 'Owned Hit', duration=240_000)
    gone = lib2_seed.track(conn, 'A', 'Al', 'No File', owned=False)
    conn.commit()
    conn.close()
    tracks = [
        {'title': 'Not In Library', 'artist': 'X', 'lib2_track_id': None, 'plays': 9},
        {'title': 'No File', 'artist': 'A', 'lib2_track_id': gone, 'plays': 8},
        {'title': 'Owned Hit', 'artist': 'A', 'lib2_track_id': owned, 'plays': 7},
    ]
    rows = year_extras.soundtrack_rows(db, tracks, lambda u: u)
    assert [r['title'] for r in rows] == ['Owned Hit']
    assert rows[0]['file_path'].endswith('.flac')
    assert rows[0]['start_ms'] == 72_000 and rows[0]['plays'] == 7


# ── the routes ───────────────────────────────────────────────────────────────

@pytest.fixture()
def http(db, monkeypatch):
    who = {'id': 1}
    monkeypatch.setattr(stats_routes, 'get_database', lambda: db)
    monkeypatch.setattr(stats_routes, 'get_current_profile_id', lambda: who['id'])
    monkeypatch.setattr(stats_routes, 'fix_artist_image_url', lambda u: u)
    app = Flask(__name__)
    app.register_blueprint(stats_routes.bp)
    return app.test_client(), who


def test_the_playlist_route_saves_the_years_top_songs(db, http):
    client, _who = http
    now = datetime.now()
    for i in range(3):
        _play(db, now.replace(hour=1, minute=i), title='Fave', artist='A')
    _play(db, now.replace(hour=2, minute=0), title='Also', artist='B')
    body = client.post('/api/stats/year/playlist', json={'size': 100}).get_json()
    assert body['success'] and body['track_count'] == 2
    assert body['name'] == f"Your {year_extras.story_year(now.strftime('%Y-%m-%d'))}"
    again = client.post('/api/stats/year/playlist', json={'size': 50}).get_json()
    assert again['id'] == body['id'] and again['created'] is False


def test_the_playlist_route_says_so_when_nothing_was_played(http):
    client, _who = http
    resp = client.post('/api/stats/year/playlist', json={})
    assert resp.status_code == 400 and 'Nothing played' in resp.get_json()['error']


def test_the_banner_shows_in_season_once_per_profile_and_year(db, http, monkeypatch):
    client, who = http
    sent = []
    monkeypatch.setattr('core.profile_notify.notify_profile',
                        lambda pid, msg, kind='info', link=None: sent.append((pid, msg, link)) or True)
    monkeypatch.setattr(year_extras, 'today_local', lambda: date(2026, 12, 2))
    monkeypatch.setattr(db, 'get_year_top_tracks', lambda **kw: [{'title': 'x'}])
    assert client.get('/api/stats/year/banner').get_json() == {'success': True, 'show': True, 'year': 2026}
    client.get('/api/stats/year/banner')
    assert sent == [(1, 'Your 2026 in listening is ready', '/stats?story=year')]   # one bell note
    client.post('/api/stats/year/banner/dismiss')
    assert client.get('/api/stats/year/banner').get_json()['show'] is False
    other = db.create_profile('Other')
    who['id'] = other
    assert client.get('/api/stats/year/banner').get_json()['show'] is True
    # next season it comes back
    who['id'] = 1
    monkeypatch.setattr(year_extras, 'today_local', lambda: date(2027, 12, 1))
    assert client.get('/api/stats/year/banner').get_json() == {'success': True, 'show': True, 'year': 2027}


def test_the_banner_stays_away_out_of_season_and_without_plays(db, http, monkeypatch):
    client, _who = http
    monkeypatch.setattr(year_extras, 'today_local', lambda: date(2026, 10, 2))
    assert client.get('/api/stats/year/banner').get_json()['show'] is False
    monkeypatch.setattr(year_extras, 'today_local', lambda: date(2026, 12, 2))
    assert client.get('/api/stats/year/banner').get_json()['show'] is False   # nothing played


def test_the_soundtrack_route_answers_in_the_player_shape(db, http):
    client, _who = http
    conn = db._get_connection()
    tid = lib2_seed.track(conn, 'A', 'Al', 'Owned Hit', duration=200_000)
    conn.commit()
    conn.close()
    _play(db, datetime.now().replace(hour=1), title='Owned Hit', artist='A', lib2_track_id=tid)
    body = client.get('/api/stats/year/soundtrack').get_json()
    assert body['success'] and body['tracks'][0]['id'] == tid
    assert {'file_path', 'start_ms', 'duration_ms', 'artist', 'album'} <= set(body['tracks'][0])
