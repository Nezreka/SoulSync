"""Anime library folder: episodes of anime-type shows import to ``anime_path``.

An optional ``anime_path`` setting files anime shows apart from TV, so an anime
collection kept in its own media-server library keeps receiving new episodes.
Empty = off: everything goes to ``tv_path`` exactly as before.

Which shows count as anime for FILING: set by hand, else in a TV library the
admin marked as anime, else (a show in no library yet) its stored hint -- TMDB's
suggestion, or the admin's choice. The Anime genre tag still steers SEARCH but
never the folder, so a show filed with TV that TMDB tags Anime stays with TV.

Covers: the pure routing helpers, the search vs routing lookups, library
tagging and multi-library picks, the TMDB hint, the wishlist drain, the grab /
pack / adopt routes, manual import's id reading, the series-type endpoints, the
folder settings round-trip, and the anime folder counting as a TV library root.
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


# ── series type: search vs folder routing ─────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed_show(db, tmdb_id, *, genres=(), server="plex", sid=None, library="TV Shows"):
    item = {"server_id": sid or "s%s" % tmdb_id, "title": "Show %s" % tmdb_id,
            "tmdb_id": tmdb_id, "genres": list(genres),
            "seasons": [{"season_number": 1, "episodes": [
                {"episode_number": 1, "title": "E1"}]}]}
    if library:
        item["library"] = library
    return db.upsert_show_tree(server, item)


def _mark_anime(db, *titles, server="plex"):
    db.set_library_selection(server, "Movies", "TV Shows", tv_more=list(titles), anime=list(titles))


def test_a_show_records_its_library(db):
    _seed_show(db, 1, library="Anime")
    _seed_show(db, 2, library=None)                     # an older source that sends none
    conn = db._get_connection()
    try:
        rows = dict(conn.execute("SELECT tmdb_id, library_name FROM shows").fetchall())
    finally:
        conn.close()
    assert rows == {1: "Anime", 2: None}


def test_routing_follows_the_library_not_the_genre(db):
    _mark_anime(db, "Anime")
    _seed_show(db, 10, library="Anime")                              # filed as anime
    _seed_show(db, 11, genres=["Animation", "Anime"], library="TV Shows")   # Star Wars: Visions
    _seed_show(db, 12, library="TV Shows")
    assert db.routing_series_type(tmdb_id=10) == "anime"
    assert db.routing_series_type(tmdb_id=11) == "standard"         # filed with TV, stays TV
    assert db.routing_series_type(tmdb_id=12) == "standard"
    # search keeps the genre as its last fallback, so it still searches 11 the anime way
    assert db.effective_series_type(tmdb_id=11) == "anime"
    assert db.effective_series_type(tmdb_id=10) == "anime"
    assert db.effective_series_type(tmdb_id=12) == "standard"


def test_set_by_hand_anime_beats_a_tv_library(db):
    _mark_anime(db, "Anime")
    in_anime = _seed_show(db, 20, library="Anime")
    in_tv = _seed_show(db, 21, library="TV Shows")
    db.set_show_series_type(in_tv, "anime")
    assert db.routing_series_type(tmdb_id=21) == "anime"
    assert db.routing_series_type(library_id=in_tv) == "anime"
    # 'standard' is stored as NULL (upstream), so it can't outvote an anime
    # library: a show in the Anime library keeps filing with its library
    db.set_show_series_type(in_anime, "standard")
    assert db.routing_series_type(tmdb_id=20) == "anime"
    db.set_show_series_type(in_anime, "daily")
    assert db.routing_series_type(tmdb_id=20) == "daily"


def test_library_marks_are_per_server(db):
    _mark_anime(db, "Anime", server="jellyfin")
    _seed_show(db, 30, library="Anime", server="plex")
    assert db.routing_series_type(tmdb_id=30) == "standard"       # plex's 'Anime' isn't marked
    _mark_anime(db, "Anime", server="plex")
    assert db.routing_series_type(tmdb_id=30) == "anime"


def test_a_garbled_anime_setting_reads_as_no_marks(db):
    db.set_setting("plex.anime_libraries", "not json")
    _seed_show(db, 31, library="Anime")
    assert db.routing_series_type(tmdb_id=31) == "standard"
    assert db.anime_libraries("plex") == []


def test_library_id_without_a_tmdb_id_is_judged_alone(db):
    _mark_anime(db, "Anime")
    lone = db.upsert_show_tree("plex", {
        "server_id": "x1", "title": "No Id", "library": "Anime",
        "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1, "title": "E1"}]}]})
    assert db.routing_series_type(library_id=lone) == "anime"


def test_the_hint_applies_only_to_shows_in_no_library(db):
    assert db.routing_series_type(tmdb_id=40) == "standard"
    assert db.set_show_type_hint(40, "anime", source="tmdb")
    assert db.routing_series_type(tmdb_id=40) == "anime"
    assert db.effective_series_type(tmdb_id=40) == "anime"
    # once the show is scanned into a library, the library decides
    _seed_show(db, 40, library="TV Shows")
    assert db.routing_series_type(tmdb_id=40) == "standard"


def test_tmdb_never_overwrites_a_type_the_admin_chose(db):
    assert db.set_show_type_hint(50, "anime", source="user")
    assert not db.set_show_type_hint(50, "standard", source="tmdb")
    assert db.show_type_hint(50) == {"series_type": "anime", "source": "user"}
    # the admin can still change it
    assert db.set_show_type_hint(50, "standard", source="user")
    assert db.show_type_hint(50) == {"series_type": "standard", "source": "user"}
    # a tmdb hint can refresh another tmdb hint
    db.set_show_type_hint(51, "standard", source="tmdb")
    assert db.set_show_type_hint(51, "anime", source="tmdb")
    assert db.show_type_hint(51)["series_type"] == "anime"
    # junk is refused
    assert not db.set_show_type_hint(52, "cartoon")
    assert db.show_type_hint(52) is None


def test_the_wishlist_query_and_the_lookups_agree(db):
    # the drain reads route_series_type off episode_wishlist_to_download; the
    # grab routes read routing_series_type. They must agree, or a manual grab
    # and the drain would file the same show in different folders.
    _mark_anime(db, "Anime")
    cases = {60: dict(library="TV Shows"), 61: dict(library="Anime"),
             62: dict(genres=("Anime",), library="TV Shows"), 63: None}
    for tmdb, kw in cases.items():
        if kw is not None:
            _seed_show(db, tmdb, **kw)
        db.add_episodes_to_wishlist(tmdb, "Show %s" % tmdb, [
            {"season_number": 1, "episode_number": 1, "air_date": "2026-07-01"}])
    db.set_show_type_hint(63, "anime", source="tmdb")
    items = {int(i["show_tmdb_id"]): i for i in db.episode_wishlist_to_download()}
    for tmdb in cases:
        assert (items[tmdb]["route_series_type"] or "standard") == db.routing_series_type(tmdb_id=tmdb)
        assert (items[tmdb]["series_type"] or "standard") == db.effective_series_type(tmdb_id=tmdb)
    assert items[61]["route_series_type"] == "anime" and items[63]["route_series_type"] == "anime"
    assert (items[62]["route_series_type"] or "standard") == "standard"
    assert items[62]["series_type"] == "anime"


def test_sibling_rows_from_other_servers_count(db):
    _seed_show(db, 70, server="plex", sid="p70")
    jelly = _seed_show(db, 70, server="jellyfin", sid="j70")
    db.set_show_series_type(jelly, "anime")
    assert db.routing_series_type(tmdb_id=70) == "anime"


def test_lookups_never_raise(db):
    for fn in (db.effective_series_type, db.routing_series_type):
        assert fn() == "standard"
        assert fn(tmdb_id=999999) == "standard"
        assert fn(library_id=999999) == "standard"
        assert fn(tmdb_id="not-a-number") == "standard"
    assert db.owns_show("nope") is False
    assert db.show_type_hint("nope") is None


# ── library picks: several TV libraries, some anime ───────────────────────────

def test_library_selection_round_trip(db):
    assert db.get_library_selection("plex") == {"movies": None, "tv": None, "tv_more": [], "anime": []}
    db.set_library_selection("plex", "Movies", "TV Shows",
                             tv_more=["Anime", " Kids ", "", "TV Shows", "Anime"], anime=["Anime"])
    assert db.get_library_selection("plex") == {
        "movies": "Movies", "tv": "TV Shows", "tv_more": ["Anime", "Kids"], "anime": ["Anime"]}
    # an older caller saving only movies + tv keeps the lists
    db.set_library_selection("plex", "Movies", "TV")
    assert db.get_library_selection("plex")["tv_more"] == ["Anime", "Kids"]
    assert db.get_library_selection("plex")["anime"] == ["Anime"]


class _Sec:
    def __init__(self, kind, title, shows=()):
        self.type, self.title, self._shows, self.updated = kind, title, list(shows), 0

    def all(self):
        return list(self._shows)

    def update(self):
        self.updated += 1


class _Lib:
    def __init__(self, secs):
        self._secs = secs

    def sections(self):
        return list(self._secs)


class _Server:
    def __init__(self, secs):
        self.library = _Lib(secs)


def _plex(secs, tv):
    from core.video.sources import PlexVideoSource
    src = PlexVideoSource(_Server(secs), movies_lib="Movies", tv_lib=tv)
    src._show = lambda sh: {"server_id": sh, "title": sh, "library": None}
    return src


def test_plex_scans_every_picked_tv_library_and_tags_each_show():
    from core.video.sources import _tv_pick
    secs = [_Sec("movie", "Movies"), _Sec("show", "TV Shows", ["a"]),
            _Sec("show", "Anime", ["b"]), _Sec("show", "Kids", ["c"])]
    tv = _tv_pick({"tv": "TV Shows", "tv_more": ["Anime"]})
    assert tv == ["TV Shows", "Anime"]
    src = _plex(secs, tv)
    got = {(d["server_id"], d["library"]) for d in src.iter_shows()}
    assert got == {("a", "TV Shows"), ("b", "Anime")}                 # Kids isn't picked
    assert src.scan_problem("show") is None
    # the post-download refresh covers both picked TV libraries (+ Movies on 'all')
    assert src.refresh_sections("show") == {"ok": True, "sections": 2}
    assert [s.updated for s in secs] == [0, 1, 1, 0]


def test_plex_reports_a_missing_extra_library():
    secs = [_Sec("show", "TV Shows")]
    problem = _plex(secs, ["TV Shows", "Anime"]).scan_problem("show")
    assert problem and "Anime" in str(problem)


def test_tv_pick_shapes():
    from core.video.sources import _title_list, _tv_pick
    assert _tv_pick({}) is None
    assert _tv_pick({"tv": ""}) is None
    assert _tv_pick({"tv": "TV"}) == ["TV"]
    assert _tv_pick({"tv": "", "tv_more": ["Anime"]}) == ["Anime"]
    assert _title_list(None) == [] and _title_list("X") == ["X"]
    assert _title_list(["A", " ", "A", "B"]) == ["A", "B"]


# ── TMDB's suggestion for shows in no library ─────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ({"keywords": {"results": [{"name": "Anime"}]}}, True),          # TMDB's anime keyword
    ({"keywords": {"results": [{"name": "space"}]}, "genres": [{"id": 16}],
      "original_language": "ja"}, True),                              # Animation + Japanese
    ({"genres": [{"name": "Animation"}], "original_language": "JA"}, True),
    ({"genres": [{"id": 16}], "original_language": "en"}, False),     # western animation
    ({"genres": [{"id": 18}], "original_language": "ja"}, False),     # Japanese live action
    ({}, False), (None, False), ("junk", False),
    ({"keywords": "junk", "genres": ["junk"]}, False),
])
def test_tmdb_suggests_anime(raw, want):
    from core.video.anime_hint import tmdb_suggests_anime
    assert tmdb_suggests_anime(raw) is want


def test_ensure_asks_tmdb_once_and_stores_the_answer(db):
    from core.video.anime_hint import ensure_show_type_hint
    calls = []

    def suggest(t):
        calls.append(t)
        return True

    assert ensure_show_type_hint(db, 80, suggest) == "anime"
    assert db.show_type_hint(80) == {"series_type": "anime", "source": "tmdb"}
    assert ensure_show_type_hint(db, 80, suggest) == "anime"
    assert calls == [80]                                     # stored: not asked again
    assert ensure_show_type_hint(db, 81, lambda t: False) == "standard"


def test_ensure_leaves_owned_shows_and_silent_tmdb_alone(db):
    from core.video.anime_hint import ensure_show_type_hint
    _seed_show(db, 82)
    assert ensure_show_type_hint(db, 82, lambda t: True) is None
    assert db.show_type_hint(82) is None
    # TMDB can't say (no key, down): nothing stored, so the next follow tries again
    assert ensure_show_type_hint(db, 83, lambda t: None) is None
    assert db.show_type_hint(83) is None
    # a failing suggester never raises
    assert ensure_show_type_hint(db, 84, lambda t: 1 / 0) is None
    assert ensure_show_type_hint(db, "junk", lambda t: True) is None


def test_note_show_wanted(db, monkeypatch):
    import core.video.anime_hint as ah
    later = []
    monkeypatch.setattr(ah, "ensure_show_type_hint_later", lambda prov, t: later.append(t))
    # an admin's choice for a show in no library is stored as theirs
    ah.note_show_wanted(lambda: db, 90, "Anime")
    assert db.show_type_hint(90) == {"series_type": "anime", "source": "user"}
    # ...but never on a show already in a library (its library decides)
    _seed_show(db, 91)
    ah.note_show_wanted(lambda: db, 91, "anime")
    assert db.show_type_hint(91) is None
    # no choice and no anime folder: TMDB isn't asked at all
    ah.note_show_wanted(lambda: db, 92)
    assert later == []
    # with an anime folder set, TMDB's suggestion is fetched in the background
    db.set_setting("anime_path", "/media/anime")
    ah.note_show_wanted(lambda: db, 92)
    ah.note_show_wanted(lambda: db, 93, "junk")
    assert later == [92, 93]
    # never raises
    ah.note_show_wanted(lambda: (_ for _ in ()).throw(RuntimeError("no db")), 94)


def test_follow_wish_and_approve_store_the_type():
    # wiring: every way a show enters acquisition notes it
    import inspect
    import api.video.requests as rq
    import api.video.watchlist as wl
    import api.video.wishlist as wi
    for mod in (rq, wl, wi):
        assert "note_show_wanted" in inspect.getsource(mod), mod.__name__
    assert "series_type" in inspect.getsource(rq)


# ── the wishlist drain ────────────────────────────────────────────────────────

class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _drain(items, *, root="/tv", anime_root="/anime", media_type="episode", route_type=None):
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
        search=search, enqueue=enqueue, route_type=route_type)
    return res, dict(enq)


def _ep(tmdb, route, search_type=None):
    return {"show_tmdb_id": tmdb, "show_title": "S%d" % tmdb, "season_number": 1,
            "episode_number": 1, "series_type": search_type or route, "route_series_type": route}


def test_drain_files_anime_shows_under_the_anime_folder():
    items = [_ep(1, "anime"), _ep(2, None), _ep(3, "daily"), _ep(4, "standard")]
    res, roots = _drain(items, route_type=lambda it: it.get("route_series_type"))
    assert res["grabbed"] == 4
    assert roots == {1: "/anime", 2: "/tv", 3: "/tv", 4: "/tv"}


def test_drain_routes_by_the_folder_type_not_the_search_type():
    # tagged Anime (searched the anime way) but filed under TV: stays with TV
    _res, roots = _drain([_ep(1, None, search_type="anime")])
    assert roots == {1: "/tv"}
    _res, roots = _drain([_ep(2, "anime")])
    assert roots == {2: "/anime"}


def test_drain_asks_for_a_type_only_when_the_row_has_none(monkeypatch):
    asked = []

    def route(it):
        asked.append(it["show_tmdb_id"])
        return it.get("route_series_type") or "anime"   # e.g. TMDB's suggestion

    _res, roots = _drain([_ep(1, "standard"), _ep(2, None)], route_type=route)
    assert roots == {1: "/tv", 2: "/anime"}
    # with no anime folder nothing is asked
    asked.clear()
    _drain([_ep(3, None)], anime_root="", route_type=route)
    assert asked == []


def test_drain_without_an_anime_folder_files_everything_under_tv():
    _res, roots = _drain([_ep(1, "anime"), _ep(2, None)], anime_root="")
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


def test_default_route_type_reads_the_row_then_falls_back_safely(monkeypatch):
    import api.video as videoapi
    from core.automation.handlers.video_process_wishlist import _default_route_type
    assert _default_route_type({"route_series_type": "anime", "show_tmdb_id": 1}) == "anime"
    assert _default_route_type({"show_tmdb_id": None}) is None
    monkeypatch.setattr(videoapi, "get_video_db",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert _default_route_type({"show_tmdb_id": 5}) is None


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


def test_grab_routes_anime_by_tmdb_and_library_id(api, monkeypatch):
    client, vdb = api
    import core.video.anime_hint as ah
    monkeypatch.setattr(ah, "default_suggest", lambda t: t == 503)   # 'TMDB'
    _mark_anime(vdb, "Anime")
    anime_lib = _seed_show(vdb, 500, library="Anime")
    _seed_show(vdb, 501)
    _seed_show(vdb, 502, genres=["Animation", "Anime"])             # tagged Anime, filed as TV

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
    assert _target(vdb, grab(media_id=502, media_source="tmdb", filename="f.mkv")) == "/media/tv"
    # a show in no library: TMDB is asked once and the answer stored
    assert _target(vdb, grab(media_id=503, media_source="tmdb", filename="g.mkv")) == "/media/anime"
    assert vdb.show_type_hint(503) == {"series_type": "anime", "source": "tmdb"}
    assert _target(vdb, grab(media_id=504, media_source="tmdb", filename="h.mkv")) == "/media/tv"
    # ...and the admin's choice beats TMDB's
    vdb.set_show_type_hint(505, "anime", source="user")
    assert _target(vdb, grab(media_id=505, media_source="tmdb", filename="i.mkv")) == "/media/anime"
    assert _target(vdb, grab(filename="d.mkv")) == "/media/tv"          # unidentified -> TV
    movie = grab(kind="movie", media_id=500, media_source="tmdb", filename="e.mkv")
    assert _target(vdb, movie) == "/media/movies"


def test_grab_with_the_setting_off_is_unchanged(api):
    client, vdb = api
    vdb.set_setting("anime_path", "")
    _mark_anime(vdb, "Anime")
    _seed_show(vdb, 500, library="Anime")
    out = client.post("/api/video/downloads/grab", json={
        "kind": "show", "title": "T", "source": "soulseek", "username": "u", "filename": "a.mkv",
        "size_bytes": 8, "media_id": 500, "media_source": "tmdb",
        "search_ctx": {"scope": "episode", "season": 1, "episode": 1}}).get_json()
    assert _target(vdb, out["id"]) == "/media/tv"


def test_grab_pack_routes_anime(api):
    client, vdb = api
    _mark_anime(vdb, "Anime")
    _seed_show(vdb, 600, library="Anime")
    out = client.post("/api/video/downloads/grab-pack", json={
        "username": "u", "title": "Pack", "media_id": 600, "media_source": "tmdb",
        "files": [{"filename": r"x\Pack.S02E01.1080p.mkv", "size_bytes": 8},
                  {"filename": r"x\Pack.S02E02.1080p.mkv", "size_bytes": 8}]}).get_json()
    assert out["ok"] is True and out["started"] == 2
    assert {_target(vdb, i) for i in out["ids"]} == {"/media/anime"}


def test_adopt_routes_anime(api, monkeypatch):
    client, vdb = api
    _mark_anime(vdb, "Anime")
    _seed_show(vdb, 700, library="Anime")
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
    _mark_anime(vdb, "Anime")
    _seed_show(vdb, 800, library="Anime")
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


# ── the show page / approve sheet type endpoints ──────────────────────────────

def _gated(tmp_path, *, is_admin):
    import api.video as videoapi
    vdb = VideoDatabase(database_path=str(tmp_path / "gated.db"))
    videoapi._video_db = vdb
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        from flask import g
        g.is_admin = is_admin
        g.can_download = True
        g.allowed_sides = "both"

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), vdb, videoapi


def test_series_type_endpoints(tmp_path, monkeypatch):
    import core.video.anime_hint as ah
    monkeypatch.setattr(ah, "default_suggest", lambda t: True)
    client, vdb, videoapi = _gated(tmp_path, is_admin=True)
    try:
        url = "/api/video/detail/tmdb/show/%d/series-type"
        # no anime folder: nothing suggested, but the chip still reads
        assert client.get(url % 1 + "?suggest=1").get_json() == {
            "owned": False, "series_type": "standard", "source": None, "anime_folder": False}
        vdb.set_setting("anime_path", "/media/anime")
        assert client.get(url % 1).get_json()["source"] is None          # no suggest: no TMDB
        out = client.get(url % 1 + "?suggest=1").get_json()
        assert out["series_type"] == "anime" and out["source"] == "hint-tmdb"
        # the admin overrides it
        r = client.put(url % 1, json={"series_type": "standard"})
        assert r.status_code == 200 and r.get_json()["source"] == "hint-user"
        assert client.get(url % 1).get_json()["series_type"] == "standard"
        assert client.put(url % 1, json={"series_type": "cartoon"}).status_code == 400
        # an owned show answers from its library and refuses a hint
        _mark_anime(vdb, "Anime")
        _seed_show(vdb, 2, library="Anime")
        assert client.get(url % 2).get_json() == {
            "owned": True, "series_type": "anime", "source": "library", "anime_folder": True}
        assert client.put(url % 2, json={"series_type": "standard"}).status_code == 409
    finally:
        videoapi._video_db = None


def test_series_type_hint_is_admin_only(tmp_path, monkeypatch):
    import core.video.anime_hint as ah
    asked = []
    monkeypatch.setattr(ah, "default_suggest", lambda t: asked.append(t) or True)
    client, vdb, videoapi = _gated(tmp_path, is_admin=False)
    try:
        vdb.set_setting("anime_path", "/media/anime")
        url = "/api/video/detail/tmdb/show/7/series-type"
        assert client.put(url, json={"series_type": "anime"}).status_code == 403
        assert vdb.show_type_hint(7) is None
        # a member may read the chip, but never trigger the TMDB call
        assert client.get(url + "?suggest=1").status_code == 200
        assert asked == [] and vdb.show_type_hint(7) is None
    finally:
        videoapi._video_db = None


def test_libraries_endpoint_saves_the_lists(api, monkeypatch):
    client, vdb = api
    import core.video.sources as vs
    monkeypatch.setattr(vs, "resolve_video_server", lambda: "plex")
    r = client.post("/api/video/libraries", json={
        "movies": "Movies", "tv": "TV Shows", "tv_more": ["Anime"], "anime": ["Anime"]})
    assert r.status_code == 200, r.get_json()
    assert vdb.get_library_selection("plex") == {
        "movies": "Movies", "tv": "TV Shows", "tv_more": ["Anime"], "anime": ["Anime"]}
    # an older client sending only movies + tv keeps the lists
    client.post("/api/video/libraries", json={"movies": "Movies", "tv": "TV Shows"})
    assert vdb.get_library_selection("plex")["anime"] == ["Anime"]


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
