"""scene numbering: TMDB numbers the wishlist, the scene names releases by TVDB.

surveyed against boulder's live wishlist with his TVDB key (oct 16 2026): of
181 wanted episodes TVDB could place, 72 are numbered differently. 60 Minutes
S59E25 is TVDB S58E48, Law & Order S25E22 is S26E01, America's Funniest Home
Videos S37E02 is S37E01, One Piece S23E1177 is S23E21. every release for those
read as "Wrong season" / "Wrong episode". the episode is mapped by air date,
and a release named by either number is accepted.
"""

import datetime as dt
import json

import pytest

import core.automation.handlers.video_process_wishlist as wl
from core.video import importer, numbering
from core.video.prowlarr_search import build_strategies
from core.video.quality_eval import evaluate_release
from core.video.quality_profile import default_profile
from core.video.release_parse import parse_release
from database.video_database import VideoDatabase

NOW = dt.datetime(2026, 10, 16, 12, 0, 0)

# 60 Minutes on TVDB around its season turnover
TVDB_60 = {
    58: [(47, "2026-08-23"), (48, "2026-08-30"), (49, "2026-09-06")],
    59: [(1, "2026-09-13"), (2, "2026-09-20")],
    60: [],
}


class FakeTVDB:
    enabled = True

    def __init__(self, seasons):
        self.seasons = seasons
        self.calls = []

    def season_episodes(self, series_id, season):
        self.calls.append((series_id, season))
        return [{"episode_number": e, "air_date": d} for e, d in self.seasons.get(season, [])]


@pytest.fixture
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


# -- the mapping ---------------------------------------------------------------

def test_maps_by_air_date():
    assert numbering.scene_numbering((59, 25), "2026-08-30", TVDB_60) == (58, 48)
    assert numbering.scene_numbering((58, 50), "2026-09-13", TVDB_60) == (59, 1)


def test_same_number_or_no_match_is_none():
    assert numbering.scene_numbering((58, 48), "2026-08-30", TVDB_60) is None
    assert numbering.scene_numbering((59, 25), "2026-07-01", TVDB_60) is None
    assert numbering.scene_numbering((59, 25), None, TVDB_60) is None
    assert numbering.scene_numbering((59, 25), "2026-08-30", {}) is None


def test_a_double_bill_maps_by_order_only_when_both_sides_agree():
    tvdb = {279: [(10, "2026-10-04"), (11, "2026-10-04")]}
    # tmdb has the same two episodes that day: first is first, second is second
    assert numbering.scene_numbering((280, 13), "2026-10-04", tvdb, [(280, 13), (280, 14)]) == (279, 10)
    assert numbering.scene_numbering((280, 14), "2026-10-04", tvdb, [(280, 13), (280, 14)]) == (279, 11)
    # we only know of one of them: guessing would grab the wrong half
    assert numbering.scene_numbering((280, 13), "2026-10-04", tvdb, []) is None


# -- the cache -----------------------------------------------------------------

def test_tvdb_seasons_are_cached_for_a_week(db):
    client = FakeTVDB(TVDB_60)
    assert numbering.tvdb_season(db, client, 73290, 58, now=NOW) == TVDB_60[58]
    assert numbering.tvdb_season(db, client, 73290, 58, now=NOW + dt.timedelta(days=3)) == TVDB_60[58]
    assert client.calls == [(73290, 58)]
    numbering.tvdb_season(db, client, 73290, 58, now=NOW + dt.timedelta(days=9))
    assert client.calls == [(73290, 58), (73290, 58)]


def test_an_empty_season_is_cached_too(db):
    client = FakeTVDB(TVDB_60)
    assert numbering.tvdb_season(db, client, 73290, 60, now=NOW) == []
    numbering.tvdb_season(db, client, 73290, 60, now=NOW)
    assert client.calls == [(73290, 60)]


