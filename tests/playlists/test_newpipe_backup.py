"""NewPipe backup import: the export zip (or newpipe.db) becomes mirrored playlists.

the db here is built with newpipe's own table and column names (playlists,
playlist_stream_join, streams, remote_playlists), so the reader is tested
against the real shape, not a convenient one.
"""

from __future__ import annotations

import io
import sqlite3
import uuid
import zipfile

import pytest

import web_server
from core.playlists.newpipe_backup import (
    NotANewPipeBackup,
    read_newpipe_backup,
    source_playlist_id,
)
from core.playlists.user_playlists import has_upstream


def _make_db(path, *, display_index=True, bookmarks=1):
    conn = sqlite3.connect(path)
    conn.executescript(f"""
        CREATE TABLE streams (uid INTEGER PRIMARY KEY AUTOINCREMENT, service_id INTEGER NOT NULL,
            url TEXT NOT NULL, title TEXT NOT NULL, stream_type TEXT NOT NULL, duration INTEGER NOT NULL,
            uploader TEXT NOT NULL, uploader_url TEXT, thumbnail_url TEXT, view_count INTEGER,
            textual_upload_date TEXT, upload_date INTEGER, is_upload_date_approximation INTEGER);
        CREATE TABLE playlists (uid INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
            is_thumbnail_permanent INTEGER NOT NULL DEFAULT 0, thumbnail_stream_id INTEGER NOT NULL DEFAULT -1
            {', display_index INTEGER NOT NULL DEFAULT 0' if display_index else ''});
        -- newpipe keys this on (playlist_id, join_index), which hands rows back in
        -- order by accident; left off here so only the reader's ORDER BY can
        CREATE TABLE playlist_stream_join (playlist_id INTEGER NOT NULL, stream_id INTEGER NOT NULL,
            join_index INTEGER NOT NULL);
        CREATE TABLE remote_playlists (uid INTEGER PRIMARY KEY AUTOINCREMENT, service_id INTEGER NOT NULL,
            name TEXT, url TEXT, thumbnail_url TEXT, uploader TEXT, stream_count INTEGER);
    """)
    streams = [
        # topic channel: the channel is the artist
        (0, 'https://www.youtube.com/watch?v=AAAAAAAAAAA', 'Sanctuary', 'TAYA. - Topic', 437, 'http://t/a.jpg'),
        # artist - title in the title, a label channel uploading it
        (0, 'https://www.youtube.com/watch?v=BBBBBBBBBBB', 'Zombie Apocalypse - Agonist (Leigh Boy Remix)',
         'Some Label', 300, 'http://t/b.jpg'),
        # nothing better: the stream's own channel
        (0, 'https://youtu.be/CCCCCCCCCCC', 'Neglect', 'Charles Oliver', 200, ''),
        # soundcloud keeps its url as the id
        (1, 'https://soundcloud.com/x/y', 'Syringa', 'Stellarplex', 180, ''),
    ]
    conn.executemany(
        "INSERT INTO streams (service_id, url, title, stream_type, duration, uploader, thumbnail_url)"
        " VALUES (?, ?, ?, 'AUDIO_STREAM', ?, ?, ?)",
        [(s, u, t, d, up, th) for s, u, t, up, d, th in streams])
    if display_index:
        conn.execute("INSERT INTO playlists (name, display_index) VALUES ('Second shown', 1)")
        conn.execute("INSERT INTO playlists (name, display_index) VALUES ('First shown', 0)")
    else:
        conn.execute("INSERT INTO playlists (name) VALUES ('Second shown')")
        conn.execute("INSERT INTO playlists (name) VALUES ('First shown')")
    if display_index:
        conn.execute("INSERT INTO playlists (name, display_index) VALUES ('Nothing in it', 2)")
    else:
        conn.execute("INSERT INTO playlists (name) VALUES ('Nothing in it')")
    # playlist 1 holds streams in a deliberately non-uid order
    conn.executemany("INSERT INTO playlist_stream_join VALUES (1, ?, ?)",
                     [(2, 2), (4, 3), (3, 0), (1, 1)])
    conn.execute("INSERT INTO playlist_stream_join VALUES (2, 1, 0)")
    for i in range(bookmarks):
        conn.execute("INSERT INTO remote_playlists (service_id, name, url) VALUES (0, ?, 'u')", (f'b{i}',))
    conn.commit()
    conn.close()
    return path


def _zip_of(db_path, tmp_path, member='newpipe.db'):
    zpath = tmp_path / 'NewPipeData-20261022.zip'
    with zipfile.ZipFile(zpath, 'w') as zf:
        zf.write(db_path, member)
        zf.writestr('newpipe.settings', b'not a db')
    return str(zpath)


@pytest.fixture
def db_path(tmp_path):
    return _make_db(str(tmp_path / 'newpipe.db'))


# ── reading ─────────────────────────────────────────────────────────────────

def test_reads_local_playlists_in_newpipe_order(db_path):
    out = read_newpipe_backup(db_path)
    assert [p['name'] for p in out['playlists']] == ['First shown', 'Second shown', 'Nothing in it']
    assert out['bookmarked'] == 1


def test_tracks_follow_join_index_not_stream_uid(db_path):
    second = next(p for p in read_newpipe_backup(db_path)['playlists'] if p['uid'] == 1)
    assert [t['track_name'] for t in second['tracks']] == [
        'Neglect', 'Sanctuary', 'Agonist (Leigh Boy Remix)', 'Syringa']
    assert second['track_count'] == 4


