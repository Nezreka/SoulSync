"""An optional native audit reports quality without changing acquisition intent."""

import hashlib
import json
import wave
from types import SimpleNamespace

import pytest

from core.repair_jobs.base import JobContext
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.lib2_seed import track


@pytest.fixture()
def audit(tmp_path):
    db = MusicDatabase(str(tmp_path / "library.db"))
    profile = db.create_quality_profile("High sample rate", {
        "ranked_targets": [{"format": "wav", "min_sample_rate": 96000}],
        "upgrade_policy": "until_cutoff", "upgrade_cutoff_index": 0,
    })
    path = tmp_path / "song.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x01" * 441)
    conn = db._get_connection()
    tid = track(conn, "Artist", "Album", "Song", path=str(path), credit="Artist",
                monitored=0, quality_profile_id=profile, quality_profile_explicit=1)
    conn.execute("UPDATE lib2_track_files SET format='wav', sample_rate=96000, bit_depth=16 "
                 "WHERE track_id=?", (tid,))
    conn.commit()
    conn.close()
    cfg = SimpleNamespace(get=lambda key, default=None: default)
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager = db, cfg
    context = JobContext(db=db, transfer_folder=str(tmp_path), config_manager=cfg,
                         create_finding=worker._create_finding)
    return SimpleNamespace(db=db, tid=tid, path=path, profile=profile, worker=worker, context=context)


def _scan(audit):
    from core.repair_jobs.quality_profile_audit import QualityProfileAuditJob
    return QualityProfileAuditJob().scan(audit.context)


def _findings(audit):
    with audit.db._get_connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM repair_findings ORDER BY id")]


def test_audit_measures_unmonitored_audio_without_queueing_or_modifying_it(audit):
    before = hashlib.sha256(audit.path.read_bytes()).hexdigest()
    result = _scan(audit)
    finding, = _findings(audit)
    assert result.findings_created == 1
    assert finding["finding_type"] == "quality_upgrade_review"
    assert finding["entity_id"] == f"lib2:{audit.tid}"
    assert json.loads(finding["details_json"])["measured_quality"]["sample_rate"] == 44100
    assert audit.db.get_wishlist_count(profile_id=1) == 0
    with audit.db._get_connection() as conn:
        assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?", (audit.tid,)).fetchone()[0] == 0
        assert conn.execute("SELECT sample_rate FROM lib2_track_files WHERE track_id=?", (audit.tid,)).fetchone()[0] == 96000
    assert hashlib.sha256(audit.path.read_bytes()).hexdigest() == before


def test_empty_file_scope_never_expands_to_the_whole_library(audit):
    audit.context.scope = {"file_paths": []}
    assert _scan(audit).scanned == 0
    assert _findings(audit) == []


def test_untargeted_format_is_a_profile_choice(audit):
    with audit.db._get_connection() as conn:
        conn.execute("UPDATE quality_profiles SET ranked_targets=? WHERE id=?",
                     ('[{"format":"mp3","min_bitrate":320}]', audit.profile))
        conn.commit()
    _scan(audit)
    finding, = _findings(audit)
    assert finding["finding_type"] == "quality_format_not_targeted"
    assert "not target" in finding["description"]
    result = audit.worker._fix_quality_profile_audit(
        "track", finding["entity_id"], str(audit.path), json.loads(finding["details_json"]))
    assert result["action"] == "ignored"
    assert audit.db.get_wishlist_count(profile_id=1) == 0


def test_unreadable_quality_is_reported_without_proposing_a_replacement(audit):
    audit.path.write_bytes(b"unreadable audio")
    _scan(audit)
    finding, = _findings(audit)
    assert finding["finding_type"] == "quality_unknown"
    assert audit.db.get_wishlist_count(profile_id=1) == 0


def test_audit_dismissal_survives_a_repeat_until_the_profile_changes(audit):
    _scan(audit)
    with audit.db._get_connection() as conn:
        conn.execute("UPDATE repair_findings SET status='dismissed'")
        conn.commit()
    assert _scan(audit).findings_created == 0
    with audit.db._get_connection() as conn:
        conn.execute("UPDATE quality_profiles SET upgrade_cutoff_index=1, ranked_targets=? WHERE id=?",
                     ('[{"format":"wav","min_sample_rate":192000},'
                      '{"format":"wav","min_sample_rate":96000}]', audit.profile))
        conn.commit()
    assert _scan(audit).findings_created == 1
    assert [row["status"] for row in _findings(audit)] == ["dismissed", "pending"]


