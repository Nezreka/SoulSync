"""Watchlist-people scan automation: for every followed person, wishlist every un-owned
MOVIE they acted in or directed (back catalog + upcoming).

Pure logic with all I/O injected (no DB, no TMDB), plus DB-seam tests for the new
``add_movie_to_wishlist`` status/detail_json behaviour + ``wishlisted_movie_status``.
"""

from __future__ import annotations

import json

import pytest

from core.automation.handlers.video_scan_watchlist_people import (
    auto_video_scan_watchlist_people,
    build_detail_blob,
    is_director_movie_credit,
    is_released,
    is_relevant_movie_credit,
    is_self_credit,
    select_person_movie_gaps,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _credit(tid, title, *, dept="Acting", role="A Character", date="2000-01-01",
            pop=10.0, kind="movie", poster="/p.jpg"):
    return {"kind": kind, "tmdb_id": tid, "title": title, "department": dept, "role": role,
            "date": date, "year": (date or "")[:4] or None, "popularity": pop, "poster": poster}


# ── pure credit classification ────────────────────────────────────────────────
def test_is_self_credit():
    assert is_self_credit("Self")
    assert is_self_credit("Himself")
    assert is_self_credit("Self - Host")
    assert is_self_credit("Narrator (archive footage)")
    assert not is_self_credit("Bruce Wayne")
    assert not is_self_credit("")
    assert not is_self_credit(None)


def test_relevant_movie_credit():
    assert is_relevant_movie_credit(_credit(1, "Acted", dept="Acting", role="Hero"))
    # crew director (TMDB files directors under the Directing department, job=Director)
    assert is_relevant_movie_credit(_credit(2, "Directed", dept="Directing", role="Director"))
    assert is_director_movie_credit(_credit(2, "Directed", dept="Directing", role="Director"))
    # 'plays themselves' is dropped
    assert not is_relevant_movie_credit(_credit(3, "Doc", dept="Acting", role="Self"))
    # a TV credit is never relevant (movies only)
    assert not is_relevant_movie_credit(_credit(4, "TV", kind="show", role="Host"))
    # other crew (writer/producer) doesn't count
    assert not is_relevant_movie_credit(_credit(5, "Wrote", dept="Writing", role="Writer"))


def test_is_released():
    assert is_released("1999-05-01", "2026-06-25")
    assert is_released("2026-06-25", "2026-06-25")        # today counts as released
    assert not is_released("2027-01-01", "2026-06-25")    # future
    assert not is_released("", "2026-06-25")              # no date → not yet released
    assert not is_released(None, "2026-06-25")


def test_select_filters_owned_ignored_and_tags_status():
    credits = [
        _credit(1, "Owned", pop=99),                                   # owned → dropped
        _credit(2, "Ignored", pop=98),                                 # ignored → dropped
        _credit(3, "Old Hit", pop=50, date="1990-01-01"),              # released actor → wanted
        _credit(4, "Future Film", pop=40, date="2099-01-01",           # upcoming director → monitored
                dept="Directing", role="Director"),
        _credit(5, "TV Thing", kind="show", pop=80),                   # show → dropped
        _credit(6, "Doc Self", role="Self", pop=70),                   # self → dropped
        _credit(7, "Wrote It", dept="Writing", role="Writer", pop=60), # writer → dropped
    ]
    gaps = select_person_movie_gaps(credits, owned_ids={1}, ignored_ids={2}, today="2026-06-25")
    ids = [(g["tmdb_id"], g["_status"]) for g in gaps]
    assert ids == [(3, "wanted"), (4, "monitored")]       # only relevant, popularity-ranked


def test_select_ranks_by_popularity():
    credits = [_credit(10, "Low", pop=1), _credit(11, "High", pop=99), _credit(12, "Mid", pop=50)]
    gaps = select_person_movie_gaps(credits, owned_ids=set(), ignored_ids=set(), today="2026-06-25")
    assert [g["tmdb_id"] for g in gaps] == [11, 12, 10]


# ── rich detail blob ──────────────────────────────────────────────────────────
def _full_detail():
    return {
        "kind": "movie", "tmdb_id": 3, "title": "Old Hit", "overview": "A synopsis.",
        "tagline": "Tag.", "status": "Released", "rating": 7.8, "imdb_id": "tt1",
        "poster_url": "https://img/p.jpg", "backdrop_url": "https://img/b.jpg",
        "logo": "https://img/l.png", "genres": ["Drama", "Thriller"],
        "runtime_minutes": 121, "studio": "A24", "year": "1990", "release_date": "1990-01-01",
        "cast": [{"name": "P%d" % i, "character": "C%d" % i} for i in range(20)],
        "crew": [{"name": "The Director", "job": "Director"}, {"name": "W", "job": "Writer"}],
        "_extras": {"similar": ["lots", "of", "heavy", "data"]},
    }


def test_build_detail_blob_trims_and_adds_provenance():
    credit = _credit(3, "Old Hit", role="Lead Role")
    person = {"tmdb_id": 500, "title": "Famous Actor"}
    blob = build_detail_blob(_full_detail(), credit, person)
    assert blob["overview"] == "A synopsis."
    assert blob["backdrop_url"] == "https://img/b.jpg"
    assert blob["genres"] == ["Drama", "Thriller"]
    assert blob["director"] == "The Director"
    assert len(blob["cast"]) == 15                     # capped, not the full 20
    assert "_extras" not in blob                        # heavy data dropped
    via = blob["added_via"]
    assert via == {"person_tmdb_id": 500, "person_name": "Famous Actor",
                   "role": "Lead Role", "as": "actor"}


def test_build_detail_blob_marks_director_provenance():
    credit = _credit(4, "Directed", dept="Directing", role="Director")
    blob = build_detail_blob(_full_detail(), credit, {"tmdb_id": 1, "title": "Auteur"})
    assert blob["added_via"]["as"] == "director"


def test_build_detail_blob_degrades_without_detail():
    credit = _credit(9, "Lost Detail", role="X", poster="/credit-poster.jpg")
    for detail in (None, {"redirect": {"source": "library", "id": 7}}):
        blob = build_detail_blob(detail, credit, {"tmdb_id": 1, "title": "P"})
        assert blob["poster_url"] == "/credit-poster.jpg"   # falls back to the credit
        assert blob["title"] == "Lost Detail"
        assert blob["added_via"]["person_name"] == "P"


# ── handler: first-run backlog ────────────────────────────────────────────────
def _handler(people, credits_by_person, *, owned=None, ignored=None, wished=None,
             detail=None, today="2026-06-25"):
    """Run the handler with fakes; return (result, list-of-add-calls, deps)."""
    adds = []
    detail_calls = []

    def add_movie(tmdb_id, title, *, year, poster_url, status, detail_json):
        adds.append({"tmdb_id": tmdb_id, "title": title, "year": year, "poster_url": poster_url,
                     "status": status, "detail_json": detail_json})
        return True

    def fetch_detail(tid):
        detail_calls.append(tid)
        return (detail or {}).get(tid)

    deps = _Deps()
    res = auto_video_scan_watchlist_people(
        {"_automation_id": "a1"}, deps,
        fetch_people=lambda: people,
        fetch_credits=lambda pid: credits_by_person.get(pid, []),
        fetch_detail=fetch_detail,
        owned_ids=lambda: set(owned or set()),
        ignored_ids=lambda: list(ignored or []),
        wishlisted_status=lambda: dict(wished or {}),
        add_movie=add_movie,
        today_fn=lambda: today)
    return res, adds, detail_calls, deps


def test_first_run_backlogs_released_and_upcoming():
    people = [{"tmdb_id": 500, "title": "Famous Actor"}]
    credits = {500: [
        _credit(3, "Old Hit", pop=50, date="1990-01-01"),                 # released → wanted
        _credit(4, "Future Film", pop=40, date="2099-01-01"),             # upcoming → monitored
        _credit(1, "Owned Movie", pop=80, date="2000-01-01"),             # owned → skipped
        _credit(7, "A Show", kind="show", pop=90),                        # show → skipped
    ]}
    detail = {3: _full_detail(), 4: {"kind": "movie", "title": "Future Film",
                                     "poster_url": "https://img/f.jpg", "cast": [], "crew": []}}
    res, adds, detail_calls, _ = _handler(people, credits, owned={1}, detail=detail)

    assert res["status"] == "completed"
    assert res["people"] == 1
    assert res["movies_added"] == 1 and res["upcoming"] == 1
    by_id = {a["tmdb_id"]: a for a in adds}
    assert set(by_id) == {3, 4}
    # released one is 'wanted' and carries the RICH blob (best-in-class data at add time)
    assert by_id[3]["status"] == "wanted"
    assert by_id[3]["detail_json"]["backdrop_url"] == "https://img/b.jpg"
    assert by_id[3]["detail_json"]["added_via"]["person_name"] == "Famous Actor"
    # upcoming one is 'monitored' so the (future) wishlist engine leaves it alone
    assert by_id[4]["status"] == "monitored"
    assert sorted(detail_calls) == [3, 4]              # detail fetched for each new gap


def test_owned_movies_are_never_wishlisted():
    people = [{"tmdb_id": 1, "title": "P"}]
    credits = {1: [_credit(10, "Have It", date="1990-01-01")]}
    res, adds, _, _ = _handler(people, credits, owned={10})
    assert adds == [] and res["movies_added"] == 0


def test_ignored_movies_are_skipped():
    people = [{"tmdb_id": 1, "title": "P"}]
    credits = {1: [_credit(10, "Not Interested", date="1990-01-01")]}
    res, adds, _, _ = _handler(people, credits, ignored=[10])
    assert adds == [] and res["movies_added"] == 0


# ── handler: fast re-runs (skip / promote) ────────────────────────────────────
def test_rerun_skips_already_wishlisted_without_refetch():
    people = [{"tmdb_id": 1, "title": "P"}]
    credits = {1: [_credit(10, "Already Wished", date="1990-01-01")]}
    res, adds, detail_calls, _ = _handler(people, credits, wished={10: "wanted"})
    assert adds == []                                  # no re-add
    assert detail_calls == []                          # and no wasted detail fetch
    assert res["movies_added"] == 0


def test_rerun_promotes_monitored_now_that_it_released():
    people = [{"tmdb_id": 1, "title": "P"}]
    credits = {1: [_credit(10, "Finally Out", date="2020-01-01")]}    # now in the past
    res, adds, detail_calls, _ = _handler(people, credits, wished={10: "monitored"})
    assert len(adds) == 1
    assert adds[0]["status"] == "wanted"               # promoted
    assert adds[0]["detail_json"] is None              # don't clobber the stored rich blob
    assert detail_calls == []                          # promotion needs no detail fetch
    assert res["promoted"] == 1 and res["movies_added"] == 0


def test_rerun_leaves_still_upcoming_monitored_alone():
    people = [{"tmdb_id": 1, "title": "P"}]
    credits = {1: [_credit(10, "Still Coming", date="2099-01-01")]}
    res, adds, _, _ = _handler(people, credits, wished={10: "monitored"})
    assert adds == [] and res["promoted"] == 0


def test_engine_advanced_status_is_never_downgraded():
    # a movie already 'downloading' must not be touched by the scan
    people = [{"tmdb_id": 1, "title": "P"}]
    credits = {1: [_credit(10, "In Flight", date="1990-01-01")]}
    res, adds, _, _ = _handler(people, credits, wished={10: "downloading"})
    assert adds == []


# ── handler: cross-person dedup + resilience ──────────────────────────────────
def test_movie_shared_by_two_people_is_added_once():
    people = [{"tmdb_id": 1, "title": "Actor A"}, {"tmdb_id": 2, "title": "Actor B"}]
    shared = _credit(10, "Co-Stars", date="1990-01-01")
    credits = {1: [shared], 2: [dict(shared)]}
    res, adds, detail_calls, _ = _handler(people, credits, detail={10: _full_detail()})
    assert len(adds) == 1                              # second person sees it already handled
    assert detail_calls == [10] and res["movies_added"] == 1


def test_one_persons_fetch_error_does_not_abort_the_scan():
    people = [{"tmdb_id": 1, "title": "Breaks"}, {"tmdb_id": 2, "title": "Works"}]
    credits = {2: [_credit(10, "Good", date="1990-01-01")]}

    def fetch_credits(pid):
        if pid == 1:
            raise RuntimeError("tmdb down")
        return credits.get(pid, [])

    adds = []
    res = auto_video_scan_watchlist_people(
        {"_automation_id": "a"}, _Deps(),
        fetch_people=lambda: people, fetch_credits=fetch_credits,
        fetch_detail=lambda t: _full_detail(),
        owned_ids=lambda: set(), ignored_ids=lambda: [], wishlisted_status=lambda: {},
        add_movie=lambda *a, **k: adds.append(k) or True, today_fn=lambda: "2026-06-25")
    assert res["status"] == "completed"
    assert res["movies_added"] == 1                    # person 2 still processed


def test_empty_watchlist_is_a_clean_noop():
    res, adds, _, _ = _handler([], {})
    assert res["status"] == "completed" and res["people"] == 0 and adds == []


def test_top_level_error_is_caught_and_reported():
    def boom():
        raise RuntimeError("watchlist read failed")
    deps = _Deps()
    res = auto_video_scan_watchlist_people({"_automation_id": "a"}, deps, fetch_people=boom)
    assert res["status"] == "error" and "watchlist read failed" in res["error"]
    assert any(p.get("status") == "error" for p in deps.progress)


# ── DB seam: status + detail_json upsert semantics ────────────────────────────
from database.video_database import VideoDatabase  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _row(db, tmdb_id):
    with db._get_connection() as conn:
        r = conn.execute("SELECT status, detail_json FROM video_wishlist "
                         "WHERE tmdb_id=? AND kind='movie'", (tmdb_id,)).fetchone()
    return (r["status"], r["detail_json"]) if r else (None, None)


def test_add_movie_stores_status_and_detail_json(db):
    blob = {"overview": "x", "added_via": {"person_name": "P"}}
    assert db.add_movie_to_wishlist(10, "M", year="1990", status="wanted", detail_json=blob)
    status, dj = _row(db, 10)
    assert status == "wanted"
    assert json.loads(dj)["added_via"]["person_name"] == "P"     # dict was serialized
    assert db.wishlisted_movie_status() == {10: "wanted"}


def test_default_status_is_wanted_for_back_compat(db):
    # existing callers that don't pass status keep getting 'wanted'
    db.add_movie_to_wishlist(11, "Legacy")
    assert _row(db, 11)[0] == "wanted"


def test_monitored_is_promoted_to_wanted_but_never_downgraded(db):
    db.add_movie_to_wishlist(12, "Upcoming", status="monitored")
    assert _row(db, 12)[0] == "monitored"
    # re-add as wanted (it released) → promoted
    db.add_movie_to_wishlist(12, "Upcoming", status="wanted")
    assert _row(db, 12)[0] == "wanted"
    # re-add as monitored again must NOT knock it back
    db.add_movie_to_wishlist(12, "Upcoming", status="monitored")
    assert _row(db, 12)[0] == "wanted"


def test_engine_status_survives_a_rescan(db):
    db.add_movie_to_wishlist(13, "Grabbing", status="wanted")
    with db._get_connection() as conn:
        conn.execute("UPDATE video_wishlist SET status='downloading' WHERE tmdb_id=13")
        conn.commit()
    # a later scan re-adds it as 'wanted' — must not undo the engine's progress
    db.add_movie_to_wishlist(13, "Grabbing", status="wanted")
    assert _row(db, 13)[0] == "downloading"


def test_detail_json_is_filled_not_wiped_on_readd(db):
    blob = {"overview": "rich"}
    db.add_movie_to_wishlist(14, "M", status="monitored", detail_json=blob)
    # promotion re-add passes no detail_json — the stored blob must remain
    db.add_movie_to_wishlist(14, "M", status="wanted", detail_json=None)
    status, dj = _row(db, 14)
    assert status == "wanted"
    assert json.loads(dj)["overview"] == "rich"
