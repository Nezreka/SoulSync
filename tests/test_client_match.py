"""which client rows soulsync already owns, so the clients tab only offers
match & import on downloads it does not follow."""

from __future__ import annotations

import threading

from core.client_match import audiobook_known, merge_known, plugin_known


def test_audiobook_downloads_are_labelled_by_client_ref():
    known = audiobook_known([
        {"source": "torrent", "client_id": "ABCDEF", "title": "Project Hail Mary", "status": "downloading"},
        {"source": "usenet", "client_id": "SABnzbd_nzo_1", "title": "Dune", "status": "completed"},
    ])
    assert known["torrent"]["abcdef"] == {"kind": "audiobook", "title": "Project Hail Mary"}
    # usenet ids are case-sensitive, so they are kept as given
    assert known["usenet"]["SABnzbd_nzo_1"]["title"] == "Dune"


def test_a_failed_audiobook_download_is_up_for_matching_again():
    # the lost-record case: the torrent finished but the soulsync side failed
    known = audiobook_known([
        {"source": "torrent", "client_id": "abc", "title": "T", "status": "failed"},
        {"source": "torrent", "client_id": "def", "title": "T", "status": "cancelled"},
    ])
    assert known["torrent"] == {}


def test_soulseek_audiobook_folders_label_every_transfer():
    # a grab remembers transfer ids; a clients-tab match remembers filenames
    from core.audiobook_soulseek import encode_refs
    known = audiobook_known([
        {"source": "soulseek", "client_id": encode_refs(["t1", "t2"], "peer", "Book"),
         "title": "Dune", "status": "downloading"},
        {"source": "soulseek", "client_id": encode_refs(["Books\\Dune\\01.mp3"], "peer2", "Dune"),
         "title": "Dune", "status": "staged"},
    ])
    assert set(known["slskd"]) == {("id", "t1"), ("id", "t2"), ("peer2", "Books\\Dune\\01.mp3")}


class _Plugin:
    def __init__(self, rows):
        self._lock = threading.Lock()
        self.active_downloads = {r["id"]: r for r in rows}


def test_music_plugin_downloads_are_labelled():
    torrent = _Plugin([{"id": "1", "torrent_hash": "ABC", "display_name": "In Rainbows"},
                       {"id": "2", "torrent_hash": None, "display_name": "not added yet"}])
    usenet = _Plugin([{"id": "3", "job_id": "nzo_9", "filename": "OK Computer"}])
    known = plugin_known(torrent, usenet)
    assert known["torrent"] == {"abc": {"kind": "album", "title": "In Rainbows"}}
    assert known["usenet"] == {"nzo_9": {"kind": "album", "title": "OK Computer"}}


def test_a_missing_plugin_labels_nothing():
    assert plugin_known(None, object()) == {"torrent": {}, "usenet": {}}


def test_merging_keeps_the_first_label():
    base = {"torrent": {"abc": {"kind": "show", "title": "Ted Lasso"}}}
    merge_known(base, {"torrent": {"abc": {"kind": "audiobook", "title": "x"},
                                   "def": {"kind": "album", "title": "y"}}}, None)
    assert base["torrent"]["abc"]["kind"] == "show"
    assert base["torrent"]["def"]["kind"] == "album"


def _compose(**overrides):
    from core.client_match import compose_known
    sources = dict(
        video_rows=lambda: [
            {"source": "torrent", "client_ref": "VID1", "kind": "show", "title": "Ted Lasso", "status": "downloading"},
            {"source": "torrent", "client_ref": "VID2", "kind": "movie", "title": "Dune", "status": "failed"},
            {"source": "soulseek", "username": "u", "filename": "f.mkv", "kind": "movie", "title": "Heat"},
        ],
        music_tasks=lambda: [{"username": "peer", "filename": "a.flac", "track_info": {"name": "Airbag"}}],
        audiobook_rows=lambda: [{"source": "torrent", "client_id": "BOOK1", "title": "PHM", "status": "staged"}],
        torrent_plugin=lambda: _Plugin([{"id": "1", "torrent_hash": "MUS1", "display_name": "In Rainbows"}]),
        usenet_plugin=lambda: None,
    )
    sources.update(overrides)
    return compose_known(**sources)


def test_every_side_is_labelled():
    known = _compose()
    assert set(known["torrent"]) == {"vid1", "book1", "mus1"}
    assert known["slskd"][("u", "f.mkv")]["title"] == "Heat"
    assert known["slskd"][("peer", "a.flac")]["title"] == "Airbag"


def test_a_failed_video_download_is_up_for_matching_again():
    assert "vid2" not in _compose()["torrent"]


def test_one_broken_source_never_hides_the_others():
    def boom():
        raise RuntimeError("video db locked")
    known = _compose(video_rows=boom)
    assert set(known["torrent"]) == {"book1", "mus1"}


# ---------------------------------------------------------------------------
# import_copy: fully-skipped albums are a successful no-op, temp cleaned up
# ---------------------------------------------------------------------------

