"""Live/Commentary Cleaner's "albums" scope: one finding per live album.

The setting was still offered but ignored, so a 20-track live album came back
as 20 findings. The album finding names every file it holds, and removing it
retires exactly those files in the catalogue.
"""

from contextlib import closing
from types import SimpleNamespace

from core.repair_jobs.base import JobContext
from core.repair_jobs.live_commentary_cleaner import LiveCommentaryCleanerJob
from database.music_database import MusicDatabase
from tests import lib2_seed


def _library(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    paths = []
    with closing(db._get_connection()) as conn:
        for n, title in enumerate(("Even Flow", "Alive", "Black"), start=1):
            path = tmp_path / "music" / "Pearl Jam" / "Live at Benaroya Hall" / f"0{n}.flac"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            paths.append(str(path))
            lib2_seed.track(conn, "Pearl Jam", "Live at Benaroya Hall", title,
                            path=str(path), track_number=n)
        studio = tmp_path / "music" / "Pearl Jam" / "Ten" / "01.flac"
        studio.parent.mkdir(parents=True, exist_ok=True)
        studio.write_bytes(b"audio")
        lib2_seed.track(conn, "Pearl Jam", "Ten", "Once", path=str(studio))
        conn.commit()
    return db, paths


def _scan(db, scope):
    findings = []
    settings = {"scope": scope}
    cfg = SimpleNamespace(get=lambda key, default=None: settings if key.endswith(".settings") else default)
    LiveCommentaryCleanerJob().scan(JobContext(
        db, "/music", cfg, create_finding=lambda **kw: findings.append(kw) or True))
    return findings


def test_album_scope_files_one_finding_for_the_whole_live_album(tmp_path):
    db, paths = _library(tmp_path)

    findings = _scan(db, "albums")

    assert len(findings) == 1
    finding = findings[0]
    assert finding["entity_type"] == "album"
    assert sorted(f["file_path"] for f in finding["details"]["album_files"]) == sorted(paths)
    assert len(finding["details"]["library_v2"]["file_ids"]) == 3


def test_track_scope_still_files_one_per_track(tmp_path):
    db, _paths = _library(tmp_path)
    assert len(_scan(db, "tracks")) == 3


def test_removing_the_album_retires_exactly_its_files(tmp_path):
    from core.library2.maintenance_sync import sync_repair_change
    from core.repair_worker import RepairWorker

    db, paths = _library(tmp_path)
    finding = _scan(db, "albums")[0]
    worker = RepairWorker(database=db)
    removed = []
    worker._remove_native_repair_file = lambda path, details, **kw: (
        removed.append(path) or {"success": True, "deleted_file": True})

    result = worker._fix_unwanted_content(
        finding["entity_type"], finding["entity_id"], finding["file_path"], finding["details"])
    assert result["success"] is True
    assert sorted(removed) == sorted(paths)

    sync_repair_change(
        db, SimpleNamespace(get=lambda key, default=None: default),
        job_id="live_commentary_cleaner", finding_type="unwanted_content",
        action=result["action"], entity_type="album", entity_id=finding["entity_id"],
        file_path=None, details=finding["details"], result=result)
    with closing(db._get_connection()) as conn:
        states = dict(conn.execute(
            "SELECT path, COALESCE(file_state,'active') FROM lib2_track_files").fetchall())
    assert all(states[p] == "deleted" for p in paths)
    assert [s for p, s in states.items() if p not in paths] == ["active"]  # the studio album
