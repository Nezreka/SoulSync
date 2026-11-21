"""Live folder watching (Phase 3 item 3.2): what a changed album folder does
to the catalogue.

New files are indexed in place, a moved file keeps its record (plays, padlocks
and history follow it, undoable from File Locations), a deleted file goes
through the missing lifecycle capped at suspected, a vanished library folder
is an outage, a mass delete is deferred, SoulSync's own writes are left alone,
and a file the catalogue holds under the media server's spelling is never
indexed twice."""

from __future__ import annotations

import json
import os
from contextlib import closing

import pytest

from core.library import own_writes
from core.library2 import folder_rescan, paths
from database.music_database import MusicDatabase


class _Config:
    def __init__(self, root):
        self._values = {"library.music_paths": [root], "soulseek.transfer_path": root}

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture
def lib(tmp_path, monkeypatch):
    own_writes.reset_for_tests()
    root = tmp_path / "music"
    root.mkdir()
    (root / "keep.txt").write_text("x")
    config = _Config(str(root))
    real_resolve = paths.resolve_lib2_path
    monkeypatch.setattr(paths, "resolve_lib2_path",
                        lambda path, config_manager=None: real_resolve(path, config))
    real_healthy = paths.missing_path_root_is_healthy
    monkeypatch.setattr(paths, "missing_path_root_is_healthy",
                        lambda path, config_manager=None: real_healthy(path, config))
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)

    # the import writer itself is covered elsewhere: here it records a file row
    linked = []

    def _link(context, **_kw):
        path = context["_final_processed_path"]
        linked.append(path)
        with closing(db._get_connection()) as conn:
            fid = conn.execute("INSERT INTO lib2_track_files(path, size, source) VALUES(?,?,?)",
                               (path, os.path.getsize(path), context.get("username"))).lastrowid
            conn.commit()
        return fid

    monkeypatch.setattr("core.library2.autolink.link_download_into_library_v2", _link)
    monkeypatch.setattr("core.library2.unmapped_trigger.schedule_unmapped_artist_reconcile",
                        lambda *a, **k: None)
    tags = {}
    monkeypatch.setattr(folder_rescan, "_found_tags", lambda path: tags.get(path, {}))
    yield {"root": root, "db": db, "linked": linked, "tags": tags, "config": config}
    own_writes.reset_for_tests()


def _track(db, title="Song", n=1, album="Album", artist="Artist"):
    with closing(db._get_connection()) as conn:
        ar = conn.execute("INSERT INTO lib2_artists(name) VALUES(?)", (artist,)).lastrowid
        al = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?,?)",
                          (ar, album)).lastrowid
        tid = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,?,?)",
                           (al, title, n)).lastrowid
        conn.commit()
    return tid


def _file_row(db, track_id, path, size, tags=None, state="active"):
    with closing(db._get_connection()) as conn:
        fid = conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, size, tags_json, file_state) "
            "VALUES(?,?,?,?,?)", (track_id, str(path), size, json.dumps(tags or {}), state)).lastrowid
        conn.commit()
    return fid


def _row(db, fid):
    with closing(db._get_connection()) as conn:
        return dict(conn.execute("SELECT * FROM lib2_track_files WHERE id=?", (fid,)).fetchone())


