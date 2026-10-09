"""Deezer worker: a track whose album is matched is found in that album's
tracklist, not by an artist search.

Soundtrack tracks are credited to "Various Artists" or a label in the library,
so search_track(artist, title) finds nothing; some songs (e.g. "A Whole New
World (Remastered 2022)") are also missing from Deezer's search index but are
on the album.
"""
import sqlite3

import pytest
from unittest.mock import MagicMock

from core.deezer_client import DeezerClient
from core.deezer_worker import DeezerWorker

ALBUM = [
    {'id': 1, 'title': 'Prince Ali', 'track_position': 1, 'artist': {'id': 9, 'name': 'Robin Williams'}},
    {'id': 2, 'title': 'A Whole New World (Remastered 2022)', 'track_position': 2,
     'artist': {'id': 8, 'name': 'Brad Kane'}},
    {'id': 3, 'title': 'A Whole New World (Reprise)', 'track_position': 3, 'artist': {'id': 8, 'name': 'Brad Kane'}},
    {'id': 4, 'title': 'Friend Like Me (Live)', 'track_position': 4, 'artist': {'id': 7, 'name': 'Robin Williams'}},
    {'id': 5, 'title': 'Friend Like Me', 'track_position': 5, 'artist': {'id': 7, 'name': 'Robin Williams'}},
]
pick = DeezerClient.pick_track_from_list


def test_exact_title():
    assert pick(ALBUM, 'Prince Ali')['id'] == 1


def test_remastered_suffix_is_same_song():
    assert pick(ALBUM, 'A Whole New World')['id'] == 2


def test_reprise_and_live_are_other_versions():
    assert pick(ALBUM, 'Friend Like Me')['id'] == 5
    assert pick([ALBUM[3]], 'Friend Like Me') is None


def test_unknown_title_is_none():
    assert pick(ALBUM, 'Not On It') is None
    assert pick([], 'Prince Ali') is None


def test_track_number_breaks_a_tie():
    two = [{'id': 10, 'title': 'Intro', 'track_position': 1}, {'id': 11, 'title': 'Intro', 'track_position': 9}]
    assert pick(two, 'Intro', 9)['id'] == 11
    assert pick(two, 'Intro')['id'] == 10


def _worker(deezer_album_id, tracklist):
    # Ours: Library v2 rows; the album's Deezer id lives in external_ids.
    import json
    db = sqlite3.connect(':memory:')
    db.execute("CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, external_ids TEXT)")
    db.execute("CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, album_id INTEGER, track_number INTEGER)")
    db.execute("INSERT INTO lib2_albums VALUES (1, ?)",
               (json.dumps({'deezer': deezer_album_id}) if deezer_album_id else '{}',))
    db.execute("INSERT INTO lib2_tracks VALUES (7, 1, 2)")
    w = DeezerWorker.__new__(DeezerWorker)
    w.db = MagicMock()
    w.db._get_connection = lambda: _Conn(db)
    w.client = MagicMock()
    w.client.get_album_tracks_raw.return_value = tracklist
    w.client.pick_track_from_list = DeezerClient.pick_track_from_list
    return w


class _Conn:
    def __init__(self, c):
        self.c = c

    def cursor(self):
        return self.c.cursor()

    def close(self):
        pass


def test_worker_uses_album_tracklist():
    w = _worker('356502127', ALBUM)
    r = w._track_from_matched_album(7, 'A Whole New World')
    assert r['id'] == 2
    w.client.get_album_tracks_raw.assert_called_once_with('356502127')


def test_worker_skips_when_album_unmatched():
    w = _worker(None, ALBUM)
    assert w._track_from_matched_album(7, 'Prince Ali') is None
    w.client.get_album_tracks_raw.assert_not_called()


def test_worker_none_when_title_not_on_album():
    assert _worker('1', ALBUM)._track_from_matched_album(7, 'Something Else') is None


# ── the album tracklist is fetched once, through the shared budget ──────────

