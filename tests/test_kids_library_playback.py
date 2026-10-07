"""a kids profile could play nothing from the library.

hide_explicit blocked every GET /stream/audio, there to stop a soulseek
result (no explicit data) from playing. but /stream/audio is also how every
library track reaches the player, after /api/library/play has already
checked it. so the kid's browser got a 403 json body as audio and said
"audio format not supported by your browser". the admin, unrestricted,
played the same track fine.

now /api/library/play vouches for the file it checked in the session, and
/stream/audio serves a kid only while the session still points at it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

web_server = pytest.importorskip('web_server')


def _as(pid):
    c = web_server.app.test_client()
    with c.session_transaction() as s:
        s.clear()
        s['profile_id'] = pid
    return c


@pytest.fixture
def kid():
    db = web_server.get_database()
    pid = db.create_profile(name=f'kid_{uuid4().hex[:8]}')
    db.update_profile(pid, hide_explicit=1)
    yield pid
    db.delete_profile(pid)


@pytest.fixture
def song(tmp_path):
    path = tmp_path / 'Clean Song.mp3'
    path.write_bytes(b'ID3' + b'\0' * 2048)
    return str(path)


def test_a_kid_plays_a_clean_library_track(kid, song):
    c = _as(kid)
    r = c.post('/api/library/play', json={'file_path': song, 'title': 'Clean Song'})
    assert r.status_code == 200 and r.get_json()['success']
    audio = c.get('/stream/audio')
    assert audio.status_code == 200
    assert audio.mimetype.startswith('audio/')


def test_once_the_session_moves_on_the_vouch_is_gone(kid, song, tmp_path):
    """anything else pointing the session at another file (a stream that
    slipped past, a review play) isn't what the library check approved"""
    c = _as(kid)
    assert c.post('/api/library/play', json={'file_path': song}).status_code == 200
    other = tmp_path / 'peer file.flac'
    other.write_bytes(b'fLaC' + b'\0' * 64)
    with c.session_transaction() as s:
        sid = s['stream_sid']
    sess = web_server.stream_state_store.get(sid)
    with sess.lock:
        sess.update({'file_path': str(other), 'stream_url': None})
    r = c.get('/stream/audio')
    assert r.status_code == 403 and r.get_json()['restricted'] is True


def test_a_kid_still_cant_stream_or_play_unchecked_files(kid):
    c = _as(kid)
    assert c.get('/stream/audio').status_code == 403            # nothing vouched yet
    assert c.post('/api/stream/start', json={'username': 'x', 'filename': 'y.flac'}).status_code == 403
    assert c.post('/api/verification/1/play').status_code == 403
    assert c.post('/api/quarantine/abc/play').status_code == 403


def test_an_explicit_track_is_still_refused(kid, song):
    db = web_server.get_database()
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO artists (id, name) VALUES (?, 'A')", (f'kid-artist-{kid}',))
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (?, ?, 'B')",
                     (f'kid-album-{kid}', f'kid-artist-{kid}'))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path, explicit) "
                     "VALUES (?, ?, ?, 'Dirty', ?, 1)",
                     (f'kid-track-{kid}', f'kid-album-{kid}', f'kid-artist-{kid}', song))
        conn.commit()
        r = _as(kid).post('/api/library/play', json={'file_path': song})
        assert r.status_code == 403 and r.get_json()['restricted'] is True
    finally:
        conn.execute("DELETE FROM tracks WHERE id = ?", (f'kid-track-{kid}',))
        conn.execute("DELETE FROM albums WHERE id = ?", (f'kid-album-{kid}',))
        conn.execute("DELETE FROM artists WHERE id = ?", (f'kid-artist-{kid}',))
        conn.commit()
        conn.close()


def test_an_unrestricted_profile_is_untouched(song):
    db = web_server.get_database()
    pid = db.create_profile(name=f'grown_{uuid4().hex[:8]}')
    try:
        c = _as(pid)
        assert c.post('/api/library/play', json={'file_path': song}).status_code == 200
        assert c.get('/stream/audio').status_code == 200
    finally:
        db.delete_profile(pid)
