"""Optional 4K libraries: a Movies 4K and a TV 4K folder beside the main ones.

Each is off until its switch is on AND a folder is set; a switched-off one is
invisible everywhere. A title files into it when its quality profile says
``library: 4k``. Anime keeps its own folder either way (that one is by type,
the 4K one by quality).

Covers: the settings helpers, the pure routing, the profile field, the
wishlist drain, the grab routes, and the folder settings round-trip.
"""

from __future__ import annotations

import pytest
from flask import Flask

from core.video.download_pipeline import target_dir_for
from core.video.library_roots import four_k_path, library_roots, output_paths
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _paths(db, *, movies_4k=None, tv_4k=None):
    db.set_setting("movies_path", "/media/movies")
    db.set_setting("tv_path", "/media/tv")
    if movies_4k is not None:
        db.set_setting("movies_4k_enabled", "1")
        db.set_setting("movies_4k_path", movies_4k)
    if tv_4k is not None:
        db.set_setting("tv_4k_enabled", "1")
        db.set_setting("tv_4k_path", tv_4k)


# ── settings helpers ──────────────────────────────────────────────────────────

def test_a_4k_folder_is_off_by_default(db):
    _paths(db)
    assert four_k_path(db, "movie") == "" and four_k_path(db, "episode") == ""
    out = output_paths(db)
    assert out["movies_4k_path"] == "" and out["tv_4k_path"] == ""


def test_a_4k_folder_needs_both_the_switch_and_a_path(db):
    _paths(db)
    db.set_setting("movies_4k_path", "/media/movies-4k")       # path, switch off
    assert four_k_path(db, "movie") == ""
    db.set_setting("movies_4k_enabled", "1")
    assert four_k_path(db, "movie") == "/media/movies-4k"
    db.set_setting("movies_4k_path", "   ")                    # switch on, no path
    assert four_k_path(db, "movie") == ""
    assert four_k_path(db, "youtube") == ""                    # no 4K library for it


def test_output_paths_falls_back_to_the_transfer_folder_for_movies(db):
    db.set_setting("transfer_path", "/legacy")
    assert output_paths(db)["movies_path"] == "/legacy"


def test_library_roots_include_a_4k_folder_only_while_it_is_on(db):
    _paths(db, movies_4k="/media/movies-4k", tv_4k="/media/tv-4k")
    assert "/media/movies-4k" in library_roots(db, "movie")
    assert "/media/tv-4k" in library_roots(db, "episode")
    assert "/media/tv-4k" not in library_roots(db, "movie")
    db.set_setting("movies_4k_enabled", "0")
    assert "/media/movies-4k" not in library_roots(db, "movie")


# ── pure routing ──────────────────────────────────────────────────────────────

PATHS = {"movies_path": "/movies", "tv_path": "/tv", "anime_path": "/anime",
         "movies_4k_path": "/movies-4k", "tv_4k_path": "/tv-4k"}


@pytest.mark.parametrize("kind,series_type,library,want", [
    ("movie", None, "4k", "/movies-4k"),
    ("movie", None, "4K", "/movies-4k"),           # case-insensitive
    ("movie", None, "main", "/movies"),
    ("movie", None, None, "/movies"),
    ("show", "standard", "4k", "/tv-4k"),
    ("episode", None, "4k", "/tv-4k"),
    ("show", "anime", "4k", "/anime"),             # anime keeps its own folder
    ("show", "anime", "main", "/anime"),
    ("show", "standard", "main", "/tv"),
    ("youtube", None, "4k", ""),                   # no 4K library for youtube
])
def test_target_dir_for_routes_4k_by_profile(kind, series_type, library, want):
    assert target_dir_for(kind, PATHS, series_type, library) == want


def test_target_dir_for_a_4k_profile_with_the_folder_off_uses_the_main_one():
    off = dict(PATHS, movies_4k_path="", tv_4k_path="")
    assert target_dir_for("movie", off, None, "4k") == "/movies"
    assert target_dir_for("show", off, "standard", "4k") == "/tv"


def test_anime_without_an_anime_folder_still_gets_4k():
    paths = dict(PATHS, anime_path="")
    assert target_dir_for("show", paths, "anime", "4k") == "/tv-4k"


# ── the quality profile field ─────────────────────────────────────────────────

def test_profile_library_defaults_to_main_and_rejects_junk():
    from core.video.quality_profile import default_profile, normalize
    assert default_profile()["library"] == "main"
    assert normalize({"library": "4k"})["library"] == "4k"
    assert normalize({"library": " 4K "})["library"] == "4k"
    assert normalize({"library": "8k"})["library"] == "main"
    assert normalize({})["library"] == "main"


def test_named_profile_keeps_its_library(db):
    from core.video.quality_profile import profile_by_id, save_named
    entry = save_named(db, None, "UHD", {"library": "4k"})
    assert profile_by_id(db, entry["id"])["library"] == "4k"
    assert profile_by_id(db, None)["library"] == "main"


