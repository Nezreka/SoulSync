"""a usenet job sab has only just been handed is not a lost job.

sab answers addurl with an nzo_id and parks the job as a 'Grabbing'
placeholder while it fetches the nzb (one at a time, so a few grabs queue up),
then builds the job: it adds the job's files one at a time, and on a busy sab a
job with ~80 files took over two minutes. while it builds, the job is in
neither the queue nor the history, so the monitor's eight 3-second polls ran out after ~25 s and failed
the row ("download client could not report this transfer"). sab downloaded the
job anyway and the hourly wishlist grabbed the same release again, so every
slow build left a duplicate in sab's completed folder.

until the client reports a new usenet job in a real state, misses inside the
grace window don't count, and each sighting of the placeholder starts the
window again. after the window, or once the job has been reported, a miss is a
real miss again. torrent and soulseek rows are untouched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.video import download_monitor as monitor
import core.video.client_download as client


def _status(**over):
    base = dict(id='abc', name='rel', state='downloading', progress=0.1, size=1000,
                downloaded=100, download_speed=10, upload_speed=0, seeders=0, peers=0,
                save_path=None, eta=None, content_path=None, waiting_in_queue=False,
                fetching_nzb=False)
    base.update(over)
    return SimpleNamespace(**base)


def _row(source="usenet", ref="abc"):
    return {"id": 1, "source": source, "status": "downloading", "client_ref": ref,
            "progress": 0, "search_ctx": {"scope": "movie", "title": "Example"}}


class _Harness:
    """Runs the real monitor tick against a fake db and a scripted client."""

    def __init__(self, monkeypatch, row):
        self.row = row
        self.now = 1000.0
        self.status = None          # what the client reports (None = not listed)
        self.failed = []
        monkeypatch.setattr(monitor, "_fail_or_retry",
                            lambda db, dl, msg, **kw: self.failed.append(msg))
        monkeypatch.setattr(monitor, "_make_organizer", lambda db: None)
        monkeypatch.setattr(monitor, "_make_pack_importer", lambda db, organizer: None)
        monkeypatch.setattr(monitor, "_misses", {})
        monkeypatch.setattr(monitor, "_grace", {}, raising=False)
        monkeypatch.setattr(monitor, "_clock", lambda: self.now, raising=False)
        monkeypatch.setattr(client, "_get_status", lambda source, ref: self.status)

    def poll(self, times=1, step=3.0):
        for _ in range(times):
            if self.failed:
                return
            db = Mock()
            db.get_active_video_downloads.return_value = [self.row]
            db.update_video_download.side_effect = lambda key, **patch: self.row.update(
                {k: v for k, v in patch.items() if not k.startswith('_')})
            monitor._tick(db)
            self.now += step


def test_a_new_usenet_job_sab_is_still_building_is_not_failed(monkeypatch):
    # the reported case: not listed for the first ~25 s (8 polls) and well beyond
    h = _Harness(monkeypatch, _row())
    h.poll(times=82)            # 246 s unlisted, past the worst build seen (245 s)
    assert h.failed == []


def test_it_is_tracked_normally_once_sab_lists_it(monkeypatch):
    h = _Harness(monkeypatch, _row())
    h.poll(times=30)            # 90 s unlisted while sab builds it
    h.status = _status()
    h.poll(times=5)
    assert h.failed == []
    assert h.row["progress"] > 0


def test_a_job_sab_never_lists_still_fails_after_the_window(monkeypatch):
    h = _Harness(monkeypatch, _row())
    polls_in_window = int(monitor._NEW_USENET_GRACE / 3) + 1
    h.poll(times=polls_in_window)
    assert h.failed == []
    h.poll(times=monitor._GIVE_UP_AFTER)
    assert len(h.failed) == 1
    assert "could not report" in h.failed[0]


def test_a_job_that_vanishes_after_being_reported_fails_as_before(monkeypatch):
    # the grace is for a job that hasn't appeared yet, not a second life for
    # one that was there and went (deleted in sab, say)
    h = _Harness(monkeypatch, _row())
    h.status = _status()
    h.poll()
    h.status = None
    h.poll(times=monitor._GIVE_UP_AFTER)
    assert len(h.failed) == 1


@pytest.mark.parametrize("source", ["torrent"])
def test_other_clients_keep_the_old_eight_polls(monkeypatch, source):
    h = _Harness(monkeypatch, _row(source=source))
    h.poll(times=monitor._GIVE_UP_AFTER)
    assert len(h.failed) == 1


def test_a_usenet_row_with_no_client_reference_gets_no_grace(monkeypatch):
    # nothing was handed to sab, so there is nothing to wait for
    h = _Harness(monkeypatch, _row(ref=""))
    h.poll(times=monitor._GIVE_UP_AFTER)
    assert len(h.failed) == 1


def test_the_window_covers_the_slowest_build_seen():
    # 1,029 jobs, 25 Oct-1 Nov: worst 121 s on a normal day, 245 s during a storage scrub
    assert monitor._NEW_USENET_GRACE > 245


def test_finished_rows_leave_no_grace_entries_behind(monkeypatch):
    h = _Harness(monkeypatch, _row())
    h.poll(times=2)
    assert monitor._grace
    db = Mock()
    db.get_active_video_downloads.return_value = []
    monitor._tick(db)
    assert monitor._grace == {}


def _placeholder():
    return _status(state='queued', progress=0.0, size=0, downloaded=0,
                   download_speed=0, fetching_nzb=True)


def test_the_live_timeline_placeholder_then_build_gap_then_listed(monkeypatch):
    # row 1390, 1 nov: grabbed 10:10:22, sab's 'Grabbing' placeholder until
    # 10:12:32 (130 s, behind two other fetches), then the build gap (79 s on
    # that run), then a normal queue slot. the old monitor failed it ~23 s into
    # the gap, because the placeholder had counted as the first report.
    h = _Harness(monkeypatch, _row())
    h.status = _placeholder()
    h.poll(times=44)            # ~130 s as a placeholder
    h.status = None
    h.poll(times=27)            # ~80 s while sab adds the files
    assert h.failed == []
    h.status = _status()
    h.poll(times=3)
    assert h.failed == []


def test_the_window_runs_from_the_last_placeholder_sighting(monkeypatch):
    # a deep pile of grabs: the placeholder alone lasts longer than the window
    h = _Harness(monkeypatch, _row())
    h.status = _placeholder()
    h.poll(times=int(monitor._NEW_USENET_GRACE / 3) + 20)
    h.status = None
    h.poll(times=60)            # 180 s of build gap, still inside the window
    assert h.failed == []


def test_a_placeholder_that_never_turns_into_a_job_still_fails(monkeypatch):
    h = _Harness(monkeypatch, _row())
    h.status = _placeholder()
    h.poll(times=5)
    h.status = None
    h.poll(times=int(monitor._NEW_USENET_GRACE / 3) + 1)
    assert h.failed == []
    h.poll(times=monitor._GIVE_UP_AFTER)
    assert len(h.failed) == 1


def test_a_placeholder_after_a_real_report_does_not_reopen_the_window(monkeypatch):
    h = _Harness(monkeypatch, _row())
    h.status = _status()
    h.poll()
    h.status = _placeholder()
    h.poll()
    h.status = None
    h.poll(times=monitor._GIVE_UP_AFTER)
    assert len(h.failed) == 1
