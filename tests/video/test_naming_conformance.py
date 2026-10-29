"""Naming Conformance job — retroactive renames from the naming templates.

Findings ARE the preview (current → expected from the CURRENT templates);
approve renames the file + same-stem sidecars and tidies the emptied folder.
Files that can't be located locally are skipped; an occupied destination is a
per-finding error (nothing overwritten); the DB's stored path follows the
rename (mirrored onto the server's prefix, like Mass Rename), and '$quality'
is read off the file's own name when the library recorded none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_DETAIL_TSX = (_ROOT / "webui" / "src" / "routes" / "tools" / "-ui" / "video-finding-detail.tsx").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.set_setting("movies_path", str(tmp_path / "Movies"))
    d.set_setting("tv_path", str(tmp_path / "TV"))
    return d


@pytest.fixture()
def worker(db):
    return VideoRepairWorker(db)


def _seed_movie_file(db, tmp_path, *, title, year, rel, quality="1080p"):
    mid = db.upsert_movie("plex", {"server_id": "sv-" + title, "title": title,
                                   "year": year, "tmdb_id": abs(hash(title)) % 10 ** 6})
    real = tmp_path / rel
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"x" * 32)
    conn = db._get_connection()
    conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes, quality) "
                 "VALUES (?,?,?,?)", (mid, str(real), 32, quality))
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    conn.commit(); conn.close()
    return mid, real


def _pending(db):
    return [f for f in db.repair_get_findings(status="pending")["items"]
            if f["finding_type"] == "naming_mismatch"]


def test_scan_flags_only_nonconforming_files(db, worker, tmp_path):
    # conforming: exactly what the default template renders
    _seed_movie_file(db, tmp_path, title="Heat", year=1995,
                     rel="Movies/Heat (1995)/Heat (1995) 1080p.mkv")
    # nonconforming: old flat layout
    _mid, messy = _seed_movie_file(db, tmp_path, title="Ronin", year=1998,
                                   rel="Movies/ronin.1998.mkv")
    worker._run_job("naming_conformance", forced=True)
    items = _pending(db)
    assert len(items) == 1
    d = items[0]["details"]
    assert d["current_path"] == str(messy)
    assert d["expected_path"].endswith("Ronin (1998) 1080p.mkv".replace("/", "" if "/" == "\\" else "/"))
    assert "Ronin (1998)" in d["expected_path"]


def test_fix_renames_file_sidecars_and_tidies_the_old_folder(db, worker, tmp_path):
    _mid, messy = _seed_movie_file(db, tmp_path, title="Ronin", year=1998,
                                   rel="Movies/Old Junk/ronin.1998.mkv")
    stem = messy.with_suffix("")
    Path(str(stem) + ".en.srt").write_text("subs")
    Path(str(stem) + "-thumb.jpg").write_bytes(b"jpg")
    Path(str(stem) + ".nfo").write_text("<movie/>")

    worker._run_job("naming_conformance", forced=True)
    f = _pending(db)[0]
    res = worker.fix_finding(f["id"])
    assert res["success"] and res["action"] == "renamed" and "+3 sidecars" in res["message"]

    new = Path(f["details"]["expected_path"])
    assert new.exists() and not messy.exists()
    new_stem = str(new.with_suffix(""))
    assert Path(new_stem + ".en.srt").exists()
    assert Path(new_stem + "-thumb.jpg").exists()
    assert Path(new_stem + ".nfo").exists()
    assert not messy.parent.exists()               # emptied folder tidied


def test_fix_never_overwrites_an_occupied_destination(db, worker, tmp_path):
    _seed_movie_file(db, tmp_path, title="Ronin", year=1998, rel="Movies/ronin.1998.mkv")
    worker._run_job("naming_conformance", forced=True)
    f = _pending(db)[0]
    blocker = Path(f["details"]["expected_path"])
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_bytes(b"already here")
    res = worker.fix_finding(f["id"])
    assert res["success"] is False and "overwritten" in res["error"]
    assert Path(f["details"]["current_path"]).exists()   # untouched
    assert blocker.read_bytes() == b"already here"


def test_template_change_reflags_and_conforming_retires(db, worker, tmp_path):
    from core.video import organization
    _seed_movie_file(db, tmp_path, title="Heat", year=1995,
                     rel="Movies/Heat (1995)/Heat (1995) 1080p.mkv")
    worker._run_job("naming_conformance", forced=True)
    assert _pending(db) == []                       # conforming today

    organization.save(db, {**organization.load(db), "movie_template": "$title/$title $year"})
    worker._run_job("naming_conformance", forced=True)
    assert len(_pending(db)) == 1                   # new template → now a mismatch

    organization.save(db, {**organization.load(db),
                           "movie_template": "$title ($year)/$title ($year) $quality"})
    worker._run_job("naming_conformance", forced=True)
    assert _pending(db) == []                       # back to conforming → retired


def _age_handled(db, fid, days=30):
    conn = db._get_connection()
    try:
        conn.execute("UPDATE video_repair_findings "
                     "SET resolved_at=datetime('now', ?) WHERE id=?",
                     (f"-{days} days", fid))
        conn.commit()
    finally:
        conn.close()


def test_template_change_after_approve_reflags_immediately(db, worker, tmp_path):
    """#1882: a finding resolved under the old template must not block the
    new one — the expected path changed, so the claim changed."""
    from core.video import organization
    _seed_movie_file(db, tmp_path, title="Ronin", year=1998,
                     rel="Movies/ronin.1998.mkv")
    worker._run_job("naming_conformance", forced=True)
    f = _pending(db)[0]
    assert worker.fix_finding(f["id"])["success"]           # resolved, in grace
    worker._run_job("naming_conformance", forced=True)
    assert _pending(db) == []                               # conforms now

    organization.save(db, {**organization.load(db), "movie_template": "$title/$title $year"})
    worker._run_job("naming_conformance", forced=True)
    assert len(_pending(db)) == 1                           # re-flagged at once


def test_wrong_rename_comes_back_after_grace(db, worker, tmp_path):
    """#1882: the file a fix placed gets renamed badly again (outside
    SoulSync, same template): the resolved finding holds the re-raise off
    for the grace window, then a re-scan flags it again."""
    mid, messy = _seed_movie_file(db, tmp_path, title="Ronin", year=1998,
                                  rel="Movies/ronin.1998.mkv")
    worker._run_job("naming_conformance", forced=True)
    f = _pending(db)[0]
    assert worker.fix_finding(f["id"])["success"]
    fid = f["id"]

    fixed = Path(f["details"]["expected_path"])
    fixed.rename(messy)                                     # bad rename returns
    conn = db._get_connection()
    conn.execute("UPDATE media_files SET relative_path=? WHERE movie_id=?",
                 (str(messy), mid))
    conn.commit(); conn.close()

    worker._run_job("naming_conformance", forced=True)
    assert _pending(db) == []                               # still inside grace
    _age_handled(db, fid)
    worker._run_job("naming_conformance", forced=True)
    assert len(_pending(db)) == 1                           # flagged again


def test_unlocatable_files_are_skipped_never_guessed(db, worker, tmp_path):
    mid = db.upsert_movie("plex", {"server_id": "svx", "title": "Ghost", "tmdb_id": 1})
    conn = db._get_connection()
    conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes) "
                 "VALUES (?,?,?)", (mid, "/mnt/plex-docker/Ghost/ghost.mkv", 99))
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    conn.commit(); conn.close()
    worker._run_job("naming_conformance", forced=True)
    assert _pending(db) == []


def test_surfaces_are_wired():
    from core.video.repair import get_all_jobs
    assert "naming_conformance" in get_all_jobs()
    from core.automation.blocks import blocks_for_scope
    block = next(b for b in blocks_for_scope("video")["actions"]
                 if b["type"] == "video_run_repair_job")
    assert "naming_conformance" in [o["value"] for o in block["config_fields"][0]["options"]]
    # the tools page is React now: labels come from the served catalog, the
    # panel from video-finding-detail.tsx
    from core.video.repair.catalog import VIDEO_ACTION_LABELS, VIDEO_FINDING_TYPE_META
    assert VIDEO_FINDING_TYPE_META["naming_mismatch"]["label"] == "Naming"
    assert VIDEO_FINDING_TYPE_META["naming_mismatch"]["verb"] == "Rename"
    assert VIDEO_ACTION_LABELS["renamed"] == "Renamed"
    assert "function NamingDetail(" in _DETAIL_TSX
    assert "type === 'naming_mismatch'" in _DETAIL_TSX


def test_renaming_an_extra_library_keeps_the_file_on_its_drive(db, worker, tmp_path):
    import json
    root = tmp_path / "Movies2"
    db.set_setting("movies_additional_paths", json.dumps([str(root)]))
    _, old = _seed_movie_file(db, tmp_path, title="Ronin", year=1998, rel="Movies2/old.mkv")
    worker._run_job("naming_conformance", forced=True)
    finding = _pending(db)[0]
    destination = Path(finding["details"]["expected_path"])
    assert destination.is_relative_to(root)
    assert worker.fix_finding(finding["id"])["success"]
    assert destination.exists() and not old.exists()


# ── the quality a scan never records, and the stored path a rename leaves ────
def test_quality_is_read_off_the_files_name_when_the_library_has_none(db, worker, tmp_path):
    """A media-server scan stores no quality: the proposal used to strip the tag."""
    from core.video import organization
    organization.save(db, {**organization.load(db),
                           "movie_template": "$title ($year)/$title ($year) [$quality]"})
    _seed_movie_file(db, tmp_path, title="Braveheart", year=1995, quality=None,
                     rel="Movies/Braveheart (1995)/Braveheart (1995) [Bluray-2160p Proper].mkv")
    _mid, messy = _seed_movie_file(db, tmp_path, title="Ronin", year=1998, quality=None,
                                   rel="Movies/ronin.1998.1080p.bluray.x264-GRP.mkv")
    worker._run_job("naming_conformance", forced=True)
    items = _pending(db)
    assert [Path(f["details"]["current_path"]) for f in items] == [messy]   # Braveheart conforms
    assert items[0]["details"]["expected_path"].endswith("Ronin (1998) [Bluray-1080p].mkv")


def test_a_stored_quality_still_wins(db, worker, tmp_path):
    from core.video import organization
    organization.save(db, {**organization.load(db),
                           "movie_template": "$title ($year)/$title ($year) [$quality]"})
    _seed_movie_file(db, tmp_path, title="Heat", year=1995, quality="Remux-2160p",
                     rel="Movies/heat.1995.1080p.bluray.mkv")
    worker._run_job("naming_conformance", forced=True)
    assert _pending(db)[0]["details"]["expected_path"].endswith("Heat (1995) [Remux-2160p].mkv")


def test_fix_moves_the_stored_path_with_the_file(db, worker, tmp_path):
    mid, messy = _seed_movie_file(db, tmp_path, title="Ronin", year=1998,
                                  rel="Movies/Old Junk/ronin.1998.mkv")
    worker._run_job("naming_conformance", forced=True)
    f = _pending(db)[0]
    assert worker.fix_finding(f["id"])["success"]
    conn = db._get_connection()
    stored = conn.execute("SELECT relative_path FROM media_files WHERE movie_id=?",
                          (mid,)).fetchone()[0]
    conn.close()
    assert stored == f["details"]["expected_path"]
    from core.video.path_resolver import resolve_video_file_path, video_base_dirs
    assert resolve_video_file_path(stored, video_base_dirs(db), size_bytes=32) == stored


def test_fix_maps_the_new_name_onto_the_servers_prefix(db, worker, tmp_path):
    """The server sees the library under its own mount (Plex on another host)."""
    mid, messy = _seed_movie_file(db, tmp_path, title="Ronin", year=1998,
                                  rel="Movies/Old Junk/ronin.1998.mkv")
    server = "Y:\\media\\screens\\movies\\Old Junk\\ronin.1998.mkv"
    conn = db._get_connection()
    conn.execute("UPDATE media_files SET relative_path=? WHERE movie_id=?", (server, mid))
    conn.commit(); conn.close()
    worker._run_job("naming_conformance", forced=True)
    f = _pending(db)[0]
    assert worker.fix_finding(f["id"])["success"]
    conn = db._get_connection()
    stored = conn.execute("SELECT relative_path FROM media_files WHERE movie_id=?",
                          (mid,)).fetchone()[0]
    conn.close()
    assert stored == "Y:/media/screens/movies/Ronin (1998)/Ronin (1998) 1080p.mkv"
