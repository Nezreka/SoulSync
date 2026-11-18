"""#2179: a bulk Keep Best never silently does nothing.

A duplicate whose titles differ needs a person to confirm it is one
recording, and a duplicate whose other copy is protected (a playlist the
kept copy is not in, say) has nothing to remove. A bulk run used to resolve
the protected ones like a dismiss. Now both stay open, marked "needs you"
with the reason, the run counts them by reason, the list can sort them
first, and moving playlist entries can be turned on for the whole run.
"""

from contextlib import closing
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from tests.library2.test_native_duplicate_review import copy, states  # noqa: F401
from tests.library2.test_native_duplicate_review import library  # noqa: F401 - fixture
from tests.library2.test_duplicate_playlists_2172 import server  # noqa: F401 - fixture
from tests.library2.test_duplicate_playlists_2172 import _map
from core.library2 import duplicate_review as dr


def _review(library, membership):
    db, config, *_ = library
    return dr.find_duplicate_candidates(db, config, server_source="plex", playlist_membership=membership)


def _apply(library, candidate, membership, **kw):
    db, config, _root, transfer = library
    return dr.apply_keep_best(db, candidate, config_manager=config, transfer_folder=str(transfer),
                              approved=True, server_source="plex", playlist_membership=membership, **kw)


def _playlist_case(library):
    """storm's pair: the worse copy is in "Anime", the kept one only in
    "Unlisted" (with no playlist at all the worse copy would be the keeper)"""
    db = library[0]
    a, af, pa = copy(library, fmt="ogg", bitrate=306)
    b, bf, pb = copy(library, album="Compilations", fmt="flac")
    _map(db, a, "pa")
    _map(db, b, "pb")
    membership = {"pa": ["Anime"], "pb": ["Unlisted"]}
    return _review(library, membership)[0], membership, (af, pa), (bf, pb)


def _weak_case(library):
    copy(library, title="See You Space Cowboy", isrc=None)
    copy(library, title="See You Space Cowboys", isrc=None, album="Other")
    return _review(library, {})[0]


# ── what the stored review says ──

def test_a_clean_pair_has_nothing_in_the_way(library):
    copy(library, fmt="mp3", bitrate=128)
    copy(library, album="Other", fmt="flac")
    candidate = _review(library, {})[0]
    assert dr.keep_best_blockers(candidate) == []


def test_differently_named_copies_need_the_same_recording_confirmed(library):
    candidate = _weak_case(library)
    assert candidate["requires_recording_confirmation"] is True
    assert dr.keep_best_blockers(candidate) == ["same_recording"]
    # moving playlist entries is no answer to that
    assert dr.keep_best_blockers(candidate, move=True) == ["same_recording"]


def test_a_playlist_the_keeper_is_not_in_needs_moving(library):
    candidate, *_ = _playlist_case(library)
    assert dr.keep_best_blockers(candidate) == ["playlist"]
    assert dr.keep_best_blockers(candidate, move=True) == []


def test_moving_cant_help_when_the_kept_copy_is_not_on_the_server(library):
    candidate, *_ = _playlist_case(library)
    for t in candidate["tracks"]:
        if t["file_id"] != candidate["recommended_file_id"]:
            assert t["playlist_movable"] is True
            t["playlist_movable"] = False  # as the scan stores it when the keeper has no item
    assert dr.keep_best_blockers(candidate) == ["protected"]
    assert dr.keep_best_blockers(candidate, move=True) == ["protected"]


def test_the_scan_says_when_moving_cant_help():
    keeper = {"playlists": [], "server_item": None, "protected_reasons": []}
    other = {"playlists": ["Anime"], "protecting_playlists": ["Anime"], "server_item": "pa",
             "protected_reasons": ["playlist_reference"]}
    dr.settle_playlists([keeper, other], keeper, move=False)
    assert other["playlists_blocked"] == "not_in_kept" and other["playlist_movable"] is False


def test_the_summary_counts_with_moving_off_and_on(library):
    candidate, *_ = _playlist_case(library)
    clean = {"recommended_file_id": 1, "tracks": [{"file_id": 1, "protected_reasons": []},
                                                  {"file_id": 2, "protected_reasons": []}]}
    weak = {**clean, "requires_recording_confirmation": True}
    summary = dr.keep_best_summary([candidate, clean, weak])
    assert summary == {"total": 3, "ready": 1, "ready_with_move": 2,
                       "needs_you": {"playlist": 1, "same_recording": 1},
                       "needs_you_with_move": {"same_recording": 1}}


