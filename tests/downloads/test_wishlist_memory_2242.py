"""#2242: a big wishlist run must not eat the worker's memory.

- a run's album batches wait for one of three album workers; waiting, they
  hold their tracks packed, not as dicts
- a stopped or cleared waiting batch never starts
- a second whole-wishlist run is refused while one is still going
- an album worker drops its search results before it waits out the download
- the duplicate cleanup streams 13.6k rows in seconds
"""

from __future__ import annotations

import gc
import json
import threading
import time
from datetime import datetime, timedelta
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from core.downloads import master as mw
from core.downloads.queued_tracks import PackedTracks, pack_tracks, unpack_tracks
from core.runtime_state import download_batches, download_tasks
from core.wishlist import processing as wp

_QUIET = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                         error=lambda *a, **k: None, debug=lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _clean_state():
    download_batches.clear()
    download_tasks.clear()
    yield
    download_batches.clear()
    download_tasks.clear()


def _track(n, album):
    """a wishlist track the size of a real one (a deezer row off a v2 library)"""
    data = {
        'id': f'dz-{n}', 'name': f'Song {n}', 'provider': 'deezer', 'source': 'deezer',
        'provider_ids': {'deezer': f'{n}', 'discogs': f'r{n}_t1', 'itunes': f'{n + 7}'},
        'artists': [{'name': f'Artist {album}', 'provider_ids': {
            'deezer': '9120', 'musicbrainz': 'c0692af0-2ccb-47b9-a293-f327c16ffcdb',
            'lastfm': 'https://www.last.fm/music/Some+Artist'}}],
        'album': {'name': f'Album {album}', 'id': f'al-{album}', 'total_tracks': 12,
                  'album_type': 'album', 'release_date': '1996-06-18',
                  'artists': [{'name': f'Artist {album}'}],
                  'images': [{'url': f'/api/library/v2/artwork/album/{album}', 'height': 500,
                              'width': 500}]},
        'track_number': n % 12 + 1, 'disc_number': 1, 'duration_ms': 240000,
        'quality_profile': {'id': 1, 'name': 'Default', 'ranked_targets': '[{"format": "flac"}]',
                            'search_mode': 'priority', 'upgrade_policy': 'acceptable'},
    }
    return {
        'wishlist_id': n, 'track_id': data['id'], 'spotify_track_id': data['id'],
        'id': data['id'], 'name': data['name'], 'artists': data['artists'],
        'album': data['album'], 'track_data': data, 'spotify_data': data,
        'source_type': 'library_v2', 'duration_ms': 240000, 'profile_id': 1,
        'source_info': json.dumps({'source': 'library_v2', 'lib2_track_id': n,
                                   'quality_profile_id': 1, 'metadata_source': 'deezer'}),
    }


def _wishlist(albums, per_album):
    return [_track(a * per_album + i, a) for a in range(albums) for i in range(per_album)]


class _HoldingExecutor:
    """keeps every job the way a busy ThreadPoolExecutor's queue does"""

    def __init__(self):
        self.jobs = []

    def submit(self, fn, *args, **kwargs):
        self.jobs.append((fn, args, kwargs))


def _runtime(album_executor, *, run=None):
    return SimpleNamespace(
        download_batches=download_batches, tasks_lock=threading.RLock(),
        missing_download_executor=_HoldingExecutor(), album_bundle_executor=album_executor,
        run_full_missing_tracks_process=run or (lambda *a, **k: None),
        get_batch_max_concurrent=lambda: 3, current_time_fn=time.time,
        profile_id=1, logger=_QUIET, get_music_database=lambda: None,
    )


# --- waiting batches hold their tracks packed -------------------------------

def test_packed_tracks_round_trip_keeps_every_field_and_shared_dict():
    tracks = _wishlist(1, 3)
    tracks[0]['used'] = {'a', 'b'}
    packed = pack_tracks(tracks)
    assert isinstance(packed, PackedTracks)
    assert len(packed) == 3
    assert packed[1]['name'] == 'Song 1'
    back = unpack_tracks(packed)
    assert back == tracks
    # track_data and spotify_data stay one dict, as the wishlist built them
    assert back[0]['track_data'] is back[0]['spotify_data']
    assert unpack_tracks(tracks) is tracks


def test_unpicklable_tracks_wait_as_they_are():
    tracks = [{'name': 'x', 'cb': lambda: None}]
    assert pack_tracks(tracks) is tracks


