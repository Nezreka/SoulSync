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


def _people(db, *rows):
    """(id, name, own pile?, shares?) profiles; an own pile is a listenbrainz token"""
    from core.personalized.listening_privacy import ensure_schema
    with db._get_connection() as conn:
        ensure_schema(conn.cursor())
        for pid, name, own, share in rows:
            conn.execute("INSERT OR REPLACE INTO profiles (id, name, listenbrainz_token, share_listening) "
                         "VALUES (?, ?, ?, ?)", (pid, name, 'tok' if own else None, 1 if share else 0))
        conn.commit()


def _blend_keys(db, pid):
    return [m['key'] for m in F.build_for_you(db, pid, now=NOW)['mixes'] if m['key'].startswith('blend_')]


def _shared_plays(db, *owners, n=10, prefix='Shared'):
    for i in range(n):
        t = _track(db, f'{prefix} {i}', f'{prefix.lower()} {i}')
        for o in owners:
            _play(db, t, 5, times=2, owner=o, artist=f'{prefix} {i}')


def test_a_blend_card_names_both_people(db):
    _people(db, (1, 'Boulder', False, True), (2, 'Thomas', True, True))
    _shared_plays(db, 1, 2)
    blends = [m for m in F.build_for_you(db, 1, now=NOW)['mixes'] if m['key'] == 'blend_2']
    assert blends and blends[0]['name'] == 'Boulder + Thomas'


def test_no_blend_unless_both_share(db):
    # nobody has said yes: the default, existing profiles included
    _people(db, (1, 'Boulder', False, False), (2, 'Thomas', True, False))
    _shared_plays(db, 1, 2)
    assert _blend_keys(db, 1) == [] and _blend_keys(db, 2) == []
    # only one side said yes: still nothing, on either side
    _people(db, (1, 'Boulder', False, True), (2, 'Thomas', True, False))
    assert _blend_keys(db, 1) == [] and _blend_keys(db, 2) == []
    _people(db, (1, 'Boulder', False, False), (2, 'Thomas', True, True))
    assert _blend_keys(db, 1) == [] and _blend_keys(db, 2) == []
    # both
    _people(db, (1, 'Boulder', False, True), (2, 'Thomas', True, True))
    assert _blend_keys(db, 1) == ['blend_2'] and _blend_keys(db, 2) == ['blend_1']


def test_the_shared_pile_needs_everyone_on_it(db):
    # profile 3 has no account of its own, so its plays are in the shared
    # pile: the admin's yes can't share them for it
    _people(db, (1, 'Boulder', False, True), (2, 'Thomas', True, True), (3, 'Kid', False, False))
    _shared_plays(db, 1, 2)
    assert _blend_keys(db, 2) == []
    _people(db, (3, 'Kid', False, True))
    assert _blend_keys(db, 2) == ['blend_1']


def test_a_turned_off_profile_does_not_hold_the_pile_back(db):
    _people(db, (1, 'Boulder', False, True), (2, 'Thomas', True, True), (3, 'Gone', False, False))
    with db._get_connection() as conn:
        conn.execute("UPDATE profiles SET disabled = 1 WHERE id = 3")
        conn.commit()
    _shared_plays(db, 1, 2)
    assert _blend_keys(db, 2) == ['blend_1']


def test_partners_are_ranked_by_artists_in_common(db):
    # four people share; the three with most artists in common with you get
    # cards, closest first, whatever their profile ids
    _people(db, (1, 'Me', False, True), (2, 'Few', True, True), (3, 'Most', True, True),
            (4, 'None', True, True), (5, 'Some', True, True))
    _shared_plays(db, 1, 3, n=12, prefix='Big')
    _shared_plays(db, 1, 5, n=8, prefix='Mid')
    _shared_plays(db, 1, 2, n=2, prefix='Tiny')
    _shared_plays(db, 4, n=10, prefix='Elsewhere')
    with db._get_connection() as conn:
        assert F.rank_partners(conn, 1, [2, 3, 4, 5], now=NOW) == [3, 5, 2, 4]
    # three cards at most: the one with nothing in common is left out
    assert _blend_keys(db, 1) == ['blend_3', 'blend_5', 'blend_2']


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
