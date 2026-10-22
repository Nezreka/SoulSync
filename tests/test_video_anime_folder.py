"""Anime library folder: episodes of anime-type shows import to ``anime_path``.

SoulSync already knows which shows are anime (the P8 series type, set by hand
or defaulted from an Anime genre) and searches them the anime way, but every TV
download was filed under ``tv_path``. An optional ``anime_path`` setting now
routes anime-type shows there, so an anime collection kept apart from TV (its
own media-server library) keeps receiving new episodes. Empty = off: everything
goes to ``tv_path`` exactly as before.

Covers: the pure routing helpers, the effective-series-type lookup, the wishlist
drain, the grab / pack / adopt routes, manual import's id reading, the folder
settings round-trip, and the anime folder counting as a TV library root.
"""

from __future__ import annotations

import os

import pytest
from flask import Flask

from core.video.download_pipeline import target_dir_for, tv_root_for
from database.video_database import VideoDatabase


# ── pure routing ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("series_type,anime_root,want", [
    ("anime", "/anime", "/anime"),
    ("ANIME", "/anime", "/anime"),          # case-insensitive
    (" anime ", "/anime", "/anime"),
    ("anime", "", "/tv"),                   # setting off -> TV, as before
    ("anime", None, "/tv"),
    ("anime", "   ", "/tv"),                # whitespace-only counts as unset
    ("standard", "/anime", "/tv"),
    ("daily", "/anime", "/tv"),
    (None, "/anime", "/tv"),
    ("", "/anime", "/tv"),
])
def test_tv_root_for(series_type, anime_root, want):
    assert tv_root_for(series_type, "/tv", anime_root) == want


def test_target_dir_for_routes_only_tv_kinds_by_series_type():
    paths = {"movies_path": "/movies", "tv_path": "/tv", "anime_path": "/anime",
             "youtube_path": "/yt"}
    for kind in ("show", "tv", "episode", "season", "series", "SHOW"):
        assert target_dir_for(kind, paths, "anime") == "/anime"
        assert target_dir_for(kind, paths, "standard") == "/tv"
        assert target_dir_for(kind, paths) == "/tv"            # old call shape unchanged
    # a series type never moves a movie or a youtube download
    assert target_dir_for("movie", paths, "anime") == "/movies"
    assert target_dir_for("youtube", paths, "anime") == "/yt"
    assert target_dir_for("nonsense", paths, "anime") == ""
    # no anime_path key at all (an older config dict) -> TV
    assert target_dir_for("show", {"tv_path": "/tv"}, "anime") == "/tv"


