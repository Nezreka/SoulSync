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


def test_soulseek_audiobook_folders_are_not_client_rows():
    known = audiobook_known([{"source": "soulseek", "client_id": "{...}", "status": "downloading"}])
    assert known == {"torrent": {}, "usenet": {}}


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
