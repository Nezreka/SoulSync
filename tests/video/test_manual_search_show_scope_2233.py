"""Manual search for a whole show (#2233).

Thibs: "add the manual search for the tv show in the ... menu, perhaps add a
kind of selector in the manual search menu for grabbing a specific season."

the show's ⋯ menu opens the same Manual search a movie's does, with a scope
picker: the whole series, one season, or one episode. what the server adds:

- a Season search lists that season's single episodes beside its packs
  (``with_episodes``), each judged as the episode it is, instead of every one
  rejected as "not a full-season pack";
- every result says what it covers of the show and how much of it you have
  (``cover``, and ``have`` on each file of a soulseek pack);
- an Episode search carries the numbering the show is released under (air
  date, absolute number, scene SxxExx), the same facts the wishlist search
  always carried, so manual search finds what the wishlist finds.
"""

from __future__ import annotations

import pytest

import api.video.downloads as dl

GB = 1024 ** 3
TMDB = 4242
TITLE = "Severed"


@pytest.fixture(autouse=True)
def _fresh_memo():
    dl._NUMBERING_MEMO.clear()
    yield
    dl._NUMBERING_MEMO.clear()


def _seed(db, *, have=((1, 1), (1, 2), (2, 1), (2, 2))):
    seasons = []
    for sn, count in ((1, 3), (2, 4)):
        eps = [{"episode_number": e, "title": "Ep %d" % e, "server_id": "s%de%d" % (sn, e),
                "air_date": "2024-0%d-%02d" % (sn, e),
                "file": ({"path": "/tv/s%de%d.mkv" % (sn, e)} if (sn, e) in have else None)}
               for e in range(1, count + 1)]
        seasons.append({"season_number": sn, "episodes": eps})
    return db.upsert_show_tree("plex", {"server_id": "show1", "title": TITLE, "tmdb_id": TMDB,
                                        "seasons": seasons})


# ── the library's answer: which episodes exist and which you have ─────────────

@pytest.fixture()
def db(tmp_path):
    from database.video_database import VideoDatabase
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_the_census_names_every_episode_and_which_you_have(db):
    sid = _seed(db)
    census = db.show_episode_census(tmdb_id=TMDB)
    assert census == {(1, 1): True, (1, 2): True, (1, 3): False,
                      (2, 1): True, (2, 2): True, (2, 3): False, (2, 4): False}
    # by library id, the same answer
    assert db.show_episode_census(show_id=sid) == census
    assert db.show_episode_census() == {}
    assert db.show_episode_census(tmdb_id=999) == {}


def test_an_episode_air_date_comes_from_the_library(db):
    _seed(db)
    assert db.episode_air_date(TMDB, 2, 3) == "2024-02-03"
    assert db.episode_air_date(TMDB, 9, 9) is None
    assert db.episode_air_date("x", 1, 1) is None


# ── what one result covers ────────────────────────────────────────────────────

CENSUS = {(1, 1): True, (1, 2): True, (1, 3): False,
          (2, 1): True, (2, 2): False, (2, 3): False}


def test_a_soulseek_pack_marks_each_file_you_have():
    row = {"files": [{"filename": "Show/Show.S02E01.mkv"}, {"filename": "Show/Show.S02E02.mkv"},
                     {"filename": "Show/sample.mkv"}]}
    assert dl._coverage(row, CENSUS) == {"have": 1, "total": 2}
    assert [f.get("have") for f in row["files"]] == [True, False, None]


def test_a_torrent_season_pack_covers_its_season():
    row = {"is_season_pack": True, "season": 1, "files": []}
    assert dl._coverage(row, CENSUS) == {"have": 2, "total": 3}


def test_a_series_pack_covers_every_real_season():
    census = {**CENSUS, (0, 1): True}
    row = {"is_series_pack": True, "is_season_pack": True, "season": None}
    assert dl._coverage(row, census) == {"have": 3, "total": 6}


def test_a_single_episode_and_a_range():
    assert dl._coverage({"season": 2, "episode": 1}, CENSUS) == {"have": 1, "total": 1}
    assert dl._coverage({"season": 1, "episode": 2, "episode_end": 3}, CENSUS) == {"have": 1, "total": 2}


def test_no_library_no_coverage():
    assert dl._coverage({"season": 1, "episode": 1}, {}) is None
    assert dl._coverage({"title": "a movie"}, CENSUS) is None


# ── a season search lists the season's episodes too ───────────────────────────

PACK = "Severed.S02.1080p.WEB-DL.DDP5.1.H.264-GRP"
EP5 = "Severed.S02E03.1080p.WEB-DL.DDP5.1.H.264-GRP"
OTHER_SEASON = "Severed.S01E03.1080p.WEB-DL.DDP5.1.H.264-GRP"