def test_import_copy_all_skipped_cleans_temp_folder(tmp_path):
    """When every track is already owned, import_copy returns ok (not a
    failure) and removes the redundant temp copies instead of pushing them
    to the manual staging inbox."""
    import logging
    from types import SimpleNamespace
    from unittest.mock import patch

    from core.client_match import import_copy

    folder = tmp_path / "copy-tmp"
    folder.mkdir()
    (folder / "song.mp3").write_bytes(b"copy")

    match = {"id": "album-1", "name": "Test Album", "artist": "A", "source": "deezer"}
    files = [str(folder / "song.mp3")]
    runtime = SimpleNamespace(logger=logging.getLogger("test"))

    fake_result = {
        "success": True,
        "processed": 0,
        "total": 1,
        "errors": [],
        "dispositions": [
            {"track_name": "Song", "file_name": "song.mp3",
             "decision": "skipped_owned", "reason": "already owned"}
        ],
        "summary": {"imported": 0, "skipped_owned": 1, "upgraded": 0},
    }

    with patch("core.imports.album.build_album_import_match_payload") as build, \
         patch("core.imports.routes.album_process",
               return_value=(fake_result, 200)) as proc:
        build.return_value = {
            "success": True,
            "album": {"id": "album-1", "name": "Test Album"},
            "source": "deezer",
            "matches": [{"staging_file": {"full_path": files[0]}}],
        }
        result = import_copy("album", match, str(folder), files, runtime)

    assert result["ok"] is True
    assert not folder.exists()  # temp copies discarded, not staged


def test_import_copy_all_skipped_with_errors_is_not_cleaned(tmp_path):
    """Skips + errors: the temp folder is left alone (user may want the
    errored files), and the import still reports ok for the skipped part."""
    import logging
    from types import SimpleNamespace
    from unittest.mock import patch

    from core.client_match import import_copy

    folder = tmp_path / "copy-tmp"
    folder.mkdir()
    (folder / "song.mp3").write_bytes(b"copy")

    match = {"id": "album-1", "name": "Test Album", "artist": "A", "source": "deezer"}
    files = [str(folder / "song.mp3")]
    runtime = SimpleNamespace(logger=logging.getLogger("test"))

    fake_result = {
        "success": True,
        "processed": 0,
        "total": 1,
        "errors": ["Song: something broke"],
        "dispositions": [
            {"track_name": "Song", "file_name": "song.mp3",
             "decision": "error", "reason": "something broke"}
        ],
        "summary": {"imported": 0, "skipped_owned": 0, "upgraded": 0},
    }

    with patch("core.imports.album.build_album_import_match_payload") as build, \
         patch("core.imports.routes.album_process",
               return_value=(fake_result, 200)):
        build.return_value = {
            "success": True,
            "album": {"id": "album-1", "name": "Test Album"},
            "source": "deezer",
            "matches": [{"staging_file": {"full_path": files[0]}}],
        }
        result = import_copy("album", match, str(folder), files, runtime)

    assert result["ok"] is False  # nothing processed, nothing skipped
    assert folder.exists()  # left for the user to inspect


def test_import_copy_partial_skip_discards_only_skipped_copies(tmp_path):
    """Partial skip: imported tracks proceed, and only the confirmed-
    duplicate temp copies are removed (so they never reach staging)."""
    import logging
    from types import SimpleNamespace
    from unittest.mock import patch

    from core.client_match import import_copy

    folder = tmp_path / "copy-tmp"
    folder.mkdir()
    (folder / "new.mp3").write_bytes(b"new")
    (folder / "dupe.mp3").write_bytes(b"dupe")

    match = {"id": "album-1", "name": "Test Album", "artist": "A", "source": "deezer"}
    files = [str(folder / "new.mp3"), str(folder / "dupe.mp3")]
    runtime = SimpleNamespace(logger=logging.getLogger("test"))

    fake_result = {
        "success": True,
        "processed": 1,
        "total": 2,
        "errors": [],
        "dispositions": [
            {"track_name": "New", "file_name": "new.mp3",
             "decision": "imported", "reason": "not in library"},
            {"track_name": "Dupe", "file_name": "dupe.mp3",
             "decision": "skipped_owned", "reason": "already owned"},
        ],
        "summary": {"imported": 1, "skipped_owned": 1, "upgraded": 0},
    }

    with patch("core.imports.album.build_album_import_match_payload") as build, \
         patch("core.imports.routes.album_process",
               return_value=(fake_result, 200)):
        build.return_value = {
            "success": True,
            "album": {"id": "album-1", "name": "Test Album"},
            "source": "deezer",
            "matches": [{"staging_file": {"full_path": f}} for f in files],
        }
        result = import_copy("album", match, str(folder), files, runtime)

    assert result["ok"] is True
    assert result["imported"] == 1
    assert not (folder / "dupe.mp3").exists()  # duplicate copy discarded
    assert (folder / "new.mp3").exists()  # (post-processor owns this in prod)
