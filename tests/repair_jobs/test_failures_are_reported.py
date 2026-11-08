"""third #1062 review: failures that went silent, and a bar that never moved.

caplog can't see soulsync's loggers, so each module's logger is swapped
for a recorder."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.repair_jobs.base import JobContext


def _ctx(**kw):
    return JobContext(db=SimpleNamespace(), transfer_folder="", config_manager=None, **kw)


def test_edition_review_says_which_album_failed_and_why(monkeypatch):
    """G11-7: every album turned into a silent error count"""
    from core.repair_jobs import album_edition_review as job
    log = MagicMock()
    monkeypatch.setattr(job, "logger", log)
    monkeypatch.setattr(job.AlbumEditionReviewJob, "_subjects",
                        lambda self, ctx: [{"album_id": 7, "path": "/m/a.flac"}])

    def _boom(*_a, **_k):
        raise NameError("schema drift")

    monkeypatch.setattr("core.library2.edition_review.propose_album_edition", _boom)
    result = job.AlbumEditionReviewJob().scan(_ctx())
    assert result.errors == 1
    assert log.warning.call_args[0][1] == 7


def test_tag_consistency_moves_its_bar(monkeypatch):
    """G11-8: update_progress(0, n) once and never again"""
    from core.repair_jobs import album_tag_consistency as job
    from core.repair_jobs.base import JobResult
    subjects = [{"album_id": n, "album_title": f"A{n}", "artist_name": "X", "album_artist_name": "X",
                 "track_id": n * 10 + i, "path": f"/m/{n}/{i}.flac"}
                for n in (1, 2, 3) for i in (1, 2)]
    monkeypatch.setattr(job, "_read_subject_tags", lambda subs, ctx: [])
    progress = []
    ctx = _ctx(update_progress=lambda done, total: progress.append((done, total)))
    job.AlbumTagConsistencyJob()._scan_native_albums(ctx, JobResult(), subjects, True, True, True)
    assert progress[-1] == (3, 3)


def test_a_deferred_start_that_fails_is_an_error(monkeypatch):
    """G17-2: on the first boot after an upgrade a failed automation start
    was logged at DEBUG, so nothing at INFO said why automations never ran"""
    from core.library2 import migration_gate
    log = MagicMock()
    monkeypatch.setattr(migration_gate, "logger", log)
    monkeypatch.setattr(migration_gate, "WORKER_STAGGER_SECONDS", 0)

    def _boom(_item):
        raise OSError("read-only file system: /app/database")

    migration_gate._staggered(["engine"], _boom, str)
    assert log.error.called and not log.debug.called


def test_a_broken_wishlist_materialize_is_a_warning(monkeypatch):
    """P2B-10: it made every album-modal add return no album id, at DEBUG"""
    from core.library2 import materialize
    log = MagicMock()
    monkeypatch.setattr(materialize, "logger", log)

    def _boom(*_a, **_k):
        raise RuntimeError("no such column")

    monkeypatch.setattr("database.music_database.get_database", _boom)
    assert materialize.materialize_wishlist_intent({"id": "sp"}, actor_profile_id=1) is None
    assert log.warning.called
