"""A usenet grab no longer leaves an empty release folder behind.

The importer removes the downloaded video once the import row is persisted,
but the job's own folder in the client's completed folder stayed, one per grab.
Now a completed usenet import carries its job folder (transient
``_cleanup_dir``) and the monitor removes it after the source file, but only
the empty parts: anything left in it keeps the folder. Torrents never get one
(still seeding), and configured roots are never removed.
"""

from __future__ import annotations

import json
import os
from unittest.mock import Mock

import core.video.client_download as cd
from core.video import download_monitor as monitor


class _St:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _job(tmp_path, name="Some.Show.S01E01.1080p.WEB-DL"):
    job = tmp_path / "complete" / "soulsync" / name
    job.mkdir(parents=True)
    video = job / "some.show.s01e01.1080p.mkv"
    video.write_bytes(b"x" * 1000)
    return job, video


def _done(dl, status, organizer, import_pack=None):
    return cd.process_client_download(
        dl, get_status=lambda s, r: status, resolve_path=lambda p: p,
        find_video=cd.find_video_file, organizer=organizer, import_pack=import_pack)


def _completed(dl, src):
    return {"status": "completed", "progress": 100.0, "dest_path": "/lib/x.mkv",
            "_cleanup_source": src}


# ── which imports carry their job folder ──────────────────────────────────────

def test_a_completed_usenet_import_carries_its_job_folder(tmp_path):
    job, video = _job(tmp_path)
    status = _St(state="completed", progress=1.0, save_path=str(job), name=job.name)
    upd = _done({"client_ref": "SABnzbd_nzo_1", "source": "usenet", "id": 1}, status, _completed)
    assert upd["_cleanup_source"] == str(video)
    assert upd["_cleanup_dir"] == str(job)


def test_a_shared_parent_is_scoped_to_the_job_folder(tmp_path):
    job, _video = _job(tmp_path)
    shared = job.parent
    status = _St(state="completed", progress=1.0, save_path=str(shared), name=job.name)
    upd = _done({"client_ref": "n1", "source": "usenet", "id": 2}, status, _completed)
    assert upd["_cleanup_dir"] == str(job)                     # never the shared parent


def test_a_failed_import_keeps_its_folder(tmp_path):
    job, video = _job(tmp_path)
    status = _St(state="completed", progress=1.0, save_path=str(job), name=job.name)
    upd = _done({"client_ref": "n1", "source": "usenet", "id": 3}, status,
                lambda dl, src: {"status": "import_failed", "dest_path": src, "error": "x"})
    assert "_cleanup_dir" not in upd                           # the user still needs the file


def test_a_torrent_never_carries_a_folder(tmp_path):
    job, _video = _job(tmp_path)
    status = _St(state="seeding", progress=1.0, content_path=str(job), save_path=str(job.parent))
    upd = _done({"client_ref": "h1", "source": "torrent", "id": 4}, status, _completed)
    assert upd["status"] == "completed" and "_cleanup_dir" not in upd


def test_a_file_from_outside_the_job_folder_is_not_tagged(tmp_path):
    job, _video = _job(tmp_path)
    elsewhere = tmp_path / "other.mkv"
    elsewhere.write_bytes(b"y")
    upd = cd.process_client_download(
        {"client_ref": "n1", "source": "usenet", "id": 5},
        get_status=lambda s, r: _St(state="completed", progress=1.0, save_path=str(job), name=job.name),
        resolve_path=lambda p: p, find_video=lambda root, name=None: str(elsewhere),
        organizer=_completed)
    assert "_cleanup_dir" not in upd


def test_a_season_pack_carries_its_folder_too(tmp_path):
    job, _video = _job(tmp_path, "Some.Show.S01.1080p.WEB-DL")
    status = _St(state="completed", progress=1.0, save_path=str(job), name=job.name)
    upd = _done({"client_ref": "n1", "source": "usenet", "id": 6,
                 "search_ctx": {"scope": "season", "season": 1}}, status, _completed,
                import_pack=lambda dl, root, name: {"status": "completed", "progress": 100.0})
    assert upd["_cleanup_dir"] == str(job)


# ── removing it ───────────────────────────────────────────────────────────────

def test_an_emptied_job_folder_is_removed_with_its_empty_subfolders(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "_protected_roots", lambda: set())
    job, video = _job(tmp_path)
    (job / "Subs" / "deeper").mkdir(parents=True)
    video.unlink()
    monitor._reclaim_job_dir(str(job))
    assert not job.exists()
    assert job.parent.is_dir()                                 # the category folder stays


