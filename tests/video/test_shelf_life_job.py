"""Shelf Life job (core/video/repair/shelf_life.py): gathers each owned title's
facts from the library, scores movies and shows with core/video/shelf_life.py,
stores every result, and flags the candidates. Approving deletes the whole
title through Delete Title, and only with the title typed."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase

GB = 1024 ** 3


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def worker(db):
    return VideoRepairWorker(db)


@pytest.fixture(autouse=True)
def _profiles(monkeypatch):
    """one admin profile and no linked accounts unless a test links some; the
    real lookups read the shared MUSIC database, which a test run shares."""
    from core import media_accounts
    monkeypatch.setattr(media_accounts, "plex_linked_profiles", lambda: [])
    monkeypatch.setattr(media_accounts, "profile_names", lambda: ({1: "Owner"}, "Owner"))
    monkeypatch.setattr(media_accounts, "admin_profile_ids", lambda: [1])


def _ago(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _sql(db, sql, args=()):
    conn = db._get_connection()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def _movie(db, title, *, tmdb, added=1000, plays=0, last=None, offset=0, size=60 * GB,
           runtime=120, imdb=None, streaming=None, count=None):
    mid = db.upsert_movie("plex", {"server_id": "m-" + title, "title": title, "year": 2015,
                                   "tmdb_id": tmdb, "play_count": plays, "last_viewed_at": last})
    _sql(db, "INSERT INTO media_files (movie_id, relative_path, size_bytes) VALUES (?, ?, ?)",
         (mid, "/m/%s.mkv" % title, size))
    _sql(db, "UPDATE movies SET has_file=1, added_at=?, runtime_minutes=?, view_offset_ms=?, "
             "imdb_rating=?, streaming_status=?, streaming_count=? WHERE id=?",
         (_ago(added), runtime, offset, imdb, streaming, count, mid))
    return mid


def _show(db, title, *, tmdb, owned=4, watched=0, status="Ended", added=1000, size=8 * GB,
          streaming=None, count=None, last=10):
    eps = [{"episode_number": i, "title": "E%d" % i, "server_id": "%s-e%d" % (title, i),
            "air_date": "2020-01-%02d" % i, "file": {"path": "/s/%s/e%d.mkv" % (title, i)}}
           for i in range(1, owned + 1)]
    sid = db.upsert_show_tree("plex", {"server_id": "s-" + title, "title": title, "tmdb_id": tmdb,
                                       "seasons": [{"season_number": 1, "episodes": eps}]})
    _sql(db, "UPDATE shows SET status=?, added_at=?, runtime_minutes=45, streaming_status=?, streaming_count=? "
             "WHERE id=?", (status, _ago(added), streaming, count, sid))
    _sql(db, "UPDATE episodes SET has_file=1, added_at=? WHERE show_id=?", (_ago(added), sid))
    _sql(db, "UPDATE media_files SET size_bytes=? WHERE episode_id IN (SELECT id FROM episodes WHERE show_id=?)",
         (size, sid))
    if watched:
        _sql(db, "UPDATE episodes SET play_count=1, last_viewed_at=? WHERE show_id=? AND episode_number<=?",
             (_ago(last), sid, watched))
    return sid


def _run(worker):
    worker._run_job("shelf_life", forced=True)


def _pending(db):
    return [f for f in db.repair_get_findings(status="pending", limit=200)["items"]
            if f["finding_type"] == "shelf_life"]


def _stored(db, kind, ids):
    return db.shelf_life_get(kind, ids)


# ── registration ─────────────────────────────────────────────────────────────
def test_job_is_registered_and_off_by_default():
    from core.video.repair import get_all_jobs
    from core.video.repair.catalog import (VIDEO_ACTION_LABELS, VIDEO_FINDING_TYPE_META,
                                           VIDEO_JOBS_WRITING_FILES, is_safe_type, job_category)
    cls = get_all_jobs()["shelf_life"]
    assert cls.default_enabled is False and cls.default_interval_hours == 24
    assert cls.default_settings["preset"] == "balanced" and cls.default_settings["threshold"] == 30
    meta = VIDEO_FINDING_TYPE_META["shelf_life"]
    assert meta["label"] == "Shelf Life" and meta["verb"] == "Delete Title"
    assert meta["destructive"] is True and is_safe_type("shelf_life") is False
    assert "shelf_life" in VIDEO_JOBS_WRITING_FILES and job_category("shelf_life") == "Files & storage"
    assert VIDEO_ACTION_LABELS["deleted"] == "Deleted"
    from core.automation.blocks import blocks_for_scope
    block = next(b for b in blocks_for_scope("video")["actions"] if b["type"] == "video_run_repair_job")
    assert "shelf_life" in [o["value"] for o in block["config_fields"][0]["options"]]


def test_the_help_explains_every_setting_and_preset():
    from core.video.repair.shelf_life import ShelfLifeJob
    from core.video.shelf_life import PRESETS

    help_text = ShelfLifeJob.help_text
    settings = help_text.split("Settings:\n", 1)[1].splitlines()
    labels = [line[2:].split(":", 1)[0] for line in settings]
    pretty = [" ".join(word.capitalize() for word in key.split("_")) for key in ShelfLifeJob.default_settings]
    assert sorted(labels) == sorted(pretty)
    preset_line = next(line for line in settings if line.startswith("- Preset:"))
    for name in PRESETS:
        assert " ".join(word.capitalize() for word in name.split("_")) + ":" in preset_line


# ── scoring, storing, flagging ───────────────────────────────────────────────
def test_dead_weight_is_flagged_and_everything_is_stored(db, worker):
    dead = _movie(db, "Dead", tmdb=1, imdb=5.0, streaming="ok", count=5)
    loved = _movie(db, "Loved", tmdb=2, plays=6, last=_ago(5), size=8 * GB, imdb=8.4)
    _run(worker)
    items = _pending(db)
    assert [f["details"]["title"] for f in items] == ["Dead"]
    f = items[0]
    assert f["entity_type"] == "movie" and f["entity_id"] == "m:%d" % dead
    assert "Shelf Life" in f["title"] and "60.0 GB" in f["title"]
    assert f["details"]["kind"] == "movie" and f["details"]["item_id"] == dead
    store = _stored(db, "movie", [dead, loved])
    assert store[dead]["score"] < 30 and store[loved]["score"] >= 30
    assert store[dead]["reasons"] and store[dead]["scored_at"]


def test_shows_are_scored_on_their_episodes(db, worker):
    unwatched = _show(db, "Nobody", tmdb=10, streaming="ok", count=5)
    # watched a while back: scored, not held as being watched
    seen = _show(db, "Somebody", tmdb=11, watched=4, size=1 * GB, last=120)
    _run(worker)
    store = _stored(db, "show", [unwatched, seen])
    assert store[unwatched]["score"] < store[seen]["score"]
    assert store[seen]["signals"]["watch"] > 40
    assert any(f["details"]["item_id"] == unwatched for f in _pending(db))


def test_a_show_with_an_episode_watched_lately_is_never_flagged(db, worker):
    # one episode of four, ten days ago: someone is working through it
    sid = _show(db, "Underway", tmdb=12, watched=1, streaming="ok", count=5, last=10)
    _run(worker)
    stored = _stored(db, "show", [sid])[sid]
    assert stored["gate"] == "watching" and stored["score"] is None
    assert _pending(db) == []
    # past Active Days it no longer holds
    _sql(db, "UPDATE episodes SET last_viewed_at=? WHERE show_id=?", (_ago(120), sid))
    _run(worker)
    assert _stored(db, "show", [sid])[sid]["gate"] is None


@pytest.mark.parametrize("setup,gate", [
    (lambda db, mid: _sql(db, "UPDATE movies SET view_offset_ms=5000 WHERE id=?", (mid,)), "in_progress"),
    (lambda db, mid: db.shelf_life_set_keep("movie", 1, True, set_by=1), "keep"),
    (lambda db, mid: _sql(db, "UPDATE movies SET added_at=? WHERE id=?", (_ago(3), mid)), "new_arrival"),
])
def test_gated_movies_are_stored_with_their_gate_and_never_flagged(db, worker, setup, gate):
    mid = _movie(db, "Gated", tmdb=1, imdb=5.0, streaming="ok", count=5)
    setup(db, mid)
    _run(worker)
    assert _pending(db) == []
    s = _stored(db, "movie", [mid])[mid]
    assert s["gate"] == gate and s["score"] is None


def test_an_unwatched_request_is_protected_until_its_grace_runs_out(db, worker):
    mid = _movie(db, "Asked", tmdb=1, imdb=5.0, streaming="ok", count=5)
    _sql(db, "INSERT INTO video_requests (profile_id, kind, tmdb_id, title, status, available_at) "
             "VALUES (1, 'movie', 1, 'Asked', 'approved', ?)", (_ago(20),))
    _run(worker)
    assert _stored(db, "movie", [mid])[mid]["gate"] == "requested"
    _sql(db, "UPDATE video_requests SET available_at=?", (_ago(400),))
    _run(worker)
    s = _stored(db, "movie", [mid])[mid]
    assert s["gate"] is None and s["signals"]["requests"] < 35


def test_a_followed_show_still_airing_is_protected(db, worker):
    sid = _show(db, "Airing", tmdb=20, status="Returning Series")
    _run(worker)
    assert _stored(db, "show", [sid])[sid]["gate"] == "following"
    # muted by every profile, it is no longer followed
    _sql(db, "INSERT INTO video_watchlist (profile_id, kind, tmdb_id, title, state) "
             "VALUES (1, 'show', 20, 'Airing', 'mute')")
    _run(worker)
    assert _stored(db, "show", [sid])[sid]["gate"] is None


def test_the_server_add_date_wins_over_soulsync_first_seeing_it(db, worker):
    # a fresh install sees every film "this week"; the server knows better
    mid = _movie(db, "Old", tmdb=1, added=3, imdb=5.0, streaming="ok", count=5)
    _sql(db, "UPDATE movies SET release_date=? WHERE id=?", (_ago(3000)[:10], mid))
    _run(worker)
    assert _stored(db, "movie", [mid])[mid]["gate"] == "new_arrival"
    _sql(db, "UPDATE movies SET server_added_at=? WHERE id=?", (_ago(1000), mid))
    _run(worker)
    s = _stored(db, "movie", [mid])[mid]
    assert s["gate"] is None and s["score"] < 30


def test_a_server_date_on_the_release_date_is_the_files_date_not_the_add_date(db, worker):
    # an *arr that stamps files with the release date makes Plex's addedAt the
    # release date: a film grabbed 3 days ago reads as 40 years in the library
    mid = _movie(db, "Highlander", tmdb=1, added=3, imdb=5.0, streaming="ok", count=5)
    _sql(db, "UPDATE movies SET release_date='1986-03-07', server_added_at='1986-03-06 16:00:00' WHERE id=?",
         (mid,))
    _run(worker)
    s = _stored(db, "movie", [mid])[mid]
    assert s["gate"] == "new_arrival"          # it arrived 3 days ago as far as anyone can tell
    assert _pending(db) == []


def test_the_trust_window_is_a_setting(db, worker):
    mid = _movie(db, "Late", tmdb=1, added=3, imdb=5.0, streaming="ok", count=5)
    # server date 120 days after release: real at 90, a file date at 180
    _sql(db, "UPDATE movies SET release_date=?, server_added_at=? WHERE id=?",
         (_ago(1120)[:10], _ago(1000), mid))
    rows = {r["id"]: r for r in db.shelf_life_movies(90)}
    assert rows[mid]["added_from"] == "server"
    rows = {r["id"]: r for r in db.shelf_life_movies(180)}
    assert rows[mid]["added_from"] == "first_seen"
    worker.set_job_config("shelf_life", settings={"trust_add_date_after_days": 180})
    _run(worker)
    assert _stored(db, "movie", [mid])[mid]["gate"] == "new_arrival"


def test_a_movie_with_no_release_date_counts_from_first_seen(db):
    mid = _movie(db, "Unknown", tmdb=1, added=40)
    _sql(db, "UPDATE movies SET server_added_at=? WHERE id=?", (_ago(5000), mid))
    row = {r["id"]: r for r in db.shelf_life_movies()}[mid]
    assert row["added_from"] == "first_seen" and row["added_at"][:10] == _ago(40)[:10]


def test_a_show_whose_episodes_carry_their_air_dates_counts_from_first_seen(db):
    sid = _show(db, "Stamped", tmdb=30)
    # every episode's server date is its own air date: the file's date
    _sql(db, "UPDATE episodes SET added_at=air_date || ' 00:00:00' WHERE show_id=?", (sid,))
    _sql(db, "UPDATE shows SET added_at=? WHERE id=?", (_ago(2), sid))
    row = {r["id"]: r for r in db.shelf_life_shows()}[sid]
    assert row["added_from"] == "first_seen" and row["added_at"][:10] == _ago(2)[:10]
    # one episode added long after it aired: the show is as old as that one
    _sql(db, "UPDATE episodes SET added_at=? WHERE show_id=? AND episode_number=3", (_ago(1000), sid))
    row = {r["id"]: r for r in db.shelf_life_shows()}[sid]
    assert row["added_from"] == "server" and row["added_at"][:10] == _ago(1000)[:10]


def test_a_show_is_as_old_as_its_first_episode_on_the_server(db, worker):
    sid = _show(db, "Old show", tmdb=30, streaming="ok", count=5)
    _sql(db, "UPDATE shows SET added_at=? WHERE id=?", (_ago(2), sid))    # SoulSync saw it 2 days ago
    _run(worker)
    assert _stored(db, "show", [sid])[sid]["gate"] is None


def test_a_jellyfin_library_scores_the_same_way(db, worker):
    # Jellyfin as the only server: no linked Plex accounts, so the owner is the
    # one viewer, and the facts come from the same columns a Plex scan fills
    from core.video.sources import JellyfinVideoSource
    jf = object.__new__(JellyfinVideoSource)
    item = jf._movie({"Id": "jf-1", "Name": "Dead", "ProductionYear": 2015, "CommunityRating": 5.0,
                      "RunTimeTicks": 120 * 600_000_000, "UserData": {"PlayCount": 0},
                      "DateCreated": "2023-01-01T00:00:00.0000000Z", "ProviderIds": {"Tmdb": "1"},
                      "MediaSources": [{"Path": "/m/Dead.mkv", "Size": 60 * GB}]})
    mid = db.upsert_movie("jellyfin", item)
    loved = jf._movie({"Id": "jf-2", "Name": "Loved", "ProductionYear": 2015, "CommunityRating": 8.4,
                       "RunTimeTicks": 120 * 600_000_000,
                       "UserData": {"PlayCount": 6, "LastPlayedDate": _ago(5).replace(" ", "T") + ".0Z"},
                       "DateCreated": "2023-01-01T00:00:00.0000000Z", "ProviderIds": {"Tmdb": "2"},
                       "MediaSources": [{"Path": "/m/Loved.mkv", "Size": 8 * GB}]})
    lid = db.upsert_movie("jellyfin", loved)
    _sql(db, "UPDATE movies SET streaming_status='ok', streaming_count=5 WHERE id=?", (mid,))
    _sql(db, "UPDATE movies SET release_date='2015-06-01'")    # TMDB enrichment's release date
    _run(worker)
    store = _stored(db, "movie", [mid, lid])
    assert store[mid]["gate"] is None and store[mid]["score"] < 30       # flagged
    assert store[lid]["score"] > 60 and store[lid]["signals"]["watch"] > 70
    assert store[mid]["signals"]["size"] is not None                     # Jellyfin's file size and runtime
    assert [f["details"]["title"] for f in _pending(db)] == ["Dead"]


def test_watched_cleanup_keeps_its_movies(db, worker):
    mid = _movie(db, "Both", tmdb=1, imdb=5.0, streaming="ok", count=5)
    db.repair_create_finding("watched_cleanup", "watched_cleanup", title="Both", entity_type="movie",
                             entity_id="m:%d" % mid, details={"movie_id": mid})
    _run(worker)
    assert _pending(db) == []
    assert _stored(db, "movie", [mid])[mid]["score"] < 30      # still scored, just not flagged twice


def test_failed_grabs_count_toward_hard_to_replace(db, worker):
    hard = _movie(db, "Hard", tmdb=1)
    easy = _movie(db, "Easy", tmdb=2)
    never = _movie(db, "Never", tmdb=3)
    for outcome in ("failed", "import_failed", "failed", "completed"):
        _sql(db, "INSERT INTO video_download_history (kind, title, media_id, media_source, outcome) "
                 "VALUES ('movie', 'Hard', ?, 'library', ?)", (str(hard), outcome))
    _sql(db, "INSERT INTO video_download_history (kind, title, media_id, media_source, outcome) "
             "VALUES ('movie', 'Easy', '2', 'tmdb', 'completed')")
    _run(worker)
    store = _stored(db, "movie", [hard, easy, never])
    assert store[hard]["signals"]["replaceability"] == pytest.approx(60.0)   # 3 failed grabs
    assert store[easy]["signals"]["replaceability"] == 0                    # first try
    assert store[never]["signals"]["replaceability"] is None                # never grabbed here


def test_a_title_no_longer_a_candidate_leaves_the_list(db, worker):
    mid = _movie(db, "Later", tmdb=1, imdb=5.0, streaming="ok", count=5)
    _run(worker)
    assert len(_pending(db)) == 1
    db.shelf_life_set_keep("movie", 1, True, set_by=1)
    _run(worker)
    assert _pending(db) == []


def test_linked_member_watching_counts(db, worker, monkeypatch):
    from core import media_accounts
    monkeypatch.setattr(media_accounts, "plex_linked_profiles", lambda: [(4, "tok")])
    monkeypatch.setattr(media_accounts, "profile_names", lambda: ({1: "Owner", 4: "Z"}, "Owner"))
    mid = _movie(db, "Shared", tmdb=1, plays=1, last=_ago(10), imdb=7.0)
    # Z watched it too, and both have watched something lately
    db.save_profile_watch_state(4, [("movie", mid, 2, _ago(8), 0)])
    _run(worker)
    w = _stored(db, "movie", [mid])[mid]
    assert w["signals"]["watch"] > 70
    # a member who watched only half of it holds it as in progress
    db.save_profile_watch_state(4, [("movie", mid, 0, _ago(2), 90000)])
    _run(worker)
    assert _stored(db, "movie", [mid])[mid]["gate"] == "in_progress"


# ── approving ────────────────────────────────────────────────────────────────
def test_fix_refuses_without_the_title_typed(db, worker, monkeypatch):
    from core.video import delete_title
    calls = []
    monkeypatch.setattr(delete_title, "run", lambda *a, **k: calls.append((a, k)) or {"success": True})
    _movie(db, "Dead", tmdb=1, imdb=5.0, streaming="ok", count=5)
    _run(worker)
    fid = _pending(db)[0]["id"]
    res = worker.fix_finding(fid)
    assert res["success"] is False and "Type the title" in res["error"]
    assert calls == []
    assert db.repair_get_finding(fid)["status"] == "pending"


def test_fix_runs_delete_title_with_the_typed_title(db, worker, monkeypatch):
    from core.video import delete_title
    calls = []

    def _run_delete(db_, kind, item_id, **kw):
        calls.append((kind, item_id, kw["typed_title"]))
        return {"success": True}
    monkeypatch.setattr(delete_title, "run", _run_delete)
    mid = _movie(db, "Dead", tmdb=1, imdb=5.0, streaming="ok", count=5)
    _run(worker)
    fid = _pending(db)[0]["id"]
    res = worker.fix_finding(fid, "Dead")
    assert res["success"] is True and calls == [("movie", mid, "Dead")]
    f = db.repair_get_finding(fid)
    assert f["status"] == "resolved"


def test_a_wrong_typed_title_deletes_nothing(db, worker, tmp_path):
    # the real Delete Title: a typed title that doesn't match refuses
    _movie(db, "Dead", tmdb=1, imdb=5.0, streaming="ok", count=5)
    _run(worker)
    fid = _pending(db)[0]["id"]
    res = worker.fix_finding(fid, "Not It")
    assert res["success"] is False and "doesn't match" in res["error"]
    assert db.repair_get_finding(fid)["status"] == "pending"


def test_fix_all_cannot_delete_anything(db, worker, monkeypatch):
    from core.video import delete_title
    calls = []
    monkeypatch.setattr(delete_title, "run", lambda *a, **k: calls.append(1) or {"success": True})
    _movie(db, "A", tmdb=1, imdb=5.0, streaming="ok", count=5)
    _movie(db, "B", tmdb=2, imdb=5.0, streaming="ok", count=5)
    _run(worker)
    out = worker.bulk_fix_findings(job_id="shelf_life")
    assert out["fixed"] == 0 and out["failed"] == 2 and calls == []


def test_a_finding_names_its_title_the_way_the_findings_page_reads_posters(db, worker):
    # the findings page puts the poster beside a row from details library_id + kind
    from api.video.repair import finding_poster
    mid = _movie(db, "Low", tmdb=7, imdb=5.0, streaming="ok", count=5)
    _run(worker)
    f = _pending(db)[0]
    assert f["details"]["library_id"] == mid
    assert finding_poster(f) == "/api/video/poster/movie/%d" % mid


# ── batch approve + the free-X-GB planner ────────────────────────────────────
def _flag(db, title, *, score, size_gb, item_id):
    """a pending Shelf Life finding; returns its id (create_finding returns a bool)"""
    assert db.repair_create_finding(
        "shelf_life", "shelf_life", title="%s: Shelf Life %d" % (title, score), entity_type="movie",
        entity_id="m:%d" % item_id,
        details={"kind": "movie", "item_id": item_id, "title": title, "score": score,
                 "size_bytes": size_gb * GB})
    items = db.repair_get_findings(job_id="shelf_life", limit=500)["items"]
    return next(f["id"] for f in items if f["entity_id"] == "m:%d" % item_id)


def test_batch_phrase():
    from core.video.repair.shelf_life import batch_phrase
    assert batch_phrase(1) == "delete 1 title" and batch_phrase(14) == "delete 14 titles"


def test_plan_picks_lowest_first_until_the_target(db):
    from core.video.repair.shelf_life import plan_cleanup
    a = _flag(db, "A", score=25, size_gb=40, item_id=1)
    b = _flag(db, "B", score=10, size_gb=10, item_id=2)
    c = _flag(db, "C", score=10, size_gb=30, item_id=3)    # same score, bigger: first
    _flag(db, "D", score=29, size_gb=50, item_id=4)
    plan = plan_cleanup(db, target_bytes=60 * GB)
    assert [x["id"] for x in plan["items"]] == [c, b, a]
    assert plan["total_bytes"] == 80 * GB and plan["short_bytes"] == 0
    assert plan["phrase"] == "delete 3 titles" and plan["candidates"] == 4
    assert plan["recycling"] is True and plan["keep_days"] == 7


def test_plan_says_how_short_and_never_reaches_past_the_candidates(db):
    from core.video.repair.shelf_life import plan_cleanup
    _flag(db, "A", score=25, size_gb=40, item_id=1)
    plan = plan_cleanup(db, target_bytes=500 * GB)
    assert plan["total_bytes"] == 40 * GB and plan["short_bytes"] == 460 * GB
    assert len(plan["items"]) == 1


def test_plan_of_chosen_ids_and_of_everything(db):
    from core.video.repair.shelf_life import plan_cleanup
    a = _flag(db, "A", score=25, size_gb=40, item_id=1)
    b = _flag(db, "B", score=10, size_gb=10, item_id=2)
    assert [x["id"] for x in plan_cleanup(db, ids=[a])["items"]] == [a]
    assert [x["id"] for x in plan_cleanup(db)["items"]] == [b, a]


def test_batch_needs_the_exact_phrase_and_open_findings(db):
    from core.video.repair.shelf_life import batch_delete_actions
    a = _flag(db, "A", score=25, size_gb=40, item_id=1)
    b = _flag(db, "B", score=10, size_gb=10, item_id=2)
    actions, err = batch_delete_actions(db, [a, b], "delete 2 titles")
    assert err is None and actions == {a: "A", b: "B"}
    for phrase in ("delete 1 title", "Delete 2 titles", "", None):
        actions, err = batch_delete_actions(db, [a, b], phrase)
        assert actions is None and "delete 2 titles" in err
    db.repair_bulk_update_findings([b], "dismissed")
    actions, err = batch_delete_actions(db, [a, b], "delete 2 titles")
    assert actions is None and "aren't open" in err
    assert batch_delete_actions(db, [], "delete 0 titles")[1] == "Nothing selected."


def test_a_watched_cleanup_finding_cannot_ride_a_shelf_life_batch(db):
    from core.video.repair.shelf_life import batch_delete_actions
    db.repair_create_finding("watched_cleanup", "watched_cleanup", title="W", entity_type="movie",
                             entity_id="m:9", details={"movie_id": 9, "title": "W"})
    wc = db.repair_get_findings(job_id="watched_cleanup", limit=5)["items"][0]["id"]
    actions, err = batch_delete_actions(db, [wc], "delete 1 title")
    assert actions is None and "aren't open Shelf Life" in err


@pytest.fixture()
def client(tmp_path):
    from flask import Flask
    import api.video as videoapi
    import core.video.repair.worker as worker_mod
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "api.db"))
    worker_mod._worker = None
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), videoapi._video_db, worker_mod
    finally:
        w = worker_mod._worker
        if w:
            w.stop()
        worker_mod._worker = None
        videoapi._video_db = None


def test_plan_route(client):
    c, db, _ = client
    _flag(db, "A", score=25, size_gb=40, item_id=1)
    r = c.post("/api/video/repair/shelf-life/plan", json={"target_gb": 10})
    assert r.status_code == 200
    body = r.get_json()
    assert body["target_bytes"] == 10 * GB and len(body["items"]) == 1
    assert c.post("/api/video/repair/shelf-life/plan", json={"target_gb": "lots"}).status_code == 400
    assert c.post("/api/video/repair/shelf-life/plan", json={"target_gb": 0}).status_code == 400
    assert c.post("/api/video/repair/shelf-life/plan", json={"ids": "1"}).status_code == 400


def test_delete_route_runs_each_title_with_its_own_name(client, monkeypatch):
    import time
    from core.video import delete_title
    c, db, worker_mod = client
    calls = []

    def _run_delete(db_, kind, item_id, **kw):
        calls.append((item_id, kw["typed_title"]))
        return {"success": True}
    monkeypatch.setattr(delete_title, "run", _run_delete)
    a = _flag(db, "A", score=25, size_gb=40, item_id=1)
    b = _flag(db, "B", score=10, size_gb=10, item_id=2)
    bad = c.post("/api/video/repair/shelf-life/delete", json={"ids": [a, b], "phrase": "delete 1 title"})
    assert bad.status_code == 400 and bad.get_json()["started"] is False and calls == []
    ok = c.post("/api/video/repair/shelf-life/delete", json={"ids": [a, b], "phrase": "delete 2 titles"})
    assert ok.status_code == 200 and ok.get_json()["started"] is True
    w = worker_mod.get_video_repair_worker()
    for _ in range(100):
        if not w.get_bulk_fix_status().get("running"):
            break
        time.sleep(0.05)
    assert sorted(calls) == [(1, "A"), (2, "B")]
    assert w.get_bulk_fix_status()["fixed"] == 2


def test_the_shelf_life_routes_are_admin_only(tmp_path):
    from flask import Flask, g
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "member.db"))
    app = Flask(__name__)

    @app.before_request
    def _member():
        g.is_admin = False
        g.profile_id = 2
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    c = app.test_client()
    try:
        fid = _flag(videoapi._video_db, "A", score=25, size_gb=40, item_id=1)
        assert c.post("/api/video/repair/shelf-life/plan", json={}).status_code == 403
        assert c.post("/api/video/repair/shelf-life/delete",
                      json={"ids": [fid], "phrase": "delete 1 title"}).status_code == 403
        assert videoapi._video_db.repair_get_finding(fid)["status"] == "pending"
    finally:
        videoapi._video_db = None


# ── the detail page: one title's Shelf Life, and Keep ─────────────────────────

def test_title_view_before_and_after_a_run(db, worker):
    from core.video.repair.shelf_life import title_view
    mid = _movie(db, "Low", tmdb=7, imdb=5.0, streaming="ok", count=5)
    before = title_view(db, "movie", mid)
    assert before["scored"] is False and before["keep"] is False and before["can_keep"] is True
    assert before["threshold"] == 30
    _run(worker)
    after = title_view(db, "movie", mid)
    assert after["scored"] is True and after["score"] < 30 and after["gate"] is None
    assert after["reasons"] and all({"signal", "text"} <= set(r) for r in after["reasons"])
    assert after["scored_at"]
    assert title_view(db, "movie", 99999) is None


def test_keep_retires_the_open_finding_at_once_and_unkeep_lets_it_back(db, worker):
    from core.video.repair.shelf_life import set_title_keep, title_view
    mid = _movie(db, "Low", tmdb=7, imdb=5.0, streaming="ok", count=5)
    other = _movie(db, "Other", tmdb=8, imdb=5.0, streaming="ok", count=5)
    _run(worker)
    assert sorted(f["entity_id"] for f in _pending(db)) == sorted(["m:%d" % mid, "m:%d" % other])
    res = set_title_keep(db, "movie", mid, True, set_by=1)
    assert res["success"] and res["keep"] is True
    assert [f["entity_id"] for f in _pending(db)] == ["m:%d" % other]     # only its own finding goes
    _run(worker)
    assert title_view(db, "movie", mid)["gate"] == "keep"
    assert [f["entity_id"] for f in _pending(db)] == ["m:%d" % other]
    set_title_keep(db, "movie", mid, False)
    _run(worker)
    # retired by Keep, not dismissed by hand, so it comes back
    assert sorted(f["entity_id"] for f in _pending(db)) == sorted(["m:%d" % mid, "m:%d" % other])


def test_keep_needs_a_tmdb_match(db):
    from core.video.repair.shelf_life import set_title_keep, title_view
    mid = _movie(db, "Unmatched", tmdb=None)
    assert title_view(db, "movie", mid)["can_keep"] is False
    res = set_title_keep(db, "movie", mid, True)
    assert res["success"] is False and "TMDB" in res["error"]
    assert set_title_keep(db, "movie", 99999, True) is None


def test_a_kept_title_is_never_deleted_even_from_an_old_finding(db, worker, monkeypatch):
    from core.video import delete_title
    calls = []
    monkeypatch.setattr(delete_title, "run", lambda *a, **k: calls.append(a) or {"success": True})
    mid = _movie(db, "Low", tmdb=7, imdb=5.0, streaming="ok", count=5)
    _run(worker)
    fid = _pending(db)[0]["id"]
    db.shelf_life_set_keep("movie", 7, True)        # kept behind the finding's back
    res = worker.fix_finding(fid, fix_action="Low")
    assert not res.get("success") and "Keep" in (res.get("error") or "") and calls == []
    assert mid


def test_detail_routes(client):
    c, db, _ = client
    mid = _movie(db, "Low", tmdb=7)
    r = c.get("/api/video/repair/shelf-life/movie/%d" % mid)
    assert r.status_code == 200 and r.get_json()["scored"] is False
    assert c.get("/api/video/repair/shelf-life/film/%d" % mid).status_code == 400
    assert c.get("/api/video/repair/shelf-life/movie/99999").status_code == 404
    k = c.post("/api/video/repair/shelf-life/movie/%d/keep" % mid, json={"keep": True})
    assert k.status_code == 200 and k.get_json()["keep"] is True
    assert ("movie", 7) in db.shelf_life_keeps()
    assert c.post("/api/video/repair/shelf-life/movie/%d/keep" % mid, json={"keep": "yes"}).status_code == 400
    assert c.post("/api/video/repair/shelf-life/movie/%d/keep" % mid, json={"keep": False}).get_json()["keep"] is False
    assert db.shelf_life_keeps() == set()


def test_the_detail_routes_are_admin_only(tmp_path):
    from flask import Flask, g
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "member.db"))
    app = Flask(__name__)

    @app.before_request
    def _member():
        g.is_admin = False
        g.profile_id = 2

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    c = app.test_client()
    try:
        mid = _movie(videoapi._video_db, "Low", tmdb=7)
        assert c.get("/api/video/repair/shelf-life/movie/%d" % mid).status_code == 403
        assert c.post("/api/video/repair/shelf-life/movie/%d/keep" % mid, json={"keep": True}).status_code == 403
        assert videoapi._video_db.shelf_life_keeps() == set()
    finally:
        videoapi._video_db = None
