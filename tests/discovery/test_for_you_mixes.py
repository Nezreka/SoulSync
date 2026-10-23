"""on repeat, repeat rewind, flow and blend (sept 29 2026).

all four are owned tracks read off listening_history.lib2_track_id, so they play
straight away. these pin what each one means, on a real MusicDatabase.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from core.personalized import for_you as F
from database.music_database import MusicDatabase

NOW = datetime(2026, 9, 29, 12, 0)
_ids = iter(range(1, 100_000))


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'm.db'))


def _track(db, artist, title, album='LP', owned=True, play_count=0):
    # Library v2: one artist row per name, one album per (artist, album)
    with db._get_connection() as conn:
        row = conn.execute("SELECT id FROM lib2_artists WHERE name = ?", (artist,)).fetchone()
        ar = row[0] if row else conn.execute(
            "INSERT INTO lib2_artists (name, name_key) VALUES (?, ?)",
            (artist, artist.lower())).lastrowid
        row = conn.execute("SELECT id FROM lib2_albums WHERE primary_artist_id = ? AND title = ?",
                           (ar, album)).fetchone()
        al = row[0] if row else conn.execute(
            "INSERT INTO lib2_albums (primary_artist_id, title, origin) VALUES (?, ?, 'library')",
            (ar, album)).lastrowid
        tid = conn.execute(
            "INSERT INTO lib2_tracks (album_id, title, duration, play_count) VALUES (?, ?, 200000, ?)",
            (al, title, play_count)).lastrowid
        if owned:
            conn.execute("INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
                         (tid, f'/m/{tid}.flac'))
        conn.commit()
    return str(tid)


def _play(db, tid, days_ago, times=1, owner=1, artist='x'):
    with db._get_connection() as conn:
        for n in range(times):
            when = (NOW - timedelta(days=days_ago, minutes=n)).strftime('%Y-%m-%d %H:%M:%S')
            conn.execute(
                "INSERT INTO listening_history "
                "(track_id, title, artist, played_at, lib2_track_id, profile_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f'k{next(_ids)}', 't', artist, when, tid, owner))
        conn.commit()


def _names(rows):
    return [r['title'] for r in rows]


# ── on repeat ────────────────────────────────────────────────────────────────

def test_on_repeat_is_what_you_played_most_this_month(db):
    a = _track(db, 'A', 'Most')
    b = _track(db, 'B', 'Some')
    c = _track(db, 'C', 'Once')
    old = _track(db, 'D', 'Last Year')
    _play(db, a, 3, times=9)
    _play(db, b, 10, times=4)
    _play(db, c, 5, times=1)          # one play is not "on repeat"
    _play(db, old, 200, times=30)     # heavy, but not this month
    with db._get_connection() as conn:
        assert _names(F.on_repeat(conn, 1, NOW)) == ['Most', 'Some']


def test_on_repeat_only_plays_what_you_own(db):
    gone = _track(db, 'A', 'Deleted', owned=False)
    kept = _track(db, 'B', 'Kept')
    _play(db, gone, 1, times=20)
    _play(db, kept, 1, times=3)
    with db._get_connection() as conn:
        assert _names(F.on_repeat(conn, 1, NOW)) == ['Kept']


def test_on_repeat_caps_one_artist(db):
    tids = [_track(db, 'A', f'song {n}') for n in range(6)]
    for t in tids:
        _play(db, t, 2, times=5)
    with db._get_connection() as conn:
        assert len(F.on_repeat(conn, 1, NOW)) == 3


def test_on_repeat_reads_only_its_own_pile(db):
    mine, theirs = _track(db, 'A', 'Mine'), _track(db, 'B', 'Theirs')
    _play(db, mine, 1, times=3, owner=1)
    _play(db, theirs, 1, times=9, owner=2)
    with db._get_connection() as conn:
        assert _names(F.on_repeat(conn, 1, NOW)) == ['Mine']


# ── repeat rewind ────────────────────────────────────────────────────────────

def test_rewind_is_heavy_then_and_silent_lately(db):
    gone_quiet = _track(db, 'A', 'Gone Quiet')
    still_on = _track(db, 'B', 'Still On')
    too_light = _track(db, 'C', 'Too Light')
    ancient = _track(db, 'D', 'Ancient')
    _play(db, gone_quiet, 150, times=10)
    _play(db, still_on, 150, times=10)
    _play(db, still_on, 5, times=1)        # played lately, so not a rewind
    _play(db, too_light, 150, times=2)
    _play(db, ancient, 500, times=50)      # older than the window
    with db._get_connection() as conn:
        assert _names(F.repeat_rewind(conn, 1, NOW)) == ['Gone Quiet']


# ── flow ─────────────────────────────────────────────────────────────────────

def test_flow_mixes_favourites_with_the_deep_cuts_of_artists_you_love(db):
    for n in range(6):
        _play(db, _track(db, f'Fav {n}', f'fav {n}'), 5, times=6, artist=f'Fav {n}')
    # a loved artist with tracks you never play
    loved = [_track(db, 'Loved', f'loved {n}') for n in range(2)]
    deep = [_track(db, 'Loved', f'deep {n}') for n in range(4)]
    for t in loved:
        _play(db, t, 20, times=10, artist='Loved')
    with db._get_connection() as conn:
        rows = F.flow(conn, 1, 1, now=NOW, rng=random.Random(1), size=20)
    titles = _names(rows)
    assert any(t.startswith('fav') for t in titles)
    assert any(t.startswith('deep') for t in titles)
    assert len(titles) == len(set(titles))


def test_flow_is_a_fresh_draw_each_time(db):
    for n in range(40):
        t = _track(db, f'Artist {n}', f'song {n}')
        _play(db, t, 3, times=2, artist=f'Artist {n}')
    with db._get_connection() as conn:
        first = _names(F.flow(conn, 1, 1, now=NOW, rng=random.Random(1), size=15))
        second = _names(F.flow(conn, 1, 1, now=NOW, rng=random.Random(2), size=15))
    assert first != second


def test_christmas_waits_for_november():
    xmas = {'title': 'Have Yourself A Merry Little Christmas', 'album': 'Christmas in the City'}
    assert F.in_season(xmas, datetime(2026, 9, 29)) is False
    assert F.in_season(xmas, datetime(2026, 12, 1)) is True
    assert F.in_season({'title': 'Worth It', 'album': 'Breakthrough'}, datetime(2026, 9, 29))


# ── blend ────────────────────────────────────────────────────────────────────

def test_a_blend_leads_with_what_you_both_play(db):
    both = _track(db, 'Shared', 'Both Love')
    # same shared artist, played far more, but only by me: must not lead
    heavy = _track(db, 'Shared', 'My Heavy One')
    _play(db, heavy, 10, times=20, owner=1)
    mine = _track(db, 'Mine', 'Only Mine')
    theirs = _track(db, 'Theirs', 'Only Theirs')
    _play(db, both, 10, times=3, owner=1)
    _play(db, both, 10, times=3, owner=2)
    _play(db, mine, 10, times=9, owner=1)
    _play(db, theirs, 10, times=9, owner=2)
    with db._get_connection() as conn:
        titles = _names(F.blend(conn, 1, 2, now=NOW))
    assert titles[0] == 'Both Love'
    assert set(titles) == {'Both Love', 'My Heavy One', 'Only Mine', 'Only Theirs'}


def test_blends_only_exist_between_profiles_with_their_own_listening(db, monkeypatch):
    # every profile reading the shared pile would be a blend of you with yourself
    monkeypatch.setattr('core.listening_scope.listening_owners', lambda database: [1])
    monkeypatch.setattr('core.listening_scope.listening_owner', lambda database, pid: 1)
    t = _track(db, 'A', 'x')
    _play(db, t, 1, times=40)
    keys = [m['key'] for m in F.build_for_you(db, 1, now=NOW)['mixes']]
    assert not any(k.startswith('blend_') for k in keys)


def test_a_blend_card_names_both_people(db, monkeypatch):
    monkeypatch.setattr('core.listening_scope.listening_owners', lambda database: [1, 2])
    monkeypatch.setattr('core.listening_scope.listening_owner', lambda database, pid: 1)
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO profiles (id, name) VALUES (1, 'Boulder')")
        conn.execute("INSERT OR REPLACE INTO profiles (id, name) VALUES (2, 'Thomas')")
        conn.commit()
    for n in range(10):
        t = _track(db, f'Shared {n}', f'shared {n}')
        _play(db, t, 5, times=2, owner=1)
        _play(db, t, 5, times=2, owner=2)
    blends = [m for m in F.build_for_you(db, 1, now=NOW)['mixes'] if m['key'] == 'blend_2']
    assert blends and blends[0]['name'] == 'Boulder + Thomas'


# ── the endpoints ────────────────────────────────────────────────────────────

def _client(monkeypatch, name, fake):
    from flask import Flask

    import api.discover_routes as routes

    monkeypatch.setattr(f'core.personalized.for_you.{name}', fake)
    monkeypatch.setattr(routes, 'get_database', lambda: object())
    monkeypatch.setattr(routes, 'get_current_profile_id', lambda: 3)
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return app.test_client()


def test_the_for_you_endpoint_serves_the_profiles_mixes(monkeypatch):
    seen = []
    client = _client(monkeypatch, 'build_for_you',
                     lambda db, pid: seen.append(pid) or {'mixes': [{'key': 'on_repeat'}]})
    assert client.get('/api/discover/for-you').get_json() == {
        'success': True, 'mixes': [{'key': 'on_repeat'}]}
    assert seen == [3]


def test_flow_is_never_served_from_a_cache(monkeypatch):
    calls = []
    client = _client(monkeypatch, 'build_flow',
                     lambda db, pid: calls.append(pid) or {'tracks': [{'name': str(len(calls))}]})
    first = client.get('/api/discover/flow').get_json()['tracks']
    second = client.get('/api/discover/flow').get_json()['tracks']
    assert calls == [3, 3] and first != second
