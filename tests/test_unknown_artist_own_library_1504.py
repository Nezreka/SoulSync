"""Regression tests for issue #1504 Phase 4 — unknown-artist fixer keeps
own-library files in the owning profile's library.

- scan selects t.owner_profile_id
- _apply_fix joins expected_rel onto the owner's library root (not shared)
- new artist/album rows are stamped with the owner's profile id
"""
import os
import sqlite3
import tempfile

import pytest

from core.repair_jobs.unknown_artist_fixer import UnknownArtistFixerJob as UnknownArtistFixer


def _make_db(owner_profile_id):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT,
                    owner_profile_id INTEGER)""")
    conn.execute("""CREATE TABLE albums (id TEXT PRIMARY KEY, title TEXT,
                    artist_id TEXT, owner_profile_id INTEGER)""")
    conn.execute("""CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT,
                    file_path TEXT, track_number INTEGER, duration INTEGER,
                    artist_id TEXT, album_id TEXT,
                    spotify_track_id TEXT, itunes_track_id TEXT, deezer_id TEXT,
                    owner_profile_id INTEGER)""")
    conn.execute("INSERT INTO artists (id, name) VALUES ('1', 'Unknown Artist')")
    conn.execute("INSERT INTO albums (id, title, artist_id) VALUES ('1', 'Mystery', '1')")
    conn.execute("""INSERT INTO tracks (id, title, file_path, track_number,
                    artist_id, album_id, owner_profile_id)
                    VALUES (1, 'Song', '/own/song.mp3', 1, '1', '1', ?)""",
                 (owner_profile_id,))
    conn.commit()
    return conn


class _FakeDB:
    def __init__(self, conn, profile_roots):
        self._conn = conn
        self._roots = profile_roots

    def _get_connection(self):
        return self._conn

    def get_profile_library(self, profile_id):
        root = self._roots.get(int(profile_id))
        if root:
            return {'mode': 'own', 'root': root}
        return {'mode': 'shared', 'root': None}


class _Ctx:
    def __init__(self, db, transfer):
        self.db = db
        self.transfer_folder = transfer
        self.config_manager = None

    def check_stop(self):
        return False

    def wait_if_paused(self):
        return False

    def report_progress(self, **kw):
        pass


def test_apply_fix_moves_into_owner_library_root():
    """expected_rel is joined onto the owner's root, not the shared folder."""
    with tempfile.TemporaryDirectory() as tmp:
        own_root = os.path.join(tmp, 'own')
        shared_root = os.path.join(tmp, 'shared')
        os.makedirs(own_root)
        os.makedirs(shared_root)
        src = os.path.join(tmp, 'song.mp3')
        with open(src, 'wb') as f:
            f.write(b'\x00' * 100)

        conn = _make_db(7)
        db = _FakeDB(conn, {7: own_root})
        ctx = _Ctx(db, shared_root)
        fixer = UnknownArtistFixer()

        track = {'id': 1, 'title': 'Song', 'album_id': '1',
                 'album_title': 'Mystery', 'owner_profile_id': 7}
        corrected = {'artist': 'Real Artist', 'album': 'Mystery',
                     'title': 'Song', 'track_number': 1}
        ok = fixer._apply_fix(ctx, track, corrected, src,
                              os.path.join('Real Artist', 'song.mp3'),
                              shared_root, fix_tags=False, reorganize_files=True)
        conn.close()

        assert ok is True
        assert os.path.isfile(os.path.join(own_root, 'Real Artist', 'song.mp3'))
        assert not os.path.exists(os.path.join(shared_root, 'Real Artist', 'song.mp3'))


def test_apply_fix_shared_track_uses_transfer_folder():
    """No owner -> shared behavior unchanged (byte-identical default)."""
    with tempfile.TemporaryDirectory() as tmp:
        shared_root = os.path.join(tmp, 'shared')
        os.makedirs(shared_root)
        src = os.path.join(tmp, 'song.mp3')
        with open(src, 'wb') as f:
            f.write(b'\x00' * 100)

        conn = _make_db(None)
        db = _FakeDB(conn, {})
        ctx = _Ctx(db, shared_root)
        fixer = UnknownArtistFixer()

        track = {'id': 1, 'title': 'Song', 'album_id': '1',
                 'album_title': 'Mystery', 'owner_profile_id': None}
        corrected = {'artist': 'Real Artist', 'album': 'Mystery',
                     'title': 'Song', 'track_number': 1}
        ok = fixer._apply_fix(ctx, track, corrected, src,
                              os.path.join('Real Artist', 'song.mp3'),
                              shared_root, fix_tags=False, reorganize_files=True)
        conn.close()

        assert ok is True
        assert os.path.isfile(os.path.join(shared_root, 'Real Artist', 'song.mp3'))


def test_apply_fix_stamps_owner_on_new_artist_and_album():
    """Retagged artist/album rows carry the track's owner_profile_id."""
    with tempfile.TemporaryDirectory() as tmp:
        own_root = os.path.join(tmp, 'own')
        os.makedirs(own_root)
        src = os.path.join(tmp, 'song.mp3')
        with open(src, 'wb') as f:
            f.write(b'\x00' * 100)

        # use a file DB so we can re-open after _apply_fix closes its handle
        db_path = os.path.join(tmp, 'test.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT,
                        owner_profile_id INTEGER)""")
        conn.execute("""CREATE TABLE albums (id TEXT PRIMARY KEY, title TEXT,
                        artist_id TEXT, owner_profile_id INTEGER)""")
        conn.execute("""CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT,
                        file_path TEXT, track_number INTEGER, artist_id TEXT,
                        album_id TEXT, owner_profile_id INTEGER)""")
        conn.execute("INSERT INTO artists (id, name) VALUES ('1', 'Unknown Artist')")
        conn.execute("INSERT INTO albums (id, title, artist_id) VALUES ('1', 'Mystery', '1')")
        conn.execute("""INSERT INTO tracks (id, title, file_path, track_number,
                        artist_id, album_id, owner_profile_id)
                        VALUES (1, 'Song', '/own/song.mp3', 1, '1', '1', 7)""")
        conn.commit()
        conn.close()

        class _FileDB:
            def _get_connection(self):
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c
            def get_profile_library(self, pid):
                return {'mode': 'own', 'root': own_root}

        ctx = _Ctx(_FileDB(), os.path.join(tmp, 'shared'))
        fixer = UnknownArtistFixer()

        track = {'id': 1, 'title': 'Song', 'album_id': '1',
                 'album_title': 'Mystery', 'owner_profile_id': 7}
        corrected = {'artist': 'Brand New Artist', 'album': 'Mystery',
                     'title': 'Song', 'track_number': 1}
        fixer._apply_fix(ctx, track, corrected, src,
                         os.path.join('Brand New Artist', 'song.mp3'),
                         os.path.join(tmp, 'shared'),
                         fix_tags=False, reorganize_files=True)

        vconn = sqlite3.connect(db_path)
        vconn.row_factory = sqlite3.Row
        cur = vconn.cursor()
        cur.execute("SELECT owner_profile_id FROM artists WHERE name = 'Brand New Artist'")
        row = cur.fetchone()
        assert row is not None and row[0] == 7
        cur.execute("SELECT owner_profile_id FROM albums WHERE id = '1'")
        assert cur.fetchone()[0] == 7
        vconn.close()