def test_no_key_or_tvdb_down_keeps_what_we_had(db):
    numbering.tvdb_season(db, FakeTVDB(TVDB_60), 73290, 58, now=NOW)

    class Down(FakeTVDB):
        def season_episodes(self, *a):
            raise RuntimeError("tvdb down")
    later = NOW + dt.timedelta(days=30)
    assert numbering.tvdb_season(db, Down(TVDB_60), 73290, 58, now=later) == TVDB_60[58]
    off = FakeTVDB(TVDB_60)
    off.enabled = False
    assert numbering.tvdb_season(db, off, 73290, 58, now=later) == TVDB_60[58]
    assert numbering.tvdb_season(db, off, 999, 1, now=later) == []


# -- the wishlist rows ---------------------------------------------------------

def _row(season, episode, air_date, **extra):
    return dict({"show_tmdb_id": 3, "show_title": "60 Minutes", "tvdb_id": 73290,
                 "season_number": season, "episode_number": episode, "air_date": air_date}, **extra)


def test_annotate_sets_the_scene_numbering(db):
    rows = [_row(59, 25, "2026-08-30"), _row(58, 48, "2026-08-30"), _row(1, 1, None),
            _row(59, 26, "2026-09-06", series_type="daily"), dict(_row(59, 25, "2026-08-30"), tvdb_id=None)]
    numbering.annotate(rows, db=db, client=FakeTVDB(TVDB_60))
    assert (rows[0]["alt_season"], rows[0]["alt_episode"]) == (58, 48)
    for r in rows[1:]:
        assert "alt_season" not in r


def test_the_drain_fetch_annotates(db, monkeypatch):
    monkeypatch.setattr(numbering, "_default_client", lambda d: FakeTVDB(TVDB_60))
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, season_number, episode_number, "
                     "air_date, status) VALUES ('episode', 3, '60 Minutes', 59, 25, '2026-08-30', 'wanted')")
        conn.commit()
    finally:
        conn.close()
    import api.video as video_api
    monkeypatch.setattr(video_api, "get_video_db", lambda: db)
    monkeypatch.setattr(db, "episode_wishlist_to_download",
                        lambda **k: [_row(59, 25, "2026-08-30")])
    items = wl._default_fetch_items("episode")
    assert (items[0]["alt_season"], items[0]["alt_episode"]) == (58, 48)


# -- the search and the gate -----------------------------------------------------

def test_search_context_and_queries_carry_it(monkeypatch):
    monkeypatch.setattr(wl, "_acceptable_titles", lambda p, k, t: [p])
    ctx = wl.search_context(_row(59, 25, "2026-08-30", alt_season=58, alt_episode=48), "episode")
    assert (ctx["alt_season"], ctx["alt_episode"]) == (58, 48)
    strategies = build_strategies("episode", "60 Minutes", season=59, episode=25,
                                  tvdb_id=73290, alt=(58, 48))
    tvsearch = [s for s in strategies if s[0] == "tvsearch"][0]
    # a tvdbid asks in TVDB numbering
    assert ("season", 58) in tvsearch[2] and ("ep", 48) in tvsearch[2]
    texts = [s[1] for s in strategies if s[0] == "search"]
    assert "60 Minutes S59E25" in texts and "60 Minutes S58E48" in texts
    # no alt: unchanged
    plain = build_strategies("episode", "60 Minutes", season=59, episode=25, tvdb_id=73290)
    assert ("season", 59) in [s for s in plain if s[0] == "tvsearch"][0][2]


def _judge(release, alt):
    return evaluate_release(parse_release(release), default_profile(), scope="episode",
                            want_season=59, want_episode=25, want_title="60 Minutes", want_alt=alt)


def test_the_gate_takes_either_number():
    assert _judge("60.Minutes.S58E48.1080p.WEB.h264-DiRT", (58, 48))["accepted"]
    assert _judge("60.Minutes.S59E25.1080p.WEB.h264-DiRT", (58, 48))["accepted"]
    v = _judge("60.Minutes.S58E47.1080p.WEB.h264-DiRT", (58, 48))
    assert not v["accepted"] and v["rejected"] == "Wrong season"
    assert not _judge("60.Minutes.S58E48.1080p.WEB.h264-DiRT", None)["accepted"]


