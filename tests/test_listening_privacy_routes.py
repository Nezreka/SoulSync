"""share listening and the private session through the real app: the switches
are your own, a private session keeps the web player's play out of history
(so out of taste, blends, stats and scrobbles), and the For You blends follow
both people's answers.

heavy (imports web_server once), so it lives in its own module like the other
route tests.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-privacy-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'privacy_routes.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def db():
    return web_server.get_database()


def _as(client, pid):
    with client.session_transaction() as sess:
        sess['profile_id'] = pid


def _plays(db, pile, artist):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM listening_history WHERE profile_id = ? AND artist = ?",
                            (pile, artist)).fetchone()[0]
    finally:
        conn.close()


def _log(client, artist):
    return client.post('/api/library/log-play', json={
        'track': {'title': f'{artist} song', 'artist': artist, 'album': 'A'},
        'duration_ms': 1000,
    }).get_json()


def test_a_private_session_keeps_the_play_out_of_history(client, db):
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    db.set_profile_listenbrainz(kim, 'token', '', 'kim_lb')
    _as(client, kim)
    artist = f'priv-{os.urandom(3).hex()}'

    r = client.post('/api/profiles/me/listening-privacy', json={'private_session': True}).get_json()
    assert r['success'] and r['private_session'] is True and r['private_until']
    out = _log(client, artist)
    assert out.get('skipped') is True and 'play_id' not in out
    assert _plays(db, kim, artist) == 0

    r = client.post('/api/profiles/me/listening-privacy', json={'private_session': False}).get_json()
    assert r['private_session'] is False
    out = _log(client, artist)
    assert out['success'] is True and out.get('play_id')
    assert _plays(db, kim, artist) == 1


def test_one_profiles_private_session_leaves_the_others_alone(client, db):
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    bob = db.create_profile(name=f'bob_{os.urandom(3).hex()}')
    db.set_profile_listenbrainz(bob, 'token', '', 'bob_lb')
    _as(client, kim)
    client.post('/api/profiles/me/listening-privacy', json={'private_session': True})
    _as(client, bob)
    artist = f'bob-{os.urandom(3).hex()}'
    assert _log(client, artist).get('play_id')
    assert _plays(db, bob, artist) == 1


def test_share_listening_is_asked_once_and_only_your_own(client, db):
    kim = db.create_profile(name=f'kim_{os.urandom(3).hex()}')
    _as(client, kim)
    got = client.get('/api/profiles/me/listening-privacy').get_json()
    assert got['share_listening'] is False and got['ask'] is True
    # "not now" stops the asking and changes nothing else
    got = client.post('/api/profiles/me/listening-privacy', json={'asked': True}).get_json()
    assert got['ask'] is False and got['share_listening'] is False
    got = client.post('/api/profiles/me/listening-privacy', json={'share_listening': True}).get_json()
    assert got['share_listening'] is True
    # a body naming another profile changes only the caller's
    from core.personalized.listening_privacy import get_privacy
    other = db.create_profile(name=f'other_{os.urandom(3).hex()}')
    client.post('/api/profiles/me/listening-privacy', json={'share_listening': True, 'profile_id': other})
    assert get_privacy(db, other)['share_listening'] is False


def test_for_you_blends_follow_both_answers(client, db):
    from core.personalized.listening_privacy import set_share_listening
    # two people with their own listening and plenty in common
    a = db.create_profile(name=f'ann_{os.urandom(3).hex()}')
    b = db.create_profile(name=f'ben_{os.urandom(3).hex()}')
    for pid in (a, b):
        db.set_profile_listenbrainz(pid, 'token', '', f'lb{pid}')
    conn = db._get_connection()
    try:
        for i in range(12):
            art = conn.execute("INSERT INTO lib2_artists (name, name_key) VALUES (?, ?)",
                               (f'Band {a}-{i}', f'band {a}-{i}')).lastrowid
            al = conn.execute("INSERT INTO lib2_albums (primary_artist_id, title, origin) "
                              "VALUES (?, 'LP', 'library')", (art,)).lastrowid
            tid = conn.execute("INSERT INTO lib2_tracks (album_id, title, duration) VALUES (?, ?, 200000)",
                               (al, f'song {i}')).lastrowid
            conn.execute("INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
                         (tid, f'/m/{a}-{i}.flac'))
            for pid in (a, b):
                for n in range(2):
                    conn.execute(
                        "INSERT INTO listening_history (track_id, title, artist, played_at, lib2_track_id, "
                        "profile_id) VALUES (?, ?, ?, datetime('now', ?), ?, ?)",
                        (f'k{pid}-{i}-{n}', f'song {i}', f'Band {a}-{i}', f'-{n + 1} days', tid, pid))
        conn.commit()
    finally:
        conn.close()

    def blends(pid):
        web_server.app  # noqa: B018 - the app is imported for its routes
        from api.discover_routes import invalidate_discover_shelf_cache
        invalidate_discover_shelf_cache()
        _as(client, pid)
        mixes = client.get('/api/discover/for-you').get_json().get('mixes') or []
        return [m['key'] for m in mixes if m['key'].startswith('blend_')]

    assert f'blend_{b}' not in blends(a)
    set_share_listening(db, a, True)
    assert f'blend_{b}' not in blends(a)          # ben hasn't said yes
    set_share_listening(db, b, True)
    assert f'blend_{b}' in blends(a)
    assert f'blend_{a}' in blends(b)
    # ben changes his mind: gone from ann's shelf straight away, cache or not
    _as(client, b)
    client.post('/api/profiles/me/listening-privacy', json={'share_listening': False})
    _as(client, a)
    mixes = client.get('/api/discover/for-you').get_json().get('mixes') or []
    assert not any(m['key'] == f'blend_{b}' for m in mixes)
