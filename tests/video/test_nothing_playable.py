"""A finished download with nothing playable in it is a bad release, not a path problem.

Crawlers.2026.MULTi.COMPLETE.BLURAY-MONUMENT finished as one .iso plus a
Sample/*.m2ts, an nfo and a proof jpg. The importer found no video, the row sat
at 100%, and after 30 minutes the stall timer failed it with the path-mapping
message ("the file never appeared where SoulSync could reach it") and no retry.
The release was never blocklisted, so the next wishlist sweep grabbed it again.

The job's own folder WAS there. That is what separates the two cases: a folder
we can see that holds files but no playable video is junk; a folder we cannot
see is still the path problem the old message describes. Only the timeout acts
on it, so a torrent client still moving files in is never judged early.
"""

from __future__ import annotations

import time
from unittest.mock import Mock

import core.video.client_download as cd
from core.video import download_monitor as monitor
from core.video import stall


class _St:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _crawlers(tmp_path):
    job = tmp_path / "complete" / "soulsync" / "Crawlers.2026.MULTi.COMPLETE.BLURAY-MONUMENT"
    (job / "Sample").mkdir(parents=True)
    (job / "monument-crawlers.iso").write_bytes(b"x" * 2000)
    (job / "Sample" / "monument-crawlers-sample.m2ts").write_bytes(b"x" * 100)
    (job / "monument-crawlers.nfo").write_text("nfo")
    (job / "monument-crawlers-proof.jpg").write_bytes(b"x")
    return job


def _done(dl, status):
    return cd.process_client_download(
        dl, get_status=lambda s, r: status, resolve_path=lambda p: p,
        find_video=cd.find_video_file,
        organizer=lambda dl, src: {"status": "completed", "progress": 100.0, "dest_path": src})


def _status(job):
    return _St(state="completed", progress=1.0, save_path=str(job), name=job.name)


# ── telling the two cases apart ──────────────────────────────────────────────

def test_a_disc_image_job_is_tagged_with_what_it_holds(tmp_path):
    job = _crawlers(tmp_path)
    upd = _done({"client_ref": "n1", "source": "usenet", "id": 1}, _status(job))
    assert upd.get("status") is None and upd["progress"] == 100.0
    assert upd["_nothing_playable"] == ".iso, .jpg, .nfo + a sample"


def test_a_shared_parent_is_scoped_to_the_job(tmp_path):
    job = _crawlers(tmp_path)
    assert cd.nothing_playable(str(job.parent), job.name) == ".iso, .jpg, .nfo + a sample"


def test_a_missing_folder_is_not_junk(tmp_path):
    """The path-mapping case: nothing to look at, so no verdict on the release."""
    gone = tmp_path / "complete" / "soulsync" / "Not.Here"
    status = _St(state="completed", progress=1.0, save_path=str(gone), name=gone.name)
    upd = _done({"client_ref": "n2", "source": "usenet", "id": 2}, status)
    assert upd == {"progress": 100.0}
    assert cd.nothing_playable(str(gone)) is None
    assert cd.nothing_playable(str(gone.parent), gone.name) is None


def test_an_empty_folder_is_not_junk(tmp_path):
    job = tmp_path / "Empty.Job"
    job.mkdir()
    assert cd.nothing_playable(str(job)) is None


def test_a_folder_with_only_a_sample_is_left_to_the_old_path(tmp_path):
    job = tmp_path / "Sample.Only"
    job.mkdir()
    (job / "x-sample.mkv").write_bytes(b"x")
    assert cd.nothing_playable(str(job)) is None


def test_a_real_video_is_never_junk(tmp_path):
    job = tmp_path / "Some.Movie.2023.1080p.BluRay.x264-GROUP"
    job.mkdir()
    (job / "some.movie.mkv").write_bytes(b"x" * 1000)
    (job / "some.movie.nfo").write_text("nfo")
    assert cd.nothing_playable(str(job)) is None
    upd = _done({"client_ref": "n3", "source": "usenet", "id": 3}, _status(job))
    assert upd["status"] == "completed" and "_nothing_playable" not in upd


def test_the_reason_names_what_was_found():
    msg = stall.reason(stall.STALLED, 31 * 60, at_completion=True,
                       nothing_playable=".iso, .nfo + a sample")
    assert "nothing playable" in msg and ".iso" in msg
    assert "never appeared" not in msg
    # without the tag the path-problem wording is unchanged
    assert "never appeared" in stall.reason(stall.STALLED, 31 * 60, at_completion=True)


# ── what the monitor does at the timeout ─────────────────────────────────────

def _ago(minutes):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - minutes * 60))


def _tick(monkeypatch, upd, minutes):
    row = {"id": 9, "source": "usenet", "status": "downloading", "client_ref": "SABnzbd_nzo_9",
           "progress": 100.0, "progress_at": _ago(minutes), "kind": "movie",
           "title": "Crawlers", "username": "DrunkenSlug",
           "filename": "Crawlers.2026.MULTi.COMPLETE.BLURAY-MONUMENT",
           "release_title": "Crawlers.2026.MULTi.COMPLETE.BLURAY-MONUMENT", "search_ctx": "{}"}
    db = Mock()
    db.get_active_video_downloads.return_value = [row]
    calls = []
    monkeypatch.setattr(monitor, "_make_organizer", lambda db: None)
    monkeypatch.setattr(monitor, "_make_pack_importer", lambda db, organizer: None)
    monkeypatch.setattr(monitor, "_misses", {})
    monkeypatch.setattr(monitor, "_fail_or_retry",
                        lambda db, dl, err, **kw: calls.append((err, kw)))
    monkeypatch.setattr(cd, "process_active_client_download",
                        lambda dl, organizer=None, import_pack=None: dict(upd))
    monitor._tick(db)
    return db, calls


def test_junk_at_the_timeout_is_blocklisted_and_retried(monkeypatch):
    db, calls = _tick(monkeypatch, {"progress": 100.0,
                                    "_nothing_playable": ".iso, .jpg, .nfo + a sample"}, 31)
    assert len(calls) == 1
    err, kw = calls[0]
    assert kw.get("allow_retry") is True and kw.get("client_failure") is True
    assert "nothing playable" in err
    db.add_video_blocklist.assert_called_once()
    entry = db.add_video_blocklist.call_args[0][0]
    assert entry["username"] == "DrunkenSlug"
    assert entry["filename"] == "Crawlers.2026.MULTi.COMPLETE.BLURAY-MONUMENT"
    assert "nothing playable" in entry["reason"]


def test_a_missing_folder_at_the_timeout_keeps_the_old_behaviour(monkeypatch):
    db, calls = _tick(monkeypatch, {"progress": 100.0}, 31)
    assert len(calls) == 1
    err, kw = calls[0]
    assert kw.get("allow_retry") is False
    assert "never appeared" in err
    db.add_video_blocklist.assert_not_called()


def test_junk_inside_the_window_just_waits(monkeypatch):
    """A torrent client may still be moving files in: no verdict before the timeout."""
    db, calls = _tick(monkeypatch, {"progress": 100.0, "_nothing_playable": ".nfo"}, 5)
    assert calls == []
    db.add_video_blocklist.assert_not_called()