# ── the apply ──

def test_bulk_keep_best_that_removes_nothing_stays_open(library):
    candidate, membership, (af, pa), _keep = _playlist_case(library)
    result = _apply(library, candidate, membership, require_removal=True)
    assert result["success"] is False and result["needs_you"] == "playlist"
    assert "playlist the kept copy is not in" in result["error"]
    assert pa.exists() and states(library)[af] == "active"


def test_keep_best_on_the_finding_itself_still_settles_it(library):
    # the finding's own Keep Best showed its plan first: nothing to remove is
    # the answer the person saw, so it resolves as it did
    candidate, membership, (_af, pa), _keep = _playlist_case(library)
    result = _apply(library, candidate, membership)
    assert result["success"] is True and result["removed_file_ids"] == [] and pa.exists()


def test_bulk_keep_best_with_moving_on_goes_through(library, server):
    candidate, membership, (af, pa), (bf, pb) = _playlist_case(library)
    result = _apply(library, candidate, membership, require_removal=True, move_playlist_entries=True)
    assert result["success"] is True, result
    assert server.calls == [("plex", ["Anime"], "pa", "pb")]
    assert result["removed_file_ids"] == [af] and not pa.exists() and pb.exists()


def test_bulk_moves_that_all_fail_leave_the_finding_open(library, server):
    server.outcome.update(moved=[], failed=[("Anime", "the server did not take the change")])
    candidate, membership, (af, pa), _keep = _playlist_case(library)
    result = _apply(library, candidate, membership, require_removal=True, move_playlist_entries=True)
    assert result["success"] is False and result["needs_you"] == "protected"
    assert pa.exists() and states(library)[af] == "active"


def test_bulk_never_confirms_the_same_recording_for_you(library):
    candidate = _weak_case(library)
    result = _apply(library, candidate, {}, require_removal=True)
    assert result["success"] is False and result["needs_you"] == "same_recording"
    assert all(state == "active" for state in states(library).values())


# ── the worker ──

def _worker_with_finding(library, candidate):
    from core.repair_worker import RepairWorker
    db, config, _root, transfer = library
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = db, config, str(transfer)
    return worker


@pytest.mark.parametrize("explicit", [False, True])
def test_the_fix_handler_marks_only_a_bulk_run(library, monkeypatch, explicit):
    candidate, membership, (af, pa), _keep = _playlist_case(library)
    monkeypatch.setattr(dr, "_playlist_context", lambda m, s: (membership, "plex"))
    worker = _worker_with_finding(library, candidate)
    tid = next(t["track_id"] for t in candidate["tracks"] if t["file_id"] == af)
    details = {**candidate, "_fix_action": "keep_best", "_explicit_single_fix": explicit}
    result = worker._fix_native_duplicate_review("track", f"lib2:{tid}", None, details)
    if explicit:
        assert result["success"] is True and "needs_you" not in result
    else:
        assert result["success"] is False and result["skipped_by_design"] is True
        assert result["needs_you"] == "playlist"
    assert pa.exists()


def _bare_worker(results):
    from core.repair_worker import RepairWorker
    worker = RepairWorker.__new__(RepairWorker)
    worker.should_stop = False
    worker._bulk_fix_stop_event = threading.Event()
    worker._bulk_fix_lock = threading.Lock()
    worker._bulk_fix_thread = None
    worker.fix_finding = lambda fid, fix_action=None: results[fid]
    worker._types_for_findings = lambda ids: {i: "native_duplicate_tracks" for i in ids}
    return worker


RESULTS = {
    1: {"success": True},
    2: {"success": False, "skipped_by_design": True, "needs_you": "same_recording", "error": "x"},
    3: {"success": False, "skipped_by_design": True, "needs_you": "playlist", "error": "y"},
    4: {"success": False, "skipped_by_design": True, "needs_you": "playlist", "error": "y"},
}


def test_the_selection_run_counts_who_needs_you(monkeypatch):
    worker = _bare_worker(RESULTS)
    worker._pending_fixable_ids = lambda **kw: [1, 2, 3, 4]
    out = worker.bulk_fix_findings(finding_ids=[1, 2, 3, 4], fix_action="keep_best")
    assert out["fixed"] == 1 and out["skipped"] == 3 and out["failed"] == 0
    assert out["needs_you"] == {"same_recording": 1, "playlist": 2}


