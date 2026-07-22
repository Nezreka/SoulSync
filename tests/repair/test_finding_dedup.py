from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _create(worker, path, details):
    return worker._create_finding(
        job_id="quality_upgrade_scan",
        finding_type="quality_below_cutoff",
        severity="info",
        entity_type="track",
        entity_id="lib2:7",
        file_path=str(path),
        title="Below cutoff",
        description="test",
        details=details,
    )


def test_file_findings_dedup_per_file_and_fingerprint(tmp_path):
    database = MusicDatabase(str(tmp_path / "repair.sqlite"))
    worker = RepairWorker(database)
    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    assert _create(worker, first, {"profile_id": 1, "target": "flac"}) is True
    assert _create(worker, second, {"profile_id": 1, "target": "flac"}) is True

    conn = database._get_connection()
    try:
        conn.execute(
            "UPDATE repair_findings SET status='dismissed' WHERE file_path=?",
            (str(first),),
        )
        conn.commit()
    finally:
        conn.close()

    assert _create(worker, first, {"profile_id": 1, "target": "flac"}) is False
    assert _create(worker, first, {"profile_id": 2, "target": "24bit-flac"}) is True


def test_failed_library_sync_keeps_successful_physical_fix_pending(
    tmp_path, monkeypatch
):
    database = MusicDatabase(str(tmp_path / "sync-failure.sqlite"))
    worker = RepairWorker(database)
    target = tmp_path / "broken.flac"
    target.write_bytes(b"broken")
    assert worker._create_finding(
        job_id="audio_corruption_detector", finding_type="corrupt_audio",
        severity="warning", entity_type="track", entity_id="lib2:9",
        file_path=str(target), title="Corrupt", description="test", details={},
    )
    conn = database._get_connection()
    try:
        finding_id = conn.execute(
            "SELECT id FROM repair_findings WHERE file_path=?", (str(target),)
        ).fetchone()[0]
    finally:
        conn.close()
    monkeypatch.setattr(
        worker, "_execute_fix",
        lambda *_args, **_kwargs: {"success": True, "action": "redownload"},
    )
    monkeypatch.setattr(
        "core.library2.maintenance_sync.sync_repair_change",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sync down")),
    )

    result = worker.fix_finding(finding_id)

    assert result["success"] is False
    assert result["retryable"] is True
    conn = database._get_connection()
    try:
        assert conn.execute(
            "SELECT status FROM repair_findings WHERE id=?", (finding_id,)
        ).fetchone()[0] == "pending"
    finally:
        conn.close()
