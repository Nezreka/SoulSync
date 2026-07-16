"""The downloads-folder bleed (Kazimir's 10GB) — three seams, one mechanism.

The monitor stuck-cancelled healthy QUEUED streaming downloads (the engine
serializes per source, so 'Initializing' is a queue position, not a hang);
a streaming cancel can't interrupt yt-dlp, so the "cancelled" file still
landed — with its record gone, nothing ever post-processed or deleted it.

Fix seams covered here:
  1. monitor: an Initializing streaming task is left alone while the
     source's queue is demonstrably moving (another record InProgress)
  2. worker: a download that completes into a Cancelled/removed record
     deletes its landed file instead of orphaning it
  3. reaper: root-level audio no live record references gets swept by the
     cleanup automation (age-gated, never descends into subdirs)

All hermetic: no threads, no network, tmp filesystems only.
"""

from __future__ import annotations

import time

import pytest

from core.download_engine import DownloadEngine
from core.download_engine.worker import BackgroundDownloadWorker
from core.downloads import monitor as dm
from core.downloads.cleanup import sweep_orphaned_download_audio


# ── 1. the monitor leaves a moving queue alone ────────────────────────────────

@pytest.fixture
def fake_monitor(monkeypatch):
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    # web_server.init() normally provides this shared set
    monkeypatch.setattr(dm, '_orphaned_download_keys', set())
    return dm.WebUIDownloadMonitor()


def _queued_task(age_seconds=120):
    now = time.time()
    return {
        'track_info': {'name': 'Test Track'},
        'username': 'youtube',
        'filename': 'vid.mp3',
        'status': 'downloading',
        'download_id': 'dl-queued',
        'status_change_time': now - 1000,
        # pretend the stuck timer has been running past the 90s threshold
        'downloading_start_time': now - age_seconds,
    }


def _row(state, username='youtube', progress=0, transferred=0):
    return {'state': state, 'percentComplete': progress,
            'bytesTransferred': transferred, 'username': username}


def test_queued_streaming_task_is_not_cancelled_while_the_queue_moves(fake_monitor):
    task = _queued_task(age_seconds=500)          # way past the old 90s trigger
    deferred_ops = []
    live = {
        'youtube::vid.mp3': _row('Initializing'),                 # this task: queued
        'youtube::other.mp3': _row('InProgress, Downloading'),    # the worker is busy
    }
    fake_monitor._should_retry_task(task_id='t1', task=task,
                                    live_transfers_lookup=live,
                                    current_time=time.time(), deferred_ops=deferred_ops)
    assert deferred_ops == []                     # no cancel — it's just waiting its turn
    assert 'downloading_start_time' not in task   # stuck timer reset


def test_streaming_task_still_times_out_when_the_queue_is_dead(fake_monitor):
    # No InProgress record for the source at all — the worker is genuinely
    # wedged, so the old stuck path must still fire.
    task = _queued_task(age_seconds=500)
    deferred_ops = []
    live = {'youtube::vid.mp3': _row('Initializing')}
    fake_monitor._should_retry_task(task_id='t1', task=task,
                                    live_transfers_lookup=live,
                                    current_time=time.time(), deferred_ops=deferred_ops)
    assert ('cancel_download', 'dl-queued', 'youtube',
            'unknown_state_no_progress_timeout') in deferred_ops


def test_soulseek_unknown_state_keeps_the_old_behavior(fake_monitor):
    # A Soulseek peer stuck in an unknown state is NOT a serialized queue —
    # the guard must only apply to streaming sources.
    task = _queued_task(age_seconds=500)
    task['username'] = 'somepeer42'
    deferred_ops = []
    live = {
        'somepeer42::vid.mp3': _row('Initializing', username='somepeer42'),
        # another slskd transfer moving must NOT excuse this one
        'otherpeer::x.mp3': _row('InProgress, Downloading', username='otherpeer'),
    }
    fake_monitor._should_retry_task(task_id='t1', task=task,
                                    live_transfers_lookup=live,
                                    current_time=time.time(), deferred_ops=deferred_ops)
    assert ('cancel_download', 'dl-queued', 'somepeer42',
            'unknown_state_no_progress_timeout') in deferred_ops


# ── 2. a cancelled-but-landed file is deleted, not orphaned ──────────────────

def _land_file(tmp_path, name='Artist - Track.mp3'):
    p = tmp_path / name
    p.write_bytes(b'a' * 2048)
    return p