def test_the_soulseek_search_asks_for_the_scene_number(monkeypatch, db):
    import api.video as video_api
    import core.video.download_monitor as mon
    import core.video.quality_profile as qp
    monkeypatch.setattr(wl, "_acceptable_titles", lambda p, k, t: [p])
    monkeypatch.setattr(video_api, "get_video_db", lambda: db)
    monkeypatch.setattr(qp, "load_for_item", lambda d, item: default_profile())
    queries = []

    def search(query):
        queries.append(query)
        return {"hits": [{"title": "60.Minutes.S58E48.1080p.WEB.h264-DiRT", "filename": "60.Minutes.S58E48.1080p.WEB.h264-DiRT.mkv",
                          "username": "u", "size_bytes": 1_500_000_000}]}
    monkeypatch.setattr(mon, "_search_for_retry", search)
    cands, err = wl._search_one_source("soulseek", _row(59, 25, "2026-08-30", alt_season=58, alt_episode=48), "episode")
    assert queries == ["60 Minutes S58E48"]
    assert cands[0]["accepted"], cands[0]["rejected"]


def test_the_importer_takes_the_scene_number_and_files_the_wished_one():
    dl = {"kind": "show", "title": "60 Minutes", "source": "soulseek",
          "release_title": "60.Minutes.S58E48.1080p.WEB.h264-DiRT", "size_bytes": 1_000_000_000,
          "target_dir": "/lib/tv",
          "search_ctx": json.dumps({"scope": "episode", "title": "60 Minutes", "season": 59, "episode": 25,
                                    "alt_season": 58, "alt_episode": 48})}
    p = importer.plan_import(dl, "/dl/x/60.minutes.s58e48.mkv", list_dir=lambda d: [])
    assert p["action"] != "reject", p.get("reason")
    assert "S59E25" in p["dest"]["filename"]
    # without the scene numbering it's the wrong episode, as before
    dl["search_ctx"] = json.dumps({"scope": "episode", "title": "60 Minutes", "season": 59, "episode": 25})
    assert importer.plan_import(dl, "/dl/x/60.minutes.s58e48.mkv", list_dir=lambda d: [])["action"] == "reject"


# -- anime ------------------------------------------------------------------------

def test_a_show_tagged_anime_is_numbered_as_anime(db):
    conn = db._get_connection()
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(shows)")}
        base = {"title": "One Piece", "tmdb_id": 37854, "year": 1999, "monitored": 1,
                "added_at": "2026-01-01", "updated_at": "2026-01-01", "episodes_synced": 1,
                "ratings_synced": 1, "details_synced": 1}
        use = {k: v for k, v in base.items() if k in cols}
        conn.execute("INSERT INTO shows (%s) VALUES (%s)" % (", ".join(use), ", ".join("?" * len(use))), list(use.values()))
        conn.execute("INSERT INTO shows (%s) VALUES (%s)" % (", ".join(use), ", ".join("?" * len(use))),
                     [{"title": "Family Guy", "tmdb_id": 1434}.get(k, v) for k, v in use.items()])
        sid = conn.execute("SELECT id FROM shows WHERE tmdb_id=37854").fetchone()[0]
        fg = conn.execute("SELECT id FROM shows WHERE tmdb_id=1434").fetchone()[0]
        for name in ("Animation", "Anime"):
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (name,))
        anime = conn.execute("SELECT id FROM genres WHERE name='Anime'").fetchone()[0]
        animation = conn.execute("SELECT id FROM genres WHERE name='Animation'").fetchone()[0]
        conn.execute("INSERT INTO show_genres (show_id, genre_id) VALUES (?, ?)", (sid, anime))
        conn.execute("INSERT INTO show_genres (show_id, genre_id) VALUES (?, ?)", (sid, animation))
        conn.execute("INSERT INTO show_genres (show_id, genre_id) VALUES (?, ?)", (fg, animation))
        for tmdb, title in ((37854, "One Piece"), (1434, "Family Guy")):
            conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, season_number, episode_number, "
                         "air_date, status) VALUES ('episode', ?, ?, 23, 1177, '2026-08-30', 'wanted')", (tmdb, title))
        conn.commit()
    finally:
        conn.close()
    rows = {r["show_title"]: r for r in db.episode_wishlist_to_download(due_only=False)}
    assert rows["One Piece"]["series_type"] == "anime"
    assert rows["Family Guy"]["series_type"] is None          # animation isn't anime
    # a type set by hand wins
    conn = db._get_connection()
    try:
        conn.execute("UPDATE shows SET series_type='standard' WHERE tmdb_id=37854")
        conn.commit()
    finally:
        conn.close()
    rows = {r["show_title"]: r for r in db.episode_wishlist_to_download(due_only=False)}
    assert rows["One Piece"]["series_type"] == "standard"


