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
    routes._scanning_history_cache.update({"path": None, "ts": 0.0,
                                            "rows": None, "problems": None})
    yield
    routes.invalidate_staging_scan_cache()
    routes._scanning_history_cache.update({"path": None, "ts": 0.0,
                                            "rows": None, "problems": None})


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
    # #1671: the stub names the folder being read — without it the page
    # rendered "Import folder not configured" for a configured folder.
    assert scanning == {"success": True, "scanning": True, "staging_path": "/x",
                        "progress": {"scanned": 5, "total": 20}}


class _InboxWorker:
    """The least worker the inbox reads: status + history rows."""

    def get_status(self):
        return {"running": True, "paused": False, "current_status": "processing",
                "last_scan_time": None, "stats": {"scanned": 3}, "active_imports": []}

    def get_results(self, limit=200):
        return [{"id": 7, "folder_hash": "h1", "folder_name": "Done Album",
                 "folder_path": "/staging/Done Album", "album_name": "Done Album",
                 "artist_name": "Artist", "status": "completed", "total_files": 2,
                 "confidence": 0.9, "image_url": None, "album_id": None,
                 "identification_method": None, "error_message": None,
                 "match_data": None, "created_at": "2026-10-01T00:00:00",
                 "processed_at": "2026-10-01T01:00:00"}]


def _force_scanning(monkeypatch):
    monkeypatch.setattr(routes, 'get_staging_records_or_status',
                        lambda rt, sp: ("scanning", {"scanned": 181, "total": 252,
                                                     "status": "scanning"}))


def test_inbox_while_scanning_keeps_path_worker_and_history(tmp_path, monkeypatch):
    """#1671: a large batch mid-scan blanked the inbox to zeroes and claimed
    the folder was not configured. The scanning reply must still carry the
    path, the worker block (the auto-import switch) and the history rows —
    none of which depend on the staging scan."""
    _force_scanning(monkeypatch)
    sp = _staging(tmp_path, 2)
    payload, status = routes.inbox(_full_runtime(sp), _InboxWorker())
    assert status == 200 and payload["scanning"] is True
    assert payload["staging_path"] == sp
    assert payload["progress"] == {"scanned": 181, "total": 252}
    assert payload["worker"]["available"] is True
    assert payload["worker"]["running"] is True
    assert payload["worker"]["current_status"] == "processing"
    # History is served from the DB while the scan runs…
    assert [row["name"] for row in payload["items"]] == ["Done Album"]
    assert payload["items"][0]["status"] == "imported"
    assert payload["items"][0]["in_staging"] is False
    # …but staged counts are NOT fabricated: no summary until the scan lands.
    assert "summary" not in payload


def _history_row(row_id, name, status, total_files=2):
    return {"id": row_id, "folder_hash": f"h{row_id}", "folder_name": name,
            "folder_path": f"/staging/{name}", "album_name": name,
            "artist_name": "Artist", "status": status, "total_files": total_files,
            "confidence": 0.9, "image_url": None, "album_id": None,
            "identification_method": None, "error_message": None,
            "match_data": None, "created_at": "2026-10-01T00:00:00",
            "processed_at": "2026-10-01T01:00:00"}


class _MixedWorker(_InboxWorker):
    """History with every flavour of unfinished row, plus worker scan problems."""

    def get_status(self):
        status = super().get_status()
        status["scan_problems"] = [{"path": "/staging/Locked", "error": "Permission denied"}]
        return status

    def get_results(self, limit=200):
        return [
            _history_row(11, "Needs Review Album", "pending_review"),
            _history_row(12, "Needs Id Album", "needs_identification"),
            _history_row(13, "In Flight Album", "processing"),
            _history_row(14, "Failed Album", "failed"),
            _history_row(15, "Done Album", "completed"),
            _history_row(16, "Dismissed Album", "rejected"),
        ]


