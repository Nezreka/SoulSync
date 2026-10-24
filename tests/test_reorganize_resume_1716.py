"""#1716 (QT3496): library reorganize started over at the first album every run.

stopping a scan at album 300 (or restarting the container) meant the next run
re-planned albums 1-300 before reaching anything new, and in live mode a stop
threw away every move the run had collected, since they were only queued at the
very end. now the scan saves where it got to, queues moves as it goes, resumes
right after the last finished album, and goes back to the top after a full pass.
"""

from __future__ import annotations

import pytest

import core.library_reorganize as lr
import core.reorganize_queue as rq
from core.repair_jobs.base import JobContext
from core.repair_jobs.library_reorganize import LibraryReorganizeJob
from database.music_database import MusicDatabase

KEY = "repair.jobs.library_reorganize.scan_checkpoint"
DRY = "repair.jobs.library_reorganize.settings.dry_run"


def _db(tmp_path, count):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists (id, name) VALUES ('ar1', 'Artist')")
    for n in range(1, count + 1):
        album_id = f"a{n:03d}"
        conn.execute("INSERT INTO albums (id, artist_id, title) VALUES (?, 'ar1', ?)",
                     (album_id, f"Album {n}"))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
                     "VALUES (?, ?, 'ar1', 't', ?)", (n, album_id, f"/m/{n}.flac"))
    conn.commit()
    conn.close()
    return db


class _Cfg:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def set(self, key, value):
        self.settings[key] = value

    def get_active_media_server(self):
        return None


class _Queue:
    def __init__(self):
        self.albums = []

    def enqueue_many(self, items):
        self.albums.extend(i["album_id"] for i in items)
        return {"enqueued": len(items), "already_queued": 0, "total": len(items)}


class _Boom(BaseException):
    """stands in for the process dying mid-scan (not caught by the job)."""


@pytest.fixture
def harness(monkeypatch):
    calls = []
    queue = _Queue()
    state = {"die_at": None}

    def preview(**kw):
        if state["die_at"] is not None and len(calls) == state["die_at"]:
            raise _Boom()
        calls.append(kw["album_id"])
        return {
            "success": True, "status": "planned", "source": "itunes",
            "source_album_id": "x", "album": "Album", "artist": "Artist",
            "tracks": [{"track_id": 1, "title": "t", "current_path": "/m/old.flac",
                        "new_path": "/m/new.flac", "matched": True, "unchanged": False,
                        "file_exists": True}],
        }

    monkeypatch.setattr(lr, "preview_album_reorganize", preview)
    monkeypatch.setattr(lr, "naming_fingerprint", lambda: "fp1")
    monkeypatch.setattr(rq, "get_queue", lambda: queue)
    return calls, queue, state


def _ctx(db, cfg, stop_after=None, calls=None):
    should_stop = (lambda: len(calls) >= stop_after) if stop_after else None
    return JobContext(db=db, transfer_folder="/m", config_manager=cfg,
                      create_finding=lambda **kw: True, should_stop=should_stop)


def test_stopped_dry_run_resumes_after_the_last_album_then_wraps(tmp_path, harness):
    calls, _queue, _ = harness
    db = _db(tmp_path, 10)
    cfg = _Cfg()

    LibraryReorganizeJob().scan(_ctx(db, cfg, stop_after=4, calls=calls))
    assert calls == ["a001", "a002", "a003", "a004"]
    assert cfg.settings[KEY]["album_id"] == "a004"

    calls.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert calls == [f"a{n:03d}" for n in range(5, 11)]   # not back at a001
    assert cfg.settings[KEY] is None                       # full pass done

    calls.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert calls[0] == "a001"                              # next pass from the top


def test_stopped_live_run_keeps_the_moves_it_planned(tmp_path, harness):
    calls, queue, _ = harness
    db = _db(tmp_path, 10)
    cfg = _Cfg({DRY: False})

    result = LibraryReorganizeJob().scan(_ctx(db, cfg, stop_after=3, calls=calls))
    assert queue.albums == ["a001", "a002", "a003"]
    assert result.auto_fixed == 3
    assert cfg.settings[KEY]["album_id"] == "a003"


def test_a_crash_mid_scan_resumes_from_the_last_batch(tmp_path, harness):
    calls, queue, state = harness
    db = _db(tmp_path, 40)
    cfg = _Cfg({DRY: False})

    state["die_at"] = 30
    with pytest.raises(_Boom):
        LibraryReorganizeJob().scan(_ctx(db, cfg))
    # albums 1-25 were queued with the checkpoint; 26-30 were only in memory
    assert queue.albums == [f"a{n:03d}" for n in range(1, 26)]
    assert cfg.settings[KEY]["album_id"] == "a025"

    state["die_at"] = None
    calls.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert calls[0] == "a026"
    assert queue.albums == [f"a{n:03d}" for n in range(1, 41)]


