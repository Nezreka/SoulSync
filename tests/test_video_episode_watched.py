"""One episode or one season can be marked watched, not just the whole show.

The detail page's episode menu only offered grabs: the only watched control
was the show-wide toggle in More. /watched now takes an episode or season id,
flips just those rows, recounts the show, and pushes the same item to the
server (plex markPlayed / jellyfin PlayedItems both take any item id).
"""
from tests.test_video_api import _make_client


def _seed(db):
    return db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Show", "tmdb_id": 1396, "seasons": [
            {"season_number": 1, "server_id": "se1", "episodes": [
                {"episode_number": 1, "title": "E1", "server_id": "ep1",
                 "file": {"relative_path": "e1.mkv", "size_bytes": 10}},
                {"episode_number": 2, "title": "E2", "server_id": "ep2",
                 "file": {"relative_path": "e2.mkv", "size_bytes": 10}}]},
            {"season_number": 2, "server_id": "se2", "episodes": [
                {"episode_number": 1, "title": "F1", "server_id": "fp1",
                 "file": {"relative_path": "f1.mkv", "size_bytes": 10}}]}]})


def _ids(db, sid):
    d = db.show_detail(sid)
    eps = {e["title"]: e for s in d["seasons"] for e in s["episodes"]}
    seasons = {s["season_number"]: s["id"] for s in d["seasons"]}
    return eps, seasons


def _watched(db, sid):
    d = db.show_detail(sid)
    return ({e["title"]: bool(e.get("watched")) for s in d["seasons"] for e in s["episodes"]},
            d.get("watched_episodes"))


def test_one_episode_flips_alone_and_the_show_recounts(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        db = videoapi._video_db
        sid = _seed(db)
        eps, _ = _ids(db, sid)
        r = client.post("/api/video/detail/episode/%d/watched" % eps["E2"]["id"], json={"watched": True})
        assert r.status_code == 200 and r.get_json()["ok"] is True
        flags, count = _watched(db, sid)
        assert flags == {"E1": False, "E2": True, "F1": False}
        assert count == 1
        client.post("/api/video/detail/episode/%d/watched" % eps["E2"]["id"], json={"watched": False})
        assert _watched(db, sid) == ({"E1": False, "E2": False, "F1": False}, 0)
    finally:
        videoapi._video_db = None


def test_a_season_flips_only_its_own_episodes(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        db = videoapi._video_db
        sid = _seed(db)
        _, seasons = _ids(db, sid)
        r = client.post("/api/video/detail/season/%d/watched" % seasons[1], json={"watched": True})
        assert r.status_code == 200
        assert _watched(db, sid) == ({"E1": True, "E2": True, "F1": False}, 2)
    finally:
        videoapi._video_db = None


def test_an_unknown_episode_is_a_404_and_a_bad_kind_a_400(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        _seed(videoapi._video_db)
        assert client.post("/api/video/detail/episode/999999/watched", json={"watched": True}).status_code == 404
        assert client.post("/api/video/detail/person/1/watched", json={"watched": True}).status_code == 400
    finally:
        videoapi._video_db = None


def test_the_server_gets_the_episode_not_the_show(tmp_path):
    from core.video import metadata as med
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    sid = _seed(db)
    eps, seasons = _ids(db, sid)

    class _Plex:
        server_name = "plex"

        def __init__(self):
            self.calls = []

        def set_watched(self, server_id, watched, kind="movie"):
            self.calls.append((server_id, watched, kind))
            return {"ok": True}

    src = _Plex()
    res = med.set_watched(db, "episode", eps["E1"]["id"], True, source=src)
    assert res == {"ok": True, "watched": True, "pushed": True}
    med.set_watched(db, "season", seasons[2], True, source=src)
    assert src.calls == [("ep1", True, "episode"), ("se2", True, "season")]


def test_a_season_leaves_episodes_the_server_does_not_hold(tmp_path):
    client, videoapi = _make_client(tmp_path)
    try:
        db = videoapi._video_db
        sid = _seed(db)
        _, seasons = _ids(db, sid)
        conn = db._get_connection()
        try:   # a tmdb-only row in season 1: no server item, no file
            conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, title) "
                         "VALUES (?, ?, 1, 3, 'E3')", (sid, seasons[1]))
            conn.commit()
        finally:
            conn.close()
        client.post("/api/video/detail/season/%d/watched" % seasons[1], json={"watched": True})
        flags, count = _watched(db, sid)
        assert flags["E1"] and flags["E2"] and not flags["E3"]
        assert count == 2
    finally:
        videoapi._video_db = None
