"""Unit tests for the targeted reconciliation worker, no network or live DB."""
import importlib
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from core.automation import album_completion_reconcile as reconcile


class Database:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE artists (id TEXT, name TEXT, server_source TEXT);
            CREATE TABLE albums (id TEXT, title TEXT, artist_id TEXT, server_source TEXT);
            CREATE TABLE tracks (album_id TEXT, track_number INTEGER);
            INSERT INTO artists VALUES ('artist1','Artist','navidrome');
            INSERT INTO albums VALUES ('album1','Album','artist1','navidrome');
            INSERT INTO tracks VALUES ('album1', 1);
        ''')
    def _get_connection(self):
        return self.conn


class Scanner:
    def __init__(self):
        self.callbacks = []
        self.requests = 0
    def add_scan_completion_callback(self, cb):
        self.callbacks.append(cb)
    def remove_scan_completion_callback(self, cb):
        self.callbacks.remove(cb)
    def request_scan(self, reason):
        self.requests += 1
        for cb in list(self.callbacks):
            cb()
        return {'status': 'started'}


class Client:
    def get_artist_by_id(self, artist_id):
        assert artist_id == 'artist1'
        return {'id': artist_id}


class Engine:
    def client(self, name):
        assert name == 'navidrome'
        return Client()


def test_track_number_parsing():
    assert reconcile._expected_track_numbers(['/media/09 - The Welterweight.mp3',
                                             '/media/10 - Saturn Song.mp3']) == {9, 10}
    assert reconcile._expected_track_numbers(['/media/unknown.flac']) == set()


def test_verification_rejects_unrelated_album_tracks():
    db = Database()
    db.conn.execute("INSERT INTO tracks VALUES ('album1', 4)")
    assert not reconcile._verified(db, 'album1', {9, 10})
    db.conn.executemany('INSERT INTO tracks VALUES (?,?)', [('album1', 9), ('album1', 10)])
    assert reconcile._verified(db, 'album1', {9, 10})


def test_retries_until_expected_tracks_appear(monkeypatch):
    db = Database()
    scanner = Scanner()
    attempts = []
    class Worker:
        def __init__(self, **kwargs):
            self.database = None
        def _process_artist_with_content(self, *args, **kwargs):
            attempts.append(1)
            if len(attempts) == 2:
                db.conn.executemany('INSERT INTO tracks VALUES (?,?)',
                                    [('album1', 9), ('album1', 10)])
            return True, 'ok', 0, 2 if len(attempts) == 2 else 0
    import database.music_database as music_db
    import core.database_update_worker as update_module
    monkeypatch.setattr(music_db, 'get_database', lambda: db)
    monkeypatch.setattr(update_module, 'DatabaseUpdateWorker', Worker)
    monkeypatch.setattr(reconcile.time, 'sleep', lambda _: None)
    assert reconcile._reconcile('album1', ('09 - The Welterweight.mp3',
                                          '10 - Saturn Song.mp3'), scanner, Engine()) is True
    assert scanner.requests == 2
    assert len(attempts) == 2
    assert scanner.callbacks == []


def test_exhausted_retries_do_not_claim_success(monkeypatch):
    db = Database()
    scanner = Scanner()
    class Worker:
        def __init__(self, **kwargs):
            self.database = None
        def _process_artist_with_content(self, *args, **kwargs):
            return True, 'ok', 0, 0
    import database.music_database as music_db
    import core.database_update_worker as update_module
    monkeypatch.setattr(music_db, 'get_database', lambda: db)
    monkeypatch.setattr(update_module, 'DatabaseUpdateWorker', Worker)
    monkeypatch.setattr(reconcile.time, 'sleep', lambda _: None)
    assert reconcile._reconcile('album1', ('09 - Missing.mp3',), scanner, Engine()) is False
    assert scanner.requests == 3
    assert scanner.callbacks == []
