"""Review the right primary file and queue into its owner's library."""

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.library_scope import library_scope
from core.repair_jobs.base import JobContext
from core.repair_jobs.quality_profile_audit import QualityProfileAuditJob
from core.repair_worker import RepairWorker
from tests.lib2_seed import track
from tests.library2.test_two_libraries import lib  # noqa: F401 - shared fixture


@pytest.fixture()
def owned_audit(lib):
    profile = lib.db.create_quality_profile("High rate", {
        "ranked_targets": [{"format": "wav", "min_sample_rate": 96000}],
        "upgrade_policy": "until_cutoff", "upgrade_cutoff_index": 0,
    })
    copies = [(Path(lib.shared) / "song.wav", 96000),
              (Path(lib.kim_root) / "old.wav", 22050),
              (Path(lib.kim_root) / "song.wav", 44100)]
    for path, rate in copies:
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x00\x01" * 441)
    with lib.db._get_connection() as conn:
        tid = track(conn, "Artist", "Album", "Song", credit="Artist", owned=False,
                    quality_profile_id=profile, quality_profile_explicit=1)
        for path, rate in copies:
            conn.execute("INSERT INTO lib2_track_files(track_id, path, format, sample_rate, bit_depth) "
                         "VALUES(?,?,'wav',?,16)", (tid, str(path), rate))
        conn.commit()
    cfg = SimpleNamespace(get=lambda key, default=None: lib.shared
                          if key == "soulseek.transfer_path" else default)
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager = lib.db, cfg
    context = JobContext(db=lib.db, transfer_folder=lib.shared, config_manager=cfg,
                         create_finding=worker._create_finding)
    with library_scope(None):
        result = QualityProfileAuditJob().scan(context)
    with lib.db._get_connection() as conn:
        findings = [dict(row) for row in conn.execute("SELECT * FROM repair_findings")]
    return SimpleNamespace(lib=lib, tid=tid, preferred=str(copies[-1][0]),
                           findings=findings, result=result, worker=worker)


def test_the_audit_uses_the_same_own_primary_as_acquisition(owned_audit):
    finding, = owned_audit.findings
    assert owned_audit.result.scanned == 2  # one primary per library
    assert finding["file_path"] == owned_audit.preferred
    assert json.loads(finding["details_json"])["owner_profile_id"] == owned_audit.lib.kim


def test_approval_queues_only_for_the_reviewed_files_owner(owned_audit):
    from core.quality.upgrades import until_cutoff_finding_ids

    finding, = owned_audit.findings
    result = owned_audit.worker._fix_quality_profile_audit(
        "track", finding["entity_id"], finding["file_path"], json.loads(finding["details_json"]))
    assert result["success"] is True
    assert owned_audit.lib.db.get_wishlist_count(profile_id=owned_audit.lib.kim) == 1
    assert owned_audit.lib.db.get_wishlist_count(profile_id=1) == 0
    assert until_cutoff_finding_ids(owned_audit.lib.db, profile_id=1) == []
    assert until_cutoff_finding_ids(owned_audit.lib.db, profile_id=owned_audit.lib.kim) == [finding["id"]]


def test_public_approval_does_not_also_mirror_a_wanted_shared_copy(owned_audit):
    from core.library2.monitor_rules import record_rule, PROVENANCE_USER
    from core.library2.wanted import recompute_wanted

    with owned_audit.lib.db._get_connection() as conn:
        record_rule(conn, "track", owned_audit.tid, True, PROVENANCE_USER, profile_id=1)
        recompute_wanted(conn, track_ids=[owned_audit.tid], profile_id=1)
        conn.execute("UPDATE lib2_track_files SET sample_rate=44100 WHERE track_id=? "
                     "AND owner_profile_id IS NULL", (owned_audit.tid,))
        conn.commit()
    finding, = owned_audit.findings
    with library_scope(owned_audit.lib.kim):
        result = owned_audit.worker.fix_finding(finding["id"])
    assert result["success"] is True
    assert owned_audit.lib.db.get_wishlist_count(profile_id=owned_audit.lib.kim) == 1
    assert owned_audit.lib.db.get_wishlist_count(profile_id=1) == 0