# ── the wishlist drain ────────────────────────────────────────────────────────

class _Deps:
    def update_progress(self, automation_id, **kw):
        pass


def _drain(items, *, media_type="movie", root="/movies", four_k="/movies-4k",
           anime_root="/anime", route_type=None):
    from core.automation.handlers.video_process_wishlist import (
        auto_video_process_wishlist, item_key)
    enq = {}

    def search(item, mt):
        return [{"filename": "rel-%s" % item_key(item, mt)[1], "title": "r", "username": "u",
                 "size_bytes": 1000, "quality_label": "WEBDL-2160p", "accepted": True,
                 "score": 10}]

    def enqueue(item, best, cands, mt, target):
        enq[item.get("show_tmdb_id") or item.get("tmdb_id")] = target
        return True

    auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 2, "max_simultaneous_downloads": 10},
        _Deps(), media_type=media_type,
        fetch_items=lambda mt: items, active_keys=lambda mt: set(),
        target_dir=lambda mt: root, anime_dir=lambda: anime_root,
        four_k_dir=lambda mt: four_k,
        route_type=route_type or (lambda it: it.get("route_series_type")),
        profile_library=lambda it: it.get("lib") or "main",
        search=search, enqueue=enqueue)
    return enq


def test_drain_files_4k_profile_movies_in_the_4k_folder():
    items = [{"tmdb_id": 1, "title": "A", "year": "2020", "lib": "4k"},
             {"tmdb_id": 2, "title": "B", "year": "2020"}]
    assert _drain(items) == {1: "/movies-4k", 2: "/movies"}


def test_drain_with_the_4k_folder_off_files_everything_in_main():
    items = [{"tmdb_id": 1, "title": "A", "year": "2020", "lib": "4k"}]
    assert _drain(items, four_k="") == {1: "/movies"}


def test_drain_episodes_anime_first_then_4k():
    def ep(tmdb, route, lib=None):
        return {"show_tmdb_id": tmdb, "show_title": "S%d" % tmdb, "season_number": 1,
                "episode_number": 1, "route_series_type": route, "lib": lib}
    items = [ep(1, "anime", "4k"), ep(2, "standard", "4k"), ep(3, "standard")]
    got = _drain(items, media_type="episode", root="/tv", four_k="/tv-4k")
    assert got == {1: "/anime", 2: "/tv-4k", 3: "/tv"}


def test_item_target_dir_skips_the_profile_lookup_while_4k_is_off():
    from core.automation.handlers.video_process_wishlist import item_target_dir

    def boom(_it):
        raise AssertionError("asked for a profile with no 4K folder")
    assert item_target_dir({}, "movie", "/movies", "", "", lambda it: None, boom) == "/movies"