def test_switching_dry_run_off_starts_over(tmp_path, harness):
    calls, _queue, _ = harness
    db = _db(tmp_path, 6)
    cfg = _Cfg()
    LibraryReorganizeJob().scan(_ctx(db, cfg, stop_after=3, calls=calls))

    # a dry-run checkpoint must not let live mode skip albums it never queued
    cfg.settings[DRY] = False
    calls.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert calls[0] == "a001"


def test_naming_change_starts_over(tmp_path, harness, monkeypatch):
    calls, _queue, _ = harness
    db = _db(tmp_path, 6)
    cfg = _Cfg()
    LibraryReorganizeJob().scan(_ctx(db, cfg, stop_after=3, calls=calls))

    monkeypatch.setattr(lr, "naming_fingerprint", lambda: "fp2")
    calls.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert calls[0] == "a001"


def test_a_dropped_checkpoint_says_what_changed(tmp_path, harness, monkeypatch):
    import core.repair_jobs.library_reorganize as job_mod
    lines = []
    monkeypatch.setattr(job_mod.logger, "info", lambda msg, *a: lines.append(msg % a))
    calls, _queue, _ = harness
    db = _db(tmp_path, 6)
    cfg = _Cfg()
    LibraryReorganizeJob().scan(_ctx(db, cfg, stop_after=3, calls=calls))

    monkeypatch.setattr(lr, "naming_fingerprint", lambda: "fp2")
    lines.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert any("naming settings changed" in line for line in lines)


def _drop_album(db, album_id):
    conn = db._get_connection()
    conn.execute("DELETE FROM tracks WHERE album_id = ?", (album_id,))
    conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
    conn.commit()
    conn.close()


def test_checkpoint_album_gone_carries_on_after_it(tmp_path, harness):
    # #1857: the album the scan stopped on vanished (deleted, or the server
    # gave it a new id). that used to send the scan back to album 1.
    calls, _queue, _ = harness
    db = _db(tmp_path, 6)
    cfg = _Cfg()
    LibraryReorganizeJob().scan(_ctx(db, cfg, stop_after=3, calls=calls))
    assert cfg.settings[KEY]["album_id"] == "a003"
    _drop_album(db, "a003")

    calls.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert calls == ["a004", "a005", "a006"]


def test_checkpoint_album_reidentified_carries_on(tmp_path, harness):
    # a media server update re-keyed the album the scan stopped on: same
    # album, new id that sorts elsewhere. the scan keeps its place.
    calls, _queue, _ = harness
    db = _db(tmp_path, 6)
    cfg = _Cfg()
    LibraryReorganizeJob().scan(_ctx(db, cfg, stop_after=3, calls=calls))
    conn = db._get_connection()
    conn.execute("INSERT INTO albums (id, artist_id, title) VALUES ('z999', 'ar1', 'Album 3')")
    conn.execute("UPDATE tracks SET album_id = 'z999' WHERE album_id = 'a003'")
    conn.execute("DELETE FROM albums WHERE id = 'a003'")
    conn.commit()
    conn.close()

    calls.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert calls == ["a004", "a005", "a006", "z999"]


def test_checkpoint_past_the_last_album_starts_the_next_pass(tmp_path, harness):
    calls, _queue, _ = harness
    db = _db(tmp_path, 4)
    cfg = _Cfg({KEY: {"album_id": "a999", "scan": None}})
    LibraryReorganizeJob().scan(_ctx(db, cfg, stop_after=1, calls=calls))
    key = cfg.settings[KEY]["scan"]
    cfg.settings[KEY] = {"album_id": "a999", "scan": key}

    calls.clear()
    LibraryReorganizeJob().scan(_ctx(db, cfg))
    assert calls[0] == "a001"


def test_integer_ids_resume_in_sqlite_order():
    job = LibraryReorganizeJob()
    rows = [{"id": 2}, {"id": 10}, {"id": "a1"}]
    ctx = JobContext(db=None, transfer_folder="/m",
                     config_manager=_Cfg({KEY: {"album_id": 5, "scan": "k"}}))
    assert job._resume_index(ctx, rows, "k") == 1      # 10, not "a1" or the top
