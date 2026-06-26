"""Soulseek auto-grab for the movie + episode wishlist: search → pick best accepted release
→ enqueue (the monitor finishes it). Pure pick/select/record + the handler with the slow
slskd search + enqueue injected, plus the DB queries that feed it.
"""

from __future__ import annotations

import json

import pytest

from core.automation.handlers.video_process_wishlist import (
    active_download_keys,
    auto_video_process_wishlist,
    build_download_record,
    item_key,
    pick_best,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _cand(fn, *, accepted=True, score=10, user="u"):
    return {"filename": fn, "title": fn, "username": user, "size_bytes": 1000,
            "quality_label": "WEBDL-1080p", "accepted": accepted, "score": score}


# ── pure ──────────────────────────────────────────────────────────────────────
def test_pick_best_takes_first_accepted():
    cands = [_cand("a", accepted=False), _cand("b", accepted=True), _cand("c", accepted=True)]
    assert pick_best(cands)["filename"] == "b"


def test_pick_best_none_when_all_rejected():
    assert pick_best([_cand("a", accepted=False), _cand("b", accepted=False)]) is None
    assert pick_best([]) is None


def test_item_key_movie_and_episode():
    assert item_key({"tmdb_id": 5}, "movie") == ("movie", "5")
    assert item_key({"show_tmdb_id": 9, "season_number": 1, "episode_number": 3}, "episode") \
        == ("episode", "9", 1, 3)


def test_active_download_keys_reads_ctx_for_episodes():
    active = [
        {"kind": "movie", "media_id": "5"},
        {"kind": "episode", "media_id": "9", "search_ctx": json.dumps({"season": 1, "episode": 3})},
    ]
    keys = active_download_keys(active)
    assert ("movie", "5") in keys and ("episode", "9", 1, 3) in keys


def test_build_record_movie_shape():
    item = {"tmdb_id": 5, "title": "The Matrix", "year": "1999", "poster_url": "/p.jpg"}
    best = _cand("Matrix.1999.1080p.mkv")
    rest = [best, _cand("other.mkv")]
    rec = build_download_record(item, best, rest, media_type="movie", target_dir="/movies", query="matrix 1999")
    assert rec["kind"] == "movie" and rec["title"] == "The Matrix" and rec["media_id"] == "5"
    assert rec["source"] == "soulseek" and rec["status"] == "downloading"
    assert rec["target_dir"] == "/movies" and rec["filename"] == "Matrix.1999.1080p.mkv"
    assert json.loads(rec["search_ctx"]) == {"scope": "movie", "title": "The Matrix", "year": "1999"}
    assert [c["filename"] for c in json.loads(rec["candidates"])] == ["other.mkv"]   # best excluded


def test_build_record_stashes_peer_availability_in_ctx():
    # the chosen source's free-slot/queue/speed snapshot rides in search_ctx for the drawer
    item = {"tmdb_id": 5, "title": "M", "year": "1999"}
    best = dict(_cand("M.1999.mkv"), slots=1, queue=0, speed=2100000, availability=0.15)
    rec = build_download_record(item, best, [best], media_type="movie", target_dir="/m", query="q")
    assert json.loads(rec["search_ctx"])["peer"] == {
        "slots": 1, "queue": 0, "speed": 2100000, "availability": 0.15}


def test_build_record_episode_shape():
    item = {"show_tmdb_id": 9, "show_title": "Breaking Bad", "season_number": 1,
            "episode_number": 3, "air_date": "2008-02-10"}
    best = _cand("BrBa.S01E03.mkv")
    rec = build_download_record(item, best, [best], media_type="episode", target_dir="/tv", query="q")
    assert rec["kind"] == "episode" and rec["media_id"] == "9" and rec["year"] == "2008"
    assert json.loads(rec["search_ctx"]) == {"scope": "episode", "title": "Breaking Bad",
                                             "season": 1, "episode": 3, "year": "2008"}


# ── handler ───────────────────────────────────────────────────────────────────
def _run(items, *, active=None, root="/movies", media_type="movie", searches=None):
    enq = []
    seen = []

    def search(item, mt):
        seen.append(item_key(item, mt))
        if searches is None:
            return [_cand("rel-" + str(item.get("tmdb_id") or item.get("show_tmdb_id")))]
        return searches.get(item_key(item, mt), [])

    def enqueue(item, best, cands, mt, target):
        enq.append((item_key(item, mt), best["filename"], target))
        return True

    deps = _Deps()
    res = auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 2}, deps, media_type=media_type,
        fetch_items=lambda mt: items, active_keys=lambda mt: set(active or set()),
        target_dir=lambda mt: root, search=search, enqueue=enqueue)
    return res, enq, seen, deps


