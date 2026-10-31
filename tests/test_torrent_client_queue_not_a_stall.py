"""a torrent waiting in the client's own queue is not a stall (thibs, discord).

qbit had the torrent parked in queuedDL (its max-active-downloads limit). the
adapter maps that to "queued", and both stall clocks counted it, so after
10 min (music) or 30 min (video) soulsync gave up on the row and stopped
polling. the user forced it in qbit, it finished, and nothing imported it.

the queue is the client's choice, not a dead swarm. the clock now starts when
the torrent actually starts downloading. a dead magnet (metaDL, which maps to
downloading) still stalls out, and usenet's queued (which also covers
grabbing/propagating, which can hang) stays on the clock.

usenet gets the same hold only when the adapter says the job is plainly
waiting its turn (SAB ``Queued``/``Idle``): SAB runs one job at a time, so the
third grab in line sat at 0% past the 30 minutes and was failed before SAB
ever started it.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock

from core.download_plugins.album_bundle import poll_album_download
from core.download_plugins.torrent_stall import StallTracker


def _status(**over):
    base = dict(id='h', name='rel', state='downloading', progress=0.0, size=1000,
                downloaded=0, download_speed=0, upload_speed=0, seeders=0, peers=0,
                save_path=None, eta=None, content_path=None)
    base.update(over)
    return SimpleNamespace(**base)


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


# ── music ────────────────────────────────────────────────────────────────────

def test_a_queued_torrent_never_trips_the_tracker():
    stall = StallTracker(60.0)
    for now in range(0, 7200, 30):
        assert not stall.is_stalled(0, 'queued', float(now), size=1000)


def test_the_clock_starts_when_it_leaves_the_queue():
    stall = StallTracker(60.0)
    for now in range(0, 3600, 30):
        stall.is_stalled(0, 'queued', float(now), size=1000)
    # out of the queue but not moving yet: a full timeout of grace counted from
    # the last queued poll (3570), then a stall
    assert not stall.is_stalled(0, 'downloading', 3600.0, size=1000)
    assert not stall.is_stalled(0, 'downloading', 3620.0, size=1000)
    assert stall.is_stalled(0, 'downloading', 3631.0, size=1000)


def test_a_dead_magnet_still_stalls_out():
    stall = StallTracker(60.0)
    stall.is_stalled(0, 'downloading', 0.0, size=0)
    assert stall.is_stalled(512, 'downloading', 61.0, size=0)


def test_the_album_poll_loop_rides_out_the_client_queue():
    """the reported shape end to end: queued for an hour, then it runs and
    finishes. it has to come back as a finished download, not a stall."""
    clock = _Clock()
    stall = StallTracker(600.0)
    killed = []

    def get_status():
        if clock.t < 3600:
            return _status(state='queued')
        if clock.t < 3900:
            return _status(downloaded=int(clock.t - 3600))
        return _status(state='seeding', downloaded=1000, save_path='/downloads')

    out = poll_album_download(
        get_status=get_status,
        title='rel',
        emit=lambda state, **f: None,
        complete_states=frozenset(['seeding', 'completed']),
        failed_states=frozenset(['error']),
        poll_interval=30.0,
        timeout=6 * 3600.0,
        sleep=clock.sleep,
        monotonic=clock,
        stall_check=lambda s, now: stall.is_stalled(s.downloaded, s.state, now, size=s.size),
        on_stall=lambda: (killed.append(1), 'stalled')[1],
    )
    assert out == '/downloads'
    assert killed == []


# ── video ────────────────────────────────────────────────────────────────────

def _video_tick(monkeypatch, row, status):
    from core.video import download_monitor as monitor
    import core.video.client_download as client
    db = Mock()
    db.get_active_video_downloads.side_effect = [[row], []]
    db.update_video_download.side_effect = lambda key, **patch: row.update(
        {k: v for k, v in patch.items() if not k.startswith('_')})
    failed = []
    monkeypatch.setattr(monitor, "_fail_or_retry",
                        lambda db, dl, msg, **kw: failed.append(msg))
    monkeypatch.setattr(monitor, "_make_organizer", lambda db: None)
    monkeypatch.setattr(monitor, "_make_pack_importer", lambda db, organizer: None)
    monkeypatch.setattr(monitor, "_misses", {})
    monkeypatch.setattr(client, "_get_status", lambda source, ref: status)
    monitor._tick(db)
    return failed


def _old_row(source):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 3 * 3600))
    return {"id": 1, "source": source, "status": "downloading", "client_ref": "abc",
            "progress": 0, "progress_at": stamp,
            "search_ctx": {"scope": "movie", "title": "Example"}}


def test_video_does_not_fail_a_torrent_sitting_in_the_client_queue(monkeypatch):
    row = _old_row("torrent")
    old_clock = row["progress_at"]
    failed = _video_tick(monkeypatch, row, _status(state='queued', size=0))
    assert failed == []
    assert row["status"] == "downloading"
    # the clock is held at now, so the half hour starts when it really downloads
    assert row["progress_at"] > old_clock


def test_video_still_fails_a_torrent_that_is_downloading_nothing(monkeypatch):
    failed = _video_tick(monkeypatch, _old_row("torrent"), _status(state='stalled'))
    assert len(failed) == 1 and "no progress" in failed[0].lower()


def test_video_usenet_queued_stays_on_the_clock(monkeypatch):
    failed = _video_tick(monkeypatch, _old_row("usenet"), _status(state='queued'))
    assert len(failed) == 1


def test_video_does_not_fail_a_usenet_job_waiting_its_turn_in_sab(monkeypatch):
    row = _old_row("usenet")
    old_clock = row["progress_at"]
    failed = _video_tick(monkeypatch, row,
                         _status(state='queued', size=0, waiting_in_queue=True))
    assert failed == []
    assert row["status"] == "downloading"
    assert row["progress_at"] > old_clock


def test_video_usenet_grabbing_or_propagating_stays_on_the_clock(monkeypatch):
    failed = _video_tick(monkeypatch, _old_row("usenet"),
                         _status(state='queued', waiting_in_queue=False))
    assert len(failed) == 1 and "no progress" in failed[0].lower()


def test_video_waiting_flag_only_holds_usenet_rows(monkeypatch):
    # torrents are held by their own 'queued' state; the usenet flag means
    # nothing to them, so a stalled torrent carrying it still fails
    failed = _video_tick(monkeypatch, _old_row("torrent"),
                         _status(state='stalled', waiting_in_queue=True))
    assert len(failed) == 1
