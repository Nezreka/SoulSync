"""#2029 step 4: each video library can limit its searches to some Prowlarr
indexers. None ticked = every indexer (the global allowlist), as before.
"""

from __future__ import annotations

import pytest
from flask import Flask

import core.video.libraries as libs
from database.video_database import VideoDatabase


@pytest.fixture(autouse=True)
def _no_tmdb(monkeypatch):
    monkeypatch.setattr(libs, "title_tags", lambda db, kind, tmdb_id: {"genres": [], "keywords": []})


@pytest.fixture()
def db(tmp_path):
    import api.video as videoapi
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    vdb.set_setting("movies_path", "/media/movies")
    videoapi._video_db = vdb
    try:
        yield vdb
    finally:
        videoapi._video_db = None


def _uhd(db, ids):
    lib = libs.create_library(db, {"kind": "movie", "name": "UHD", "path": "/media/uhd",
                                   "indexer_ids": ids})
    libs.assign_title(db, "movie", 5, lib["id"])
    return lib


def test_indexer_ids_are_cleaned(db):
    lib = _uhd(db, [3, "7", 3, -1, "x", 0, None])
    assert lib["options"]["indexer_ids"] == [3, 7]
    assert libs.indexer_ids_for(lib) == [3, 7]
    assert libs.indexer_ids_for(None) is None
    out = libs.update_library(db, lib["id"], {"indexer_ids": []})
    assert libs.indexer_ids_for(out) is None


class _Client:
    def __init__(self):
        self.ids = []

    def is_configured(self):
        return True

    def _search_sync(self, q, cats, ids, limit, **kw):
        self.ids.append(ids)
        return []


def test_prowlarr_search_takes_a_library_allowlist(monkeypatch):
    import core.video.prowlarr_search as ps
    c = _Client()
    monkeypatch.setattr(ps, "_client", lambda: c)
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [1, 2])
    ps.prowlarr_search("movie", "Heat", year=1995, indexer_ids=[7])
    assert c.ids and all(ids == [7] for ids in c.ids)
    c.ids.clear()
    ps.prowlarr_search("movie", "Heat", year=1995)
    assert all(ids == [1, 2] for ids in c.ids)          # the global allowlist, as before


def test_drain_searches_the_library_indexers(db, monkeypatch):
    import core.automation.handlers.video_process_wishlist as vpw
    import core.video.prowlarr_search as ps
    _uhd(db, [7])
    seen = []
    monkeypatch.setattr(ps, "prowlarr_search",
                        lambda *a, **k: seen.append(k.get("indexer_ids")) or {"configured": True, "hits": []})
    vpw._search_one_source("usenet", {"tmdb_id": 5, "title": "Up", "year": 2009}, "movie")
    vpw._search_one_source("usenet", {"tmdb_id": 6, "title": "Heat", "year": 1995}, "movie")
    assert seen == [[7], None]


def test_rss_keeps_only_the_library_indexers(db):
    from core.video.rss_sync import _library_sources_only
    _uhd(db, [7])
    cands = [{"source": "torrent", "indexer_id": 7}, {"source": "torrent", "indexer_id": 8},
             {"source": "torrent", "indexer_id": None}]
    got = _library_sources_only(list(cands), {"tmdb_id": 5, "title": "Up"}, "movie")
    assert got == [cands[0], cands[2]]                  # EXT.to has no indexer, it stays
    assert _library_sources_only(list(cands), {"tmdb_id": 6, "title": "Heat"}, "movie") == cands


def test_manual_search_uses_the_library_indexers(db, monkeypatch):
    import api.video as videoapi
    import core.video.prowlarr_search as ps
    _uhd(db, [7])
    seen = []
    monkeypatch.setattr(ps, "prowlarr_search",
                        lambda *a, **k: seen.append(k.get("indexer_ids")) or {"configured": True, "hits": []})
    from api.video.downloads import _torrent_lane_hits
    _torrent_lane_hits({"kind": "movie", "media_id": 5, "media_source": "tmdb", "title": "Up"},
                       "movie", "Up", None, None, "usenet")
    _torrent_lane_hits({"kind": "movie", "media_id": 6, "media_source": "tmdb", "title": "Heat"},
                       "movie", "Heat", None, None, "usenet")
    assert seen == [[7], None]
    assert videoapi  # the blueprint module imports cleanly


def test_api_saves_indexers(db):
    import api.video as videoapi
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    lib = client.post("/api/video/libraries/folders", json={
        "kind": "movie", "name": "UHD", "path": "/media/uhd", "indexer_ids": [4, 9]}).get_json()["library"]
    assert lib["options"]["indexer_ids"] == [4, 9]
    out = client.put("/api/video/libraries/folders/%d" % lib["id"], json={"indexer_ids": []}).get_json()
    assert out["library"]["options"]["indexer_ids"] == []