def test_anything_left_keeps_the_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "_protected_roots", lambda: set())
    job, video = _job(tmp_path)
    video.unlink()
    (job / "Subs").mkdir()
    (job / "thumbnail.jpg").write_bytes(b"j")
    monitor._reclaim_job_dir(str(job))
    assert (job / "thumbnail.jpg").exists()
    assert not (job / "Subs").exists()                         # only the empty part went


def test_a_configured_root_is_never_removed(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setattr(monitor, "_protected_roots", lambda: {os.path.abspath(str(root))})
    monitor._reclaim_job_dir(str(root))
    assert root.is_dir()


def test_a_symlinked_folder_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "_protected_roots", lambda: set())
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monitor._reclaim_job_dir(str(link))
    assert link.is_symlink() and real.is_dir()


def test_reclaim_never_raises(tmp_path):
    monitor._reclaim_job_dir(None)
    monitor._reclaim_job_dir("")
    monitor._reclaim_job_dir(str(tmp_path / "gone"))


# ── end to end through a monitor tick ─────────────────────────────────────────

def test_a_tick_persists_the_row_then_removes_the_file_then_the_folder(tmp_path, monkeypatch):
    import core.video.client_download as client
    job, video = _job(tmp_path)
    row = {"id": 7, "source": "usenet", "status": "downloading", "client_ref": "SABnzbd_nzo_7",
           "progress": 99, "search_ctx": {"scope": "episode", "title": "Some Show",
                                          "season": 1, "episode": 1}}
    order = []
    db = Mock()
    db.get_active_video_downloads.side_effect = [[row], []]

    def persist(key, **patch):
        order.append(("persist", video.exists(), job.exists()))
        row.update({k: v for k, v in patch.items() if not k.startswith("_")})
    db.update_video_download.side_effect = persist
    monkeypatch.setattr(monitor, "_make_organizer", lambda db: _completed)
    monkeypatch.setattr(monitor, "_make_pack_importer", lambda db, organizer: None)
    monkeypatch.setattr(monitor, "_misses", {})
    monkeypatch.setattr(monitor, "_protected_roots", lambda: set())
    monkeypatch.setattr(monitor, "_wishlist_obtained", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_archive_history", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "_publish_terminal", lambda *a, **k: None)
    monkeypatch.setattr(client, "_get_status", lambda source, ref: _St(
        state="completed", progress=1.0, save_path=str(job), name=job.name))
    monitor._tick(db)
    assert row["status"] == "completed"
    assert order and order[0] == ("persist", True, True)       # row saved while both still exist
    assert not video.exists() and not job.exists()
    assert job.parent.is_dir()


# ── NFS: a deleted-but-open file lingers as .nfsXXXX ──────────────────────────
# No retry: a folder holding anything, NFS placeholders included, is kept and
# logged. (Live, the folder goes on the first try; the 20 Oct leftovers were
# sab reporting the file, below.)

def _fresh(monkeypatch):
    monkeypatch.setattr(monitor, "_protected_roots", lambda: set())


def test_a_folder_holding_only_an_nfs_placeholder_is_kept_and_logged(tmp_path, monkeypatch, caplog):
    import logging
    _fresh(monkeypatch)
    job, video = _job(tmp_path)
    video.rename(job / ".nfs0000000001a2b3c400000001")   # what NFS does to an open file on delete
    with caplog.at_level(logging.INFO):
        assert monitor._reclaim_job_dir(str(job)) == "kept"
    assert job.exists()
    assert "kept download folder" in caplog.text and "1 file(s) left" in caplog.text


def test_a_real_file_beside_a_placeholder_keeps_the_folder_for_good(tmp_path, monkeypatch):
    _fresh(monkeypatch)
    job, video = _job(tmp_path)
    video.rename(job / ".nfs0000000001a2b3c400000002")
    (job / "release.nfo").write_text("x")
    assert monitor._reclaim_job_dir(str(job)) == "kept"
    assert job.exists()


def test_every_outcome_is_logged(tmp_path, monkeypatch, caplog):
    import logging
    _fresh(monkeypatch)
    job, video = _job(tmp_path)
    (job / "keep.nfo").write_text("x")
    video.unlink()
    with caplog.at_level(logging.INFO):
        monitor._reclaim_job_dir(str(job))
        (job / "keep.nfo").unlink()
        monitor._reclaim_job_dir(str(job))
    text = caplog.text
    assert "kept download folder" in text and "removed emptied download folder" in text


# ── sab reports a single-file job's storage as the FILE (seen live 20 Oct) ────

REL = "Avatar.The.Last.Airbender.2024.S02E03.City.of.Walls.and.Secrets.2160p.NF.WEB-DL.DD_5.1.Atmos.HDR.H.265-playWEB"
MKV = "Avatar.The.Last.Airbender.2024.S02E03.City.of.Walls.and.Secrets.2160p.NF.WEB-DL.DD+5.1.Atmos.HDR.H.265-playWEB.mkv"


def _sab_file_job(tmp_path):
    cat = tmp_path / "complete" / "soulsync"
    job = cat / REL
    job.mkdir(parents=True)
    video = job / MKV
    video.write_bytes(b"x" * 4096)
    return cat, job, video


def test_sab_reporting_the_finished_file_still_finds_the_job_folder(tmp_path):
    _cat, job, video = _sab_file_job(tmp_path)
    status = _St(state="completed", progress=1.0, save_path=str(video), name=REL)
    upd = _done({"client_ref": "36ef60d1", "source": "usenet", "id": 6}, status, _completed)
    assert upd["_cleanup_source"] == str(video)
    assert upd["_cleanup_dir"] == str(job)


def test_the_job_name_match_ignores_case(tmp_path):
    _cat, job, video = _sab_file_job(tmp_path)
    status = _St(state="completed", progress=1.0, save_path=str(video), name=REL.lower())
    upd = _done({"client_ref": "n", "source": "usenet", "id": 7}, status, _completed)
    assert upd["_cleanup_dir"] == str(job)


def test_a_file_straight_in_the_category_folder_never_tags_the_category(tmp_path):
    """a category without job folders: the file's parent IS the category
    folder, and it must never be treated as the job's own"""
    cat = tmp_path / "complete" / "soulsync"
    cat.mkdir(parents=True)
    video = cat / MKV
    video.write_bytes(b"x" * 4096)
    status = _St(state="completed", progress=1.0, save_path=str(video), name=REL)
    upd = _done({"client_ref": "n", "source": "usenet", "id": 8}, status, _completed)
    assert upd["status"] == "completed" and "_cleanup_dir" not in upd


def test_a_parent_named_after_something_else_is_not_the_job_folder(tmp_path):
    _cat, job, video = _sab_file_job(tmp_path)
    status = _St(state="completed", progress=1.0, save_path=str(video), name="Some.Other.Job")
    upd = _done({"client_ref": "n", "source": "usenet", "id": 9}, status, _completed)
    assert "_cleanup_dir" not in upd


def test_real_import_with_sab_file_storage_removes_the_folder_in_the_same_tick(tmp_path, monkeypatch):
    """the 20 Oct live failure, end to end: sab hands back the .mkv as storage,
    the real organizer imports it, and the emptied job folder goes in the same
    tick -- the category folder stays"""
    import core.video.client_download as client
    import core.video.mediainfo as mediainfo
    from database.video_database import VideoDatabase
    _fresh(monkeypatch)
    lib = tmp_path / "tv"
    lib.mkdir()
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    vdb.set_setting("tv_path", str(lib))
    cat, job, video = _sab_file_job(tmp_path)
    dl_id = vdb.add_video_download({
        "kind": "show", "title": "Avatar: The Last Airbender (2024)", "source": "usenet",
        "status": "downloading", "client_ref": "36ef60d1", "target_dir": str(lib),
        "quality_label": "WEBDL-2160p",
        "search_ctx": json.dumps({"scope": "episode", "title": "Avatar: The Last Airbender",
                                  "year": 2024, "season": 2, "episode": 3})})
    monkeypatch.setattr(mediainfo, "probe", lambda p: None)
    monkeypatch.setattr(monitor, "_expected_duration_sec", lambda db, dl: None)
    monkeypatch.setattr(monitor, "_misses", {})
    for name in ("_wishlist_obtained", "_archive_history", "_publish_terminal"):
        monkeypatch.setattr(monitor, name, lambda *a, **k: None)
    monkeypatch.setattr(client, "_get_status", lambda source, ref: _St(
        state="completed", progress=1.0, save_path=str(video), name=REL))
    monitor._tick(vdb)
    row = vdb.get_video_download(dl_id)
    assert row["status"] == "completed", row
    placed = [f for _d, _s, fs in os.walk(lib) for f in fs if f.endswith(".mkv")]
    assert len(placed) == 1 and "S02E03" in placed[0]
    assert not video.exists() and not job.exists()
    assert cat.is_dir()
