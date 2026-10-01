"""keep best keeps the copy a playlist points at (discord, jadux).

    "I have over 500 duplicates right now and I will have to review them one by
    one.. if it would keep playlists intact I could just bulk accept everything"

deleting a copy drops it from any server playlist that held it, and keep best
picked by quality only. now the detector tags each copy with the playlists it's
in, keep best ranks that first, and the fix asks the server again so a playlist
edited after the scan still counts.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.library import playlist_membership as pm
from core.repair_jobs.duplicate_detector import DuplicateDetectorJob, _normalize
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


@pytest.fixture(autouse=True)
def _fresh_cache():
    pm.clear_cache()
    yield
    pm.clear_cache()


# ── reading membership off each server ──────────────────────────────────


class _Navidrome:
    def get_all_playlists(self):
        return [SimpleNamespace(id='p1', title='Road Trip'), SimpleNamespace(id='p2', title='Gym')]

    def get_playlist_tracks(self, playlist_id):
        ids = {'p1': ['t1', 't2'], 'p2': ['t2']}[playlist_id]
        return [SimpleNamespace(ratingKey=i) for i in ids]


class _Plex:
    def __init__(self):
        self.asked = []

    def get_all_playlists(self):
        return [SimpleNamespace(id='9', title='Chill')]

    def get_playlist_track_ids(self, playlist_id, playlist_name=''):
        self.asked.append((playlist_id, playlist_name))
        return ['101']


def test_navidrome_membership_by_track_id():
    assert pm.read_server_playlist_membership('navidrome', _Navidrome()) == {
        't1': ['Road Trip'],
        't2': ['Road Trip', 'Gym'],
    }


def test_plex_passes_the_playlist_name_along():
    plex = _Plex()
    assert pm.read_server_playlist_membership('plex', plex) == {'101': ['Chill']}
    assert plex.asked == [('9', 'Chill')]


def test_a_broken_playlist_is_skipped_not_fatal():
    class _Half(_Navidrome):
        def get_playlist_tracks(self, playlist_id):
            if playlist_id == 'p2':
                raise RuntimeError('boom')
            return super().get_playlist_tracks(playlist_id)

    assert pm.read_server_playlist_membership('navidrome', _Half()) == {
        't1': ['Road Trip'], 't2': ['Road Trip'],
    }


def test_no_server_engine_means_no_membership():
    # a repair thread (or a test) must never build an engine of its own
    assert pm.server_playlist_membership(resolve=lambda: (None, None)) == {}


def test_membership_is_cached_so_bulk_fix_reads_the_server_once():
    calls = []

    class _Counting(_Navidrome):
        def get_all_playlists(self):
            calls.append(1)
            return super().get_all_playlists()

    clock = [1000.0]
    resolve = lambda: ('navidrome', _Counting())  # noqa: E731
    for _ in range(500):
        pm.server_playlist_membership(now=lambda: clock[0], resolve=resolve)
    assert len(calls) == 1
    clock[0] += 301
    pm.server_playlist_membership(now=lambda: clock[0], resolve=resolve)
    assert len(calls) == 2


# ── the detector tags each copy ─────────────────────────────────────────


def _track(track_id, path, bitrate):
    return {
        'id': track_id, 'title': 'Numb', 'norm_title': _normalize('Numb'),
        'artist': 'Linkin Park', 'norm_artist': _normalize('Linkin Park'),
        'artist_names': ['Linkin Park'], 'album': 'Meteora', 'file_path': path,
        'bitrate': bitrate, 'duration': 185.0, 'album_thumb_url': None,
        'artist_thumb_url': None, 'artist_id': None,
    }


def test_detector_tags_copies_and_reads_membership_once():
    reads = []
    findings = []
    ctx = SimpleNamespace(
        create_finding=lambda **kw: findings.append(kw) or True,
        report_progress=None, update_progress=None, check_stop=lambda: False,
        playlist_membership=lambda: reads.append(1) or {'2': ['Road Trip']},
    )
    job = DuplicateDetectorJob()
    job._membership = None
    for pair in ((1, 2), (3, 4)):
        job._scan_bucket(
            bucket_tracks=[_track(pair[0], f'/m/{pair[0]}.flac', 1000),
                           _track(pair[1], f'/n/{pair[1]}.mp3', 320)],
            require_metadata_match=True, title_threshold=0.85, artist_threshold=0.8,
            ignore_cross_album=False, found_groups=set(), processed_holder={'count': 0},
            total=2, result=SimpleNamespace(scanned=0, findings_created=0, errors=0,
                                            findings_skipped_dedup=0),
            context=ctx,
        )
    assert len(reads) == 1
    tracks = {t['id']: t['playlists'] for t in findings[0]['details']['tracks']}
    assert tracks == {1: [], 2: ['Road Trip']}


# ── the fix keeps the playlist copy, through the real worker ────────────


def _worker(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'A', 'test')")
        conn.execute("INSERT INTO albums (id, title, artist_id, server_source) VALUES (1, 'Alb', 1, 'test')")
        for tid, path in ((1, tmp_path / 'song.flac'), (2, tmp_path / 'song.mp3')):
            path.write_text('x')
            conn.execute(
                "INSERT INTO tracks (id, title, file_path, artist_id, album_id, server_source) "
                "VALUES (?, 'Song', ?, 1, 1, 'test')", (tid, str(path)))
        conn.commit()
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    return db, w


def _details(tmp_path, mp3_playlists):
    return {'tracks': [
        {'id': 1, 'file_path': str(tmp_path / 'song.flac'), 'bitrate': 1000, 'playlists': []},
        {'id': 2, 'file_path': str(tmp_path / 'song.mp3'), 'bitrate': 320,
         'playlists': mp3_playlists},
    ]}


def _left(db):
    with db._get_connection() as conn:
        return [int(r[0]) for r in conn.execute("SELECT id FROM tracks ORDER BY id")]


def test_keep_best_keeps_the_playlist_copy_over_the_flac(tmp_path, monkeypatch):
    db, w = _worker(tmp_path)
    monkeypatch.setattr(pm, 'server_playlist_membership', lambda: {})
    res = w._fix_duplicates('track', '1', '', _details(tmp_path, ['Road Trip']))
    assert res['success'] is True
    assert _left(db) == [2]
    assert (tmp_path / 'song.mp3').exists()
    assert not (tmp_path / 'song.flac').exists()


def test_fix_uses_live_membership_over_the_scan(tmp_path, monkeypatch):
    # scanned with no playlists, then the user added the mp3 to one
    db, w = _worker(tmp_path)
    monkeypatch.setattr(pm, 'server_playlist_membership', lambda: {'2': ['Added Later']})
    w._fix_duplicates('track', '1', '', _details(tmp_path, []))
    assert _left(db) == [2]


def test_no_playlists_anywhere_still_keeps_the_best_quality(tmp_path, monkeypatch):
    db, w = _worker(tmp_path)
    monkeypatch.setattr(pm, 'server_playlist_membership', lambda: {})
    w._fix_duplicates('track', '1', '', _details(tmp_path, []))
    assert _left(db) == [1]


def test_picking_a_copy_by_hand_still_wins(tmp_path, monkeypatch):
    db, w = _worker(tmp_path)
    monkeypatch.setattr(pm, 'server_playlist_membership', lambda: {'2': ['Road Trip']})
    details = dict(_details(tmp_path, ['Road Trip']), _fix_action='1')
    w._fix_duplicates('track', '1', '', details)
    assert _left(db) == [1]