def test_a_13k_wishlist_run_waits_in_a_fraction_of_the_memory():
    """13,608 tracks in 1,134 albums: every album batch waits in the album pool.
    the jobs used to hold each track as dicts (~128 MB on a real wishlist)"""
    executor = _HoldingExecutor()
    runtime = _runtime(executor)

    def waiting_bytes(pack):
        gc.collect()
        tracemalloc.start()
        tracks = _wishlist(1134, 12)
        if not pack:
            original = wp.pack_tracks
            wp.pack_tracks = lambda t: t
        try:
            wp._run_wishlist_cycle(runtime, playlist_id='wishlist', cycle='albums', tracks=tracks,
                                   run_id='r1', auto_initiated=False)
        finally:
            if not pack:
                wp.pack_tracks = original
        del tracks
        gc.collect()
        held = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        executor.jobs.clear()
        download_batches.clear()
        return held

    as_dicts = waiting_bytes(pack=False)
    packed = waiting_bytes(pack=True)
    print(f"\n#2242 waiting run, 13608 tracks: dicts {as_dicts / 1e6:.1f} MB, "
          f"packed {packed / 1e6:.1f} MB")
    assert packed < as_dicts / 5


def test_only_three_albums_run_at_once_and_the_rest_start_as_workers_free():
    """218 albums on the album pool: never more than its three workers run,
    the rest wait as 'queued' and each starts when a worker frees up"""
    gate = threading.Event()
    lock = threading.Lock()
    running = {'now': 0, 'max': 0, 'done': 0}

    def run(batch_id, playlist_id, tracks, serialize=False):
        tracks = unpack_tracks(tracks)
        assert tracks and serialize is True
        with lock:
            running['now'] += 1
            running['max'] = max(running['max'], running['now'])
        gate.wait(5)
        with lock:
            running['now'] -= 1
            running['done'] += 1

    pool = ThreadPoolExecutor(max_workers=3)
    try:
        runtime = _runtime(pool, run=run)
        result = wp._run_wishlist_cycle(runtime, playlist_id='wishlist', cycle='albums',
                                        tracks=_wishlist(218, 2), run_id='r2',
                                        auto_initiated=False)
        assert result['album_batches'] == 218
        time.sleep(0.3)
        assert running['now'] == 3
        assert sum(1 for b in download_batches.values() if b['phase'] == 'queued') == 218
        gate.set()
    finally:
        pool.shutdown(wait=True)
    assert running['max'] == 3
    assert running['done'] == 218


# --- a stopped or cleared waiting batch never starts ------------------------

def _master_deps():
    from tests.downloads.test_downloads_master import _build_deps
    return _build_deps()


@pytest.mark.parametrize('row', [None, {'phase': 'cancelled', 'queue': []}])
def test_a_waiting_batch_stopped_or_cleared_does_not_start(monkeypatch, row):
    started = []
    monkeypatch.setattr(mw, '_run_full_missing_tracks_process',
                        lambda *a, **k: started.append(a))
    if row is not None:
        download_batches['w1'] = row
    mw.run_full_missing_tracks_process('w1', 'wishlist', pack_tracks(_wishlist(1, 2)),
                                       _master_deps(), serialize=True)
    assert started == []
    if row is not None:
        assert download_batches['w1']['phase'] == 'cancelled'


def test_a_waiting_batch_starts_with_its_tracks_unpacked(monkeypatch):
    seen = []
    monkeypatch.setattr(mw, '_run_full_missing_tracks_process',
                        lambda bid, pid, tracks, deps: seen.append(tracks))
    monkeypatch.setattr(mw, '_wait_for_batch_drain', lambda bid: None)
    download_batches['w2'] = {'phase': 'queued', 'queue': []}
    tracks = _wishlist(1, 2)
    mw.run_full_missing_tracks_process('w2', 'wishlist', pack_tracks(tracks), _master_deps())
    assert seen == [tracks]
    assert isinstance(seen[0], list)


# --- one whole-wishlist run at a time ---------------------------------------

def _manual_runtime():
    return wp.WishlistManualDownloadRuntime(
        get_music_database=lambda: None, download_batches=download_batches,
        tasks_lock=threading.RLock(), missing_download_executor=_HoldingExecutor(),
        run_full_missing_tracks_process=lambda *a, **k: None,
        get_batch_max_concurrent=lambda: 3, add_activity_item=lambda *a: None,
        active_server='jellyfin', profile_id=1, logger=_QUIET,
    )


def test_a_second_download_now_waits_for_the_first_run():
    runtime = _manual_runtime()
    first, status = wp.start_manual_wishlist_download_batch(runtime)
    assert status == 200 and first['success']
    assert download_batches[first['batch_id']][wp.FULL_RUN_KEY] is True

    second, status = wp.start_manual_wishlist_download_batch(runtime)
    assert status == 409 and second['already_running']
    assert len(download_batches) == 1

    # picked tracks are not the whole wishlist: they still start
    picked, status = wp.start_manual_wishlist_download_batch(runtime, track_ids=['dz-1'])
    assert status == 200
    assert not download_batches[picked['batch_id']].get(wp.FULL_RUN_KEY)

    # once the run is over (or stopped) a new one starts
    download_batches[first['batch_id']]['phase'] = 'cancelled'
    _again, status = wp.start_manual_wishlist_download_batch(runtime)
    assert status == 200


