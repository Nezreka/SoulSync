"""Naming Conformance job — retroactive renames from the naming templates.

Findings ARE the preview (current → expected from the CURRENT templates);
approve renames the file + same-stem sidecars and tidies the emptied folder.
Files that can't be located locally are skipped; an occupied destination is a
per-finding error (nothing overwritten); DB paths are left for the next scan
to reconcile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_REPAIR_JS = (_ROOT / "webui" / "static" / "video" / "video-repair.js").read_text(encoding="utf-8")


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
    assert "naming_mismatch: 'Naming'" in _REPAIR_JS
    assert "naming_mismatch: 'Rename'" in _REPAIR_JS
    assert "renamed: 'Renamed'" in _REPAIR_JS
    assert "function namingDetailHTML" in _REPAIR_JS