def _write(path, data=b"audio"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return str(path)


def _process(lib, units, moves=()):
    return folder_rescan.process_changes(lib["db"], str(lib["root"]), units, list(moves),
                                         config_manager=lib["config"])


def test_an_album_copied_in_is_indexed_in_place(lib):
    album = lib["root"] / "Artist" / "Album"
    files = [_write(album / f"{i:02d}.flac") for i in range(1, 4)]
    _write(album / "04.flac.part")
    stats = _process(lib, [(str(album), True)])
    assert sorted(lib["linked"]) == sorted(files)
    assert stats["added"] == 3
    with closing(lib["db"]._get_connection()) as conn:
        sources = {r[0] for r in conn.execute("SELECT source FROM lib2_track_files")}
    assert sources == {folder_rescan.SOURCE}


def test_a_second_pass_indexes_nothing_twice(lib):
    album = lib["root"] / "Artist" / "Album"
    _write(album / "01.flac")
    _process(lib, [(str(album), True)])
    _process(lib, [(str(album), True)])
    assert len(lib["linked"]) == 1


def test_soulsyncs_own_writes_are_left_alone(lib):
    album = lib["root"] / "Artist" / "Album"
    ours = _write(album / "01.flac")
    theirs = _write(album / "02.flac")
    own_writes.note(ours)
    stats = _process(lib, [(str(album), True)])
    assert lib["linked"] == [theirs]
    assert stats["own_writes_skipped"] == 1


def test_a_known_file_is_rescanned_not_reindexed(lib, monkeypatch):
    album = lib["root"] / "Artist" / "Album"
    path = _write(album / "01.flac")
    fid = _file_row(lib["db"], _track(lib["db"]), path, 5)
    scanned = []
    from core.library2 import scan
    real = scan.rescan_files
    monkeypatch.setattr(scan, "rescan_files",
                        lambda db, **kw: scanned.append(kw) or real(db, **kw))
    stats = _process(lib, [(str(album), True)])
    assert lib["linked"] == []
    assert scanned[0]["file_ids"] == [fid] and scanned[0]["manual"] is False
    assert stats["rescanned"] == 1


def test_a_deleted_file_is_suspected_never_confirmed_by_the_watcher(lib):
    album = lib["root"] / "Artist" / "Album"
    _write(album / "02.flac")  # the album folder is still there
    gone = str(album / "01.flac")
    fid = _file_row(lib["db"], _track(lib["db"]), gone, 5)
    for _ in range(3):
        _process(lib, [(str(album), True)])
    row = _row(lib["db"], fid)
    assert row["file_state"] == "missing_suspected"
    # the next library scan is what confirms it
    from core.library2.scan import rescan_files
    rescan_files(lib["db"], file_ids=[fid])
    assert _row(lib["db"], fid)["file_state"] == "missing_confirmed"


def test_a_vanished_library_folder_is_an_outage(lib):
    album = lib["root"] / "Artist" / "Album"
    gone = str(album / "01.flac")
    fid = _file_row(lib["db"], _track(lib["db"]), gone, 5)
    os.remove(lib["root"] / "keep.txt")  # an empty mount point: the share is away
    stats = _process(lib, [(str(album), True)])
    assert stats["outage"] is True
    assert _row(lib["db"], fid)["file_state"] == "active"


def test_a_mass_delete_in_one_batch_is_deferred(lib):
    album = lib["root"] / "Artist" / "Album"
    album.mkdir(parents=True)
    tid = _track(lib["db"])
    ids = [_file_row(lib["db"], tid, album / f"{i:03d}.flac", 5) for i in range(60)]
    stats = _process(lib, [(str(album), True)])
    assert stats.get("outage_deferred") == 60
    assert {_row(lib["db"], i)["file_state"] for i in ids} == {"active"}


def test_a_reported_rename_keeps_the_record(lib):
    old = str(lib["root"] / "Artist" / "Old" / "01.flac")
    new = _write(lib["root"] / "Artist" / "New" / "01.flac")
    fid = _file_row(lib["db"], _track(lib["db"]), old, 5)
    with closing(lib["db"]._get_connection()) as conn:
        conn.execute("INSERT INTO library_history(event_type, title, file_path) VALUES('download','Song',?)",
                     (old,))
        conn.commit()
    stats = _process(lib, [(str(lib["root"] / "Artist" / "Old"), True),
                           (str(lib["root"] / "Artist" / "New"), True)], [(old, new)])
    assert stats["moved"] == 1 and stats["added"] == 0
    assert lib["linked"] == []
    row = _row(lib["db"], fid)
    assert row["path"] == new and row["file_state"] == "active"
    with closing(lib["db"]._get_connection()) as conn:
        assert conn.execute("SELECT file_path FROM library_history").fetchone()[0] == new
        run = conn.execute("SELECT kind, repointed FROM lib2_path_repoint_runs").fetchone()
    assert tuple(run) == ("watch", 1)
    # and it can be undone from Tools > File Locations
    from core.library2.path_repoint import list_runs, undo_run
    undo_run(lib["db"], list_runs(lib["db"])[0]["id"])
    assert _row(lib["db"], fid)["path"] == old


def test_a_renamed_album_folder_keeps_every_record(lib):
    old_dir = lib["root"] / "Artist" / "Old"
    new_dir = lib["root"] / "Artist" / "New"
    tid = _track(lib["db"])
    ids = [_file_row(lib["db"], tid, old_dir / f"{i}.flac", 5) for i in range(3)]
    for i in range(3):
        _write(new_dir / f"{i}.flac")
    stats = _process(lib, [(str(old_dir), True), (str(new_dir), True)],
                     [(str(old_dir), str(new_dir))])
    assert stats["moved"] == 3 and lib["linked"] == []
    assert [_row(lib["db"], i)["path"] for i in ids] == [str(new_dir / f"{i}.flac") for i in range(3)]


def test_a_move_without_an_event_is_found_by_size_and_tags(lib):
    """a polled share, or a copy then a delete: no rename event, the same
    file with the same tags turns up somewhere else"""
    old = str(lib["root"] / "Artist" / "Old" / "01.flac")
    new = _write(lib["root"] / "Artist" / "New" / "01 - Song.flac")
    stored = {"title": "Song", "album": "Album", "artist": "Artist", "track_number": 1}
    fid = _file_row(lib["db"], _track(lib["db"]), old, 5, tags=stored)
    lib["tags"][new] = dict(stored)
    stats = _process(lib, [(str(lib["root"] / "Artist" / "Old"), True),
                           (str(lib["root"] / "Artist" / "New"), True)])
    assert stats["moved"] == 1 and lib["linked"] == []
    assert _row(lib["db"], fid)["path"] == new


def test_a_file_that_left_in_an_earlier_batch_is_still_found(lib):
    old = str(lib["root"] / "Artist" / "Old" / "01.flac")
    stored = {"title": "Song", "album": "Album", "artist": "Artist"}
    fid = _file_row(lib["db"], _track(lib["db"]), old, 5, tags=stored, state="missing_suspected")
    new = _write(lib["root"] / "Elsewhere" / "Album" / "01.flac")
    lib["tags"][new] = dict(stored)
    stats = _process(lib, [(str(lib["root"] / "Elsewhere" / "Album"), True)])
    assert stats["moved"] == 1
    row = _row(lib["db"], fid)
    assert row["path"] == new and row["file_state"] == "active"


def test_different_tags_or_size_are_a_new_file_not_a_move(lib):
    old = str(lib["root"] / "Artist" / "Old" / "01.flac")
    fid = _file_row(lib["db"], _track(lib["db"]), old, 5,
                    tags={"title": "Song", "album": "Album"})
    other = _write(lib["root"] / "Artist" / "New" / "01.flac")
    lib["tags"][other] = {"title": "Another Song", "album": "Album"}
    bigger = _write(lib["root"] / "Artist" / "New" / "02.flac", b"longer audio")
    lib["tags"][bigger] = {"title": "Song", "album": "Album"}
    stats = _process(lib, [(str(lib["root"] / "Artist" / "Old"), True),
                           (str(lib["root"] / "Artist" / "New"), True)])
    assert stats["moved"] == 0
    assert sorted(lib["linked"]) == sorted([other, bigger])
    assert _row(lib["db"], fid)["path"] == old


def test_two_equal_candidates_are_never_guessed(lib):
    stored = {"title": "Song", "album": "Album"}
    tid = _track(lib["db"])
    a = _file_row(lib["db"], tid, str(lib["root"] / "X" / "A" / "01.flac"), 5, tags=stored)
    b = _file_row(lib["db"], tid, str(lib["root"] / "X" / "B" / "01.flac"), 5, tags=stored)
    new = _write(lib["root"] / "X" / "C" / "01.flac")
    lib["tags"][new] = dict(stored)
    stats = _process(lib, [(str(lib["root"] / "X"), True)])
    assert stats["moved"] == 0
    assert _row(lib["db"], a)["path"].endswith(os.path.join("A", "01.flac"))
    assert _row(lib["db"], b)["path"].endswith(os.path.join("B", "01.flac"))


def test_a_copy_is_not_a_move(lib):
    """the system reported a rename, but the old file is still there"""
    old = _write(lib["root"] / "A" / "Old" / "01.flac")
    new = _write(lib["root"] / "A" / "New" / "01.flac")
    fid = _file_row(lib["db"], _track(lib["db"]), old, 5)
    stats = _process(lib, [(str(lib["root"] / "A" / "New"), True)], [(old, new)])
    assert stats["moved"] == 0
    assert _row(lib["db"], fid)["path"] == old


def test_the_media_servers_spelling_is_never_indexed_twice(lib):
    """the catalogue stores /data/music/...: the server's view of the folder"""
    stored = "/data/music/Artist/Album/01.flac"
    tid = _track(lib["db"])
    _file_row(lib["db"], tid, stored, 5)
    _write(lib["root"] / "Artist" / "Album" / "01.flac")
    new = _write(lib["root"] / "Artist" / "Album" / "02.flac")
    stats = _process(lib, [(str(lib["root"] / "Artist" / "Album"), True)])
    assert lib["linked"] == [new]
    assert stats["added"] == 1


def test_windows_spellings_are_one_folder():
    lo, hi = folder_rescan._range("C:\\Music\\A")
    assert lo == "C:\\Music\\A\\" and hi == "C:\\Music\\A]"
    lo, hi = folder_rescan._range("C:/Music/A")
    assert lo == "C:/Music/A/" and hi == "C:/Music/A0"
    assert folder_rescan._suffix_key("\\\\nas\\m\\Artist\\Album\\01.flac") == "artist/album/01.flac"
    assert folder_rescan._suffix_key("/data/music/Artist/Album/01.flac") == "artist/album/01.flac"


def test_same_recording_needs_the_title_and_agreeing_details():
    stored = {"title": "Song", "album": "Album", "track_number": "3/12", "duration_ms": 200000}
    assert folder_rescan.same_recording(stored, {"title": "song", "album": "ALBUM",
                                                 "track_number": 3, "duration_ms": 201000})
    assert not folder_rescan.same_recording(stored, {"title": "Song", "track_number": 4})
    assert not folder_rescan.same_recording(stored, {"title": "Song", "duration_ms": 260000})
    assert not folder_rescan.same_recording({}, {"title": "Song"})


def test_a_reported_rename_to_a_different_file_is_not_a_move(lib):
    """the system said renamed, but the file at the new path is not the size
    the record remembers: a replacement, indexed as its own file"""
    old = str(lib["root"] / "A" / "Old" / "01.flac")
    new = _write(lib["root"] / "A" / "New" / "01.flac", b"a much longer file")
    fid = _file_row(lib["db"], _track(lib["db"]), old, 5)
    stats = _process(lib, [(str(lib["root"] / "A" / "New"), True)], [(old, new)])
    assert stats["moved"] == 0 and lib["linked"] == [new]
    assert _row(lib["db"], fid)["path"] == old


def test_a_spelling_the_resolver_cant_map_is_still_never_indexed_twice(lib, monkeypatch):
    """the server's folder is one SoulSync can't translate: the album folder
    and file name still say the record is this file"""
    monkeypatch.setattr(paths, "resolve_lib2_path", lambda path, config_manager=None: None)
    _file_row(lib["db"], _track(lib["db"]), "/srv/share/Artist/Album/01.flac", 5)
    _write(lib["root"] / "Artist" / "Album" / "01.flac")
    new = _write(lib["root"] / "Artist" / "Album" / "02.flac")
    _process(lib, [(str(lib["root"] / "Artist" / "Album"), True)])
    assert lib["linked"] == [new]
