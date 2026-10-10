"""The native scan reports stable, approvable reviews without file writes."""

from contextlib import closing

from core.repair_jobs.base import JobContext
from tests.library2.test_native_duplicate_review import library, copy, states  # noqa: F401


def test_job_creates_native_reviews_with_file_ids_and_never_applies(library):
    from core.repair_jobs.native_duplicate_detector import NativeDuplicateDetectorJob

    _a, af, pa = copy(library, album="Single", fmt="mp3")
    _b, bf, pb = copy(library, album="Album", fmt="flac")
    findings = []
    def create(**finding):
        findings.append(finding)
        return True
    context = JobContext(db=library[0], config_manager=library[1],
                         transfer_folder=str(library[3]), create_finding=create,
                         playlist_membership=lambda: {})
    result = NativeDuplicateDetectorJob().scan(context)
    assert result.scanned == 2
    assert result.findings_created == 1 and result.auto_fixed == 0
    assert pa.exists() and pb.exists()
    assert set(states(library).values()) == {"active"}
    finding = findings[0]
    assert finding["entity_id"].startswith("lib2:")
    assert finding["finding_type"] == "native_duplicate_tracks"
    assert finding["details"]["recommended_file_id"] == bf
    assert {row["file_id"] for row in finding["details"]["tracks"]} == {af, bf}


def test_job_reports_dedup_stop_and_scope_without_touching_other_artists(library):
    from core.repair_jobs.native_duplicate_detector import NativeDuplicateDetectorJob

    _a, _af, pa = copy(library)
    _b, _bf, pb = copy(library, album="Other")
    copy(library, artist="Someone Else", album="One")
    copy(library, artist="Someone Else", album="Two")
    context = JobContext(db=library[0], config_manager=library[1], transfer_folder=str(library[3]),
                         scope={"file_paths": [str(pa), str(pb)]}, create_finding=lambda **_: False,
                         playlist_membership=lambda: {})
    job = NativeDuplicateDetectorJob()
    assert job.estimate_scope(context) == 2
    result = job.scan(context)
    assert result.scanned == 2 and result.findings_skipped_dedup == 1
    context.should_stop = lambda: True
    result = job.scan(context)
    assert result.scanned == result.findings_created == 0


def test_worker_rejects_legacy_ids_and_honors_exact_file_selection(library, monkeypatch):
    from core.repair_worker import RepairWorker
    from core.library2.duplicate_review import find_duplicate_candidates

    a, af, pa = copy(library)
    _b, bf, pb = copy(library, album="Other", bit_depth=24)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET track_id=? WHERE id=?", (a, bf))
        conn.commit()
    candidate = find_duplicate_candidates(library[0], library[1], playlist_membership={})[0]
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = library[0], library[1], str(library[3])
    # Prevent optional media-server discovery; all other apply behavior is real.
    import core.library.playlist_membership as playlists
    monkeypatch.setattr(playlists, "_active_server_and_client", lambda: (None, None))
    assert not worker._execute_fix("native_duplicate_tracks", "track", str(a), str(pa),
                                   {"tracks": [{"id": a}], "_fix_action": f"file-{af}"})["success"]
    result = worker._execute_fix("native_duplicate_tracks", "track", f"lib2:{a}", str(pa),
                                 {**candidate, "_fix_action": f"file-{af}"})
    assert result["success"] is True
    assert result["kept_file_id"] == af
    assert pa.exists() and not pb.exists()


