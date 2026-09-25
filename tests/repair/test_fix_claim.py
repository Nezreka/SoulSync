"""`fix_finding` must claim a finding before running its handler.

The row was read as `pending` and only transitioned AFTER the handler returned,
so a background "Fix All" and a user's single Fix click could both pass the
check and both execute — two concurrent ffmpeg transcodes writing one output
path, or a duplicate file row from the SELECT/INSERT race inside
`_link_new_output_file` (bug-audit BUG-10).

The claim deliberately does NOT introduce a new `status`. `status` is filtered
in a dozen places, and a finding vanishing from the list mid-fix would be a
worse bug than the race; the row stays `pending` and only `fix_claimed_at` moves.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _worker(tmp_path: Path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
        "entity_type, entity_id, title) VALUES "
        "('j','dead_file','info','pending','track','lib2:1','t')")
    conn.commit()
    finding_id = conn.execute("SELECT id FROM repair_findings").fetchone()[0]
    conn.close()

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    worker.resolve_finding = lambda *a, **k: None
    worker._set_finding_error = lambda *a, **k: None
    return db, worker, finding_id


def test_two_concurrent_fixes_execute_the_handler_once(tmp_path: Path):
    db, worker, finding_id = _worker(tmp_path)
    executed = []
    gate = threading.Event()

    def slow_fix(*_a, **_k):
        executed.append(1)
        gate.wait(timeout=5)
        return {"success": False, "error": "stub"}

    worker._execute_fix = slow_fix

    results = []
    threads = [threading.Thread(target=lambda: results.append(worker.fix_finding(finding_id)))
               for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.4)          # let the winner get inside the handler
    gate.set()
    for t in threads:
        t.join(timeout=15)

    assert len(executed) == 1, f"handler ran {len(executed)} times"
    assert sum(1 for r in results if "being fixed" in str((r or {}).get("error", ""))) == 1


def test_a_failed_fix_releases_the_claim_and_stays_retryable(tmp_path: Path):
    db, worker, finding_id = _worker(tmp_path)
    worker._execute_fix = lambda *a, **k: {"success": False, "error": "nope"}

    assert worker.fix_finding(finding_id)["success"] is False

    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT status, fix_claimed_at FROM repair_findings WHERE id=?",
            (finding_id,)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "pending", "a failed fix must remain fixable"
    assert row["fix_claimed_at"] is None, "the claim must not outlive the attempt"

    # ...and the next attempt is therefore allowed in.
    assert worker.fix_finding(finding_id)["error"] == "nope"


def test_an_exception_in_the_handler_still_releases_the_claim(tmp_path: Path):
    db, worker, finding_id = _worker(tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("handler exploded")

    worker._execute_fix = boom
    assert worker.fix_finding(finding_id)["success"] is False

    conn = db._get_connection()
    try:
        claimed = conn.execute(
            "SELECT fix_claimed_at FROM repair_findings WHERE id=?",
            (finding_id,)).fetchone()[0]
    finally:
        conn.close()
    assert claimed is None, "an exploding handler must not wedge the finding"


def test_an_abandoned_claim_expires(tmp_path: Path):
    """A process that died mid-fix must not lock the finding forever."""
    db, worker, finding_id = _worker(tmp_path)
    conn = db._get_connection()
    conn.execute(
        "UPDATE repair_findings SET fix_claimed_at = datetime('now','-1 day') WHERE id=?",
        (finding_id,))
    conn.commit()
    conn.close()

    worker._execute_fix = lambda *a, **k: {"success": False, "error": "ran anyway"}
    assert worker.fix_finding(finding_id)["error"] == "ran anyway"