def test_the_background_run_counts_who_needs_you():
    worker = _bare_worker(RESULTS)
    worker._bulk_fix_state = {"running": True, "total": 4, "done": 0, "fixed": 0, "failed": 0,
                              "skipped": 0, "partial": 0, "other_artist_skipped": 0, "needs_you": {},
                              "stopped": False, "errors": [], "error": None, "note": None,
                              "per_type": {}}
    worker._run_bulk_fix([1, 2, 3, 4], "keep_best")
    status = worker.get_bulk_fix_status()
    assert status["fixed"] == 1 and status["skipped"] == 3 and status["failed"] == 0
    assert status["needs_you"] == {"same_recording": 1, "playlist": 2}


# ── the list ──

class _NonClosingConn(sqlite3.Connection):
    def close(self):  # noqa: A003
        pass


@pytest.fixture
def findings_worker(monkeypatch):
    import core.library_scope as library_scope
    from core.repair_worker import RepairWorker
    monkeypatch.setattr(library_scope, "SCOPE_PARKED", True)
    library_scope.invalidate_library_scope_cache()
    conn = sqlite3.connect(":memory:", factory=_NonClosingConn)
    conn.execute("""CREATE TABLE repair_findings (
        id INTEGER PRIMARY KEY, job_id TEXT, finding_type TEXT, severity TEXT,
        status TEXT, entity_type TEXT, entity_id TEXT, file_path TEXT,
        title TEXT, description TEXT, details_json TEXT, user_action TEXT,
        resolved_at TEXT, created_at TEXT, updated_at TEXT)""")
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = SimpleNamespace(_get_connection=lambda: conn)
    worker._conn = conn
    yield worker
    library_scope.invalidate_library_scope_cache()


def _details_set(library):
    clean = {"recommended_file_id": 1, "tracks": [{"file_id": 1, "protected_reasons": []},
                                                  {"file_id": 2, "protected_reasons": []}]}
    playlist, *_ = _playlist_case(library)
    return {
        "clean": clean,
        "weak": {**clean, "requires_recording_confirmation": True},
        "playlist": playlist,
        "protected": {"recommended_file_id": 1, "tracks": [
            {"file_id": 1, "protected_reasons": []},
            {"file_id": 2, "protected_reasons": ["manual_metadata"]}]},
        "partly": {"recommended_file_id": 1, "tracks": [
            {"file_id": 1, "protected_reasons": []},
            {"file_id": 2, "protected_reasons": ["manual_metadata"]},
            {"file_id": 3, "protected_reasons": []}]},
    }


def _insert(worker, rows):
    for i, (name, details) in enumerate(rows, start=1):
        worker._conn.execute(
            "INSERT INTO repair_findings(id,job_id,finding_type,severity,status,title,details_json,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (i, "native_duplicate_detector", "native_duplicate_tracks", "info", "pending", name,
             json.dumps(details), f"2026-11-{i:02d}"))
    worker._conn.commit()


def test_the_sql_and_the_python_agree(library, findings_worker):
    rows = list(_details_set(library).items())
    _insert(findings_worker, rows)
    flagged = {r[0] for r in findings_worker._conn.execute(
        f"SELECT title FROM repair_findings WHERE {dr.NEEDS_YOU_SQL}")}
    assert flagged == {name for name, details in rows if dr.keep_best_blockers(details)}
    assert flagged == {"weak", "playlist", "protected"}


def test_needs_you_first_and_marked(library, findings_worker):
    _insert(findings_worker, list(_details_set(library).items()))
    page = findings_worker.get_findings(status="pending", finding_type="native_duplicate_tracks",
                                        sort="needs_you", limit=10)
    titles = [item["title"] for item in page["items"]]
    # needs you first, newest first inside each half
    assert titles == ["protected", "playlist", "weak", "partly", "clean"]
    marks = {item["title"]: item.get("needs_you") for item in page["items"]}
    assert marks["playlist"] == {"reasons": ["playlist"], "with_move": []}
    assert marks["weak"] == {"reasons": ["same_recording"], "with_move": ["same_recording"]}
    assert marks["clean"] is None and marks["partly"] is None


def test_the_worker_summary_and_the_group_count(library, findings_worker):
    _insert(findings_worker, list(_details_set(library).items()))
    summary = findings_worker.keep_best_summary()
    assert summary["total"] == 5 and summary["ready"] == 2 and summary["ready_with_move"] == 3
    assert findings_worker.keep_best_summary(finding_ids=[1, 2])["total"] == 2
    groups = {g["finding_type"]: g for g in findings_worker.get_finding_groups()}
    assert groups["native_duplicate_tracks"]["needs_you"] == 3