# -- when the TMDB number is another episode -------------------------------------

# House Hunters: TMDB and TVDB swap numbers across the season boundary. TVDB's
# S279E10 aired 09-27; TMDB calls the 10-04 episode S279E10.
TVDB_HH = {279: [(10, "2026-09-27")], 280: [(13, "2026-10-04")], 278: []}


def test_a_number_that_belongs_to_another_episode_is_exclusive(db):
    rows = [dict(_row(279, 10, "2026-10-04"), show_title="House Hunters", tvdb_id=1)]
    numbering.annotate(rows, db=db, client=FakeTVDB(TVDB_HH))
    assert (rows[0]["alt_season"], rows[0]["alt_episode"]) == (280, 13)
    assert rows[0]["alt_exclusive"] is True
    # 60 Minutes' S59E25 isn't another episode on TVDB: not exclusive
    rows = [_row(59, 25, "2026-08-30")]
    numbering.annotate(rows, db=db, client=FakeTVDB(TVDB_60))
    assert "alt_exclusive" not in rows[0]


def test_exclusive_refuses_the_tmdb_number():
    def judge(release, alt):
        return evaluate_release(parse_release(release), default_profile(), scope="episode",
                                want_season=279, want_episode=10, want_title="House Hunters", want_alt=alt)
    assert judge("House.Hunters.S280E13.1080p.WEB.h264-GRP", (280, 13, True))["accepted"]
    v = judge("House.Hunters.S279E10.1080p.WEB.h264-GRP", (280, 13, True))
    assert not v["accepted"] and "S280E13" in v["rejected"]
    # not exclusive: both still count
    assert judge("House.Hunters.S279E10.1080p.WEB.h264-GRP", (280, 13))["accepted"]


def test_exclusive_searches_only_the_scene_number():
    strategies = build_strategies("episode", "House Hunters", season=279, episode=10, alt=(280, 13, True))
    texts = [s[1] for s in strategies if s[0] == "search"]
    assert "House Hunters S280E13" in texts and "House Hunters S279E10" not in texts


def test_exclusive_importer_refuses_the_tmdb_number():
    ctx = {"scope": "episode", "title": "House Hunters", "season": 279, "episode": 10,
           "alt_season": 280, "alt_episode": 13, "alt_exclusive": True}
    dl = {"kind": "show", "title": "House Hunters", "source": "soulseek", "size_bytes": 1_000_000_000,
          "target_dir": "/lib/tv", "search_ctx": json.dumps(ctx),
          "release_title": "House.Hunters.S279E10.1080p.WEB.h264-GRP"}
    assert importer.plan_import(dl, "/dl/x/hh.s279e10.mkv", list_dir=lambda d: [])["action"] == "reject"
    dl["release_title"] = "House.Hunters.S280E13.1080p.WEB.h264-GRP"
    p = importer.plan_import(dl, "/dl/x/hh.s280e13.mkv", list_dir=lambda d: [])
    assert p["action"] != "reject" and "S279E10" in p["dest"]["filename"]