def test_default_lookups_fail_safe(monkeypatch):
    import api.video as videoapi
    from core.automation.handlers.video_process_wishlist import (
        _default_four_k_dir, _default_profile_library)
    monkeypatch.setattr(videoapi, "get_video_db",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert _default_four_k_dir("movie") == ""
    assert _default_profile_library({"quality_profile_id": 3}) == "main"


# ── the grab routes ───────────────────────────────────────────────────────────

@pytest.fixture()
def api(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.download_monitor as mon
    import core.video.slskd_download as slskd
    monkeypatch.setattr(slskd, "start_download", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(mon, "ensure_started", lambda *a, **k: None)
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    _paths(vdb, movies_4k="/media/movies-4k", tv_4k="/media/tv-4k")
    videoapi._video_db = vdb
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), vdb
    finally:
        videoapi._video_db = None


def _grab(client, **extra):
    body = {"kind": "movie", "title": "T", "source": "soulseek", "username": "u",
            "size_bytes": 8, "quality_label": "2160p"}
    body.update(extra)
    out = client.post("/api/video/downloads/grab", json=body).get_json()
    assert out["ok"] is True, out
    return out["id"]


def test_grab_routes_by_the_titles_profile(api):
    client, vdb = api
    from core.video.quality_profile import save_named
    uhd = save_named(vdb, None, "UHD", {"library": "4k"})["id"]
    hd = save_named(vdb, None, "HD", {"library": "main"})["id"]
    target = lambda i: vdb.get_video_download(i)["target_dir"]   # noqa: E731
    # the request's own profile
    assert target(_grab(client, filename="a.mkv", quality_profile_id=uhd)) == "/media/movies-4k"
    assert target(_grab(client, filename="b.mkv", quality_profile_id=hd)) == "/media/movies"
    # a wished title's assignment, by TMDB id
    vdb.add_movie_to_wishlist(900, "Wished")
    vdb.set_wishlist_quality_for_tmdb(900, uhd)
    assert target(_grab(client, filename="c.mkv", media_id=900,
                        media_source="tmdb")) == "/media/movies-4k"
    # nothing assigned: Default (main)
    assert target(_grab(client, filename="d.mkv", media_id=901,
                        media_source="tmdb")) == "/media/movies"
    # switched off: the 4K profile falls back to the main folder
    vdb.set_setting("movies_4k_enabled", "0")
    assert target(_grab(client, filename="e.mkv", quality_profile_id=uhd)) == "/media/movies"


def test_grab_show_on_a_4k_profile_goes_to_tv_4k(api):
    client, vdb = api
    from core.video.quality_profile import save_named
    uhd = save_named(vdb, None, "UHD", {"library": "4k"})["id"]
    out = _grab(client, kind="show", filename="s.mkv", quality_profile_id=uhd,
                search_ctx={"scope": "episode", "season": 1, "episode": 1})
    assert vdb.get_video_download(out)["target_dir"] == "/media/tv-4k"


def test_folder_settings_round_trip_4k(api):
    client, _vdb = api
    cfg = client.get("/api/video/downloads/config").get_json()
    assert cfg["movies_4k_enabled"] is True and cfg["movies_4k_path"] == "/media/movies-4k"
    client.post("/api/video/downloads/config",
                json={"tv_4k_enabled": False, "tv_4k_path": "  /data/tv-4k  "})
    cfg = client.get("/api/video/downloads/config").get_json()
    assert cfg["tv_4k_enabled"] is False and cfg["tv_4k_path"] == "/data/tv-4k"
    # a save without the keys leaves them alone
    client.post("/api/video/downloads/config", json={"tv_path": "/media/tv2"})
    cfg = client.get("/api/video/downloads/config").get_json()
    assert cfg["movies_4k_enabled"] is True and cfg["tv_4k_path"] == "/data/tv-4k"


def test_a_fresh_install_reports_4k_off(tmp_path, monkeypatch):
    import api.video as videoapi
    vdb = VideoDatabase(database_path=str(tmp_path / "fresh.db"))
    monkeypatch.setattr(videoapi, "_video_db", vdb)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    cfg = app.test_client().get("/api/video/downloads/config").get_json()
    assert cfg["movies_4k_enabled"] is False and cfg["tv_4k_enabled"] is False
    assert cfg["movies_4k_path"] == "" and cfg["tv_4k_path"] == ""


# ── docker installs start on the compose folders ─────────────────────────────

def test_docker_seeds_only_mapped_never_saved_folders(db):
    from core.video.library_roots import seed_docker_defaults
    mounts = ["/", "/media/movies", "/media/tv"]          # youtube isn't mapped
    assert seed_docker_defaults(db, docker=True, mounts=mounts) == {
        "movies_path": "/media/movies", "tv_path": "/media/tv"}
    assert db.get_setting("movies_path") == "/media/movies"
    assert db.get_setting("youtube_path") is None          # would land inside the container


def test_docker_seeding_never_touches_a_saved_value(db):
    from core.video.library_roots import seed_docker_defaults
    db.set_setting("movies_path", "/data/films")
    db.set_setting("tv_path", "")                          # cleared on purpose
    mounts = ["/", "/media/movies", "/media/tv", "/media/youtube"]
    assert seed_docker_defaults(db, docker=True, mounts=mounts) == {"youtube_path": "/media/youtube"}
    assert db.get_setting("movies_path") == "/data/films"
    assert db.get_setting("tv_path") == ""


def test_docker_seeding_keeps_the_legacy_transfer_folder(db):
    from core.video.library_roots import seed_docker_defaults
    db.set_setting("transfer_path", "/legacy")
    seed_docker_defaults(db, docker=True, mounts=["/", "/media/movies"])
    assert db.get_setting("movies_path") is None
    assert output_paths(db)["movies_path"] == "/legacy"


def test_bare_metal_and_unknown_mounts_seed_nothing(db):
    from core.video.library_roots import seed_docker_defaults
    assert seed_docker_defaults(db, docker=False, mounts=["/", "/media/movies"]) == {}
    assert seed_docker_defaults(db, docker=True, mounts=[]) == {}
    assert db.get_setting("movies_path") is None


def test_get_video_db_seeds_on_first_build(monkeypatch, tmp_path):
    import api.video as videoapi
    import core.onboarding.environment as env
    import core.onboarding.paths as paths
    import database.video_database as vdbmod
    built = []

    class _Db(VideoDatabase):
        def __init__(self):
            super().__init__(database_path=str(tmp_path / "v.db"))
            built.append(self)

    monkeypatch.setattr(vdbmod, "VideoDatabase", _Db)
    monkeypatch.setattr(env, "is_docker", lambda *a, **k: True)
    monkeypatch.setattr(paths, "mount_points", lambda *a, **k: ["/", "/media/tv"])
    monkeypatch.setattr(videoapi, "_video_db", None)
    db = videoapi.get_video_db()
    assert built and db.get_setting("tv_path") == "/media/tv"
    assert db.get_setting("movies_path") is None
