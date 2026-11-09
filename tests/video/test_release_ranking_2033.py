"""video release ranking (#2033, SeadogsBooty).

a +100 MeGusta custom format lost to bigger non-MeGusta releases that only
spelled out ENGLISH or carried Atmos, because everything was one flat sum.
the ranking is now Radarr's: quality (ladder tier + the profile's own
preferences) first, then custom formats, then tie-breaks, then
availability and size. every picker uses core/video/release_rank.
"""

from __future__ import annotations

import pytest
from flask import Flask

import core.video.custom_formats as cf
from core.video import quality_profile
from core.video.quality_eval import evaluate_release
from core.video.release_parse import parse_release
from core.video.release_rank import rank_key, sort_ranked
from database.video_database import VideoDatabase

GB = 1024 ** 3


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def _hit(name, gb, user="u", **kw):
    return {"title": name, "filename": name + ".mkv", "size_bytes": int(gb * GB),
            "username": user, **kw}


def _rank(hits, profile, scope="episode", season=1, episode=1, title="Dexter"):
    from api.video.downloads import _evaluate_hits
    return _evaluate_hits(hits, profile, scope, season, episode, blocked=frozenset(),
                          blocked_users=frozenset(), want_title=title)


def _profile(**kw):
    return quality_profile.normalize(kw)


# the requester's case: same 1080p HEVC quality, the other one is bigger,
# spells out ENGLISH and carries Atmos
MEGUSTA = "Dexter.S01E01.1080p.WEB.h265-MeGusta"
OTHER = "Dexter.S01E01.ENGLISH.1080p.WEB.DDP5.1.Atmos.h265-OTHER"


def test_a_100_format_beats_tagged_english_atmos_and_size(db, client):
    cf.save_format(db, {"name": "MeGusta", "include": ["-MeGusta"], "score": 100})
    p = _profile(audio_language_mode="prefer", audio_languages=["en"])
    out = _rank([_hit(OTHER, 3.0, "a"), _hit(MEGUSTA, 0.6, "b")], p)
    assert out[0]["title"] == MEGUSTA
    assert out[0]["accepted"] and out[1]["accepted"]


def test_a_format_never_beats_a_quality_step(db, client):
    cf.save_format(db, {"name": "MeGusta", "include": ["-MeGusta"], "score": 100})
    out = _rank([_hit("Dexter.S01E01.720p.WEB.h265-MeGusta", 0.4, "a"),
                 _hit("Dexter.S01E01.1080p.WEB.h265-OTHER", 2.0, "b")], _profile())
    assert out[0]["title"].endswith("1080p.WEB.h265-OTHER")


def test_without_formats_the_order_is_what_it_was(db, client):
    # no formats, no preferences touched: bigger file still breaks a full tie
    out = _rank([_hit("Dexter.S01E01.1080p.WEB.h265-A", 1.0, "a"),
                 _hit("Dexter.S01E01.1080p.WEB.h265-B", 2.0, "b")], _profile())
    assert out[0]["title"].endswith("-B")


# ── language ────────────────────────────────────────────────────────────────

def _ev(name, profile):
    return evaluate_release(parse_release(name), profile, scope="episode",
                            want_season=1, want_episode=1, want_title="Dexter")


def test_untagged_is_not_penalised_for_an_english_profile():
    for mode in ("prefer", "require"):
        p = _profile(audio_language_mode=mode, audio_languages=["en"])
        bare = _ev("Dexter.S01E01.1080p.WEB.h265-GRP", p)
        eng = _ev("Dexter.S01E01.ENGLISH.1080p.WEB.h265-GRP", p)
        assert bare["accepted"] and eng["accepted"]
        assert bare["parts"]["quality"] == eng["parts"]["quality"]


def test_a_release_naming_only_another_language_is_penalised_or_rejected():
    pref = _profile(audio_language_mode="prefer", audio_languages=["en"])
    french = _ev("Dexter.S01E01.FRENCH.1080p.WEB.h265-GRP", pref)
    bare = _ev("Dexter.S01E01.1080p.WEB.h265-GRP", pref)
    assert french["parts"]["quality"] < bare["parts"]["quality"]
    req = _profile(audio_language_mode="require", audio_languages=["en"])
    v = _ev("Dexter.S01E01.FRENCH.1080p.WEB.h265-GRP", req)
    assert not v["accepted"] and v["rejected"].startswith("Tagged FR audio, you want EN")
    assert _ev("Dexter.S01E01.MULTi.FRENCH.1080p.WEB.h265-GRP", req)["accepted"]


def test_a_dub_profile_still_skips_the_english_original():
    # Thibs' french profile: an untagged release is the english original
    req = _profile(audio_language_mode="require", audio_languages=["fr"])
    assert not _ev("Dexter.S01E01.1080p.WEB.h265-GRP", req)["accepted"]
    pref = _profile(audio_language_mode="prefer", audio_languages=["fr"])
    dub = _ev("Dexter.S01E01.MULTi.720p.WEB.h265-GRP", pref)
    orig = _ev("Dexter.S01E01.1080p.WEB.h265-GRP", pref)
    assert dub["parts"]["quality"] > orig["parts"]["quality"]


