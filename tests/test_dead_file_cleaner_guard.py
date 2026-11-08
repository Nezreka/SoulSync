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
    scans = []
    monkeypatch.setattr(
        "core.library2.scan.rescan_files",
        lambda _db, **kwargs: scans.append(kwargs) or scan_result or {"scanned": len(before)},
    )
    # the read-only look before any write sees what the scan would report
    monkeypatch.setattr(
        "core.repair_jobs.dead_file_cleaner._count_unreachable",
        lambda _context, subjects: int((scan_result or {}).get("missing") or 0),
    )
    findings = []
    context = JobContext(
        db=SimpleNamespace(),
        transfer_folder="/music",
        config_manager=SimpleNamespace(get=lambda key, default=None: True),
        create_finding=lambda **kwargs: findings.append(kwargs) or True,
    )
    result = DeadFileCleanerJob().scan(context)
    _run.scans = scans
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


# --- 3.6.1: a library out of reach says why, not "0 findings" --------------------


def _unreachable(monkeypatch, *, total, missing, deferred=0, server=None):
    before = [_subject(i) for i in range(total)]
    monkeypatch.setattr("core.library2.maintenance_subjects.active_file_subjects",
                        lambda *_a, **_k: list(before))
    gone = {i for i in range(missing)}  # the first ``missing`` files are out of reach

    def rescan(_db, **kw):
        chunk = kw["file_ids"]
        lost = sum(1 for f in chunk if f in gone)
        return {"scanned": len(chunk) - lost, "missing": lost,
                "mass_missing_deferred": lost if deferred else 0}
    monkeypatch.setattr("core.library2.scan.rescan_files", rescan)
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda *_a, **_k: None)
    monkeypatch.setattr("core.repair_jobs.dead_file_cleaner._count_unreachable",
                        lambda _context, _subjects: missing)
    progress = []
    context = JobContext(
        db=SimpleNamespace(), transfer_folder="/music",
        config_manager=SimpleNamespace(get=lambda key, default=None: default,
                                       get_active_media_server=lambda: server),
        create_finding=lambda **_k: (_ for _ in ()).throw(AssertionError("no finding")),
        report_progress=lambda **kw: progress.append(kw),
    )
    return DeadFileCleanerJob().scan(context), progress


def test_a_mass_unreachable_library_says_it_is_a_mapping_problem(monkeypatch):
    """#828 / 3.6.1: every path unreachable is a mount or mapping problem;
    the run says so, with an example path and the navidrome advice"""
    result, progress = _unreachable(monkeypatch, total=600, missing=600, deferred=600,
                                    server="navidrome")
    assert result.findings_created == 0
    assert "600 of 600 files" in result.stopped_early
    assert "/music/0.flac" in result.stopped_early
    assert "Report Real Path" in result.stopped_early
    assert any(p.get("log_type") == "error" and "path-mapping" in p.get("log_line", "")
               for p in progress)


def test_half_a_small_library_unreachable_points_at_music_paths(monkeypatch):
    result, _ = _unreachable(monkeypatch, total=30, missing=15, server="plex")
    assert "15 of 30 files" in result.stopped_early
    assert "Music Paths" in result.stopped_early


def test_a_few_missing_files_are_just_missing_files(monkeypatch):
    result, progress = _unreachable(monkeypatch, total=30, missing=2)
    assert result.stopped_early == ""
    assert not any(p.get("log_type") == "error" for p in progress)


def test_the_saved_guard_settings_still_decide(monkeypatch):
    """found in the second #1062 review: 3.6.1 read max_unresolved_fraction
    and min_tracks_for_guard; v2 hardcoded 0.5 / 25 while the Tools page still
    showed the saved values"""
    before = [_subject(i) for i in range(30)]
    monkeypatch.setattr("core.library2.maintenance_subjects.active_file_subjects",
                        lambda *_a, **_k: list(before))
    monkeypatch.setattr("core.library2.scan.rescan_files", lambda _db, **kw: {
        "scanned": len(kw["file_ids"]) - 18, "missing": 18})
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda *_a, **_k: None)
    monkeypatch.setattr("core.repair_jobs.dead_file_cleaner._count_unreachable",
                        lambda _context, _subjects: 18)

    def run(settings):
        context = JobContext(
            db=SimpleNamespace(), transfer_folder="/music",
            config_manager=SimpleNamespace(
                get=lambda key, default=None: settings if key.endswith(".settings") else default,
                get_active_media_server=lambda: None),
            create_finding=lambda **_k: True)
        return DeadFileCleanerJob().scan(context).stopped_early

    assert run({}) != ""                                        # 18 of 30 >= 0.5
    assert run({"max_unresolved_fraction": 0.9}) == ""          # the user's 0.9
    assert run({"min_tracks_for_guard": 100}) == ""             # too small a library
    assert run({"max_unresolved_fraction": "junk"}) != ""       # garbage: default


def test_a_run_that_trips_the_guard_files_nothing(monkeypatch):
    """a file confirmed missing in an earlier healthy run waited, in 3.6.1,
    for a run that could reach the library. v2 filed it while the guard said
    the library was out of reach."""
    total, lost = 30, 20
    before = [_subject(i) for i in range(total)]
    after = [_subject(i, "missing_confirmed" if i == 25 else "active") for i in range(total)]
    calls = []
    monkeypatch.setattr("core.library2.maintenance_subjects.active_file_subjects",
                        lambda *_a, **_k: calls.append(1) or list(before if len(calls) == 1 else after))
    monkeypatch.setattr("core.library2.scan.rescan_files", lambda _db, **kw: {
        "scanned": len(kw["file_ids"]) - lost, "missing": lost})
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda *_a, **_k: None)
    findings = []
    context = JobContext(
        db=SimpleNamespace(), transfer_folder="/music",
        config_manager=SimpleNamespace(get=lambda key, default=None: default,
                                       get_active_media_server=lambda: None),
        create_finding=lambda **kw: findings.append(kw) or True)

    result = DeadFileCleanerJob().scan(context)

    assert result.stopped_early
    assert findings == []


def test_the_guard_stops_before_anything_is_marked_missing(monkeypatch):
    """third #1062 review G11-2: the guard ran after every chunk had already
    been rescanned (and its misses written), then said nothing was marked.
    3.6.1 looked first and changed nothing"""
    before = [_subject(n) for n in range(30)]
    result, findings, _calls = _run(monkeypatch, before,
                                    scan_result={"scanned": 0, "missing": 30})
    assert result.stopped_early and findings == []
    assert _run.scans == []