def test_artist_comes_from_topic_title_split_or_the_streams_channel(db_path):
    tracks = {t['track_name']: t for t in next(
        p for p in read_newpipe_backup(db_path)['playlists'] if p['uid'] == 1)['tracks']}
    assert tracks['Sanctuary']['artist_name'] == 'TAYA.'
    assert tracks['Agonist (Leigh Boy Remix)']['artist_name'] == 'Zombie Apocalypse'
    assert tracks['Neglect']['artist_name'] == 'Charles Oliver'


def test_ids_are_video_ids_and_soundcloud_keeps_its_url(db_path):
    tracks = {t['track_name']: t for t in next(
        p for p in read_newpipe_backup(db_path)['playlists'] if p['uid'] == 1)['tracks']}
    assert tracks['Sanctuary']['source_track_id'] == 'AAAAAAAAAAA'
    assert tracks['Neglect']['source_track_id'] == 'CCCCCCCCCCC'
    assert tracks['Syringa']['source_track_id'] == 'https://soundcloud.com/x/y'
    assert tracks['Sanctuary']['duration_ms'] == 437000
    assert tracks['Sanctuary']['album_name'] == ''


def test_mirror_key_is_the_newpipe_uid_and_art_is_the_first_thumbnail(db_path):
    second = next(p for p in read_newpipe_backup(db_path)['playlists'] if p['uid'] == 1)
    assert second['id'] == source_playlist_id(1) == 'newpipe_1'
    # neglect (first) has no thumbnail, sanctuary's is the first there is
    assert second['image_url'] == 'http://t/a.jpg'


def test_the_cleaners_run_on_artist_and_title(db_path):
    out = read_newpipe_backup(db_path, clean_artist=lambda a: a.upper(),
                              clean_title=lambda t, a: f'{t}!')
    t = next(p for p in out['playlists'] if p['uid'] == 2)['tracks'][0]
    assert (t['artist_name'], t['track_name']) == ('TAYA.', 'Sanctuary!')


def test_reads_the_export_zip(db_path, tmp_path):
    out = read_newpipe_backup(_zip_of(db_path, tmp_path))
    assert len(out['playlists']) == 3


def test_reads_a_zip_with_the_db_in_a_folder(db_path, tmp_path):
    out = read_newpipe_backup(_zip_of(db_path, tmp_path, member='backup/newpipe.db'))
    assert len(out['playlists']) == 3


def test_older_backup_without_display_index(tmp_path):
    out = read_newpipe_backup(_make_db(str(tmp_path / 'old.db'), display_index=False))
    assert [p['name'] for p in out['playlists']] == ['Second shown', 'First shown', 'Nothing in it']


def test_the_db_file_is_left_untouched(db_path):
    before = open(db_path, 'rb').read()
    read_newpipe_backup(db_path)
    assert open(db_path, 'rb').read() == before


@pytest.mark.parametrize('make', [
    lambda p: p.write_bytes(b'hello, not a backup'),
    lambda p: zipfile.ZipFile(p, 'w').writestr('other.txt', 'x'),
])
def test_not_a_backup_is_refused(tmp_path, make):
    bad = tmp_path / 'bad.zip'
    make(bad)
    with pytest.raises(NotANewPipeBackup):
        read_newpipe_backup(str(bad))


def test_a_sqlite_db_that_is_not_newpipe_is_refused(tmp_path):
    other = tmp_path / 'other.db'
    sqlite3.connect(other).execute('CREATE TABLE x (a)').connection.commit()
    with pytest.raises(NotANewPipeBackup, match='playlists'):
        read_newpipe_backup(str(other))


def test_newpipe_mirrors_have_no_upstream_to_refresh():
    assert has_upstream({'source': 'newpipe'}) is False


# ── through the routes ──────────────────────────────────────────────────────

def _upload(db_file, name='NewPipeData.zip'):
    return web_server.app.test_client().post(
        '/api/import/newpipe',
        data={'file': (io.BytesIO(open(db_file, 'rb').read()), name)},
        content_type='multipart/form-data')


def test_parse_route_returns_the_playlists(db_path, tmp_path):
    resp = _upload(_zip_of(db_path, tmp_path))
    body = resp.get_json()
    assert resp.status_code == 200 and body['success'] is True
    assert [p['id'] for p in body['playlists']] == ['newpipe_2', 'newpipe_1', 'newpipe_3']
    assert body['bookmarked'] == 1


def test_parse_route_refuses_a_non_backup_with_400(tmp_path):
    bad = tmp_path / 'x.zip'
    bad.write_bytes(b'nope')
    resp = _upload(str(bad))
    assert resp.status_code == 400 and resp.get_json()['success'] is False


def test_parse_route_wants_a_file():
    resp = web_server.app.test_client().post('/api/import/newpipe', data={},
                                             content_type='multipart/form-data')
    assert resp.status_code == 400


def test_reimport_overwrites_the_same_mirror():
    client = web_server.app.test_client()
    pid = f'newpipe_{uuid.uuid4().hex[:8]}'

    def mirror(tracks):
        return client.post('/api/mirror-playlist', json={
            'source': 'newpipe', 'source_playlist_id': pid, 'name': f'NP {pid}', 'tracks': tracks,
        }).get_json()

    first = mirror([{'track_name': 'A', 'artist_name': 'x', 'source_track_id': 'a1'}])
    second = mirror([{'track_name': 'B', 'artist_name': 'y', 'source_track_id': 'b1'},
                     {'track_name': 'C', 'artist_name': 'z', 'source_track_id': 'c1'}])
    assert first['playlist_id'] == second['playlist_id']
    tracks = client.get(f"/api/mirrored-playlists/{second['playlist_id']}").get_json()['tracks']
    assert [t['track_name'] for t in tracks] == ['B', 'C']