# ── preferred size ──────────────────────────────────────────────────────────

def test_preferred_size_is_off_by_default_and_clamped():
    d = quality_profile.default_profile()
    assert d["preferred_movie_gb"] == 0 and d["preferred_episode_gb"] == 0
    p = quality_profile.normalize({"preferred_episode_gb": "0.84", "max_episode_gb": 3,
                                   "preferred_movie_gb": 50, "max_movie_gb": 20})
    assert p["preferred_episode_gb"] == 0.8
    assert p["preferred_movie_gb"] == 20            # never past the hard cap
    assert quality_profile.normalize({"preferred_movie_gb": "x"})["preferred_movie_gb"] == 0
    assert quality_profile.normalize({"preferred_movie_gb": -2})["preferred_movie_gb"] == 0


def test_closer_to_the_preferred_size_wins_a_tie(db, client):
    p = _profile(preferred_episode_gb=1, max_episode_gb=3)
    out = _rank([_hit("Dexter.S01E01.1080p.WEB.h265-A", 2.8, "a"),
                 _hit("Dexter.S01E01.1080p.WEB.h265-B", 0.9, "b")], p)
    assert out[0]["title"].endswith("-B")
    assert any("preferred size" in b["label"] for b in out[0]["breakdown"])


def test_preferred_size_never_beats_a_format(db, client):
    cf.save_format(db, {"name": "MeGusta", "include": ["-MeGusta"], "score": 100})
    p = _profile(preferred_episode_gb=1)
    out = _rank([_hit("Dexter.S01E01.1080p.WEB.h265-A", 1.0, "a"),
                 _hit(MEGUSTA, 2.5, "b")], p)
    assert out[0]["title"] == MEGUSTA


def test_preferred_size_ignores_packs():
    p = _profile(preferred_episode_gb=1, preferred_movie_gb=1)
    v = evaluate_release(parse_release("Dexter.S01.1080p.WEB.h265-GRP"), p, scope="season",
                         want_season=1, size_gb=30)
    assert v["parts"]["size_fit"] == 0


# ── the profile's own ladder order and audio preference ─────────────────────

def test_the_default_ladder_keeps_its_old_points():
    from core.video.quality_eval import tier_points
    d = quality_profile.default_profile()
    old = {"remux-2160p": 490, "bluray-2160p": 470, "web-2160p": 455, "webrip-2160p": 440,
           "hdtv-2160p": 425, "remux-1080p": 390, "bluray-1080p": 370, "web-1080p": 355,
           "webrip-1080p": 340, "hdtv-1080p": 325, "bluray-720p": 270, "web-720p": 255,
           "webrip-720p": 240, "hdtv-720p": 225}
    for k, pts in old.items():
        assert tier_points(k, d) == pts


def test_moving_a_tier_up_the_ladder_ranks_it_higher():
    d = quality_profile.default_profile()
    web = _ev("Dexter.S01E01.1080p.WEB-DL.x264-GRP", d)
    blu = _ev("Dexter.S01E01.1080p.BluRay.x264-GRP", d)
    assert blu["parts"]["quality"] > web["parts"]["quality"]
    keys = [t["key"] for t in d["tiers"]]
    keys.remove("web-1080p")
    keys.insert(keys.index("bluray-1080p"), "web-1080p")
    moved = quality_profile.normalize({"tiers": [{"key": k, "enabled": True} for k in keys]})
    web = _ev("Dexter.S01E01.1080p.WEB-DL.x264-GRP", moved)
    blu = _ev("Dexter.S01E01.1080p.BluRay.x264-GRP", moved)
    assert web["parts"]["quality"] > blu["parts"]["quality"]


def test_prefer_audio_counts_as_a_profile_preference():
    any_ = _profile()
    atmos = _profile(prefer_audio="atmos")
    a = _ev(OTHER, any_)
    b = _ev(OTHER, atmos)
    assert b["parts"]["quality"] == a["parts"]["quality"] + 30
    # with 'any' atmos is only a tie-break, below custom formats
    assert any(x["group"] == "tiebreak" and "ATMOS" in x["label"] for x in a["breakdown"])


# ── transparency ────────────────────────────────────────────────────────────

def test_every_row_explains_its_rank(db, client):
    cf.save_format(db, {"name": "MeGusta", "include": ["-MeGusta"], "score": 100})
    out = _rank([_hit(OTHER, 3.0, "a", seeders=4), _hit(MEGUSTA, 0.6, "b")], _profile())
    for r in out:
        assert r["breakdown"] and len(r["rank"]) == 7
        for g, idx in (("quality", 1), ("formats", 2), ("tiebreak", 3)):
            assert sum(b["points"] for b in r["breakdown"] if b["group"] == g) == r["rank"][idx]
        assert r["score"] == r["rank"][1] + r["rank"][2] + r["rank"][3]
    top = out[0]
    assert {"group": "formats", "label": "Custom format: MeGusta", "points": 100} in top["breakdown"]


