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
                                        "path": None, "error": None, "started_at": None})
    routes._scanning_history_cache.update({"path": None, "ts": 0.0,
                                            "rows": None, "problems": None})
    routes._last_inbox_summary.update({"entry": None, "skips": 0})
    routes._inbox_build_counter.update({"value": 0})
    routes._inbox_remember_skips.update({"value": 0})
    yield
    routes.invalidate_staging_scan_cache()
    routes._staging_scan_status.update({"status": "idle", "scanned": 0, "total": 0,
                                        "path": None, "error": None, "started_at": None})
    routes._scanning_history_cache.update({"path": None, "ts": 0.0,
                                            "rows": None, "problems": None})
    routes._last_inbox_summary.update({"entry": None, "skips": 0})
    routes._inbox_build_counter.update({"value": 0})
    routes._inbox_remember_skips.update({"value": 0})


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


class _EnumeratingWorker(_InboxWorker):
    """A worker that can also enumerate candidates, for complete builds."""

    def enumerate_candidates(self, staging_path):
        from core.auto_import_worker import AutoImportWorker
        bare = AutoImportWorker.__new__(AutoImportWorker)
        return bare.enumerate_candidates(staging_path)

    def get_results(self, limit=200):
        return []


def test_inbox_while_scanning_keeps_last_complete_summary(tmp_path, monkeypatch):
    """#1874: the header stats (items / files / GB) vanished for the whole
    scan whenever the page had no client-side snapshot — a cold open, a
    refresh, or a return from the matcher while processing was running.
    The scanning reply must carry the last COMPLETE build's summary for
    the same folder: counted numbers, not a fabrication."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    complete, status = routes.inbox(rt, _EnumeratingWorker())
    assert status == 200 and not complete.get("scanning")
    assert complete["summary"]["items"] == 1
    assert complete["summary"]["files"] == 2

    _force_scanning(monkeypatch)
    payload, status = routes.inbox(rt, _EnumeratingWorker())
    assert status == 200 and payload["scanning"] is True
    assert payload["summary"] == complete["summary"]


def test_inbox_while_scanning_summary_is_scoped_to_its_folder(tmp_path, monkeypatch):
    """The remembered summary stands in only for its own folder: a scan of
    a different staging path must not inherit another folder's numbers."""
    sp = _staging(tmp_path, 2)
    complete, _ = routes.inbox(_full_runtime(sp), _EnumeratingWorker())
    assert complete["summary"]["files"] == 2

    other = tmp_path / "other"
    other.mkdir()
    _force_scanning(monkeypatch)
    payload, status = routes.inbox(_full_runtime(str(other)), _EnumeratingWorker())
    assert status == 200 and payload["scanning"] is True
    assert "summary" not in payload


def test_inbox_summary_survives_scan_cache_invalidation(tmp_path, monkeypatch):
    """Imports invalidate the staging scan cache, and the re-scan that
    follows is exactly when processing is under way — the summary must
    survive invalidation (as the page's own snapshot does) until the next
    complete build replaces it."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    complete, _ = routes.inbox(rt, _EnumeratingWorker())
    routes.invalidate_staging_scan_cache()

    _force_scanning(monkeypatch)
    payload, status = routes.inbox(rt, _EnumeratingWorker())
    assert status == 200 and payload["scanning"] is True
    assert payload["summary"] == complete["summary"]


def test_scanning_summary_is_a_copy_not_the_cache(tmp_path, monkeypatch):
    """Mutating one reply's summary must not poison the remembered one."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    payload["summary"]["files"] = 999
    payload["summary"]["by_status"]["waiting"] = 999
    again, _ = routes.inbox(rt, _EnumeratingWorker())
    assert again["summary"]["files"] == 2
    assert again["summary"]["by_status"].get("waiting") != 999


