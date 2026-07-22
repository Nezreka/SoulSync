"""Native missing-lifecycle safety tests for Dead File Cleaner (P3)."""

from __future__ import annotations

from types import SimpleNamespace

from core.repair_jobs.base import JobContext
from core.repair_jobs.dead_file_cleaner import DeadFileCleanerJob


def _subject(file_id: int, state: str = "active"):
    return {
        "file_id": file_id,
        "track_id": file_id,
        "album_id": 10,
        "artist_id": 1,
        "title": f"Track {file_id}",
        "artist_name": "Yellowcard",
        "album_title": "Ocean Avenue",
        "path": f"/music/{file_id}.flac",
        "file_state": state,
        "track_source_ids": {"deezer": f"dz-{file_id}"},
        "album_source_ids": {},
        "artist_source_ids": {},
    }


def _run(monkeypatch, before, after=None, *, scan_result=None):
    calls = []

    def active_subjects(_db, _config, **kwargs):
        calls.append(kwargs)
        return list(before if len(calls) == 1 else (after or before))

    monkeypatch.setattr(
        "core.library2.maintenance_subjects.active_file_subjects",
        active_subjects,
    )
    monkeypatch.setattr(
        "core.library2.scan.rescan_files",
        lambda _db, **kwargs: scan_result or {"scanned": len(before)},
    )
    findings = []
    context = JobContext(
        db=SimpleNamespace(),
        transfer_folder="/music",
        config_manager=SimpleNamespace(get=lambda key, default=None: True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )
    result = DeadFileCleanerJob().scan(context)
    return result, findings, calls


def test_empty_native_catalogue_is_a_noop(monkeypatch):
    result, findings, calls = _run(monkeypatch, [])

    assert result.scanned == 0
    assert findings == []
    assert calls == [{"include_missing": True}]


def test_first_healthy_miss_does_not_create_destructive_finding(monkeypatch):
    before = [_subject(1)]
    after = [_subject(1, "missing_suspected")]

    result, findings, _calls = _run(monkeypatch, before, after)

    assert result.scanned == 1
    assert result.findings_created == 0
    assert findings == []


def test_second_healthy_miss_creates_native_finding(monkeypatch):
    before = [_subject(7, "missing_suspected")]
    after = [_subject(7, "missing_confirmed")]

    result, findings, _calls = _run(monkeypatch, before, after)

    assert result.findings_created == 1
    assert findings[0]["entity_id"] == "lib2:7"
    assert findings[0]["details"]["library_v2"]["file_ids"] == [7]
    assert findings[0]["details"]["provider_ids"]["track"] == {
        "deezer": "dz-7"
    }


def test_active_files_never_surface_as_dead(monkeypatch):
    subjects = [_subject(index) for index in range(30)]

    result, findings, _calls = _run(monkeypatch, subjects, subjects)

    assert result.scanned == 30
    assert result.errors == 0
    assert findings == []


def test_native_rescan_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "core.library2.maintenance_subjects.active_file_subjects",
        lambda *_args, **_kwargs: [_subject(1)],
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("storage health unavailable")

    monkeypatch.setattr("core.library2.scan.rescan_files", fail)
    context = JobContext(
        db=SimpleNamespace(),
        transfer_folder="/music",
        config_manager=SimpleNamespace(get=lambda key, default=None: True),
        create_finding=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not create a finding")
        ),
    )

    result = DeadFileCleanerJob().scan(context)

    assert result.errors == 1
    assert result.findings_created == 0
