"""the dead file cleaner can be stopped and shows progress again.

it handed every library file to one rescan_files call: on a big library that
ran to the end with no progress and Stop / Pause did nothing until it was
done. it goes a chunk at a time now.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.repair_jobs import dead_file_cleaner as job_module


def test_stop_between_chunks_ends_the_scan(monkeypatch):
    subjects = [{"file_id": i, "track_id": i, "path": f"/m/{i}.flac"} for i in range(1, 6)]
    import core.library2.maintenance_subjects as subjects_module
    import core.library2.scan as scan_module
    monkeypatch.setattr(subjects_module, "active_file_subjects", lambda *a, **k: list(subjects))
    monkeypatch.setattr(job_module, "scoped_file_subjects", lambda context, rows: rows, raising=False)
    calls = []
    monkeypatch.setattr(scan_module, "rescan_files",
                        lambda db, file_ids: calls.append(list(file_ids)) or {"scanned": len(file_ids)})
    monkeypatch.setattr(job_module, "_CHUNK", 2)
    progress = []
    stops = iter([False, True])
    context = SimpleNamespace(
        db=object(), config_manager=None, scope=None,
        check_stop=lambda: next(stops, True), wait_if_paused=lambda: False,
        update_progress=lambda done, total: progress.append((done, total)),
        report_progress=None, create_finding=None,
        get_job_settings=lambda *_: {}, settings={})
    job = job_module.DeadFileCleanerJob() if hasattr(job_module, "DeadFileCleanerJob") else None
    assert job is not None
    job.scan(context)
    assert calls == [[1, 2]]
    assert progress == [(2, 5)]