# ── effective series type ─────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed_show(db, tmdb_id, *, genres=(), server="plex", sid=None):
    return db.upsert_show_tree(server, {
        "server_id": sid or "s%s" % tmdb_id, "title": "Show %s" % tmdb_id,
        "tmdb_id": tmdb_id, "genres": list(genres),
        "seasons": [{"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "E1"}]}]})


def test_effective_series_type_by_hand_genre_and_default(db):
    plain = _seed_show(db, 10)
    by_hand = _seed_show(db, 20)
    by_genre = _seed_show(db, 30, genres=["Animation", "Anime"])
    db.set_show_series_type(by_hand, "anime")
    assert db.effective_series_type(tmdb_id=10) == "standard"
    assert db.effective_series_type(tmdb_id=20) == "anime"
    assert db.effective_series_type(tmdb_id=30) == "anime"            # Anime genre default
    # library ids resolve the same way
    assert db.effective_series_type(library_id=plain) == "standard"
    assert db.effective_series_type(library_id=by_hand) == "anime"
    assert db.effective_series_type(library_id=by_genre) == "anime"
    # daily is reported too (it routes like standard)
    db.set_show_series_type(plain, "daily")
    assert db.effective_series_type(tmdb_id=10) == "daily"


def test_effective_series_type_matches_the_wishlist_rule(db):
    # the drain reads series_type off episode_wishlist_to_download; the routes
    # read effective_series_type. They must agree, or a manual grab and the
    # drain would file the same show in different folders.
    for tmdb, genres, set_to in ((40, (), None), (41, (), "anime"),
                                 (42, ("Anime",), None), (43, (), "daily")):
        sid = _seed_show(db, tmdb, genres=genres)
        if set_to:
            db.set_show_series_type(sid, set_to)
        db.add_episodes_to_wishlist(tmdb, "Show %s" % tmdb, [
            {"season_number": 1, "episode_number": 1, "air_date": "2026-07-01"}])
    items = {int(i["show_tmdb_id"]): i for i in db.episode_wishlist_to_download()}
    for tmdb in (40, 41, 42, 43):
        assert (items[tmdb]["series_type"] or "standard") == db.effective_series_type(tmdb_id=tmdb)


def test_effective_series_type_sees_sibling_rows_from_other_servers(db):
    _seed_show(db, 50, server="plex", sid="p50")
    jelly = _seed_show(db, 50, server="jellyfin", sid="j50")
    db.set_show_series_type(jelly, "anime")
    assert db.effective_series_type(tmdb_id=50) == "anime"


def test_effective_series_type_never_raises(db):
    assert db.effective_series_type() == "standard"
    assert db.effective_series_type(tmdb_id=999999) == "standard"
    assert db.effective_series_type(library_id=999999) == "standard"
    assert db.effective_series_type(tmdb_id="not-a-number") == "standard"


# ── the wishlist drain ────────────────────────────────────────────────────────

class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _drain(items, *, root="/tv", anime_root="/anime", media_type="episode"):
    from core.automation.handlers.video_process_wishlist import (
        auto_video_process_wishlist, item_key)
    enq = []

    def search(item, mt):
        return [{"filename": "rel-%s" % item_key(item, mt)[1], "title": "r", "username": "u",
                 "size_bytes": 1000, "quality_label": "WEBDL-1080p", "accepted": True,
                 "score": 10}]

    def enqueue(item, best, cands, mt, target):
        enq.append((item.get("show_tmdb_id") or item.get("tmdb_id"), target))
        return True

    res = auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 2, "max_simultaneous_downloads": 10},
        _Deps(), media_type=media_type,
        fetch_items=lambda mt: items, active_keys=lambda mt: set(),
        target_dir=lambda mt: root, anime_dir=lambda: anime_root,
        search=search, enqueue=enqueue)
    return res, dict(enq)


def _ep(tmdb, series_type):
    return {"show_tmdb_id": tmdb, "show_title": "S%d" % tmdb, "season_number": 1,
            "episode_number": 1, "series_type": series_type}


def test_drain_files_anime_shows_under_the_anime_folder():
    items = [_ep(1, "anime"), _ep(2, None), _ep(3, "daily"), _ep(4, "standard")]
    res, roots = _drain(items)
    assert res["grabbed"] == 4
    assert roots == {1: "/anime", 2: "/tv", 3: "/tv", 4: "/tv"}


def test_drain_without_an_anime_folder_files_everything_under_tv():
    res, roots = _drain([_ep(1, "anime"), _ep(2, None)], anime_root="")
    assert roots == {1: "/tv", 2: "/tv"}


def test_drain_movies_ignore_the_anime_folder():
    items = [{"tmdb_id": 7, "title": "Akira", "year": "1988", "series_type": "anime"}]
    _res, roots = _drain(items, root="/movies", media_type="movie")
    assert roots == {7: "/movies"}


def test_drain_default_anime_lookup_fails_safe(monkeypatch):
    # no injected anime_dir and no video DB wired: routing falls back to TV
    import api.video as videoapi
    from core.automation.handlers.video_process_wishlist import _default_anime_dir
    monkeypatch.setattr(videoapi, "_video_db", None, raising=False)
    monkeypatch.setattr(videoapi, "get_video_db",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert _default_anime_dir() == ""


# ── the grab routes ───────────────────────────────────────────────────────────

@pytest.fixture()
def api(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.download_monitor as mon
    import core.video.slskd_download as slskd
    monkeypatch.setattr(slskd, "start_download", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(mon, "ensure_started", lambda *a, **k: None)
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    vdb.set_setting("movies_path", "/media/movies")
    vdb.set_setting("tv_path", "/media/tv")
    vdb.set_setting("anime_path", "/media/anime")
    videoapi._video_db = vdb
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), vdb
    finally:
        videoapi._video_db = None


def _target(vdb, dl_id):
    return vdb.get_video_download(dl_id)["target_dir"]


def test_grab_routes_anime_by_tmdb_and_library_id(api):
    client, vdb = api
    anime_lib = _seed_show(vdb, 500, genres=["Anime"])
    _seed_show(vdb, 501)

    n = [0]

    def grab(**extra):
        # a distinct episode each time: a grab of an episode already in flight
        # is refused as {'already': True}
        n[0] += 1
        body = {"kind": "show", "title": "T", "source": "soulseek", "username": "u",
                "filename": extra.pop("filename"), "size_bytes": 8, "quality_label": "720p",
                "search_ctx": {"scope": "episode", "season": 1, "episode": n[0]}}
        body.update(extra)
        out = client.post("/api/video/downloads/grab", json=body).get_json()
        assert out["ok"] is True, out
        return out["id"]

    assert _target(vdb, grab(media_id=500, media_source="tmdb", filename="a.mkv")) == "/media/anime"
    assert _target(vdb, grab(media_id=anime_lib, media_source="plex", filename="b.mkv")) == "/media/anime"
    assert _target(vdb, grab(media_id=501, media_source="tmdb", filename="c.mkv")) == "/media/tv"
    assert _target(vdb, grab(filename="d.mkv")) == "/media/tv"          # unidentified -> TV
    movie = grab(kind="movie", media_id=500, media_source="tmdb", filename="e.mkv")
    assert _target(vdb, movie) == "/media/movies"


def test_grab_with_the_setting_off_is_unchanged(api):
    client, vdb = api
    vdb.set_setting("anime_path", "")
    _seed_show(vdb, 500, genres=["Anime"])
    out = client.post("/api/video/downloads/grab", json={
        "kind": "show", "title": "T", "source": "soulseek", "username": "u", "filename": "a.mkv",
        "size_bytes": 8, "media_id": 500, "media_source": "tmdb",
        "search_ctx": {"scope": "episode", "season": 1, "episode": 1}}).get_json()
    assert _target(vdb, out["id"]) == "/media/tv"


def test_grab_pack_routes_anime(api):
    client, vdb = api
    _seed_show(vdb, 600, genres=["Anime"])
    out = client.post("/api/video/downloads/grab-pack", json={
        "username": "u", "title": "Pack", "media_id": 600, "media_source": "tmdb",
        "files": [{"filename": r"x\Pack.S02E01.1080p.mkv", "size_bytes": 8},
                  {"filename": r"x\Pack.S02E02.1080p.mkv", "size_bytes": 8}]}).get_json()
    assert out["ok"] is True and out["started"] == 2
    assert {_target(vdb, i) for i in out["ids"]} == {"/media/anime"}


def test_adopt_routes_anime(api, monkeypatch):
    client, vdb = api
    _seed_show(vdb, 700, genres=["Anime"])
    out = client.post("/api/video/downloads/adopt", json={
        "source": "usenet", "client_ref": "SABnzbd_nzo_1", "kind": "show", "title": "T",
        "media_id": 700, "media_source": "tmdb", "release_title": "T.S01E01.1080p",
        "search_ctx": {"scope": "episode", "title": "T", "season": 1, "episode": 1}}).get_json()
    assert out["ok"] is True, out
    assert _target(vdb, out["id"]) == "/media/anime"


def test_folder_settings_round_trip_anime_path(api):
    client, _vdb = api
    assert client.get("/api/video/downloads/config").get_json()["anime_path"] == "/media/anime"
    assert client.post("/api/video/downloads/config",
                       json={"anime_path": "  /data/anime  "}).status_code == 200
    cfg = client.get("/api/video/downloads/config").get_json()
    assert cfg["anime_path"] == "/data/anime" and cfg["tv_path"] == "/media/tv"
    # leaving the key out of a save keeps it (the endpoint only writes keys present)
    client.post("/api/video/downloads/config", json={"tv_path": "/media/tv2"})
    assert client.get("/api/video/downloads/config").get_json()["anime_path"] == "/data/anime"


# ── manual import reads media_id as a TMDB id ─────────────────────────────────

def test_manual_place_routes_anime_by_the_picked_tmdb_id(api, tmp_path, monkeypatch):
    client, vdb = api
    _seed_show(vdb, 800, genres=["Anime"])
    _seed_show(vdb, 801)
    src = tmp_path / "unplaced.mkv"
    src.write_bytes(b"x")
    seen = []

    import core.video.importer as importer
    monkeypatch.setattr(importer, "plan_import",
                        lambda row, s, **kw: seen.append(kw["override"]["target_dir"])
                        or {"action": "skip"})
    monkeypatch.setattr(importer, "run_import", lambda *a, **kw: {"status": "import_failed"})

    def place(tmdb):
        dl = vdb.add_video_download({"kind": "show", "title": "T", "source": "soulseek",
                                     "status": "import_failed", "target_dir": "/media/tv",
                                     "media_source": "plex", "media_id": "1"})
        vdb.update_video_download(dl, dest_path=str(src))
        client.post("/api/video/import/%d/place" % dl, json={
            "scope": "episode", "title": "T", "season": 1, "episode": 1, "media_id": tmdb})

    place(800)
    place(801)
    # the row's own media_source ('plex') must NOT reinterpret the picked TMDB id
    assert seen == ["/media/anime", "/media/tv"]


# ── library roots ─────────────────────────────────────────────────────────────

def test_anime_folder_counts_as_a_tv_library_root(db):
    from core.video.library_roots import library_roots
    db.set_setting("tv_path", "/media/tv")
    assert library_roots(db, "episode") == ["/media/tv"]
    db.set_setting("anime_path", "/media/anime")
    assert library_roots(db, "episode") == ["/media/tv", "/media/anime"]
    # also listed as an additional TV library: still counted once
    import json
    db.set_setting("tv_additional_paths", json.dumps(["/media/anime", "/media/tv2"]))
    assert library_roots(db, "episode") == ["/media/tv", "/media/anime", "/media/tv2"]
    # never a movie or youtube root
    db.set_setting("movies_path", "/media/movies")
    assert "/media/anime" not in library_roots(db, "movie")
    assert "/media/anime" not in library_roots(db, "youtube")


def test_stale_temp_reaper_watches_the_anime_folder(db, tmp_path):
    import time
    import core.video.download_monitor as mon
    anime = tmp_path / "anime"
    anime.mkdir()
    stale = anime / "Ep.mkv.tmp.abcdef12"          # the importer's '.tmp.<8 hex>' name
    stale.write_bytes(b"x")
    old = time.time() - 3 * 3600
    os.utime(stale, (old, old))
    db.set_setting("anime_path", str(anime))
    assert mon._sweep_import_temps(db) >= 1
    assert not stale.exists()
