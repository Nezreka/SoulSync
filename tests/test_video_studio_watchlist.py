"""Studio watchlist (Phase 2): follow a studio → its un-owned films get wishlisted.

Pure selection logic with all I/O injected (no DB, no TMDB), the full scan over fakes,
the engine catalog-paging seam, plus DB + API seam tests for the studio watchlist kind.
"""

from __future__ import annotations

import pytest
from flask import Flask

from core.automation.handlers.video_scan_watchlist_studios import (
    auto_video_scan_watchlist_studios,
    build_detail_blob,
    select_studio_movie_gaps,
)
from database.video_database import VideoDatabase


TODAY = "2026-07-13"          # settle cutoff = 2026-05-29 (TODAY - 45d)


def _film(tid, title, *, date="2020-01-01", votes=500, pop=10.0, poster="/p.jpg"):
    """A film in the shape engine.company_movies / company_films yields."""
    return {"kind": "movie", "tmdb_id": tid, "title": title, "date": date,
            "year": (date or "")[:4] or None, "vote_count": votes, "popularity": pop,
            "poster": poster, "rating": 7.0}


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


# ── pure: select_studio_movie_gaps ────────────────────────────────────────────

def test_status_tagging_and_look_ahead_horizon():
    films = [_film(1, "Out", date="2026-06-01"),       # released → wanted
             _film(2, "Soon", date="2027-01-01"),      # within a year → monitored
             _film(3, "Undated", date=None),           # no date → skipped
             _film(4, "Way off", date="2029-01-01")]   # >1yr out → skipped
    gaps = {g["tmdb_id"]: g["_status"] for g in
            select_studio_movie_gaps(films, [], [], today=TODAY, since=None)}
    assert gaps == {1: "wanted", 2: "monitored"}       # undated + far-future dropped


def test_vote_floor_gates_only_settled_obscure_films():
    films = [
        _film(1, "Old obscure", date="2000-01-01", votes=5),      # settled + few votes → drop
        _film(2, "Old classic", date="2000-01-01", votes=800),    # settled but popular → keep
        _film(3, "Just out, few votes", date="2026-07-10", votes=2),  # not settled → keep
        _film(4, "Upcoming, no votes", date="2027-01-01", votes=0),   # upcoming → keep
    ]
    kept = {g["tmdb_id"] for g in
            select_studio_movie_gaps(films, [], [], today=TODAY, since=None, vote_floor=40)}
    assert kept == {2, 3, 4}                        # the settled-obscure film is the only drop


def test_forward_only_since_cutoff_skips_old_catalog_but_keeps_upcoming():
    films = [_film(1, "Ancient", date="2000-01-01", votes=900),
             _film(2, "Recent", date="2026-06-01"), _film(3, "Upcoming", date="2027-01-01")]
    kept = {g["tmdb_id"] for g in
            select_studio_movie_gaps(films, [], [], today=TODAY, since="2026-01-01")}
    assert kept == {2, 3}                           # old is before the follow date; upcoming always kept


def test_owned_ignored_and_dupes_dropped():
    films = [_film(1, "Owned"), _film(2, "Ignored"), _film(3, "Keep"), _film(3, "Dupe")]
    kept = [g["tmdb_id"] for g in
            select_studio_movie_gaps(films, [1], [2], today=TODAY, since=None, vote_floor=0)]
    assert kept == [3]                              # owned + ignored out, id 3 once


# ── full scan over fakes ──────────────────────────────────────────────────────

def test_scan_wishlists_missing_films_and_promotes():
    studios = [{"tmdb_id": 41077, "title": "A24", "date_added": "2026-01-01", "lookback_years": 0}]
    films = [_film(10, "Released", date="2026-06-01"),      # new → wanted (added)
             _film(11, "Upcoming", date="2027-01-01"),      # → monitored (upcoming)
             _film(12, "Out now", date="2026-06-15")]       # already monitored → promoted
    wished = {12: "monitored"}                              # 12 already on the wishlist, unreleased-when-added
    added_calls = []

    def add_movie(tid, title, *, year, poster_url, status, detail_json):
        added_calls.append((tid, status))
        return True

    deps = _Deps()
    res = auto_video_scan_watchlist_studios(
        {"_automation_id": "a1"}, deps,
        fetch_studios=lambda: studios,
        fetch_films=lambda cid: films,
        fetch_detail=lambda tid: {"title": "T", "crew": [{"job": "Director", "name": "D"}]},
        owned_ids=lambda: set(),
        ignored_ids=lambda: [],
        wishlisted_status=lambda: dict(wished),
        add_movie=add_movie,
        today_fn=lambda: TODAY)
    assert res["status"] == "completed" and res["studios"] == 1
    assert res["movies_added"] == 1 and res["upcoming"] == 1 and res["promoted"] == 1
    assert (10, "wanted") in added_calls and (11, "monitored") in added_calls
    assert (12, "wanted") in added_calls        # promotion re-adds as wanted


def test_scan_no_studios_is_a_clean_noop():
    res = auto_video_scan_watchlist_studios({"_automation_id": "x"}, _Deps(),
                                            fetch_studios=lambda: [])
    assert res == {"status": "completed", "studios": 0, "movies_added": 0, "upcoming": 0,
                   "promoted": 0, "_manages_own_progress": True}