class _FakeCache:
    def __init__(self):
        self.store = {}

    def get_entity(self, source, kind, entity_id):
        return self.store.get((source, kind, entity_id))

    def store_entity(self, source, kind, entity_id, data):
        self.store[(source, kind, entity_id)] = data


def _client(monkeypatch, cache, api):
    c = DeezerClient.__new__(DeezerClient)
    c._api_get = api
    monkeypatch.setattr('core.deezer_client.get_metadata_cache', lambda: cache)
    import core.deezer_throttle as throttle
    monkeypatch.setattr(throttle, 'wait_for_slot', lambda *a, **k: True)
    return c


def test_tracklist_is_fetched_once_and_cached_under_the_playlist_key(monkeypatch):
    cache, calls = _FakeCache(), []

    def api(endpoint, params=None, **kw):
        calls.append(endpoint)
        return {'data': ALBUM}

    c = _client(monkeypatch, cache, api)
    assert c.get_album_tracks_raw(356502127) == ALBUM
    assert c.get_album_tracks_raw('356502127') == ALBUM
    assert calls == ['album/356502127/tracks']
    # the same entry the playlist track-position pass reads
    assert cache.store[('deezer', 'album_tracks', '356502127')] == {'data': ALBUM}


def test_tracklist_cached_by_the_playlist_code_is_reused(monkeypatch):
    cache = _FakeCache()
    cache.store[('deezer', 'album_tracks', '9')] = {'data': ALBUM}
    c = _client(monkeypatch, cache, lambda *a, **k: pytest.fail("should not request"))
    assert c.get_album_tracks_raw(9) == ALBUM


def test_failed_tracklist_fetch_is_empty_and_not_cached(monkeypatch):
    cache = _FakeCache()

    def api(*a, **k):
        raise RuntimeError("boom")

    c = _client(monkeypatch, cache, api)
    assert c.get_album_tracks_raw(9) == []
    assert cache.store == {}


def test_tracklist_fetch_waits_for_a_shared_budget_slot(monkeypatch):
    import core.deezer_throttle as throttle
    slots = []
    c = _client(monkeypatch, _FakeCache(), lambda *a, **k: {'data': ALBUM})
    monkeypatch.setattr(throttle, 'wait_for_slot', lambda *a, **k: slots.append(1) or True)
    c.get_album_tracks_raw(9)
    assert slots == [1]

# ── _process_track tries the album tracklist before the artist search ───────

def _process(monkeypatch, tracklist, search_hit=None):
    # ours: honor_stored_match takes the db positionally
    monkeypatch.setattr('core.deezer_worker.honor_stored_match', lambda *a, **kw: None)
    w = _worker('356502127', tracklist)
    w.stats = {'matched': 0, 'not_found': 0, 'errors': 0}
    w.name_similarity_threshold = 0.80
    w.client.search_track.return_value = search_hit
    w.client.get_track_raw.side_effect = lambda tid: {'id': tid, 'bpm': 120}
    w._verify_artist_id = MagicMock()
    w._mark_status = MagicMock()
    w._update_track = MagicMock()
    w._process_track(7, 'A Whole New World', 'Various Artists', {'type': 'track'})
    return w


def test_process_track_matches_from_album_without_searching(monkeypatch):
    w = _process(monkeypatch, ALBUM)
    w.client.search_track.assert_not_called()
    assert w._update_track.call_args.args[1]['id'] == 2
    assert w.stats['matched'] == 1


def test_process_track_falls_back_to_search_when_not_on_album(monkeypatch):
    hit = {'id': 99, 'title': 'A Whole New World', 'artist': {'id': 8, 'name': 'Brad Kane'}}
    w = _process(monkeypatch, [ALBUM[0]], search_hit=hit)
    w.client.search_track.assert_called_once_with('Various Artists', 'A Whole New World')
    assert w._update_track.call_args.args[1]['id'] == 99
