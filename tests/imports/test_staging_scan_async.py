"""Async/background staging scan (#947): a whole-library migration makes the synchronous
scan exceed gunicorn's 120s timeout. The runner moves the SAME scan off the request thread
with progress + a generation guard. Metadata reads are injected so no real audio is needed."""

import os
import time
import types

import pytest

import core.imports.routes as routes


def _meta(_full, _rel):
    return {"title": "t", "album": "Alb", "artist": "Art", "albumartist": "Art",
            "track_number": None, "disc_number": None}


def _runtime(read=_meta):
    return types.SimpleNamespace(read_staging_file_metadata=read)


def _staging(tmp_path, n=3, subdir="Artist/Album"):
    d = tmp_path / "staging"
    for part in subdir.split("/"):
        d = d / part
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"{i:02d}.flac").write_text("x")
    return str(tmp_path / "staging")


@pytest.fixture(autouse=True)
def _reset():
    routes.invalidate_staging_scan_cache()
    routes._staging_scan_status.update({"status": "idle", "scanned": 0, "total": 0,
                                        "path": None, "error": None})
    yield
    routes.invalidate_staging_scan_cache()


def _await_done(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and routes._staging_scan_status["status"] == "scanning":
        time.sleep(0.03)


def test_scan_reports_progress(tmp_path):
    sp = _staging(tmp_path, 3)
    prog = {}
    recs = routes._scan_staging_records(_runtime(), sp, progress=prog)
    assert len(recs) == 3
    assert prog["total"] == 3 and prog["scanned"] == 3


def test_default_scan_behaviour_unchanged(tmp_path):
    sp = _staging(tmp_path, 2)
    assert len(routes._scan_staging_records(_runtime(), sp)) == 2   # no progress arg = as before


def test_accessor_ready_for_small_folder(tmp_path):
    sp = _staging(tmp_path, 2)
    state, val = routes.get_staging_records_or_status(_runtime(), sp, grace_seconds=3.0)
    assert state == "ready" and len(val) == 2


def test_accessor_scanning_when_scan_exceeds_grace(tmp_path):
    sp = _staging(tmp_path, 2)

    def slow(_f, _r):
        time.sleep(0.4)
        return _meta(_f, _r)

    state, val = routes.get_staging_records_or_status(_runtime(slow), sp, grace_seconds=0.1)
    assert state == "scanning"
    assert val["status"] == "scanning" and val["total"] in (0, 2)
    _await_done()


def test_ensure_scan_is_idempotent(tmp_path):
    sp = _staging(tmp_path, 2)
    calls = {"n": 0}

    def counting(_f, _r):
        calls["n"] += 1
        time.sleep(0.15)
        return _meta(_f, _r)

    rt = _runtime(counting)
    routes.ensure_background_staging_scan(rt, sp)
    routes.ensure_background_staging_scan(rt, sp)   # must NOT start a second scan
    _await_done()
    assert calls["n"] == 2                          # 2 files read once, not 4


def test_generation_guard_discards_stale_records(tmp_path):
    sp = _staging(tmp_path, 2)

    def read_then_import(_f, _r):
        routes.invalidate_staging_scan_cache()      # simulate an import landing mid-scan
        return _meta(_f, _r)

    recs = routes._scan_staging_records(_runtime(read_then_import), sp)
    assert len(recs) == 2                            # caller still gets its records
    assert routes._staging_scan_cache["records"] is None   # but stale set NOT committed to cache


def _full_runtime(staging_path, read=_meta):
    return types.SimpleNamespace(
        get_staging_path=lambda: staging_path,
        read_staging_file_metadata=read,
        logger=types.SimpleNamespace(error=lambda *a, **k: None),
    )


def test_helper_passes_records_through_when_ready(monkeypatch):
    monkeypatch.setattr(routes, 'get_staging_records_or_status',
                        lambda rt, sp: ("ready", [{"x": 1}]))
    records, scanning = routes._records_or_scanning_payload(_runtime(), "/x")
    assert scanning is None and records == [{"x": 1}]


def test_helper_builds_scanning_payload(monkeypatch):
    monkeypatch.setattr(routes, 'get_staging_records_or_status',
                        lambda rt, sp: ("scanning", {"scanned": 5, "total": 20, "status": "scanning"}))
    records, scanning = routes._records_or_scanning_payload(_runtime(), "/x")
    assert records is None
    assert scanning == {"success": True, "scanning": True,
                        "progress": {"scanned": 5, "total": 20}}


def test_staging_files_endpoint_ready(tmp_path):
    payload, status = routes.staging_files(_full_runtime(_staging(tmp_path, 2)))
    assert status == 200 and payload["success"] and len(payload["files"]) == 2


def test_staging_files_endpoint_returns_scanning(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, 'get_staging_records_or_status',
                        lambda r, p: ("scanning", {"scanned": 3, "total": 10}))
    payload, status = routes.staging_files(_full_runtime(_staging(tmp_path, 2)))
    assert status == 200 and payload.get("scanning") is True
    assert payload["progress"] == {"scanned": 3, "total": 10}


def test_staging_groups_endpoint_returns_scanning(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, 'get_staging_records_or_status',
                        lambda r, p: ("scanning", {"scanned": 1, "total": 9}))
    payload, status = routes.staging_groups(_full_runtime(_staging(tmp_path, 2)))
    assert payload.get("scanning") is True


def test_scan_status_ready_after_warm(tmp_path):
    sp = _staging(tmp_path, 2)
    routes._scan_staging_records(_runtime(), sp)            # warm the cache
    payload, status = routes.staging_scan_status(_full_runtime(sp))
    assert status == 200 and payload["success"] and payload["ready"] is True


def test_scan_status_not_ready_when_cold(tmp_path):
    sp = _staging(tmp_path, 2)                              # cold (autouse reset)
    payload, _ = routes.staging_scan_status(_full_runtime(sp))
    assert payload["ready"] is False
    assert "scanned" in payload and "total" in payload
