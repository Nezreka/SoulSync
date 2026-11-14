"""fixing an orphan rechecks its file without reading the whole catalogue (#2095).

a file can enter the library after its finding was raised, so the fix looks
again before it moves or deletes anything. it used to build the suffix set of
every tracked file for that, once per finding: a bulk fix of 700 findings on a
274k file library took over ten hours. only the tracked files with the
orphan's file name can match it, so only those are read.
"""

from __future__ import annotations

import os

import pytest

from core.repair_jobs.orphan_file_detector import (
    is_tracked_path, known_file_suffixes, tracked_suffixes_for)
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests import lib2_seed as seed


@pytest.fixture()
def db(tmp_path):
    database = MusicDatabase(str(tmp_path / "music.db"))
    with database._get_connection() as conn:
        seed.track(conn, "Massive Attack", "Mezzanine", "Teardrop",
                   path="/music/Massive Attack/Mezzanine/03 - Teardrop.flac")
        seed.track(conn, "A", "B (Deluxe)", "Intro", path="/music/A/B (Deluxe)/01 - Intro.flac")
        seed.track(conn, "Z", "Y", "Intro", path="/music/Z/Y/01 - Intro.flac")
        seed.track(conn, "Sigur Rós", "Ágætis byrjun", "Svefn-g-englar",
                   path="/music/Sigur Rós/Ágætis byrjun/02 - SVEFN-G-ENGLAR.flac")
        seed.track(conn, "W", "Win", "Song", path="C:\\Music\\W\\Win\\04 - Song_100%.mp3")       # a windows server's path
        seed.track(conn, "Gone", "Album", "Old", path="/music/Gone/Album/09 - Old.flac")
        conn.execute("UPDATE lib2_track_files SET file_state='deleted' WHERE path LIKE '/music/Gone/%'")
        conn.commit()
    return database


CASES = [
    "/transfer/Massive Attack/Mezzanine/03 - Teardrop.flac",      # tracked, another mount
    "/downloads/mezzanine/03 - teardrop.FLAC",                    # same folder and name, other case
    "/transfer/Somewhere Else/03 - Teardrop.flac",                # same name only
    "/transfer/Q/R/01 - Intro.flac",                              # a name two albums share
    "/transfer/Z/Y/01 - Intro.flac",
    "/transfer/Sigur Rós/Ágætis byrjun/02 - svefn-g-englar.flac",  # case beyond ascii
    "/transfer/W/Win/04 - Song_100%.mp3",                         # like's own wildcards in a name
    "/transfer/W/Win/04 - SongX100Y.mp3",                         # which must not match as wildcards
    "/transfer/Gone/Album/09 - Old.flac",                         # a deleted row tracks nothing
    "/transfer/Nobody/Nothing/77 - Unknown.flac",
]


@pytest.mark.parametrize("path", CASES)
def test_the_recheck_of_one_file_answers_as_the_full_snapshot_does(db, path):
    everything = known_file_suffixes(db)
    mine = tracked_suffixes_for(db, path)
    for depth in (2, 1):
        assert is_tracked_path(path, mine, min_depth=depth) == \
            is_tracked_path(path, everything, min_depth=depth), (path, depth)
    # and it holds nothing but this file's namesakes
    name = path.replace("\\", "/").split("/")[-1].lower()
    assert all(s.split("/")[-1] == name for s in mine)


def test_what_is_tracked_and_what_is_not(db):
    def tracked(path, depth=2):
        return is_tracked_path(path, tracked_suffixes_for(db, path), min_depth=depth)
    assert tracked("/transfer/Massive Attack/Mezzanine/03 - Teardrop.flac")
    assert not tracked("/transfer/Somewhere Else/03 - Teardrop.flac")
    assert tracked("/transfer/Somewhere Else/03 - Teardrop.flac", depth=1)     # the same name, for a delete
    assert tracked("/transfer/Sigur Rós/Ágætis byrjun/02 - svefn-g-englar.flac")
    assert tracked("/transfer/W/Win/04 - Song_100%.mp3")
    assert not tracked("/transfer/W/Win/04 - SongX100Y.mp3", depth=1)
    assert not tracked("/transfer/Gone/Album/09 - Old.flac", depth=1)
    assert tracked_suffixes_for(db, "") == set()


