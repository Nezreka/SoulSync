import sqlite3
from types import SimpleNamespace

from core.repair_jobs.base import JobContext
from core.repair_jobs.metadata_gap_filler import MetadataGapFillerJob


def test_metadata_gap_filler_reaches_subjects_after_500(monkeypatch):
    subjects = [
        {
            "track_id": track_id,
            "title": f"Track {track_id}",
            "artist_name": "Artist",
            "isrc": None,
            "track_source_ids": {},
        }
        for track_id in range(1, 1002)
    ]
    monkeypatch.setattr(
        "core.repair_jobs.metadata_gap_filler.active_file_subjects",
        lambda _db, _config: subjects,
    )
    progress = []
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    context = JobContext(
        db=SimpleNamespace(_get_connection=lambda: conn),
        transfer_folder="",
        config_manager=SimpleNamespace(get=lambda _key, default=None: default),
        update_progress=lambda done, total: progress.append((done, total)),
    )
    job = MetadataGapFillerJob()

    results = [job.scan(context) for _ in range(3)]

    assert [result.scanned for result in results] == [500, 500, 1]
    assert sum(result.skipped for result in results) == 1001
    assert job.estimate_scope(context) == 1001
    assert progress[-1] == (1, 1)
    assert conn.execute(
        "SELECT value FROM metadata WHERE key LIKE 'repair.metadata_gap.cursor:%'"
    ).fetchone()[0] == "1001"
