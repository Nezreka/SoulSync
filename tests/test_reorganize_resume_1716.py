"""#1716 (QT3496) on Library v2: library reorganize started over every run.

Stopping a scan at album 300 (or a restart) meant the next run re-planned
albums 1-300 first. The native job saves the last album it finished (every 25
and on a stop), resumes right after it, and goes back to the top after a full
pass. A different dry-run mode or naming starts over; a run scoped to one
artist never touches the checkpoint. Live mode queues each album as it goes,
so a stop cannot drop planned moves.
"""

from __future__ import annotations

from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reorganize import LibraryReorganizeJob

KEY = "repair.jobs.library_reorganize.scan_checkpoint"


class _Config:
    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value


def _reorganize(monkeypatch, config, albums, *, stop_after=None, scope=None, crash_at=None):
    planned = []

    def preview(_db, _cm, album_id, mode="api"):
        if crash_at is not None and len(planned) == crash_at:
            raise KeyboardInterrupt  # the process dies mid-scan
        planned.append(album_id)
        return {"status": "planned", "tracks": []}

    monkeypatch.setattr("core.library2.reorganize_bridge.preview_album_reorganize", preview)
    monkeypatch.setattr(LibraryReorganizeJob, "_albums",
                        staticmethod(lambda _ctx: [{"id": a, "title": "", "artist_name": ""}
                                                   for a in albums]))
    ctx = JobContext(
        db=None, transfer_folder="/music", config_manager=config,
        should_stop=lambda: stop_after is not None and len(planned) >= stop_after,
    )
    if scope is not None:
        ctx.scope = scope
    try:
        LibraryReorganizeJob().scan(ctx)
    except KeyboardInterrupt:
        pass
    return planned


def test_reorganize_resumes_where_a_stopped_scan_left_off(monkeypatch):
    config = _Config()
    assert _reorganize(monkeypatch, config, [10, 11, 12], stop_after=1) == [10]
    assert _reorganize(monkeypatch, config, [10, 11, 12]) == [11, 12]
    assert _reorganize(monkeypatch, config, [10, 11, 12]) == [10, 11, 12]


def test_a_crash_mid_scan_resumes_from_the_last_batch(monkeypatch):
    config = _Config()
    albums = list(range(1, 61))
    _reorganize(monkeypatch, config, albums, crash_at=40)
    # saved every 25 albums: album 25 was the last one written down
    assert config.store[KEY]["id"] == 25
    assert _reorganize(monkeypatch, config, albums)[0] == 26


def test_reorganize_starts_over_when_dry_run_changes(monkeypatch):
    config = _Config()
    _reorganize(monkeypatch, config, [10, 11, 12], stop_after=1)
    config.store["repair.jobs.library_reorganize.settings"] = {"dry_run": False}
    assert _reorganize(monkeypatch, config, [10, 11, 12]) == [10, 11, 12]


def test_naming_change_starts_over(monkeypatch):
    import core.library_reorganize as lr

    config = _Config()
    monkeypatch.setattr(lr, "naming_fingerprint", lambda: "before")
    _reorganize(monkeypatch, config, [10, 11, 12], stop_after=1)
    monkeypatch.setattr(lr, "naming_fingerprint", lambda: "after")
    assert _reorganize(monkeypatch, config, [10, 11, 12]) == [10, 11, 12]


def test_checkpoint_album_gone_starts_over(monkeypatch):
    config = _Config()
    _reorganize(monkeypatch, config, [10, 11, 12], stop_after=1)
    assert _reorganize(monkeypatch, config, [11, 12]) == [11, 12]


def test_a_gone_checkpoint_album_resumes_at_the_next_one(monkeypatch):
    """upstream #1857: the album a stop saved was merged away meanwhile. The
    pass carries on after it instead of re-planning the albums before it."""
    config = _Config()
    assert _reorganize(monkeypatch, config, [10, 11, 12, 13], stop_after=2) == [10, 11]
    assert _reorganize(monkeypatch, config, [10, 12, 13]) == [12, 13]


def test_a_scoped_reorganize_run_never_touches_the_checkpoint(monkeypatch):
    config = _Config()
    _reorganize(monkeypatch, config, [10, 11, 12], stop_after=1)
    saved = config.store[KEY]
    assert _reorganize(monkeypatch, config, [10, 11, 12],
                       scope={"file_paths": ["/music/a.flac"]}) == [10, 11, 12]
    assert config.store[KEY] == saved
