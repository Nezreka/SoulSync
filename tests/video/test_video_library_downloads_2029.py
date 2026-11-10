"""#2029 step 3: each video library picks its download sources (in order) and
its own client category / label. SoulSync has one torrent and one usenet
client, so the per-library client choice is which of them (and Soulseek /
EXT.to) a library's searches use, in what order, and the category its jobs
get in the client.
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
    vdb.set_setting("tv_path", "/media/tv")
    videoapi._video_db = vdb
    try:
        yield vdb
    finally:
        videoapi._video_db = None


def _kids(db, **opts):
    return libs.create_library(db, {"kind": "movie", "name": "Kids", "path": "/media/kids", **opts})


def test_options_are_cleaned(db):
    lib = _kids(db, sources=["usenet", "TORRENT", "usenet", "ftp", None],
                torrent_category="  kids/4k; rm -rf  ", usenet_category="x" * 99)
    o = lib["options"]
    assert o["sources"] == ["usenet", "torrent"]
    assert o["torrent_category"] == "kids4k rm -rf"
    assert o["usenet_category"] == "x" * 64
    out = libs.update_library(db, lib["id"], {"sources": [], "torrent_category": ""})
    assert out["options"]["sources"] == [] and out["options"]["torrent_category"] == ""
    assert out["options"]["usenet_category"] == "x" * 64      # untouched keys stay


def test_client_category_by_target_folder(db):
    _kids(db, torrent_category="kids", usenet_category="kids-nzb")
    assert libs.client_category(db, "/media/kids", "torrent") == "kids"
    assert libs.client_category(db, "/media/kids/Up (2009)", "extto") == "kids"
    assert libs.client_category(db, "/media/kids", "usenet") == "kids-nzb"
    assert libs.client_category(db, "/media/kids", "soulseek") is None
    assert libs.client_category(db, "/media/movies", "torrent") is None   # main: the client's own
    assert libs.client_category(db, "/elsewhere", "torrent") is None


def test_source_chain():
    assert libs.source_chain(None, ["soulseek"]) == ["soulseek"]
    assert libs.source_chain({"options": {"sources": []}}, ["soulseek", "torrent"]) == ["soulseek", "torrent"]
    assert libs.source_chain({"options": {"sources": ["usenet", "torrent"]}}, ["soulseek"]) == ["usenet", "torrent"]


def test_client_grab_passes_the_category(monkeypatch):
    import core.torrent_clients as tc
    import core.torrent_clients.base as tb
    import core.video.client_grab as cg
    seen = {}

    class _A:
        def is_configured(self):
            return True

    async def smart(adapter, url, category=None, save_path=None, fallback_magnet=None):
        seen["category"] = category
        return "hash"
    monkeypatch.setattr(tc, "get_active_adapter", lambda: _A())
    monkeypatch.setattr(tb, "add_torrent_smart", smart)
    monkeypatch.setattr(cg, "_torrent_category", lambda: "soulsync")
    assert cg.grab("torrent", "magnet:x", category="kids")["ok"]
    assert seen["category"] == "kids"
    assert cg.grab("torrent", "magnet:x")["ok"]
    assert seen["category"] == "soulsync"


def test_drain_search_uses_the_library_order(db, monkeypatch):
    import core.automation.handlers.video_process_wishlist as vpw
    db.set_setting("download_mode", "soulseek")
    kids = _kids(db, sources=["usenet", "torrent"])
    libs.assign_title(db, "movie", 5, kids["id"])
    tried = []
    monkeypatch.setattr(vpw, "_search_one_source",
                        lambda src, item, mt: tried.append(src) or ([], None))
    vpw._default_search({"tmdb_id": 5, "title": "Up", "year": 2009}, "movie")
    assert tried == ["usenet", "torrent"]
    tried.clear()
    vpw._default_search({"tmdb_id": 6, "title": "Heat", "year": 1995}, "movie")
    assert tried == ["soulseek"]                     # no library order: the global one


def test_drain_enqueue_hands_the_category_to_the_client(db, monkeypatch):
    import core.automation.handlers.video_process_wishlist as vpw
    import core.video.client_grab as cg
    import core.video.disk_guard as dg
    import core.video.download_monitor as mon
    _kids(db, usenet_category="kids-nzb")
    seen = []
    monkeypatch.setattr(cg, "grab", lambda src, url, **k: seen.append(k) or {"ok": True, "ref": "r"})
    monkeypatch.setattr(dg, "check_room", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(mon, "ensure_started", lambda *a, **k: None)
    best = {"source": "usenet", "title": "Up.2009.1080p", "download_url": "http://nzb", "size_bytes": 5}
    item = {"tmdb_id": 5, "title": "Up", "year": 2009}
    assert vpw._default_enqueue(item, best, [best], "movie", "/media/kids")
    assert vpw._default_enqueue(item, best, [best], "movie", "/media/movies")
    assert seen[0].get("category") == "kids-nzb" and "category" not in seen[1]


def test_rss_keeps_only_the_library_sources(db):
    from core.video.rss_sync import _library_sources_only
    kids = _kids(db, sources=["usenet"])
    libs.assign_title(db, "movie", 5, kids["id"])
    cands = [{"source": "torrent"}, {"source": "usenet"}]
    assert _library_sources_only(list(cands), {"tmdb_id": 5, "title": "Up"}, "movie") == [{"source": "usenet"}]
    assert _library_sources_only(list(cands), {"tmdb_id": 6, "title": "Heat"}, "movie") == cands


def test_api_grab_uses_the_library_category(db, monkeypatch):
    import api.video as videoapi
    import core.video.client_grab as cg
    import core.video.download_monitor as mon
    kids = _kids(db, torrent_category="kids")
    libs.assign_title(db, "movie", 5, kids["id"])
    seen = []
    monkeypatch.setattr(cg, "grab", lambda src, url, **k: seen.append(k) or {"ok": True, "ref": "h"})
    monkeypatch.setattr(mon, "ensure_started", lambda *a, **k: None)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    body = {"kind": "movie", "title": "Up", "source": "torrent", "download_url": "magnet:x",
            "size_bytes": 8, "media_source": "tmdb"}
    assert client.post("/api/video/downloads/grab", json={**body, "media_id": 5}).get_json()["ok"]
    assert client.post("/api/video/downloads/grab", json={**body, "media_id": 6}).get_json()["ok"]
    assert seen[0].get("category") == "kids" and "category" not in seen[1]