def test_every_batch_of_a_whole_run_is_marked_and_blocks_download_now():
    runtime = _runtime(_HoldingExecutor())
    wp._run_wishlist_cycle(runtime, playlist_id='wishlist', cycle='albums',
                           tracks=_wishlist(3, 2) + [_track(999, 999)], run_id='auto',
                           auto_initiated=True, batch_extra_fields={wp.FULL_RUN_KEY: True})
    assert all(b.get(wp.FULL_RUN_KEY) for b in download_batches.values())
    _payload, status = wp.start_manual_wishlist_download_batch(_manual_runtime())
    assert status == 409


# --- an album worker lets go of its search before it waits -----------------

def _fresh_results(n, tag):
    from core.download_plugins.types import TrackResult
    return [TrackResult(username=f'peer{i % 250}', size=30_000_000 + i, bitrate=None,
                        duration=240000, quality='flac', free_upload_slots=1,
                        upload_speed=1_000_000, queue_length=0,
                        filename=f'@@music\\Artist\\Some Album ({tag})\\{i:05d} - Artist - '
                                 f'Track title number {i}.flac')
            for i in range(n)]


class _BigSearchSoulseek:
    """an album search that turns up 5,000 loose files per query, fresh each call
    (a real search hands back a new list every time)"""

    def __init__(self):
        self.calls = 0

    async def search(self, query, timeout=30):
        self.calls += 1
        return _fresh_results(5000, self.calls), []

    def filter_results_by_quality_preference(self, tracks, **_k):
        return tracks


def test_the_album_worker_drops_its_search_results_before_waiting_out_the_download(monkeypatch):
    from tests.downloads.test_downloads_master import (
        _FakeConfig, _FakeDB, _FakeSoulseekWrapper, _build_deps)
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: _FakeDB())
    slsk = _BigSearchSoulseek()
    deps = _build_deps(config=_FakeConfig({'download_source.mode': 'soulseek'}),
                       soulseek=_FakeSoulseekWrapper(slsk))
    download_batches['alb'] = {
        'phase': 'queued', 'queue': [], 'analysis_total': 0, 'analysis_processed': 0,
        'analysis_results': [], 'is_album_download': True, 'force_download_all': True,
        'album_context': {'name': 'Some Album', 'total_tracks': 2},
        'artist_context': {'name': 'Artist'},
    }
    held = {}

    def measure_while_waiting(batch_id, *a, **k):
        gc.collect()
        held['bytes'] = tracemalloc.get_traced_memory()[0]

    monkeypatch.setattr(mw, '_wait_for_batch_drain', measure_while_waiting)
    tracks = [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1},
              {'name': 'T2', 'artists': ['Artist'], 'track_number': 2}]
    gc.collect()
    tracemalloc.start()
    try:
        mw.run_full_missing_tracks_process('alb', 'wishlist', tracks, deps, serialize=True)
    finally:
        tracemalloc.stop()
    assert slsk.calls >= 2  # several queries, each 5,000 files
    pool = download_batches['alb'].get('preflight_pool') or []
    assert len(pool) == 800  # the batch's own retry pool stays
    print(f"\n#2242 album worker holds {held['bytes'] / 1e6:.1f} MB while its download runs "
          f"({slsk.calls} x 5000 search hits)")
    # 800 kept hits are well under 1 MB; every hit of every query is several
    assert held['bytes'] < 2_500_000


# --- the duplicate cleanup --------------------------------------------------

def test_duplicate_cleanup_streams_13k_rows_in_seconds(tmp_path, monkeypatch):
    from core.settings import config_manager
    from database.music_database import MusicDatabase
    monkeypatch.setattr(config_manager, 'get',
                        lambda key, default=None: True if key == 'wishlist.allow_duplicate_tracks'
                        else default)
    db = MusicDatabase(str(tmp_path / 'w.db'))
    base = datetime(2026, 1, 1)
    rows = []
    for n in range(13608):
        # every 9th row repeats the one before it under another id
        same = n - 1 if n % 9 == 8 else n
        data = _track(same, same // 12)['track_data']
        added = (base + timedelta(seconds=n)).strftime('%Y-%m-%d %H:%M:%S')
        rows.append((f'row-{n}', json.dumps(data), added, 1))
    rows.append(('broken', '{not json', '2026-01-02 00:00:00', 1))
    with db._get_connection() as conn:
        conn.executemany("INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, date_added, "
                         "profile_id) VALUES (?, ?, ?, ?)", rows)
        conn.commit()

    gc.collect()
    tracemalloc.start()
    started = time.monotonic()
    removed = db.remove_wishlist_duplicates(profile_id=1)
    took = time.monotonic() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    print(f"\n#2242 duplicate cleanup, 13609 rows: {took:.2f}s, {removed} removed, "
          f"peak {peak / 1e6:.1f} MB")
    assert removed == 13608 // 9
    assert took < 10
    # a key per row, never every row's json at once
    assert peak < 12_000_000
    with db._get_connection() as conn:
        left = conn.execute("SELECT COUNT(*) FROM wishlist_tracks").fetchone()[0]
    assert left == 13609 - removed  # the unreadable row is kept, as before
    assert db.remove_wishlist_duplicates(profile_id=1) == 0