def test_approval_opts_only_the_reviewed_track_into_the_existing_queue(audit):
    _scan(audit)
    finding, = _findings(audit)
    result = audit.worker._fix_quality_profile_audit(
        "track", finding["entity_id"], str(audit.path), json.loads(finding["details_json"]))
    assert result["success"] is True
    assert audit.db.get_wishlist_count(profile_id=1) == 1
    with audit.db._get_connection() as conn:
        assert conn.execute("SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1",
                            (audit.tid,)).fetchone()[0] == 1


def test_a_live_profile_change_makes_an_old_upgrade_proposal_inert(audit):
    _scan(audit)
    finding, = _findings(audit)
    with audit.db._get_connection() as conn:
        conn.execute("UPDATE quality_profiles SET upgrade_policy='none' WHERE id=?", (audit.profile,))
        conn.commit()
    result = audit.worker._fix_quality_profile_audit(
        "track", finding["entity_id"], str(audit.path), json.loads(finding["details_json"]))
    assert result["success"] is True
    assert result["action"] == "no_longer_candidate"
    assert audit.db.get_wishlist_count(profile_id=1) == 0


def test_a_stopped_audit_never_reports_completion(audit):
    audit.context.should_stop = lambda: True
    assert _scan(audit).stopped_early


def test_the_audit_uses_an_inherited_album_profile(audit):
    with audit.db._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET quality_profile_id=1, quality_profile_explicit=0 WHERE id=?",
                     (audit.tid,))
        conn.execute("UPDATE lib2_albums SET quality_profile_id=?, quality_profile_explicit=1",
                     (audit.profile,))
        conn.commit()
    _scan(audit)
    finding, = _findings(audit)
    assert json.loads(finding["details_json"])["quality_profile_id"] == audit.profile


def test_intentional_retention_does_not_repropose_the_acquisition(audit):
    with audit.db._get_connection() as conn:
        conn.execute("UPDATE lib2_track_files SET acquired_quality_json=?, retention_json=? WHERE track_id=?",
                     (json.dumps({"format": "wav", "sample_rate": 96000, "bit_depth": 16}),
                      json.dumps([{"type": "downsample_hires_flac", "source_replaced": True}]), audit.tid))
        conn.commit()
    assert _scan(audit).findings_created == 0
    assert _findings(audit) == []


def test_a_manual_quality_override_protects_scan_and_pending_approval(audit):
    from core.library2.manual_skips import record_manual_skip, attach_manual_skip_file

    _scan(audit)
    finding, = _findings(audit)
    record_manual_skip(audit.db, content_key="manual", title="Song", artist="Artist",
                       skipped_checks=["quality"], profile_id=1)
    assert attach_manual_skip_file(audit.db, content_key="manual", file_path=str(audit.path))
    assert _scan(audit).skipped == 1
    result = audit.worker._fix_quality_profile_audit(
        "track", finding["entity_id"], finding["file_path"], json.loads(finding["details_json"]))
    assert result["action"] == "ignored"
    assert audit.db.get_wishlist_count(profile_id=1) == 0


def test_automation_uses_native_findings_only_for_already_wanted_tracks(audit):
    from core.quality.upgrades import until_cutoff_finding_ids
    from core.library2.monitor_rules import record_rule, PROVENANCE_USER
    from core.library2.wanted import recompute_wanted

    _scan(audit)
    finding, = _findings(audit)
    assert until_cutoff_finding_ids(audit.db) == []
    with audit.db._get_connection() as conn:
        record_rule(conn, "track", audit.tid, True, PROVENANCE_USER)
        recompute_wanted(conn, track_ids=[audit.tid])
        conn.commit()
    assert until_cutoff_finding_ids(audit.db) == [finding["id"]]


def test_automation_never_uses_a_stale_reviewed_file(audit):
    from core.quality.upgrades import until_cutoff_finding_ids
    from core.library2.monitor_rules import record_rule, PROVENANCE_USER
    from core.library2.wanted import recompute_wanted

    _scan(audit)
    with audit.db._get_connection() as conn:
        record_rule(conn, "track", audit.tid, True, PROVENANCE_USER)
        recompute_wanted(conn, track_ids=[audit.tid])
        conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE track_id=?", (audit.tid,))
        conn.commit()
    assert until_cutoff_finding_ids(audit.db) == []