def test_inbox_while_scanning_keeps_unfinished_rows(tmp_path, monkeypatch):
    """Review round 1: build_inbox drops file-less unfinished rows, so a cold
    open mid-batch showed Needs attention 0 while the DB already knew about
    needs-review items. Unfinished rows must come back as staged rows with
    their real status; terminal rows stay history."""
    _force_scanning(monkeypatch)
    payload, status = routes.inbox(_full_runtime(_staging(tmp_path, 2)), _MixedWorker())
    assert status == 200
    by_name = {row["name"]: row for row in payload["items"]}
    assert by_name["Needs Review Album"]["status"] == "needs_review"
    assert by_name["Needs Review Album"]["in_staging"] is True
    assert by_name["Needs Id Album"]["status"] == "needs_identify"
    assert by_name["Needs Id Album"]["in_staging"] is True
    assert by_name["In Flight Album"]["status"] == "importing"
    assert by_name["Failed Album"]["status"] == "failed"
    # a failed row built as staged keeps its Retry action during the scan
    assert by_name["Failed Album"]["in_staging"] is True
    assert by_name["Done Album"]["status"] == "imported"
    assert by_name["Done Album"]["in_staging"] is False
    assert by_name["Dismissed Album"]["status"] == "dismissed"
    assert by_name["Dismissed Album"]["in_staging"] is False
    # the worker's own scan problems are not suppressed for the scan's duration
    assert {"path": "/staging/Locked", "error": "Permission denied"} in payload["problems"]


def test_inbox_while_scanning_one_row_per_folder(tmp_path, monkeypatch):
    """Review round 2: history is INSERT-per-attempt and the folder hash is
    content-derived, so one folder can carry a failed row under an old hash
    and a needs-review row under a new one, same path. The settled inbox
    shows ONE staged row (newest, claimed by path); the scanning payload
    must not show the folder twice — and must not let the dead row swallow
    the live one when the hash stayed the same either."""
    _force_scanning(monkeypatch)

    class _Retried(_InboxWorker):
        def get_results(self, limit=200):
            return [
                _history_row(21, "Same Album", "pending_review"),
                _history_row(20, "Same Album", "failed"),
                {**_history_row(31, "Same Hash Album", "pending_review"),
                 "folder_hash": "h-same"},
                {**_history_row(30, "Same Hash Album", "failed"),
                 "folder_hash": "h-same"},
            ]

    payload, status = routes.inbox(_full_runtime(_staging(tmp_path, 2)), _Retried())
    assert status == 200
    by_name = {}
    for row in payload["items"]:
        assert row["name"] not in by_name, f"folder rendered twice: {row['name']}"
        by_name[row["name"]] = row
    assert by_name["Same Album"]["status"] == "needs_review"
    assert by_name["Same Album"]["history_id"] == 21
    assert by_name["Same Hash Album"]["status"] == "needs_review"
    assert by_name["Same Hash Album"]["history_id"] == 31


def test_inbox_while_scanning_caches_history_reads(tmp_path, monkeypatch):
    """The page polls every ~1.5s during a scan; the history read is cached
    briefly so a multi-minute scan is not a DB query per poll."""
    _force_scanning(monkeypatch)
    calls = {"n": 0}

    class _Counting(_InboxWorker):
        def get_results(self, limit=200):
            calls["n"] += 1
            return super().get_results(limit=limit)

    rt = _full_runtime(_staging(tmp_path, 2))
    routes.inbox(rt, _Counting())
    routes.inbox(rt, _Counting())
    assert calls["n"] == 1


def test_inbox_while_scanning_keeps_pathless_history_rows(tmp_path, monkeypatch):
    """Review fold: the dedupe identity normpath'd the folder path
    unconditionally, and normpath("") is "." — every history row without a
    folder path shared one identity, so all but the newest were dropped
    even with distinct hashes. Pathless rows stand on their hash alone."""
    _force_scanning(monkeypatch)

    class _Pathless(_InboxWorker):
        def get_results(self, limit=200):
            return [
                {**_history_row(41, "Newer Pathless", "completed"), "folder_path": None},
                {**_history_row(40, "Older Pathless", "completed"), "folder_path": ""},
            ]

    payload, status = routes.inbox(_full_runtime(_staging(tmp_path, 2)), _Pathless())
    assert status == 200
    assert {row["history_id"] for row in payload["items"]} == {40, 41}