def test_complete_reply_summary_mutation_does_not_poison_the_cache(tmp_path, monkeypatch):
    """The store direction of the copy rule: mutating the COMPLETE reply's
    summary must not change what a later scanning reply serves."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    complete, _ = routes.inbox(rt, _EnumeratingWorker())
    complete["summary"]["files"] = 999
    complete["summary"]["by_status"]["waiting"] = 999

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    assert payload["summary"]["files"] == 2
    assert payload["summary"]["by_status"].get("waiting") != 999


def test_empty_complete_build_clears_the_remembered_summary(tmp_path, monkeypatch):
    """Review fold (#1874): a complete build of an EMPTY folder must not
    leave the previous batch's numbers in place — and remembering its
    zeroes would resurrect the fabricated '0 items' display #1671 forbids
    for the next, not-yet-counted batch. Either way the scanning reply
    after an empty build carries no summary."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    complete, _ = routes.inbox(rt, _EnumeratingWorker())
    assert complete["summary"]["files"] == 2

    for f in (tmp_path / "staging").rglob("*.flac"):
        f.unlink()
    routes.invalidate_staging_scan_cache()
    emptied, _ = routes.inbox(rt, _EnumeratingWorker())
    assert emptied["summary"]["items"] == 0 and emptied["summary"]["files"] == 0

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    assert "summary" not in payload


def test_remembered_summary_expires(tmp_path, monkeypatch):
    """Review fold (#1874): last-known is bounded — a summary counted past
    its TTL is not presented as a later scan's numbers."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    entry = routes._last_inbox_summary["entry"]
    routes._last_inbox_summary["entry"] = (
        entry[0], entry[1], time.time() - routes._LAST_INBOX_SUMMARY_TTL - 1, *entry[3:])

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    assert "summary" not in payload
    again, _ = routes.inbox(rt, _EnumeratingWorker())
    assert "summary" not in again


def test_summary_aged_past_ttl_is_expired(tmp_path, monkeypatch):
    """The freshness convention matches the neighbouring caches: age >=
    TTL is stale, including exactly at the boundary. (The frozen-clock
    test below pins the boundary exactly; this one ages an entry past it
    and starts a fresh scan, judged at its start.)"""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    entry = routes._last_inbox_summary["entry"]
    routes._last_inbox_summary["entry"] = (
        entry[0], entry[1], time.time() - routes._LAST_INBOX_SUMMARY_TTL, *entry[3:])
    routes._staging_scan_status.update(
        {"status": "scanning", "path": sp, "started_at": time.time()})

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    assert "summary" not in payload


def test_summary_fresh_at_scan_start_is_served_for_the_whole_scan(tmp_path, monkeypatch):
    """Claude gate fold, pass 2 (#1874): age is judged at the scan's start.
    An entry fresh enough when the scan began must not expire mid-scan
    (that is the original bug returning), even once it passes the TTL."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    entry = routes._last_inbox_summary["entry"]
    routes._last_inbox_summary["entry"] = (
        entry[0], entry[1], time.time() - routes._LAST_INBOX_SUMMARY_TTL - 60, *entry[3:])
    routes._staging_scan_status.update(
        {"status": "scanning", "path": sp, "started_at": time.time() - 120})

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    assert payload["summary"]["files"] == 2


def test_summary_expired_at_scan_start_is_not_served(tmp_path, monkeypatch):
    """The other half of scan-start judging: an entry already past the TTL
    when the scan began is cross-session stale and is never served."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    entry = routes._last_inbox_summary["entry"]
    routes._last_inbox_summary["entry"] = (
        entry[0], entry[1], time.time() - routes._LAST_INBOX_SUMMARY_TTL - 300, *entry[3:])
    routes._staging_scan_status.update(
        {"status": "scanning", "path": sp, "started_at": time.time() - 120})

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    assert "summary" not in payload


def test_identical_builds_keep_their_seq_and_changed_counts_advance_it(tmp_path):
    """Claude gate fold, pass 4 (#1874): the seq advances only when the
    counted numbers change, so identical polls stay deeply equal (the
    page's structural sharing survives) and seq comparisons are only ever
    asked to order summaries whose numbers actually differ."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    first, _ = routes.inbox(rt, _EnumeratingWorker())
    second, _ = routes.inbox(rt, _EnumeratingWorker())
    assert second["summary"] == first["summary"]

    (tmp_path / "staging" / "Artist" / "Album" / "99.flac").write_text("x")
    routes.invalidate_staging_scan_cache()
    third, _ = routes.inbox(rt, _EnumeratingWorker())
    assert third["summary"]["files"] == 3
    assert third["summary"]["seq"] > first["summary"]["seq"]


def test_scanning_payload_reports_the_clear_sequence(tmp_path, monkeypatch):
    """Claude gate fold, pass 4 (#1874): when a later build counted the
    folder empty, the scanning reply carries the clear's build seq so a
    tab holding a pre-clear snapshot can drop it."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    for f in (tmp_path / "staging").rglob("*.flac"):
        f.unlink()
    routes.invalidate_staging_scan_cache()
    emptied, _ = routes.inbox(rt, _EnumeratingWorker())
    assert emptied["summary"]["files"] == 0

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    assert "summary" not in payload
    assert payload["summary_cleared_seq"] == emptied["summary"]["seq"]
    # Folder-scoped (pass 7): another folder's payload must not carry
    # this folder's clear.
    other = routes._scanning_inbox_payload(
        {"success": True, "scanning": True, "staging_path": "/elsewhere",
         "progress": {"scanned": 0, "total": 0}}, "/elsewhere", None)
    assert "summary_cleared_seq" not in other


def test_empty_builds_keep_their_seq(tmp_path):
    """Claude gate fold, pass 5 (#1874): an empty folder's polls must not
    churn the sequence — the tombstone holds the folder and its counted
    zeroes, so the next identical empty build adopts its wire seq."""
    sp = _staging(tmp_path, 0)
    rt = _full_runtime(sp)
    first, _ = routes.inbox(rt, _EnumeratingWorker())
    assert first["summary"]["files"] == 0
    second, _ = routes.inbox(rt, _EnumeratingWorker())
    assert second["summary"] == first["summary"]


def test_malformed_entry_does_not_fail_a_complete_build(tmp_path):
    """Claude gate folds, passes 5–6 (#1874): the remember path is a
    nicety on the COMPLETE path too — a malformed slot entry degrades,
    never a 500 for an inbox that was already built — and the failure
    resets the slot so the NEXT build remembers again instead of the
    malformed entry wedging the slot for the process's life."""
    sp = _staging(tmp_path, 2)
    routes._last_inbox_summary["entry"] = (sp,)
    payload, status = routes.inbox(_full_runtime(sp), _EnumeratingWorker())
    assert status == 200 and payload["summary"]["files"] == 2
    routes.invalidate_staging_scan_cache()
    payload, status = routes.inbox(_full_runtime(sp), _EnumeratingWorker())
    assert status == 200
    assert routes._last_inbox_summary_for(sp)["files"] == 2


def test_summary_at_the_exact_ttl_boundary_with_a_frozen_clock(tmp_path, monkeypatch):
    """Claude gate fold, pass 5 (#1874): pin >= vs > — with the clock
    frozen, an entry aged EXACTLY the TTL is expired."""
    sp = _staging(tmp_path, 2)
    frozen = 1_000_000.0
    summary = {"items": 1, "files": 2, "size": 0, "attention": 1,
               "by_status": {}, "seq": 1}
    routes._last_inbox_summary["entry"] = (
        sp, summary, frozen - routes._LAST_INBOX_SUMMARY_TTL, 1, 1, None)
    monkeypatch.setattr(time, "time", lambda: frozen)

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(_full_runtime(sp), _EnumeratingWorker())
    assert "summary" not in payload


def test_out_of_order_live_builds_keep_the_later_started_numbers(tmp_path, monkeypatch):
    """Claude gate fold, pass 5 (#1874): the real ordering claim, live.
    Build A starts first (counts 2 files) but is slow finishing; build B
    starts later, counts the folder as it now is (1 file), and remembers
    first. When A finally finishes, its older numbers must NOT overwrite
    B's — ordering follows build START, not finish."""
    import threading

    import core.imports.inbox as inbox_mod

    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    real_build = inbox_mod.build_inbox
    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}
    calls_lock = threading.Lock()

    def blocking_build(*a, **k):
        with calls_lock:
            calls["n"] += 1
            mine = calls["n"]
        if mine == 1:
            entered.set()
            release.wait(10)
        return real_build(*a, **k)

    monkeypatch.setattr(inbox_mod, "build_inbox", blocking_build)
    result_a = {}

    def run_a():
        result_a["payload"], result_a["status"] = routes.inbox(rt, _EnumeratingWorker())

    thread_a = threading.Thread(target=run_a)
    thread_a.start()
    try:
        assert entered.wait(10)
        # the folder changes while A is mid-build; B recounts from scratch
        next((tmp_path / "staging").rglob("*.flac")).unlink()
        routes._staging_scan_cache.update({"path": None, "ts": 0.0, "records": None})
        payload_b, status_b = routes.inbox(rt, _EnumeratingWorker())
        assert status_b == 200 and payload_b["summary"]["files"] == 1
    finally:
        release.set()
        thread_a.join(10)
    assert result_a["status"] == 200 and result_a["payload"]["summary"]["files"] == 2
    assert routes._last_inbox_summary_for(sp)["files"] == 1


def test_build_seq_seed_is_js_safe_and_restart_ordered(monkeypatch):
    """Claude gate fold, pass 3 (#1874): the seq is compared in the
    browser, so the seed must stay inside JavaScript's safe-integer range
    with adjacent values distinct after a JSON round-trip (a nanosecond
    seed, ~1.8e18, rounded neighbours to equal and silently broke the
    ordering), and a later process must start ahead of an earlier one."""
    import json

    first = routes._initial_build_seq()
    second = routes._initial_build_seq()
    assert first < 2**53
    assert second >= first
    # a process started an hour later seeds ahead of this one's seed by
    # the elapsed wall clock (the restart-ordering claim, not a tautology)
    import time as _time
    real_time = _time.time
    monkeypatch.setattr(_time, "time", lambda: real_time() + 3600)
    assert routes._initial_build_seq() >= first + 3_600_000 - 1000
    for seq in (first, first + 1, first + 1000):
        assert json.loads(json.dumps(seq)) == seq
    assert json.loads(json.dumps(first)) != json.loads(json.dumps(first + 1))


def test_summary_past_the_hard_cap_is_not_served_even_mid_scan(tmp_path, monkeypatch):
    """Claude gate fold, pass 3 (#1874): scan-start judging is capped — a
    hung scan must not make last-known immortal."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    entry = routes._last_inbox_summary["entry"]
    routes._last_inbox_summary["entry"] = (
        entry[0], entry[1], time.time() - 5 * routes._LAST_INBOX_SUMMARY_TTL, *entry[3:])
    routes._staging_scan_status.update(
        {"status": "scanning", "path": sp, "started_at": time.time()})

    _force_scanning(monkeypatch)
    payload, _ = routes.inbox(rt, _EnumeratingWorker())
    assert "summary" not in payload


def test_real_scan_start_stamps_started_at_and_serves_summary(tmp_path):
    """Claude gate fold, pass 3 (#1874): the real scan path — not a hand-
    written status dict. ensure_background_staging_scan stamps
    started_at, and a scanning payload built during that real scan
    carries the remembered summary."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    assert routes._last_inbox_summary_for(sp)["files"] == 2

    def slow(_f, _r):
        time.sleep(0.4)
        return _meta(_f, _r)

    routes.invalidate_staging_scan_cache()
    try:
        routes.ensure_background_staging_scan(_runtime(slow), sp)
        assert routes._staging_scan_status["status"] == "scanning"
        assert isinstance(routes._staging_scan_status["started_at"], float)
        payload = routes._scanning_inbox_payload(
            {"success": True, "scanning": True, "staging_path": sp,
             "progress": {"scanned": 0, "total": 2}}, sp, None)
        assert payload["summary"]["files"] == 2
    finally:
        _await_done()


def test_older_zero_build_cannot_clear_a_newer_entry(tmp_path):
    """The empty build's tombstone obeys the start-sequence guard: an
    older-started zero build finishing late cannot clear counts a
    later-started build already remembered."""
    sp = _staging(tmp_path, 2)
    counted = {"items": 1, "files": 5, "size": 5, "attention": 0, "by_status": {}}
    zero = {"items": 0, "files": 0, "size": 0, "attention": 0, "by_status": {}}
    routes._remember_inbox_summary(sp, counted, build_seq=10)
    routes._remember_inbox_summary(sp, zero, build_seq=9)
    assert routes._last_inbox_summary_for(sp)["files"] == 5
    routes._remember_inbox_summary(sp, zero, build_seq=11)
    assert routes._last_inbox_summary_for(sp) is None


def test_adoption_blocked_by_an_intervening_skipped_build(tmp_path):
    """Claude gate fold, pass 6 (#1874): adoption reuses a sequence only
    when no unremembered build intervenes. A generation-skipped build's
    different counts may sit in a tab under a higher sequence; reusing
    the older sequence for the same numbers again would order the
    fresher count behind them for that tab."""
    sp = _staging(tmp_path, 2)
    n1 = {"items": 1, "files": 2, "size": 2, "attention": 0, "by_status": {}}
    n2 = {"items": 1, "files": 9, "size": 9, "attention": 0, "by_status": {}}
    assert routes._remember_inbox_summary(sp, n1, build_seq=101) == 101
    gen = routes._staging_scan_generation["value"]
    routes.invalidate_staging_scan_cache()
    assert routes._remember_inbox_summary(
        sp, n2, generation=gen, build_seq=102) is None
    # Same numbers as 101, but a skipped build intervened: fresh seq.
    assert routes._remember_inbox_summary(sp, n1, build_seq=103) == 103
    # No further skip: adoption works again.
    assert routes._remember_inbox_summary(sp, n1, build_seq=104) == 103


def test_summary_judged_at_start_of_a_just_finished_scan(tmp_path, monkeypatch):
    """Claude gate fold, pass 6 (#1874): the recently-done branch — a scan
    that finished moments ago still judges the entry's age at ITS start,
    so an entry that crossed the TTL during the scan is still served for
    the post-scan payload."""
    sp = _staging(tmp_path, 2)
    payload, _ = routes.inbox(_full_runtime(sp), _EnumeratingWorker())
    assert payload["summary"]["files"] == 2
    entry = routes._last_inbox_summary["entry"]
    routes._last_inbox_summary["entry"] = (
        entry[0], entry[1],
        time.time() - routes._LAST_INBOX_SUMMARY_TTL - 60,
        entry[3], entry[4], entry[5])
    started = time.time() - 90
    assert routes._last_inbox_summary_for(sp) is None
    served = routes._last_inbox_summary_for(sp, scan_started_at=started)
    assert served is not None and served["files"] == 2


def test_scanning_payload_for_a_just_finished_scan(tmp_path):
    """Claude gate fold, pass 7 (#1874): the recently-done branch of
    _scanning_inbox_payload itself — a reply already determined to be a
    scanning reply whose scan finished moments ago still judges the
    entry's age at the scan's start; a STALE done (an earlier scan's)
    does not get that treatment."""
    sp = _staging(tmp_path, 2)
    routes.inbox(_full_runtime(sp), _EnumeratingWorker())
    entry = routes._last_inbox_summary["entry"]
    routes._last_inbox_summary["entry"] = (
        entry[0], entry[1],
        time.time() - routes._LAST_INBOX_SUMMARY_TTL - 60,
        entry[3], entry[4], entry[5])
    base = {"success": True, "scanning": True, "staging_path": sp,
            "progress": {"scanned": 2, "total": 2}}
    routes._staging_scan_status.update(
        {"status": "done", "path": sp, "started_at": time.time() - 90})
    payload = routes._scanning_inbox_payload(dict(base), sp, None)
    assert payload["summary"]["files"] == 2
    routes._staging_scan_status.update(
        {"status": "done", "path": sp, "started_at": time.time() - 300})
    payload = routes._scanning_inbox_payload(dict(base), sp, None)
    assert "summary" not in payload


def test_remembered_summary_is_a_single_slot(tmp_path, monkeypatch):
    """Review fold (#1874): only the most recently built folder's summary
    is kept. Building folder B evicts folder A — A then gets NO summary
    (never B's numbers, which the path check forbids)."""
    sp_a = _staging(tmp_path / "a", 2)
    sp_b = _staging(tmp_path / "b", 3)
    routes.inbox(_full_runtime(sp_a), _EnumeratingWorker())
    routes.inbox(_full_runtime(sp_b), _EnumeratingWorker())

    _force_scanning(monkeypatch)
    payload_b, _ = routes.inbox(_full_runtime(sp_b), _EnumeratingWorker())
    assert payload_b["summary"]["files"] == 3
    payload_a, _ = routes.inbox(_full_runtime(sp_a), _EnumeratingWorker())
    assert "summary" not in payload_a


def test_build_invalidated_mid_build_does_not_remember(tmp_path, monkeypatch):
    """Review fold, round 2 (#1874): ordering guards. A complete build that
    STARTED before an invalidation (an import/upload moved files mid-build)
    must not resurrect the pre-invalidation counts into the slot when it
    finishes — the same generation discipline the staging scan cache uses.
    The build's own reply is unaffected; only remembering is skipped."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    import core.imports.inbox as inbox_mod
    real_build = inbox_mod.build_inbox

    def build_then_invalidate(*a, **k):
        rows = real_build(*a, **k)
        routes.invalidate_staging_scan_cache()
        return rows

    monkeypatch.setattr(inbox_mod, "build_inbox", build_then_invalidate)
    payload, status = routes.inbox(rt, _EnumeratingWorker())
    assert status == 200 and payload["summary"]["files"] == 2
    assert routes._last_inbox_summary["entry"] is None


def test_empty_build_with_problems_still_clears(tmp_path):
    """Claude gate fold (#1874): a complete build is authoritative for the
    folder's READABLE staged content even when it also reports problems —
    an unreadable subdirectory is surfaced in the reply's problems list,
    and its unknown contents were never in any build's counts. An empty
    build with problems therefore still clears the remembered summary;
    keeping it would serve counts for files a complete build just counted
    as gone (and overrule the tab's own honest empty snapshot)."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    assert routes._last_inbox_summary_for(sp)["files"] == 2

    for f in (tmp_path / "staging").rglob("*.flac"):
        f.unlink()
    routes.invalidate_staging_scan_cache()

    class _ProblemWorker(_EnumeratingWorker):
        def enumerate_candidates(self, staging_path):
            return [], [{"path": f"{staging_path}/Locked", "error": "Permission denied"}]

    payload, status = routes.inbox(rt, _ProblemWorker())
    assert status == 200 and payload["summary"]["files"] == 0
    assert payload["problems"]
    assert routes._last_inbox_summary_for(sp) is None


def test_clear_leaves_a_tombstone_older_builds_cannot_resurrect(tmp_path):
    """Claude gate fold (#1874): clearing must keep the build sequence —
    a late-finishing build older than the clear cannot write its (pre-
    clear) counts back into the slot."""
    sp = _staging(tmp_path, 2)
    counted = {"items": 1, "files": 10, "size": 10, "attention": 0, "by_status": {}}
    zero = {"items": 0, "files": 0, "size": 0, "attention": 0, "by_status": {}}
    routes._remember_inbox_summary(sp, counted, build_seq=5)
    routes._remember_inbox_summary(sp, zero, build_seq=7)
    assert routes._last_inbox_summary_for(sp) is None
    routes._remember_inbox_summary(sp, counted, build_seq=6)
    assert routes._last_inbox_summary_for(sp) is None
    routes._remember_inbox_summary(sp, counted, build_seq=8)
    assert routes._last_inbox_summary_for(sp)["files"] == 10


def test_concurrent_remembers_serialise_on_the_highest_build_seq(tmp_path):
    """Claude gate fold (#1874): parallel complete builds (gunicorn runs
    several threads) must leave the highest build sequence's summary in
    the slot, whatever order they finish in."""
    import threading

    sp = _staging(tmp_path, 2)
    seqs = list(range(1, 41))

    def remember(seq):
        routes._remember_inbox_summary(
            sp, {"items": 1, "files": seq, "size": seq, "attention": 0,
                 "by_status": {}}, build_seq=seq)

    threads = [threading.Thread(target=remember, args=(s,)) for s in reversed(seqs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert routes._last_inbox_summary_for(sp)["files"] == 40


def test_failed_remember_resets_the_slot_and_recovers(tmp_path, monkeypatch):
    """Claude gate folds (#1874): the except path in inbox() — a remember
    that raises must still return the built inbox. The slot is reset to
    EMPTY (not tombstoned): sequence ordering already keeps the older
    entry behind this build's snapshot for its own readers, a tombstone
    would needlessly strip stats from other tabs, and a plain reset is
    what lets a wedged (e.g. malformed) slot recover on the next build
    instead of failing every remember for the process's life."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())
    assert routes._last_inbox_summary_for(sp)["files"] == 2

    def _boom(*a, **k):
        raise RuntimeError("slot broken")

    monkeypatch.setattr(routes, "_remember_inbox_summary", _boom)
    routes.invalidate_staging_scan_cache()
    payload, status = routes.inbox(rt, _EnumeratingWorker())
    assert status == 200 and payload["summary"]["files"] == 2
    assert routes._last_inbox_summary["entry"] is None
    # Recovery: with remember working again, the next build re-remembers.
    monkeypatch.undo()
    routes.invalidate_staging_scan_cache()
    routes.inbox(rt, _EnumeratingWorker())
    assert routes._last_inbox_summary_for(sp)["files"] == 2


def test_out_of_order_builds_do_not_overwrite_fresher_summary(tmp_path):
    """Review fold, round 2 (#1874): a later-started build's entry is never
    replaced by an earlier-started build that merely finished later."""
    sp = _staging(tmp_path, 2)
    fresh = {"items": 1, "files": 5, "size": 5, "attention": 0, "by_status": {}}
    stale = {"items": 1, "files": 2, "size": 2, "attention": 0, "by_status": {}}
    routes._remember_inbox_summary(sp, fresh, build_seq=10)
    routes._remember_inbox_summary(sp, stale, build_seq=9)
    assert routes._last_inbox_summary_for(sp)["files"] == 5


def test_scanning_summary_survives_malformed_entry(tmp_path, monkeypatch):
    """Review fold, round 2 (#1874): the summary lookup is enrichment — a
    malformed remembered entry degrades to no summary, never a 500."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes._last_inbox_summary["entry"] = (sp,)  # not an entry tuple at all

    _force_scanning(monkeypatch)
    payload, status = routes.inbox(rt, None)
    assert status == 200 and payload["scanning"] is True
    assert "summary" not in payload


def test_inbox_while_scanning_summary_without_worker(tmp_path, monkeypatch):
    """The summary is attached before any worker handling: a scanning poll
    with no worker still carries the last complete build's numbers."""
    sp = _staging(tmp_path, 2)
    rt = _full_runtime(sp)
    routes.inbox(rt, _EnumeratingWorker())

    _force_scanning(monkeypatch)
    payload, status = routes.inbox(rt, None)
    assert status == 200 and payload["scanning"] is True
    assert payload["summary"]["files"] == 2
    assert payload["worker"]["available"] is False


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