def test_the_apply_automation_runs_the_real_native_fix_path(audit):
    from core.automation.handlers.apply_quality_upgrades import auto_apply_quality_upgrades
    from core.library2.monitor_rules import record_rule, PROVENANCE_USER
    from core.library2.wanted import recompute_wanted

    _scan(audit)
    with audit.db._get_connection() as conn:
        record_rule(conn, "track", audit.tid, True, PROVENANCE_USER)
        recompute_wanted(conn, track_ids=[audit.tid])
        conn.commit()
    progress = []

    def apply(ids, fix_action=None):
        fixed = 0
        with audit.db._get_connection() as conn:
            for fid in ids:
                row = conn.execute("SELECT * FROM repair_findings WHERE id=?", (fid,)).fetchone()
                details = json.loads(row["details_json"])
                details['_fix_action'] = fix_action
                result = audit.worker._fix_quality_profile_audit(
                    row["entity_type"], row["entity_id"], row["file_path"], details)
                fixed += int(result["success"])
        return {"fixed": fixed, "failed": len(ids) - fixed}

    deps = SimpleNamespace(get_database=lambda: audit.db, bulk_fix_repair_findings=apply,
                           get_current_profile_id=lambda: 1,
                           update_progress=lambda *args, **kwargs: progress.append(kwargs))
    result = auto_apply_quality_upgrades({"_automation_id": "native", "limit": 1}, deps)
    assert result["applied"] == 1
    assert audit.db.get_wishlist_count(profile_id=1) == 1
    assert progress[-1]["status"] == "finished"


def _monitor(audit, wanted):
    from core.library2.monitor_rules import record_rule, PROVENANCE_USER
    from core.library2.wanted import recompute_wanted
    with audit.db._get_connection() as conn:
        record_rule(conn, "track", audit.tid, wanted, PROVENANCE_USER)
        recompute_wanted(conn, track_ids=[audit.tid])
        conn.commit()


def test_public_leave_as_is_never_runs_a_second_acquisition_pass(audit):
    with audit.db._get_connection() as conn:
        conn.execute("UPDATE quality_profiles SET ranked_targets=? WHERE id=?",
                     ('[{"format":"mp3","min_bitrate":320}]', audit.profile))
        conn.commit()
    _monitor(audit, True)
    _scan(audit)
    finding, = _findings(audit)
    result = audit.worker.fix_finding(finding["id"])
    assert result["success"] is True
    assert result["action"] == "ignored"
    assert audit.db.get_wishlist_count(profile_id=1) == 0
    with audit.db._get_connection() as conn:
        assert conn.execute("SELECT sample_rate FROM lib2_track_files WHERE track_id=?",
                            (audit.tid,)).fetchone()[0] == 96000


@pytest.mark.parametrize("change", ["unmonitor", "policy"])
def test_scheduled_application_respects_changes_after_selection(audit, change):
    from core.automation.handlers.apply_quality_upgrades import auto_apply_quality_upgrades

    _monitor(audit, True)
    _scan(audit)

    def apply(ids, **kwargs):
        if change == "unmonitor":
            _monitor(audit, False)
        else:
            with audit.db._get_connection() as conn:
                conn.execute("UPDATE quality_profiles SET upgrade_policy='acceptable' WHERE id=?",
                             (audit.profile,))
                conn.commit()
        return audit.worker.bulk_fix_findings(finding_ids=ids, **kwargs)

    deps = SimpleNamespace(get_database=lambda: audit.db, bulk_fix_repair_findings=apply,
                           get_current_profile_id=lambda: 1, update_progress=lambda *a, **kw: None)
    result = auto_apply_quality_upgrades({"_profile_id": 1}, deps)
    assert result["applied"] == 0
    assert audit.db.get_wishlist_count(profile_id=1) == 0
    assert _findings(audit)[0]["status"] == "pending"
    if change == "unmonitor":
        with audit.db._get_connection() as conn:
            assert conn.execute("SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1",
                                (audit.tid,)).fetchone()[0] == 0


def test_explicit_review_approval_readds_a_previously_cancelled_upgrade(audit):
    from core.library2.wishlist_mirror import track_wishlist_payload

    _scan(audit)
    finding, = _findings(audit)
    with audit.db._get_connection() as conn:
        key = track_wishlist_payload(conn, audit.tid)["id"]
    assert audit.db.add_to_wishlist_ignore(key, profile_id=1)
    result = audit.worker.fix_finding(finding["id"])
    assert result["success"] is True
    assert audit.db.get_wishlist_count(profile_id=1) == 1
    assert not audit.db.is_track_ignored(key, profile_id=1)


def test_a_skipped_mirror_add_is_not_reported_as_a_queued_upgrade(audit):
    _scan(audit)
    finding, = _findings(audit)
    # The wishlist deliberately skips a newly blocklisted artist.
    assert audit.db.add_blocklist_entry(1, "artist", "Artist")
    result = audit.worker.fix_finding(finding["id"])
    assert result["success"] is False
    assert _findings(audit)[0]["status"] == "pending"
    assert audit.db.get_wishlist_count(profile_id=1) == 0
