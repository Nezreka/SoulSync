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
        # the one finding a stopped run may file is the question (#2005)
        create_finding=lambda **k: k["finding_type"] == "library_unreachable" or (
            _ for _ in ()).throw(AssertionError("no dead file finding")),
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
    assert [f["finding_type"] for f in findings] == ["library_unreachable"]


def test_the_guard_stops_before_anything_is_marked_missing(monkeypatch):
    """third #1062 review G11-2: the guard ran after every chunk had already
    been rescanned (and its misses written), then said nothing was marked.
    3.6.1 looked first and changed nothing"""
    before = [_subject(n) for n in range(30)]
    result, findings, _calls = _run(monkeypatch, before,
                                    scan_result={"scanned": 0, "missing": 30})
    assert result.stopped_early
    assert [f["finding_type"] for f in findings] == ["library_unreachable"]
    assert _run.scans == []


# --- #2005: a library emptied on purpose can be cleared ----------------------

def test_a_stopped_run_asks_whether_the_files_are_gone(monkeypatch):
    """someone deleted all their music and the library never cleared: every
    guard read it as a dropped mount and there was nowhere to say otherwise."""
    before = [_subject(n) for n in range(30)]
    result, findings, _calls = _run(monkeypatch, before,
                                    scan_result={"scanned": 0, "missing": 30})
    (asked,) = findings
    assert asked["finding_type"] == "library_unreachable"
    assert asked["title"] == "30 of 30 files can't be reached"
    assert asked["details"]["outage"]["unreachable"] == 30
    assert result.findings_created == 0


def test_the_question_is_one_finding_however_the_counts_move():
    from core.repair_worker import (
        DESTRUCTIVE_FINDING_TYPES, FINDING_TYPE_META, _stable_finding_payload,
    )
    assert _stable_finding_payload({"outage": {"unreachable": 30}}) == \
        _stable_finding_payload({"outage": {"unreachable": 31}})
    # never part of Fix All: it is a person's answer
    assert "library_unreachable" in DESTRUCTIVE_FINDING_TYPES
    assert "library_unreachable" in FINDING_TYPE_META


def _real_library(tmp_path, monkeypatch, count):
    from core.library2.schema import ensure_library_v2_schema
    from tests.lib2_seed import row_conn

    db_path = str(tmp_path / "lib2.db")
    conn = row_conn(db_path)
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(1,'B')")
    root = tmp_path / "music"
    root.mkdir()
    for i in range(count):
        conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(1,?,?)",
                     (f"T{i}", i + 1))
        conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                     (i + 1, str(root / f"{i}.flac")))
    conn.commit()

    class _Shim:
        def _get_connection(self):
            return row_conn(db_path)

    import os
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
                        lambda path, config_manager=None: path if os.path.exists(path) else None)
    return _Shim(), conn, root


def test_confirmed_gone_gets_past_the_outage_guards(tmp_path, monkeypatch):
    from core.library2 import scan

    db, conn, _root = _real_library(tmp_path, monkeypatch, scan.MASS_MISSING_MIN)
    # an emptied library root is exactly what an unmounted share looks like
    held = scan.rescan_files(db, manual=True)
    assert held["mass_missing_deferred"] == scan.MASS_MISSING_MIN
    assert {r[0] for r in conn.execute("SELECT file_state FROM lib2_track_files")} == {"active"}

    said = scan.rescan_files(db, confirmed_gone=True)

    assert said["missing_confirmed"] == scan.MASS_MISSING_MIN
    assert "mass_missing_deferred" not in said
    assert {r[0] for r in conn.execute("SELECT file_state FROM lib2_track_files")} == {
        "missing_confirmed"}
    # and the scans after it do not read the known misses as a new outage
    assert "mass_missing_deferred" not in scan.rescan_files(db)


def test_confirmed_gone_leaves_a_file_that_is_there(tmp_path, monkeypatch):
    from core.library2 import scan

    db, conn, root = _real_library(tmp_path, monkeypatch, 3)
    (root / "1.flac").write_bytes(b"not audio, but present")
    scan.rescan_files(db, confirmed_gone=True)
    states = dict(conn.execute("SELECT track_id, file_state FROM lib2_track_files"))
    assert states[2] == "active"
    assert states[1] == states[3] == "missing_confirmed"


def test_the_answer_lists_the_files_under_dead_files(tmp_path, monkeypatch):
    from core.repair_jobs import dead_file_cleaner as cleaner

    db, conn, root = _real_library(tmp_path, monkeypatch, 60)
    (root / "5.flac").write_bytes(b"present")

    def subjects(_db, _config, **_kw):
        return [{**_subject(row["id"], row["file_state"] or "active"), "path": row["path"],
                 "track_id": row["track_id"]}
                for row in conn.execute("SELECT id, track_id, path, file_state FROM lib2_track_files")]
    monkeypatch.setattr("core.library2.maintenance_subjects.active_file_subjects", subjects)
    findings = []
    result = cleaner.confirm_unreachable_gone(
        db, None, lambda **kw: findings.append(kw) or True)

    assert result["success"] is True and result["confirmed"] == 59
    assert "59 files marked missing" in result["message"]
    assert len(findings) == 59 and {f["finding_type"] for f in findings} == {"dead_file"}
    assert "lib2:6" not in {f["entity_id"] for f in findings}

    # asked again with nothing left to clear: says so, changes nothing
    again = cleaner.confirm_unreachable_gone(db, None, lambda **kw: findings.append(kw) or True)
    assert again["success"] is False and len(findings) == 59
