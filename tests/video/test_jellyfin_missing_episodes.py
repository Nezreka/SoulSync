"""jellyfin lists its virtual (missing) episodes to any api-key request, each
with a media source that has no path. the scan stored every one as owned, so
a show folder holding only a poster and an nfo came in fully owned, and a
show with gaps never had them wished for. the post-download "is it indexed
yet" probe said yes for an episode jellyfin only had as virtual."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.video.scanner import VideoLibraryScanner
from core.video.sources import JellyfinVideoSource
from database.video_database import VideoDatabase

VIEWS = {"Items": [{"Id": "tv", "Name": "Shows", "CollectionType": "tvshows"}]}


def _ep(eid, sn, en, real):
    ep = {"Id": eid, "Name": f"Episode {en}", "Type": "Episode", "ParentIndexNumber": sn,
          "IndexNumber": en, "PremiereDate": f"2026-0{sn}-0{en}T00:00:00Z"}
    if real:
        path = f"/media/tv/Show/Season 0{sn}/Show - S0{sn}E0{en}.mkv"
        ep.update(LocationType="FileSystem", Path=path,
                  MediaSources=[{"Id": eid, "Path": path, "Size": 1000}])
    else:
        # what jellyfin 12 sends for a virtual episode: one source, no path
        ep.update(LocationType="Virtual", MediaSources=[{"Id": eid}])
    return ep


SHOWS = {
    # two episodes on disk, two jellyfin knows from metadata
    "s1": {"Id": "s1", "Name": "Dark Winds", "Type": "Series",
           "episodes": [_ep("e1", 1, 1, True), _ep("e2", 1, 2, True),
                        _ep("e3", 1, 3, False), _ep("e4", 1, 4, False)]},
    # a folder with only sidecars in it
    "s2": {"Id": "s2", "Name": "Blade Runner 2099", "Type": "Series",
           "episodes": [_ep("e5", 1, 1, False), _ep("e6", 1, 2, False)]},
}


def _episodes(series_id, params, honor_flag=True):
    eps = SHOWS[series_id]["episodes"]
    if honor_flag and str(params.get("IsMissing")).lower() == "false":
        eps = [e for e in eps if e["LocationType"] != "Virtual"]
    if "season" in params:
        eps = [e for e in eps if e["ParentIndexNumber"] == int(params["season"])]
    return {"Items": eps}


class FakeJellyfin:
    """``honor_flag=False`` plays an old scan that never sent IsMissing."""

    def __init__(self, honor_flag=True):
        self.user_id = "u1"
        self.base_url = "http://jf"
        self.api_key = "k"
        self.last_error = None
        self.honor_flag = honor_flag

    def _make_request(self, path, params=None):
        params = dict(params or {})
        if path.endswith("/Views"):
            return VIEWS
        if path.endswith("/Items"):
            items = [{k: v for k, v in s.items() if k != "episodes"} for s in SHOWS.values()]
            return {"Items": items, "TotalRecordCount": len(items)}
        if path.startswith("/Shows/") and path.endswith("/Episodes"):
            return _episodes(path.split("/")[2], params, self.honor_flag)
        if path.endswith("/Seasons"):
            return {"Items": []}
        return None


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _scan(db, jf, mode="deep"):
    src = JellyfinVideoSource(jf, tv_lib="Shows")
    return VideoLibraryScanner(db).scan_sync(lambda: src, mode=mode, media_type="show")


def _episodes_of(db, title):
    conn = db._get_connection()
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT e.episode_number, e.has_file, e.server_id IS NOT NULL FROM episodes e "
            "JOIN shows s ON s.id = e.show_id WHERE s.title=? ORDER BY e.episode_number", (title,))]
    finally:
        conn.close()


def test_a_virtual_episode_is_not_stored_as_owned(db):
    assert _scan(db, FakeJellyfin())["state"] == "done"
    assert _episodes_of(db, "Dark Winds") == [(1, 1, 1), (2, 1, 1)]


def test_a_folder_of_sidecars_owns_no_episodes(db):
    _scan(db, FakeJellyfin())
    assert _episodes_of(db, "Blade Runner 2099") == []


def test_virtual_rows_an_older_scan_stored_go_back_to_missing(db):
    _scan(db, FakeJellyfin(honor_flag=False))
    assert _episodes_of(db, "Dark Winds") == [(1, 1, 1), (2, 1, 1), (3, 1, 1), (4, 1, 1)]
    _scan(db, FakeJellyfin())
    # they carry an air date, so the scan keeps them as missing episodes
    assert _episodes_of(db, "Dark Winds") == [(1, 1, 1), (2, 1, 1), (3, 0, 0), (4, 0, 0)]
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM media_files WHERE episode_id IS NOT NULL").fetchone()[0] == 2
    finally:
        conn.close()


@pytest.fixture()
def jf_http(monkeypatch):
    """has_item talks to jellyfin with requests directly."""
    import requests

    def fake_get(url, headers=None, timeout=None, params=None):
        params = dict(params or {})
        if url.endswith("/Items"):
            hits = [{"Id": s["Id"], "Name": s["Name"]} for s in SHOWS.values()
                    if s["Name"] == params.get("searchTerm")]
            body = {"Items": hits}
        else:
            body = _episodes(url.split("/Shows/")[1].split("/")[0], params)
        return SimpleNamespace(ok=True, json=lambda: body)

    monkeypatch.setattr(requests, "get", fake_get)
    return JellyfinVideoSource(FakeJellyfin(), tv_lib="Shows")


def test_an_episode_jellyfin_only_has_as_virtual_is_not_indexed_yet(jf_http):
    item = {"title": "Dark Winds", "season_number": 1, "episode_number": 3}
    assert jf_http.has_item("show", item) is False


def test_an_episode_on_disk_is_indexed(jf_http):
    item = {"title": "Dark Winds", "season_number": 1, "episode_number": 2}
    assert jf_http.has_item("show", item) is True