def test_worker_skips_a_download_cancelled_while_queued(tmp_path):
    # Pre-existing clobber bug: the InProgress write at worker start used to
    # OVERWRITE a Cancelled state, so a cancel-while-queued download ran to
    # completion anyway. Now it never even starts.
    engine = DownloadEngine()
    worker = BackgroundDownloadWorker(engine)
    calls = []
    engine.add_record('youtube', 'dl-1', {'state': 'Cancelled'})
    worker._worker_loop('youtube', 'dl-1', 'vid', 'Artist - Track',
                        lambda *a: calls.append(1) or str(tmp_path / 'x.mp3'))
    assert calls == []                                          # impl never invoked
    assert engine.get_record('youtube', 'dl-1')['state'] == 'Cancelled'


def test_worker_skips_a_download_whose_record_was_removed_while_queued(tmp_path):
    engine = DownloadEngine()
    worker = BackgroundDownloadWorker(engine)
    calls = []
    worker._worker_loop('youtube', 'dl-gone', 'vid', 'Artist - Track',
                        lambda *a: calls.append(1) or str(tmp_path / 'x.mp3'))
    assert calls == []


def test_worker_deletes_the_file_when_cancel_lands_mid_download(tmp_path):
    # yt-dlp can't be interrupted: the cancel flips the record while the
    # stream is still writing. The finished file must be deleted on landing —
    # this was the exact bleed path in Kazimir's log (his monitor cancels used
    # remove=True; both shapes are covered).
    engine = DownloadEngine()
    worker = BackgroundDownloadWorker(engine)
    landed = tmp_path / 'Artist - Track.mp3'
    engine.add_record('youtube', 'dl-1', {'state': 'Initializing'})

    def impl_cancelled_mid_stream(*_a):
        landed.write_bytes(b'a' * 2048)
        engine.update_record('youtube', 'dl-1', {'state': 'Cancelled'})
        return str(landed)

    worker._worker_loop('youtube', 'dl-1', 'vid', 'Artist - Track', impl_cancelled_mid_stream)
    assert not landed.exists()                                  # cleaned up
    assert engine.get_record('youtube', 'dl-1')['state'] == 'Cancelled'


def test_worker_deletes_the_file_when_record_removed_mid_download(tmp_path):
    engine = DownloadEngine()
    worker = BackgroundDownloadWorker(engine)
    landed = tmp_path / 'Artist - Track.mp3'
    engine.add_record('youtube', 'dl-1', {'state': 'Initializing'})

    def impl_removed_mid_stream(*_a):
        landed.write_bytes(b'a' * 2048)
        engine.remove_record('youtube', 'dl-1')
        return str(landed)

    worker._worker_loop('youtube', 'dl-1', 'vid', 'Artist - Track', impl_removed_mid_stream)
    assert not landed.exists()


def test_worker_keeps_the_file_on_a_normal_completion(tmp_path):
    engine = DownloadEngine()
    worker = BackgroundDownloadWorker(engine)
    landed = _land_file(tmp_path)
    engine.add_record('youtube', 'dl-2', {'state': 'Initializing'})
    worker._worker_loop('youtube', 'dl-2', 'vid', 'Artist - Track', lambda *a: str(landed))
    assert landed.exists()
    rec = engine.get_record('youtube', 'dl-2')
    assert rec['state'] == 'Completed, Succeeded' and rec['file_path'] == str(landed)


# ── 3. the reaper ─────────────────────────────────────────────────────────────

def test_reaper_removes_only_old_unreferenced_root_audio(tmp_path):
    old = time.time() - 7200
    (tmp_path / 'orphan.mp3').write_bytes(b'x')
    (tmp_path / 'fresh.mp3').write_bytes(b'x')                  # too young
    (tmp_path / 'claimed.mp3').write_bytes(b'x')                # a live record names it
    (tmp_path / 'notes.txt').write_bytes(b'x')                  # not audio
    sub = tmp_path / 'Some Peer Folder'
    sub.mkdir()
    (sub / 'nested.mp3').write_bytes(b'x')                      # subdir: never touched
    import os
    for name in ('orphan.mp3', 'claimed.mp3', 'notes.txt'):
        os.utime(tmp_path / name, (old, old))
    os.utime(sub / 'nested.mp3', (old, old))

    removed = sweep_orphaned_download_audio(
        str(tmp_path), referenced_basenames={'CLAIMED.mp3'})    # case-insensitive
    assert [p.endswith('orphan.mp3') for p in removed] == [True]
    assert (tmp_path / 'fresh.mp3').exists()
    assert (tmp_path / 'claimed.mp3').exists()
    assert (tmp_path / 'notes.txt').exists()
    assert (sub / 'nested.mp3').exists()


def test_reaper_handles_a_missing_dir(tmp_path):
    assert sweep_orphaned_download_audio(str(tmp_path / 'nope')) == []