def test_inbox_while_scanning_survives_cross_mount_history_paths(tmp_path, monkeypatch):
    """Review fold: on Windows, os.path.relpath raises ValueError when a
    history path sits on another drive/share than the staging root (the
    folder moved mounts). build_inbox must fall back to the full path —
    before the guard, the error emptied the whole scanning payload."""
    _force_scanning(monkeypatch)
    real_relpath = os.path.relpath

    def _cross_mount(path, start):
        if str(path).startswith("/staging/"):
            raise ValueError("path is on mount 'E:', start on mount 'C:'")
        return real_relpath(path, start)

    monkeypatch.setattr(os.path, "relpath", _cross_mount)
    payload, status = routes.inbox(_full_runtime(_staging(tmp_path, 2)), _MixedWorker())
    assert status == 200
    by_name = {row["name"]: row for row in payload["items"]}
    assert by_name["Needs Review Album"]["in_staging"] is True
    assert by_name["Needs Review Album"]["rel_path"] == "/staging/Needs Review Album"
    assert by_name["Failed Album"]["status"] == "failed"


def test_scan_cache_invalidation_clears_history_cache(tmp_path, monkeypatch):
    """Review fold: invalidate_staging_scan_cache() is what actions call
    after moving files; it must drop the scanning history cache too, not
    leave stale rows served for the rest of the TTL."""
    _force_scanning(monkeypatch)
    state = {"rows": [_history_row(51, "Before Move", "completed")]}

    class _Shifting(_InboxWorker):
        def get_results(self, limit=200):
            return state["rows"]

    rt = _full_runtime(_staging(tmp_path, 2))
    payload, _ = routes.inbox(rt, _Shifting())
    assert [row["name"] for row in payload["items"]] == ["Before Move"]
    state["rows"] = [_history_row(52, "After Move", "completed")]
    payload, _ = routes.inbox(rt, _Shifting())
    assert [row["name"] for row in payload["items"]] == ["Before Move"]  # cache warm
    routes.invalidate_staging_scan_cache()
    payload, _ = routes.inbox(rt, _Shifting())
    assert [row["name"] for row in payload["items"]] == ["After Move"]


def test_inbox_while_scanning_shows_live_progress(tmp_path, monkeypatch):
    """Review fold: a processing row built from history during a scan must
    carry the worker's live track progress, like the settled inbox does."""
    _force_scanning(monkeypatch)

    class _Live(_MixedWorker):
        def get_status(self):
            status = super().get_status()
            status["active_imports"] = [{
                "folder_hash": "h13", "folder_name": "In Flight Album",
                "status": "processing", "track_index": 3, "track_total": 9,
                "track_name": "Song Three",
            }]
            return status

    payload, status = routes.inbox(_full_runtime(_staging(tmp_path, 2)), _Live())
    assert status == 200
    by_name = {row["name"]: row for row in payload["items"]}
    assert by_name["In Flight Album"]["live"] == {
        "track_index": 3, "track_total": 9, "track_name": "Song Three"}


def test_inbox_while_scanning_without_worker(tmp_path, monkeypatch):
    _force_scanning(monkeypatch)
    payload, status = routes.inbox(_full_runtime(_staging(tmp_path, 2)), None)
    assert status == 200 and payload["scanning"] is True
    assert payload["staging_path"]
    assert payload["items"] == []
    assert payload["worker"]["available"] is False


def test_inbox_while_scanning_survives_broken_worker(tmp_path, monkeypatch):
    """Enrichment is a nicety: a worker whose status/history reads raise must
    not turn the scanning poll into a 500."""
    _force_scanning(monkeypatch)

    class _Broken:
        def get_status(self):
            raise RuntimeError("db gone")

        def get_results(self, limit=200):
            raise RuntimeError("db gone")

    payload, status = routes.inbox(_full_runtime(_staging(tmp_path, 2)), _Broken())
    assert status == 200 and payload["scanning"] is True
    assert payload["items"] == []
    assert payload["worker"]["available"] is True


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