class _Cfg:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _worker(db, tmp_path):
    w = RepairWorker(database=db)
    w._config_manager = _Cfg({"soulseek.download_path": str(tmp_path / "Downloads"),
                              "import.staging_path": str(tmp_path / "Staging"),
                              "auto_import.enabled": False})
    w.transfer_folder = str(tmp_path / "Transfer")
    os.makedirs(w.transfer_folder, exist_ok=True)
    return w


def _file(tmp_path, *parts):
    path = tmp_path.joinpath("Transfer", *parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 16)
    return path


def test_a_bulk_fix_never_reads_the_whole_catalogue(db, tmp_path, monkeypatch):
    import core.repair_jobs.orphan_file_detector as detector
    w = _worker(db, tmp_path)
    monkeypatch.setattr(w, "_trigger_auto_import_scan", lambda: None)

    def never(_db):
        raise AssertionError("the whole catalogue was read to recheck one file")
    monkeypatch.setattr(detector, "known_file_suffixes", never)
    for i in range(5):
        src = _file(tmp_path, "Loose", "song %d.mp3" % i)
        res = w._fix_orphan_file("file", str(i), str(src), {"_fix_action": "staging"})
        assert res["success"] and res["action"] == "moved_to_staging", res
        assert not src.exists()


def test_a_file_that_entered_the_library_since_is_left_where_it_is(db, tmp_path, monkeypatch):
    w = _worker(db, tmp_path)
    monkeypatch.setattr(w, "_trigger_auto_import_scan", lambda: None)
    src = _file(tmp_path, "Massive Attack", "Mezzanine", "03 - Teardrop.flac")
    for action in ("staging", "delete"):
        res = w._fix_orphan_file("file", "1", str(src), {"_fix_action": action})
        assert res["action"] == "already_tracked" and src.exists(), res


def test_a_delete_still_stops_for_a_library_track_with_the_same_name(db, tmp_path):
    w = _worker(db, tmp_path)
    src = _file(tmp_path, "Unsorted", "03 - Teardrop.flac")
    res = w._fix_orphan_file("file", "1", str(src), {"_fix_action": "delete"})
    assert res["needs_confirm"] and src.exists()
    assert res["library_paths"] == ["/music/Massive Attack/Mezzanine/03 - Teardrop.flac"]


def test_the_fix_still_fails_closed_when_the_catalogue_cannot_be_read(db, tmp_path, monkeypatch):
    import core.repair_jobs.orphan_file_detector as detector
    w = _worker(db, tmp_path)
    src = _file(tmp_path, "Loose", "song.mp3")

    def broken(_db, _path):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(detector, "tracked_suffixes_for", broken)
    for action in ("staging", "delete"):
        res = w._fix_orphan_file("file", "1", str(src), {"_fix_action": action})
        assert not res["success"] and "no file was changed" in res["error"]
        assert src.exists()


def test_the_recheck_reads_paths_off_their_index_never_the_table(db):
    # a row carries the file's tags and pipeline detail. reading every row to
    # compare a path took as long as the snapshot this replaced (33s against
    # 51s on a 311k file library); the path index alone takes about two
    import inspect

    import core.repair_jobs.orphan_file_detector as detector
    src = inspect.getsource(detector.tracked_suffixes_for)
    assert "INDEXED BY idx_lib2_track_files_path" in src
    conn = db._get_connection()
    try:
        plan = " ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT id, path FROM lib2_track_files "
            "INDEXED BY idx_lib2_track_files_path WHERE path LIKE ? ESCAPE '\\'", ("%x.flac",)))
    finally:
        conn.close()
    assert "COVERING INDEX idx_lib2_track_files_path" in plan
