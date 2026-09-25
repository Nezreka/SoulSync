"""pathdrift25-01 — the operator-facing half of the stale-index-path repair.

LV2-017's correction contract promised a *read-only* backfill for rows that
drifted before the forward fix landed. This is that job: it proposes, the
operator approves, and only then does the index move. Nothing on disk is ever
touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.repair_jobs.base import JobContext
from core.repair_jobs.path_drift_reconcile import PathDriftReconcileJob


class _EnabledConfig:
    def get(self, key, default=None):
        return True if key == "features.library_v2" else default


@pytest.fixture
def drifted(tmp_path: Path):
    music = tmp_path / "music" / "1nonly" / "Bunny Girl"
    music.mkdir(parents=True)
    (music / "01 - Bunny Girl.flac").write_bytes(b"audio")

    db_path = str(tmp_path / "lib2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)
    conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, '1nonly')")
    conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title) "
                 "VALUES(1, 1, 'Bunny Girl')")
    conn.execute("INSERT INTO lib2_tracks(id, album_id, title, track_number) "
                 "VALUES(1, 1, 'Bunny Girl', 1)")
    conn.execute("INSERT INTO lib2_track_files(id, track_id, path) VALUES(1, 1, ?)",
                 (str(music / "01-01 - Bunny Girl.flac"),))
    conn.commit()
    conn.close()

    class _DB:
        database_path = db_path

        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    return _DB(), music


def _ctx(db, findings):
    return JobContext(
        db=db, transfer_folder="/tmp", config_manager=_EnabledConfig(),
        create_finding=lambda **kw: findings.append(kw) or True,
        should_stop=lambda: False, is_paused=lambda: False,
    )


def test_scan_reports_a_proposal_without_touching_anything(drifted):
    db, music = drifted
    findings = []

    result = PathDriftReconcileJob().scan(_ctx(db, findings))

    assert result.findings_created == 1
    finding = findings[0]
    assert finding["finding_type"] == "stale_index_path"
    assert finding["entity_id"] == "lib2:1"
    assert finding["details"]["proposed_path"] == str(music / "01 - Bunny Girl.flac")
    assert finding["details"]["_fix_action"] == "repoint"

    conn = db._get_connection()
    stored = conn.execute("SELECT path FROM lib2_track_files WHERE id=1").fetchone()[0]
    conn.close()
    assert stored.endswith("01-01 - Bunny Girl.flac")


def test_scan_stays_quiet_when_nothing_drifted(drifted, tmp_path):
    db, music = drifted
    conn = db._get_connection()
    conn.execute("UPDATE lib2_track_files SET path=? WHERE id=1",
                 (str(music / "01 - Bunny Girl.flac"),))
    conn.commit()
    conn.close()

    findings = []
    result = PathDriftReconcileJob().scan(_ctx(db, findings))
    assert findings == []
    assert result.findings_created == 0


def test_approving_a_proposal_repoints_the_index_and_moves_no_file(drifted):
    from core.repair_worker import RepairWorker

    db, music = drifted
    findings = []
    PathDriftReconcileJob().scan(_ctx(db, findings))
    details = findings[0]["details"]

    worker = RepairWorker(db, transfer_folder="/tmp")
    result = worker._execute_fix(
        "stale_index_path", "file", "lib2:1",
        details["stored_path"], details,
    )

    assert result["success"] is True
    conn = db._get_connection()
    stored = conn.execute("SELECT path FROM lib2_track_files WHERE id=1").fetchone()[0]
    conn.close()
    assert stored == str(music / "01 - Bunny Girl.flac")
    assert (music / "01 - Bunny Girl.flac").exists()
    assert sorted(p.name for p in music.iterdir()) == ["01 - Bunny Girl.flac"]


def test_an_ambiguous_finding_cannot_be_fixed_by_the_worker(drifted):
    from core.repair_worker import RepairWorker

    db, music = drifted
    (music / "1-01 - Bunny Girl.flac").write_bytes(b"audio-two")
    findings = []
    PathDriftReconcileJob().scan(_ctx(db, findings))

    worker = RepairWorker(db, transfer_folder="/tmp")
    result = worker._execute_fix(
        "stale_index_path", "file", "lib2:1",
        findings[0]["details"]["stored_path"], findings[0]["details"],
    )
    assert result["success"] is False


def test_ambiguous_directories_are_reported_but_carry_no_fix(drifted):
    db, music = drifted
    (music / "1-01 - Bunny Girl.flac").write_bytes(b"audio-two")
    findings = []

    PathDriftReconcileJob().scan(_ctx(db, findings))

    assert len(findings) == 1
    details = findings[0]["details"]
    assert details["_fix_action"] == "review"
    assert details["proposed_path"] is None
    assert len(details["alternatives"]) == 2