def test_worker_confirmed_action_applies_fuzzy_review_and_unknown_ids_fail(library, monkeypatch):
    from core.repair_worker import RepairWorker
    from core.library2.duplicate_review import find_duplicate_candidates

    a, af, pa = copy(library, isrc=None)
    _b, _bf, pb = copy(library, album="Other", isrc=None)
    candidate = find_duplicate_candidates(library[0], library[1], playlist_membership={})[0]
    worker = RepairWorker.__new__(RepairWorker)
    worker.db, worker._config_manager, worker.transfer_folder = library[0], library[1], str(library[3])
    import core.library.playlist_membership as playlists
    monkeypatch.setattr(playlists, "_active_server_and_client", lambda: (None, None))
    for action in ("file-99999", "track-99999", "keep_best"):
        result = worker._execute_fix("native_duplicate_tracks", "track", f"lib2:{a}", str(pa),
                                     {**candidate, "_fix_action": action})
        assert result["success"] is False
        assert pa.exists() and pb.exists()
    result = worker._execute_fix("native_duplicate_tracks", "track", f"lib2:{a}", str(pa),
                                 {**candidate, "_fix_action": f"file-{af}:confirmed"})
    assert result["success"] is True and pa.exists() and not pb.exists()


def test_registry_and_saved_duplicate_settings_resolve_to_native_review_job():
    from core.repair_jobs import get_all_jobs, JOB_ID_MIGRATIONS
    from core.repair_worker import RepairWorker

    jobs = get_all_jobs()
    cls = jobs[JOB_ID_MIGRATIONS['duplicate_detector']]
    assert cls.data_basis == 'lib2'
    assert cls.library_v2_effects == frozenset({'observe', 'metadata', 'delete', 'wanted'})
    assert cls.auto_fix is False
    class SavedConfig:
        def __init__(self):
            self.values = {'repair.jobs.duplicate_detector': {
                'enabled': True, 'interval_hours': 48,
                'settings': {'title_similarity': 0.9, 'artist_similarity': 0.75, 'ignore_cross_album': True},
            }}
        def get(self, key, default=None):
            return self.values.get(key, default)
        def set(self, key, value):
            self.values[key] = value
    config = SavedConfig()
    RepairWorker.__new__(RepairWorker)._migrate_legacy_job_configs(config)
    assert config.values['repair.jobs.native_duplicate_detector'] == config.values['repair.jobs.duplicate_detector']


def test_full_worker_fix_keeps_keeper_active_and_failed_approval_pending(library, monkeypatch):
    import json
    import wave
    from database.music_database import MusicDatabase
    from core.repair_worker import RepairWorker
    from core.library2.duplicate_review import find_duplicate_candidates

    a, af, pa = copy(library, fmt='wav')
    _b, bf, pb = copy(library, album='Other', fmt='wav')
    for path in (pa, pb):
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b'\x00\x00' * 8000)
    db = MusicDatabase(library[0].path)
    candidate = find_duplicate_candidates(db, library[1], playlist_membership={})[0]
    with closing(db._get_connection()) as conn:
        fid = conn.execute("INSERT INTO repair_findings(job_id,finding_type,severity,entity_type,entity_id,file_path,title,details_json) VALUES('native_duplicate_detector','native_duplicate_tracks','info','track',?,?,'Duplicate',?)",
                           (f'lib2:{a}', str(pa), json.dumps(candidate))).lastrowid
        conn.commit()
    worker = RepairWorker.__new__(RepairWorker)
    library[1].values['soulseek.transfer_path'] = str(library[3])
    worker.db, worker._config_manager, worker.transfer_folder = db, library[1], str(library[3])
    import core.library.playlist_membership as playlists
    monkeypatch.setattr(playlists, '_active_server_and_client', lambda: (None, None))
    denied = worker.fix_finding(fid)
    assert denied['success'] is False
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT status FROM repair_findings WHERE id=?', (fid,)).fetchone()[0] == 'pending'
    result = worker.fix_finding(fid, f'file-{af}')
    assert result['success'] is True, result
    assert pa.exists() and not pb.exists()
    with closing(db._get_connection()) as conn:
        assert conn.execute('SELECT file_state FROM lib2_track_files WHERE id=?', (af,)).fetchone()[0] == 'active'
        assert conn.execute('SELECT file_state FROM lib2_track_files WHERE id=?', (bf,)).fetchone()[0] == 'deleted'
        assert conn.execute('SELECT status FROM repair_findings WHERE id=?', (fid,)).fetchone()[0] == 'resolved'
