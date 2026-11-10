"""Unknown Artist recovery per track on Library v2 (feature-parity A03).

A placeholder holding tracks by two different artists is split: each track is
identified from its own evidence and re-filed under its own artist."""

from __future__ import annotations

from types import SimpleNamespace

from core.repair_jobs import get_all_jobs
from core.repair_jobs.base import JobContext
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests import lib2_seed


def _world(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "music.db"))
    files = {}
    with db._get_connection() as conn:
        for title, performer in (("Teardrop", "Massive Attack"), ("Roads", "Portishead")):
            path = tmp_path / f"{title}.flac"
            path.write_bytes(b"audio")
            files[title] = (lib2_seed.track(conn, "Unknown Artist", "Unknown Album", title,
                                            path=str(path)), performer)
        conn.commit()
    tags = {str(tmp_path / f"{t}.flac"): {"artist": p, "album": f"{p} LP", "title": t,
                                           "track_number": 1}
            for t, (_id, p) in files.items()}
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda path: tags.get(path, {}))
    monkeypatch.setattr("core.tag_writer.write_tags_to_file", lambda *a, **k: True)
    return db, files


def _context(db, findings, dry_run=True):
    cfg = SimpleNamespace(get=lambda key, default=None: (
        {"dry_run": dry_run} if key.endswith(".settings") else default))
    return JobContext(db=db, transfer_folder="", config_manager=cfg,
                      create_finding=lambda **kw: findings.append(kw) or True)


def test_the_job_is_registered_again():
    assert "unknown_artist_fixer" in get_all_jobs()


def test_a_mixed_placeholder_is_split_per_track(tmp_path, monkeypatch):
    db, files = _world(tmp_path, monkeypatch)
    findings = []
    job = get_all_jobs()["unknown_artist_fixer"]()

    result = job.scan(_context(db, findings))

    assert result.findings_created == 2
    assert {f["details"]["corrected_artist"] for f in findings} == {"Massive Attack", "Portishead"}

    worker = RepairWorker(db)
    for finding in findings:
        outcome = worker._execute_fix("unknown_artist", "track", finding["entity_id"],
                                      finding["file_path"], finding["details"])
        assert outcome["success"], outcome

    with db._get_connection() as conn:
        rows = {r[0]: r[1] for r in conn.execute(
            """SELECT t.title, ar.name FROM lib2_track_files f
                 JOIN lib2_tracks t ON t.id=f.track_id
                 JOIN lib2_albums al ON al.id=t.album_id
                 JOIN lib2_artists ar ON ar.id=al.primary_artist_id""")}
        placeholder_tracks = conn.execute(
            "SELECT COUNT(*) FROM lib2_tracks WHERE id IN (?, ?)",
            tuple(track_id for track_id, _ in files.values())).fetchone()[0]
    assert rows == {"Teardrop": "Massive Attack", "Roads": "Portishead"}
    assert placeholder_tracks == 0


def test_with_dry_run_off_a_run_fixes_by_itself(tmp_path, monkeypatch):
    db, _files = _world(tmp_path, monkeypatch)
    job = get_all_jobs()["unknown_artist_fixer"]()

    result = job.scan(_context(db, [], dry_run=False))

    assert result.auto_fixed == 2
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM lib2_artists WHERE name='Portishead'"
                            ).fetchone()[0] == 1


def test_a_scheduled_fix_moves_and_syncs_the_refiled_track(tmp_path, monkeypatch):
    """dry run off: the fix re-homes the file onto a new track and deletes the
    placeholder row. the move and the library sync must use the new id, or
    the file stays in the Unknown Artist folder (3.6.1's live mode moved it)."""
    db, files = _world(tmp_path, monkeypatch)
    queued, changes = [], []
    monkeypatch.setattr("core.library2.reorganize_bridge.enqueue_album_reorganize",
                        lambda _db, album_id: queued.append(album_id))
    cfg = SimpleNamespace(get=lambda key, default=None: (
        {"dry_run": False} if key.endswith(".settings") else default))
    context = JobContext(db=db, transfer_folder=str(tmp_path), config_manager=cfg,
                         create_finding=lambda **kw: True,
                         report_change=lambda **change: changes.append(change))

    result = get_all_jobs()["unknown_artist_fixer"]().scan(context)

    assert result.auto_fixed == 2
    with db._get_connection() as conn:
        albums = {r[0] for r in conn.execute(
            "SELECT al.id FROM lib2_albums al JOIN lib2_artists ar ON ar.id=al.primary_artist_id "
            "WHERE ar.name IN ('Massive Attack', 'Portishead')")}
        live = {r[0] for r in conn.execute("SELECT track_id FROM lib2_track_files")}
    assert set(queued) == albums and len(queued) == 2
    assert {c["entity_id"] for c in changes} == {f"lib2:{t}" for t in live}
    assert all(c["action"] == "identified_artist" for c in changes)
    old_ids = {f"lib2:{track_id}" for track_id, _ in files.values()}
    assert not old_ids & {c["entity_id"] for c in changes}