def test_grabs_each_wished_movie():
    items = [{"tmdb_id": 1, "title": "A", "year": "2020"}, {"tmdb_id": 2, "title": "B", "year": "2021"}]
    res, enq, seen, _ = _run(items)
    assert res["status"] == "completed" and res["grabbed"] == 2 and res["searched"] == 2
    assert {e[0] for e in enq} == {("movie", "1"), ("movie", "2")}
    assert enq[0][2] == "/movies"


def test_skips_items_already_downloading():
    items = [{"tmdb_id": 1, "title": "A"}, {"tmdb_id": 2, "title": "B"}]
    res, enq, seen, _ = _run(items, active={("movie", "1")})
    assert {e[0] for e in enq} == {("movie", "2")}            # 1 already in flight
    assert ("movie", "1") not in seen                          # not even searched


def test_no_acceptable_release_grabs_nothing():
    items = [{"tmdb_id": 1, "title": "A"}]
    res, enq, _, _ = _run(items, searches={("movie", "1"): [_cand("junk", accepted=False)]})
    assert res["searched"] == 1 and res["grabbed"] == 0 and enq == []


def test_breakdown_distinguishes_no_results_from_quality_rejection():
    # the diagnostic: "the source had nothing" vs "had hits but quality rejected them"
    items = [{"tmdb_id": 1, "title": "A"}, {"tmdb_id": 2, "title": "B"}]
    res, enq, _, deps = _run(items, searches={
        ("movie", "1"): [],                                                  # source had nothing
        ("movie", "2"): [dict(_cand("junk", accepted=False), rejected="Unknown / unsupported quality")],
    })
    assert res["grabbed"] == 0 and res["noresults"] == 1 and res["rejected"] == 1
    logs = " ".join(p.get("log_line") or "" for p in deps.progress)
    assert "No search results for 'A'" in logs
    assert "none accepted — Unknown / unsupported quality" in logs   # reason surfaced


def test_breakdown_flags_searches_that_didnt_run():
    # a search that never ran (slskd didn't accept it) is NOT a genuine "no results"
    items = [{"tmdb_id": 1, "title": "A"}]
    res, enq, _, deps = _run(items, searches={("movie", "1"): None})
    assert res["grabbed"] == 0 and res["notrun"] == 1 and res["noresults"] == 0
    logs = " ".join(p.get("log_line") or "" for p in deps.progress)
    assert "Search didn't run for 'A'" in logs and "slskd" in logs


def test_missing_library_folder_is_a_quiet_skip():
    res, enq, _, deps = _run([{"tmdb_id": 1, "title": "A"}], root="")
    assert res["status"] == "completed" and res.get("skipped") == "no_folder"
    assert enq == [] and not any(p.get("status") == "error" for p in deps.progress)


def test_nothing_wished_is_a_clean_noop():
    res, enq, _, _ = _run([])
    assert res["status"] == "completed" and res["grabbed"] == 0 and enq == []


def test_episode_mode_keys_and_grabs():
    items = [{"show_tmdb_id": 9, "show_title": "BrBa", "season_number": 1, "episode_number": 3}]
    res, enq, seen, _ = _run(items, root="/tv", media_type="episode")
    assert res["grabbed"] == 1 and enq[0][0] == ("episode", "9", 1, 3) and enq[0][2] == "/tv"


def test_top_level_error_is_caught_and_clears_guard():
    from core.automation.handlers.video_process_wishlist import is_running

    def boom(mt):
        raise RuntimeError("db down")
    res = auto_video_process_wishlist({"_automation_id": "x"}, _Deps(), media_type="movie",
                                      target_dir=lambda mt: "/m", fetch_items=boom)
    assert res["status"] == "error" and "db down" in res["error"]
    assert is_running("movie") is False                        # guard released even on error


# ── DB queries ────────────────────────────────────────────────────────────────
from database.video_database import VideoDatabase  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_movie_wishlist_to_download_only_wanted(db):
    db.add_movie_to_wishlist(1, "Released", year="2020", status="wanted")
    db.add_movie_to_wishlist(2, "Upcoming", year="2027", status="monitored")
    rows = db.movie_wishlist_to_download()
    assert [r["tmdb_id"] for r in rows] == [1]                 # monitored is skipped


def test_episode_wishlist_to_download_shape(db):
    db.add_episodes_to_wishlist(9, "Breaking Bad", [
        {"season_number": 1, "episode_number": 1, "air_date": "2008-01-20"},
        {"season_number": 1, "episode_number": 2, "air_date": "2008-01-27"}])
    rows = db.episode_wishlist_to_download()
    assert len(rows) == 2
    top = rows[0]
    assert top["show_tmdb_id"] == 9 and top["show_title"] == "Breaking Bad"
    assert top["season_number"] == 1 and top["episode_number"] == 2   # newest air date first