def _hit(name, gb, user="indexer", **kw):
    return {"title": name, "filename": name + ".mkv", "size_bytes": int(gb * GB),
            "seeders": 40, "username": user, "protocol": "usenet", **kw}


def _judge(hits, **kw):
    from core.video import quality_profile
    return dl._evaluate_hits(hits, quality_profile.normalize({}), "season", 2, None,
                             blocked=frozenset(), blocked_users=frozenset(),
                             want_title=TITLE, **kw)


@pytest.fixture()
def no_formats(monkeypatch):
    import core.video.custom_formats as cf
    monkeypatch.setattr(cf, "load_formats", lambda db: [])


def test_a_season_search_keeps_rejecting_single_episodes_by_default(no_formats):
    rows = {r["title"]: r for r in _judge([_hit(PACK, 20), _hit(EP5, 2)])}
    assert rows[PACK]["accepted"] is True, rows[PACK]["rejected"]
    assert rows[EP5]["accepted"] is False
    assert rows[EP5]["rejected"] == "Not a full-season pack"


def test_with_episodes_a_season_search_judges_each_episode_as_itself(no_formats):
    rows = {r["title"]: r for r in _judge([_hit(PACK, 20), _hit(EP5, 2), _hit(OTHER_SEASON, 2)],
                                          season_episodes=True)}
    assert rows[PACK]["accepted"] is True
    assert rows[EP5]["accepted"] is True, rows[EP5]["rejected"]
    # an episode of another season is still not this season
    assert rows[OTHER_SEASON]["accepted"] is False


def test_each_row_says_what_it_covers(no_formats):
    rows = {r["title"]: r for r in _judge([_hit(PACK, 20), _hit(EP5, 2)],
                                          season_episodes=True, census=CENSUS)}
    assert rows[PACK]["cover"] == {"have": 1, "total": 3}
    assert rows[EP5]["cover"] == {"have": 0, "total": 1}


def test_judge_kwargs_switches_on_only_for_a_season_search():
    assert _flags({"with_episodes": "1"}, "season") is True
    assert _flags({"with_episodes": True}, "season") is True
    assert _flags({"with_episodes": "0"}, "season") is None
    assert _flags({"with_episodes": "1"}, "episode") is None
    assert _flags({"with_episodes": "1"}, "series") is None


def _flags(body, scope):
    return dl._judge_kwargs({"title": TITLE, **body}, scope).get("season_episodes")


# ── an episode search carries the show's numbering ────────────────────────────

class _NumDB:
    def __init__(self, stype="anime"):
        self.stype = stype

    def effective_series_type(self, tmdb_id=None, library_id=None):
        return self.stype

    def episode_air_date(self, tmdb, s, e):
        return "2024-02-03"

    def episode_absolute_number(self, tmdb, s, e):
        return 1071


@pytest.fixture()
def scene(monkeypatch):
    import core.video.numbering as numbering

    def fake_annotate(items, db=None, client=None):
        for it in items:
            it["alt_season"], it["alt_episode"] = 3, 1
        return items
    monkeypatch.setattr(numbering, "annotate", fake_annotate)


def test_an_episode_search_carries_absolute_air_date_and_scene_numbering(scene):
    body = dl._with_episode_numbering(_NumDB(), {"scope": "episode", "tmdb_id": TMDB,
                                                 "season": 2, "episode": 13}, "episode")
    assert body["series_type"] == "anime"
    assert body["absolute"] == 1071
    assert body["air_date"] == "2024-02-03"
    assert (body["alt_season"], body["alt_episode"]) == (3, 1)
    judge = dl._judge_kwargs(body, "episode")
    assert judge["want_absolute"] == 1071
    assert judge["want_date"] == "2024-02-03"
    assert judge["want_alt"] == (3, 1)


def test_a_standard_show_gets_no_absolute_number(scene):
    body = dl._with_episode_numbering(_NumDB("standard"), {"scope": "episode", "tmdb_id": TMDB,
                                                           "season": 2, "episode": 1}, "episode")
    assert "absolute" not in body and "series_type" not in body


def test_what_the_client_sent_wins_and_other_scopes_are_left_alone(scene):
    body = dl._with_episode_numbering(_NumDB(), {"scope": "episode", "tmdb_id": TMDB, "season": 2,
                                                 "episode": 13, "absolute": 5}, "episode")
    assert body["absolute"] == 5
    season = {"scope": "season", "tmdb_id": TMDB, "season": 2}
    assert dl._with_episode_numbering(_NumDB(), dict(season), "season") == season
    no_id = {"scope": "episode", "season": 2, "episode": 1}
    assert dl._with_episode_numbering(_NumDB(), dict(no_id), "episode") == no_id