def test_search_results_endpoint_carries_the_breakdown(db, client):
    # the mock source returns canned hits through the same _evaluate_hits
    res = client.post("/api/video/downloads/search", json={"scope": "movie", "title": "Dune",
                                                           "source": "mock"})
    rows = (res.get_json() or {}).get("results") or []
    assert rows
    for r in rows:
        assert "breakdown" in r and "rank" in r


# ── one ranking, every picker ───────────────────────────────────────────────

POOL = [
    _hit(OTHER, 3.0, "a", seeders=50, protocol="torrent", download_url="magnet:?a"),
    _hit(MEGUSTA, 0.6, "b", seeders=5, protocol="torrent", download_url="magnet:?b"),
    _hit("Dexter.S01E01.720p.WEB.h265-MeGusta", 0.3, "c", seeders=90, protocol="torrent",
         download_url="magnet:?c"),
    _hit("Dexter.S01E01.FRENCH.1080p.WEB.h265-FR", 1.0, "d", seeders=9, protocol="torrent",
         download_url="magnet:?d"),
]


def test_every_picker_agrees(db, client, monkeypatch):
    """interactive search, wishlist 'Search now', the drain, RSS, upgrades and
    the retry requery all take the same release (#2033)."""
    import core.automation.handlers.video_process_wishlist as vpw
    import core.video.prowlarr_search as ps
    import core.video.download_monitor as dm
    from core.video import wishlist_search
    cf.save_format(db, {"name": "MeGusta", "include": ["-MeGusta"], "score": 100})
    profile = _profile(audio_language_mode="prefer", audio_languages=["en"])
    quality_profile.save(db, profile)

    # interactive / manual search on a title page
    interactive = _rank(list(POOL), profile)
    want = interactive[0]["title"]
    assert want == MEGUSTA

    # the drain, wishlist 'Search now', RSS and upgrades all search through
    # _search_one_source; feed it the same pool
    monkeypatch.setattr(ps, "prowlarr_search", lambda *a, **k: {"configured": True,
                                                                "hits": [dict(h) for h in POOL]})
    monkeypatch.setattr(vpw, "_extto_hits_for_context", lambda ctx: ([], None))
    monkeypatch.setattr(vpw, "search_context", lambda it, mt: {
        "scope": "episode", "title": "Dexter", "season": 1, "episode": 1})
    item = {"show_title": "Dexter", "title": "Dexter", "season_number": 1, "episode_number": 1,
            "show_tmdb_id": 1}
    cands, _ = vpw._search_one_source("torrent", dict(item), "episode")
    assert vpw.pick_best(cands)["title"] == want                     # drain + RSS
    assert vpw.pick_best(cands, min_rank=2)["title"] == want         # upgrade over a 720p copy
    assert [c["title"] for c in cands] == [r["title"] for r in interactive]

    # wishlist 'Search now' merges every source and re-sorts
    # torrent answers, the other sources find nothing; listed worst-first so
    # the merge has to sort
    monkeypatch.setattr(vpw, "_search_one_source",
                        lambda src, it, mt: ([dict(c) for c in reversed(cands)], None)
                        if src == "torrent" else ([], None))
    merged, _ = wishlist_search._all_source_search(dict(item), "episode")
    assert merged[0]["title"] == want

    # the download monitor's retry requery (slskd) ranks with the same judge
    picked = {}
    monkeypatch.setattr(dm, "_search_for_retry", lambda q: {"hits": [dict(h) for h in POOL]})
    monkeypatch.setattr(dm, "_apply_candidate",
                        lambda db_, dl_id, row, cand, rest: picked.setdefault("c", cand) or True)
    row = {"id": 1, "status": "searching", "kind": "show", "attempts": 0,
           "tried_files": "[]", "tried_queries": "[]", "candidates": "[]",
           "search_ctx": '{"scope": "episode", "title": "Dexter", "season": 1, "episode": 1}'}

    class _Db:
        def get_video_download(self, _):
            return dict(row)

        def update_video_download(self, *_a, **_k):
            return None

        def blocked_usernames(self):
            return frozenset()

        def video_blocklist_pairs(self):
            return frozenset()

        def __getattr__(self, name):
            return getattr(db, name)

    import core.video.retry as retry
    monkeypatch.setattr(retry, "plan_retry",
                        lambda *a, **k: {"action": "requery", "query": "Dexter S01E01", "ctx":
                                         {"scope": "episode", "title": "Dexter", "season": 1,
                                          "episode": 1}})
    monkeypatch.setattr(dm, "_db_provider", lambda: _Db())
    dm._requery_worker(1)
    assert picked["c"]["filename"] == want + ".mkv"


def test_rank_key_falls_back_for_a_row_without_rank():
    rows = [{"accepted": True, "score": 10}, {"accepted": True, "score": 50},
            {"accepted": False, "score": 900}]
    assert [r["score"] for r in sort_ranked(rows)] == [50, 10, 900]
    assert rank_key({"rank": [1, 2, 3, 4, 5, 6, 7]}) == (1, 2, 3, 4, 5, 6, 7)
