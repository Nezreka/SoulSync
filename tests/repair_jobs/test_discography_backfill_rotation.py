"""#1709 on Library v2: a stopped library-wide pass resumes.

Upstream's Discography Backfill rescanned the same first artists every run.
Here the retired backfill's job is Monitored Discography Refresh, which walks
every monitored artist each run, so the per-run window does not apply; the
resume does. It shares ``RepairJob.resume_index`` / ``save_checkpoint`` with
Library Reorganize (tests/test_reorganize_resume_1716.py): a stopped run
resumes right after the last item it finished, a finished pass clears the
checkpoint, and a vanished or foreign checkpoint starts at the top.
"""

from __future__ import annotations

import logging

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.lib2_discography_refresh import Lib2DiscographyRefreshJob

KEY = "repair.jobs.monitored_discography_refresh.scan_checkpoint"


class _Config:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value


class _BrokenConfig(_Config):
    def set(self, key, value):
        raise OSError("disk full")


def _context(config, *, stop_after=None, seen=None):
    seen = seen if seen is not None else []
    return JobContext(
        db=None, transfer_folder="/music", config_manager=config,
        should_stop=lambda: stop_after is not None and len(seen) >= stop_after,
    )


def _run(monkeypatch, config, artists, *, stop_after=None, fail_on=()):
    seen = []

    def refresh(_db, artist_id, *_a, **_kw):
        seen.append(artist_id)
        if artist_id in fail_on:
            raise RuntimeError("provider down")
        return {"auto_monitor_album_ids": []}, 0

    monkeypatch.setattr("core.library2.discography.refresh_artist_discography", refresh)
    job = Lib2DiscographyRefreshJob()
    monkeypatch.setattr(job, "_artist_ids", lambda _conn: list(artists))

    class _Conn:
        def close(self):
            pass

    ctx = _context(config, stop_after=stop_after, seen=seen)
    ctx.db = type("DB", (), {"_get_connection": lambda self: _Conn()})()
    job.scan(ctx)
    return seen


def test_a_finished_pass_scans_everyone_and_clears_the_checkpoint(monkeypatch):
    config = _Config()
    assert _run(monkeypatch, config, [1, 2, 3]) == [1, 2, 3]
    assert config.store[KEY] is None


def test_stopped_run_resumes_after_the_last_finished_artist(monkeypatch):
    config = _Config()
    assert _run(monkeypatch, config, [1, 2, 3, 4, 5], stop_after=2) == [1, 2]
    assert config.store[KEY] == {"id": 2, "scan": ""}
    assert _run(monkeypatch, config, [1, 2, 3, 4, 5]) == [3, 4, 5]
    assert config.store[KEY] is None
    assert _run(monkeypatch, config, [1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]


def test_errored_artist_still_advances(monkeypatch):
    config = _Config()
    _run(monkeypatch, config, [1, 2, 3], stop_after=2, fail_on={1})
    assert _run(monkeypatch, config, [1, 2, 3]) == [3]


def test_checkpoint_artist_gone_starts_at_the_top(monkeypatch):
    config = _Config()
    config.store[KEY] = {"id": 99, "scan": ""}
    assert _run(monkeypatch, config, [1, 2, 3]) == [1, 2, 3]


def test_checkpoint_on_the_last_artist_wraps_to_the_top(monkeypatch):
    config = _Config()
    config.store[KEY] = {"id": 3, "scan": ""}
    assert _run(monkeypatch, config, [1, 2, 3]) == [1, 2, 3]


@pytest.mark.parametrize("saved", ["2", 2, ["x"], {"scan": ""}, {"id": 2, "scan": "other"}])
def test_corrupt_or_foreign_checkpoints_start_at_the_top(monkeypatch, saved):
    config = _Config()
    config.store[KEY] = saved
    assert _run(monkeypatch, config, [1, 2, 3]) == [1, 2, 3]


def test_checkpoint_save_failure_warns_but_never_fails_the_scan(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        assert _run(monkeypatch, _BrokenConfig(), [1, 2, 3], stop_after=2) == [1, 2]
    assert "could not save checkpoint" in caplog.text


def test_no_config_manager_starts_at_the_top(monkeypatch):
    job = Lib2DiscographyRefreshJob()
    ctx = JobContext(db=None, transfer_folder="/music", config_manager=None)
    assert job.resume_index(ctx, [1, 2, 3]) == 0
    job.save_checkpoint(ctx, 2)  # no store: nothing to do, nothing raised
