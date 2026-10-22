"""batch tag write downloaded every cover before the unchanged check (#1798).

the worker pre-fetched one cover per unique album url up front, then skipped
any file that already matched the db. rerunning a batch with cover embedding
on pulled every cover again even when every track was skipped.

covers now download only for tracks that will actually be written, and still
just once per url per batch.
"""

import sqlite3
from types import SimpleNamespace

import pytest

import core.tag_writer as tag_writer
import web_server


class _Db:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


def _seed(tracks):
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE artists (id TEXT, name TEXT, thumb_url TEXT);
        CREATE TABLE albums (id TEXT, title TEXT, year INTEGER, release_date TEXT,
                             genres TEXT, track_count INTEGER, thumb_url TEXT);
        CREATE TABLE tracks (id TEXT, title TEXT, file_path TEXT,
                             artist_id TEXT, album_id TEXT);
        INSERT INTO artists VALUES ('ar1', 'Artist', NULL);
        INSERT INTO albums VALUES ('al1', 'One', 2020, NULL, NULL, 2, 'http://art/one.jpg');
        INSERT INTO albums VALUES ('al2', 'Two', 2021, NULL, NULL, 1, 'http://art/two.jpg');
    """)
    conn.executemany("INSERT INTO tracks VALUES (?, ?, ?, 'ar1', ?)", tracks)
    conn.commit()
    return conn


@pytest.fixture
def batch(monkeypatch):
    """run the batch worker inline with the file io stubbed. ``changed`` is the
    set of paths whose diff says they need a write."""
    calls = {'downloads': [], 'writes': []}
    changed = set()

    monkeypatch.setattr(tag_writer, 'download_cover_art',
                        lambda url: calls['downloads'].append(url) or (b'img', 'image/jpeg'))
    monkeypatch.setattr(tag_writer, 'read_file_tags', lambda path: {'path': path})
    monkeypatch.setattr(tag_writer, 'build_tag_diff',
                        lambda existing, db: [{'changed': existing['path'] in changed}])
    monkeypatch.setattr(tag_writer, 'write_tags_to_file',
                        lambda path, db, embed_cover, cover_data: calls['writes'].append(
                            (path, cover_data)) or {'success': True})
    monkeypatch.setattr(web_server, '_resolve_library_file_path', lambda p: p)
    monkeypatch.setattr(web_server, 'threading', SimpleNamespace(
        Thread=lambda target, **kw: SimpleNamespace(start=target)))
    monkeypatch.setattr(web_server, '_write_tags_batch_state',
                        dict(web_server._write_tags_batch_state, status='idle'))

    conn = _seed([
        ('t1', 'a', '/m/a.flac', 'al1'),
        ('t2', 'b', '/m/b.flac', 'al1'),
        ('t3', 'c', '/m/c.flac', 'al2'),
    ])
    monkeypatch.setattr(web_server, 'get_database', lambda: _Db(conn))

    def run(paths_changed, embed_cover=True):
        changed.clear()
        changed.update(paths_changed)
        resp = web_server.app.test_client().post(
            '/api/library/tracks/write-tags-batch',
            json={'track_ids': ['t1', 't2', 't3'], 'embed_cover': embed_cover})
        assert resp.status_code == 200, resp.get_json()
        return dict(web_server._write_tags_batch_state)

    yield run, calls
    conn.close()


def test_an_all_unchanged_batch_downloads_no_covers(batch):
    run, calls = batch
    state = run(set())
    assert state['skipped'] == 3
    assert calls['writes'] == []
    assert calls['downloads'] == []


def test_only_the_written_tracks_albums_get_a_cover(batch):
    run, calls = batch
    state = run({'/m/c.flac'})
    assert state['written'] == 1 and state['skipped'] == 2
    assert calls['downloads'] == ['http://art/two.jpg']
    assert calls['writes'] == [('/m/c.flac', (b'img', 'image/jpeg'))]


def test_a_cover_still_downloads_once_per_url(batch):
    run, calls = batch
    run({'/m/a.flac', '/m/b.flac', '/m/c.flac'})
    assert sorted(calls['downloads']) == ['http://art/one.jpg', 'http://art/two.jpg']
    assert [c for _, c in calls['writes']] == [(b'img', 'image/jpeg')] * 3


def test_cover_embedding_off_never_downloads(batch):
    run, calls = batch
    run({'/m/a.flac', '/m/c.flac'}, embed_cover=False)
    assert calls['downloads'] == []
    assert [c for _, c in calls['writes']] == [None, None]
