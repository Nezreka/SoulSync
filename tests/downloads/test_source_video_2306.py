"""#2306: a song only YouTube has downloads from its own video"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from core.discovery.wing_it import stub_track_id
from core.downloads import source_video as sv

STUB = stub_track_id("Some Channel", "A Song Only Here")


class _Db:
    def __init__(self, path):
        self.path = path
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE mirrored_playlists (id INTEGER PRIMARY KEY, source TEXT)")
            c.execute("CREATE TABLE mirrored_playlist_tracks (id INTEGER PRIMARY KEY, playlist_id INTEGER, "
                      "source_track_id TEXT, extra_data TEXT)")

    def _get_connection(self):
        return sqlite3.connect(self.path)

    def add(self, source, video, stub):
        with sqlite3.connect(self.path) as c:
            pid = c.execute("INSERT INTO mirrored_playlists (source) VALUES (?)", (source,)).lastrowid
            c.execute("INSERT INTO mirrored_playlist_tracks (playlist_id, source_track_id, extra_data) VALUES (?,?,?)",
                      (pid, video, json.dumps({"matched_data": {"id": stub}})))


def test_the_stub_carries_its_video():
    assert sv.video_id_for({"id": STUB, "youtube_video_id": "dQw4w9WgXcQ"}) == "dQw4w9WgXcQ"
    assert sv.video_id_for({"id": STUB, "matched_data": {"youtube_video_id": "dQw4w9WgXcQ"}}) == "dQw4w9WgXcQ"


def test_an_older_stub_is_found_through_its_youtube_playlist(tmp_path):
    db = _Db(str(tmp_path / "m.db"))
    db.add("youtube", "abcdefghijk", STUB)
    assert sv.video_id_for({"id": STUB}, db) == "abcdefghijk"


def test_only_a_stub_and_only_a_youtube_playlist(tmp_path):
    db = _Db(str(tmp_path / "m.db"))
    db.add("spotify", "abcdefghijk", STUB)              # a spotify playlist's stub: no video
    assert sv.video_id_for({"id": STUB}, db) is None
    # a song a catalogue identified downloads the usual way
    assert sv.video_id_for({"id": "4uLU6hMCjMI75M1A2tKUQC", "youtube_video_id": "dQw4w9WgXcQ"}) is None
    assert sv.video_id_for({"id": STUB, "youtube_video_id": "not a video id"}) is None
    assert sv.video_id_for(None) is None


def test_the_candidate_is_a_youtube_result_for_that_video():
    c = sv.candidate_for("dQw4w9WgXcQ", {"id": STUB, "name": "A Song Only Here",
                                          "artists": [{"name": "Some Channel"}], "duration_ms": 201000})
    assert c.username == "youtube" and c.filename == "dQw4w9WgXcQ||A Song Only Here"
    assert c.artist == "Some Channel" and c.title == "A Song Only Here" and c.duration == 201000


def _worker(monkeypatch, track_info, client=object(), attempt=True):
    from core.downloads import task_worker as tw
    tw.download_tasks["t1"] = {"track_info": track_info}
    monkeypatch.setattr(sv, "youtube_client", lambda deps: client)
    monkeypatch.setattr("database.music_database.get_database", lambda: None)
    calls = []
    deps = SimpleNamespace(attempt_download_with_candidates=lambda tid, cands, track, batch:
                           calls.append([c.filename for c in cands]) or attempt)
    try:
        got = tw._try_source_video("t1", "b1", SimpleNamespace(name="A Song Only Here"), deps)
        again = tw._try_source_video("t1", "b1", SimpleNamespace(name="A Song Only Here"), deps)
    finally:
        tw.download_tasks.pop("t1", None)
    return got, again, calls


def test_the_worker_takes_the_video_once_before_searching(monkeypatch):
    got, again, calls = _worker(monkeypatch, {"id": STUB, "name": "A Song Only Here",
                                              "youtube_video_id": "dQw4w9WgXcQ"})
    assert got is True and calls == [["dQw4w9WgXcQ||A Song Only Here"]]
    assert again is False, "a retry searches instead of taking the same video again"


def test_the_worker_searches_as_usual_otherwise(monkeypatch):
    # no youtube source set up
    got, _, calls = _worker(monkeypatch, {"id": STUB, "youtube_video_id": "dQw4w9WgXcQ"}, client=None)
    assert got is False and calls == []
    # not a stub
    got, _, calls = _worker(monkeypatch, {"id": "4uLU6hMCjMI75M1A2tKUQC", "youtube_video_id": "dQw4w9WgXcQ"})
    assert got is False and calls == []
    # the video failed: the search runs
    got, _, calls = _worker(monkeypatch, {"id": STUB, "youtube_video_id": "dQw4w9WgXcQ"}, attempt=False)
    assert got is False and len(calls) == 1