def test_build_detail_blob_carries_studio_provenance():
    blob = build_detail_blob({"title": "Film", "overview": "O",
                              "crew": [{"job": "Director", "name": "Dir"}]},
                             _film(5, "Film"), {"tmdb_id": 41077, "title": "A24"})
    assert blob["added_via"] == {"studio_tmdb_id": 41077, "studio_name": "A24", "as": "studio"}
    assert blob["director"] == "Dir"
    # degrades to catalog fields when detail is missing
    bare = build_detail_blob(None, _film(6, "Bare", date="2025-01-01"),
                             {"tmdb_id": 1, "title": "S"})
    assert bare["title"] == "Bare" and bare["added_via"]["as"] == "studio"


# ── engine catalog paging ─────────────────────────────────────────────────────

class _FakeTmdb:
    enabled = True

    def __init__(self, pages):
        self._pages = pages           # {page_no: {results, total_pages}}
        self.calls = []

    def company_movies(self, company_id, *, page=1, sort="primary_release_date.desc"):
        self.calls.append(page)
        return self._pages.get(page, {"results": [], "total_pages": len(self._pages)})


def test_company_films_pages_through_and_bounds(tmp_path):
    from core.video.enrichment.engine import VideoEnrichmentEngine
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    pages = {1: {"results": [_film(1, "a")], "total_pages": 3},
             2: {"results": [_film(2, "b")], "total_pages": 3},
             3: {"results": [_film(3, "c")], "total_pages": 3}}
    eng = VideoEnrichmentEngine(db, {"tmdb": _FakeTmdb(pages)})
    films = eng.company_films(41077, max_pages=10)
    assert [f["tmdb_id"] for f in films] == [1, 2, 3]      # all pages walked, in order
    # max_pages caps a huge catalog
    big = {p: {"results": [_film(p, str(p))], "total_pages": 50} for p in range(1, 51)}
    eng2 = VideoEnrichmentEngine(db, {"tmdb": _FakeTmdb(big)})
    assert len(eng2.company_films(41077, max_pages=2)) == 2


# ── DB + API seams ────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_db_follow_list_count_state_and_lookback(db):
    assert db.add_to_watchlist("studio", 41077, "A24", poster_url="/logo.png") is True
    lst = db.list_watchlist("studio")
    assert [s["tmdb_id"] for s in lst] == [41077] and lst[0]["kind"] == "studio"
    assert db.watchlist_counts()["studio"] == 1
    assert db.watchlist_state("studio", [41077, 99]) == {41077: True}
    # lookback defaults forward-only (0), settable, and scoped to the studio kind
    assert db.get_studio_lookback(41077)["lookback_years"] == 0
    assert db.set_studio_lookback(41077, 5) is True
    assert db.get_studio_lookback(41077)["lookback_years"] == 5
    assert db.get_person_lookback(41077) is None        # not a person follow
    # unfollow tombstones it
    assert db.remove_from_watchlist("studio", 41077) is True
    assert db.list_watchlist("studio") == []


def _client(tmp_path):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi, db


def test_api_follow_and_settings(tmp_path):
    client, videoapi, _ = _client(tmp_path)
    try:
        r = client.post("/api/video/watchlist/add",
                        json={"kind": "studio", "tmdb_id": 41077, "title": "A24",
                              "poster_url": "/logo.png"}).get_json()
        assert r["success"] and r["watched"] is True
        grouped = client.get("/api/video/watchlist").get_json()
        assert [s["tmdb_id"] for s in grouped["studios"]] == [41077]
        assert grouped["counts"]["studio"] == 1
        s = client.get("/api/video/watchlist/studio/41077/settings").get_json()
        assert s["success"] and s["settings"]["lookback_years"] == 0
        saved = client.post("/api/video/watchlist/studio/41077/settings",
                            json={"lookback_years": -1}).get_json()
        assert saved["settings"]["lookback_years"] == -1
        # unknown studio → 404
        assert client.get("/api/video/watchlist/studio/999/settings").status_code == 404
        # check hydrates the follow button
        chk = client.post("/api/video/watchlist/check",
                          json={"kind": "studio", "tmdb_ids": [41077, 1]}).get_json()
        assert chk["results"] == {"41077": True}
    finally:
        videoapi._video_db = None


# ── studio family presets (Phase 3) ───────────────────────────────────────────

def test_studio_presets_are_pure_with_valid_deduped_members():
    from core.video.studio_presets import studio_presets, preset_member_ids, get_preset
    ps = studio_presets()
    disney = get_preset("disney")
    assert disney and {3, 420, 1}.issubset({m["tmdb_id"] for m in disney["members"]})  # pixar/marvel/lucasfilm
    ids = preset_member_ids()
    assert len(ids) == len(set(ids))                # deduped across families
    for p in ps:
        for m in p["members"]:
            assert isinstance(m["tmdb_id"], int) and m["name"] and m["logo"]   # logos baked in
    # returned copies are independent — mutating one call doesn't leak into the source
    studio_presets()[0]["members"][0]["logo"] = "MUTATED"
    assert get_preset("disney")["members"][0]["logo"] != "MUTATED"


def test_studio_presets_endpoint_carries_logo_and_followed(tmp_path):
    # No engine/TMDB needed — logos are baked into the preset data, so the picker is instant.
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.add_to_watchlist("studio", 3, "Pixar")        # follow one member only
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        d = client.get("/api/video/studio/presets").get_json()
        assert d["success"]
        disney = [p for p in d["presets"] if p["id"] == "disney"][0]
        pix = [m for m in disney["members"] if m["tmdb_id"] == 3][0]
        marvel = [m for m in disney["members"] if m["tmdb_id"] == 420][0]
        assert pix["followed"] is True and pix["logo"]        # followed member, logo baked in
        assert marvel["followed"] is False                    # sibling stays independent
    finally:
        videoapi._video_db = None