def test_the_soulseek_query_uses_the_scene_numbering():
    body = {"alt_season": 3, "alt_episode": 1}
    assert dl._slskd_query(body, "episode", "Show", 2, 13) == "Show S03E01"
    assert dl._slskd_query({}, "episode", "Show", 2, 13) == "Show S02E13"
    assert dl._slskd_query({"series_type": "anime", "absolute": 1071}, "episode", "Show", 2, 13) == "Show 1071"


# ── through the endpoints ─────────────────────────────────────────────────────

@pytest.fixture()
def api(tmp_path, monkeypatch, no_formats):
    from flask import Flask

    import api.video as videoapi
    import core.video.extto_search as es
    import core.video.prowlarr_search as ps
    import core.video.slskd_search as ss
    from database.video_database import VideoDatabase

    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    _seed(db)
    videoapi._video_db = db
    # a show outside TMDB's reach: the identity is the library's alone
    import core.automation.handlers.video_process_wishlist as vpw
    monkeypatch.setattr(vpw, "tmdb_identity", lambda kind, tmdb: {})
    monkeypatch.setattr(vpw, "_acceptable_titles", lambda title, kind, tmdb, season=None: [title])
    monkeypatch.setattr(vpw, "search_titles", lambda title, kind, tmdb: [title])
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    state = {"hits": [], "calls": []}

    def fake_indexers(scope, title, **kw):
        state["calls"].append({"scope": scope, "title": title, **kw})
        return {"configured": True, "hits": state["hits"]}
    monkeypatch.setattr(ps, "prowlarr_search", fake_indexers)
    monkeypatch.setattr(es, "extto_search", lambda *a, **k: {"configured": False, "hits": []})
    monkeypatch.setattr(ss, "poll_search", lambda sid: {"hits": state["hits"], "total_files": 2})
    client.state = state
    try:
        yield client
    finally:
        videoapi._video_db = None


SEASON = {"scope": "season", "title": TITLE, "season": 2, "source": "usenet",
          "tmdb_id": TMDB, "with_episodes": True}


def test_a_season_search_lists_packs_and_episodes_with_what_you_have(api):
    api.state["hits"] = [_hit(PACK, 20), _hit(EP5, 2), _hit(OTHER_SEASON, 2)]
    out = api.post("/api/video/downloads/search/start", json=SEASON).get_json()
    rows = {r["title"]: r for r in out["results"]}
    assert rows[PACK]["accepted"] and rows[EP5]["accepted"]
    assert not rows[OTHER_SEASON]["accepted"]
    assert rows[PACK]["cover"] == {"have": 2, "total": 4}
    assert rows[EP5]["cover"] == {"have": 0, "total": 1}


def test_the_soulseek_poll_judges_the_same_way(api):
    api.state["hits"] = [_hit(PACK, 20, user="peer"), _hit(EP5, 2, user="peer")]
    out = api.get("/api/video/downloads/search/poll", query_string={
        "id": "x", "scope": "season", "title": TITLE, "season": 2, "tmdb_id": TMDB,
        "with_episodes": "1"}).get_json()
    rows = {r["title"]: r for r in out["results"]}
    assert rows[EP5]["accepted"] is True, rows[EP5]["rejected"]
    assert rows[PACK]["cover"] == {"have": 2, "total": 4}


def test_a_census_sent_by_the_client_is_ignored(api):
    api.state["hits"] = [_hit(PACK, 20)]
    resp = api.post("/api/video/downloads/search/start", json={**SEASON, "_census": "junk"})
    assert resp.status_code == 200
    assert resp.get_json()["results"][0]["cover"] == {"have": 2, "total": 4}


def test_a_series_search_counts_the_whole_show(api):
    api.state["hits"] = [_hit("Severed.Complete.Series.1080p.WEB-DL.DDP5.1.H.264-GRP", 40)]
    out = api.post("/api/video/downloads/search/start", json={
        "scope": "series", "title": TITLE, "source": "usenet", "tmdb_id": TMDB}).get_json()
    row = out["results"][0]
    assert row["accepted"] is True, row["rejected"]
    assert row["cover"] == {"have": 4, "total": 7}


def test_an_episode_search_asks_the_indexers_with_the_scene_numbering(api, scene):
    api.state["hits"] = [_hit("Severed.S03E01.1080p.WEB-DL.DDP5.1.H.264-GRP", 2)]
    out = api.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": TITLE, "season": 2, "episode": 4, "source": "usenet",
        "tmdb_id": TMDB}).get_json()
    call = api.state["calls"][-1]
    assert call["alt"] == (3, 1)
    assert call["air_date"] == "2024-02-04"
    # the scene's own S03E01 is this episode
    assert out["results"][0]["accepted"] is True, out["results"][0]["rejected"]
