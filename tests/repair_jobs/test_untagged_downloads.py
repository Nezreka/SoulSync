"""Untagged Downloads job: downloads that landed with no title/artist/album.

oct 2026: enrichment crashed after clearing tags, 25 Discover Weekly tracks
were filed in the right folders but showed up as Unknown Album. the scan
reads real FLAC files against a real library_history table, the fix writes
the tags back from the download record.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

pytest.importorskip("mutagen")
from mutagen.flac import FLAC  # noqa: E402

from core.repair_jobs.base import JobContext  # noqa: E402
from core.repair_jobs.untagged_downloads import (  # noqa: E402
    UntaggedDownloadsJob,
    missing_core_fields,
    track_number_from_filename,
)
from core.repair_worker import RepairWorker  # noqa: E402

_MINIMAL_FLAC = (
    b"fLaC" + b"\x80\x00\x00\x22" + b"\x00\x10\x00\x10" + b"\x00\x00\x00\x00\x00\x00"
    + b"\x0a\xc4\x42\xf0\x00\x00\x00\x00" + b"\x00" * 16
)


def _flac(path, **tags):
    path.write_bytes(_MINIMAL_FLAC)
    f = FLAC(str(path))
    for k, v in tags.items():
        f[k] = [v]
    f.save()
    return path


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


def _db(tmp_path, rows):
    path = str(tmp_path / "lib.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE library_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
        title TEXT NOT NULL, artist_name TEXT, album_name TEXT, file_path TEXT)""")
    conn.executemany(
        "INSERT INTO library_history (event_type, title, artist_name, album_name, file_path) "
        "VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return _Db(path)


def _scan(db, transfer):
    found = []
    ctx = JobContext(db=db, transfer_folder=str(transfer), config_manager=None,
                     create_finding=lambda **kw: found.append(kw) or True)
    result = UntaggedDownloadsJob().scan(ctx)
    return result, found


def test_scan_flags_only_the_untagged_download(tmp_path):
    bare = _flac(tmp_path / "01 - ZAMA ZAMA.flac")
    good = _flac(tmp_path / "01 - Debbie.flac", title="Debbie", artist="Yung Gravy", album="Debbie")
    db = _db(tmp_path, [
        ("download", "ZAMA ZAMA", "Die Antwoord", "ZAMA ZAMA", str(bare)),
        ("download", "Debbie", "Yung Gravy", "Debbie", str(good)),
        ("import", "Ignored", "Someone", "Else", str(bare)),
    ])
    result, found = _scan(db, tmp_path)
    assert result.scanned == 2
    assert [f["file_path"] for f in found] == [str(bare)]
    d = found[0]["details"]
    assert found[0]["finding_type"] == "untagged_download"
    assert d["missing"] == ["title", "artist", "album"]
    assert (d["track_title"], d["artist"], d["album_title"], d["track_number"]) == (
        "ZAMA ZAMA", "Die Antwoord", "ZAMA ZAMA", 1)


def test_scan_uses_the_newest_record_for_a_redownloaded_file(tmp_path):
    bare = _flac(tmp_path / "02 - Song.flac")
    db = _db(tmp_path, [
        ("download", "Old Name", "Old Artist", "Old Album", str(bare)),
        ("download", "Song", "Artist", "Album", str(bare)),
    ])
    _, found = _scan(db, tmp_path)
    assert len(found) == 1
    assert found[0]["details"]["track_title"] == "Song"


def test_scan_skips_files_that_are_gone(tmp_path):
    db = _db(tmp_path, [("download", "Gone", "A", "B", str(tmp_path / "gone.flac"))])
    result, found = _scan(db, tmp_path)
    assert found == [] and result.skipped == 1


def _worker():
    w = RepairWorker(database=SimpleNamespace())
    w._config_manager = None
    return w


def test_fix_writes_missing_tags_from_the_download_record(tmp_path):
    bare = _flac(tmp_path / "01 - Fondue.flac")
    details = {"file_path": str(bare), "track_title": "Fondue", "artist": "SonReal",
               "album_title": "Fondue", "track_number": 1}
    res = _worker()._fix_untagged_download("file", "1", str(bare), details)
    assert res["success"] is True and res["action"] == "wrote_tags"
    f = FLAC(str(bare))
    assert f["title"] == ["Fondue"]
    assert f["artist"] == ["SonReal"]
    assert f["album"] == ["Fondue"]
    assert f["tracknumber"] == ["1"]


def test_fix_keeps_tags_fixed_by_hand_since_the_scan(tmp_path):
    path = _flac(tmp_path / "01 - x.flac", title="Hand Fixed")
    details = {"file_path": str(path), "track_title": "From History", "artist": "A",
               "album_title": "B"}
    res = _worker()._fix_untagged_download("file", "1", str(path), details)
    assert res["success"] is True
    f = FLAC(str(path))
    assert f["title"] == ["Hand Fixed"]
    assert f["album"] == ["B"]


def test_fix_marks_a_vanished_file_stale(tmp_path):
    res = _worker()._fix_untagged_download(
        "file", "1", str(tmp_path / "gone.flac"), {"file_path": str(tmp_path / "gone.flac")})
    assert res["success"] is False and res["stale"] is True


def test_fix_is_registered():
    assert "untagged_download" in _worker()._fix_handlers()


def test_job_is_registered_and_on_by_default():
    from core.repair_jobs import get_all_jobs
    job = get_all_jobs().get("untagged_downloads")
    assert job is not None and job.default_enabled is True


def test_pure_helpers():
    assert track_number_from_filename("H:/Music/A/03 - Song.flac") == 3
    assert track_number_from_filename("Song.flac") is None
    assert track_number_from_filename("H:\\Music\\A\\A - 2026 B\\05 - GIGAWATTS.mp3") == 5
    assert missing_core_fields({"title": "x", "artist": " ", "album": None}) == ["artist", "album"]
    assert missing_core_fields({"error": "File not found"}) == []
