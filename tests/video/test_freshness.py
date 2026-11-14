"""a title's page keeps the title current while someone looks at it.

each source (the media server, the metadata provider, the ratings) has its
own clock; a stale one is asked again, a fresh one is left alone, and the
page is told whether anything it shows moved. nothing is removed by looking.
"""

from __future__ import annotations

from datetime import date

from core.video import freshness
from tests.video.test_library_manage import _add_file, _movie, _row, _show, db  # noqa: F401 - fixtures
from tests.video.test_library_manager import _client


def _age(db, kind, item_id, source, seconds):
    conn = db._get_connection()
    conn.execute(
        "UPDATE video_title_freshness SET checked_at = datetime('now', ?) "
        "WHERE kind=? AND item_id=? AND source=?", ("-%d seconds" % seconds, kind, item_id, source))
    conn.commit()
    conn.close()


def _quiet(calls):
    """readers that answer ok and count how often each source was asked"""
    def reader(name):
        def run():
            calls.append(name)
            return {"ok": True}
        return run
    return {s: reader(s) for s in freshness.SOURCES}


# ── the clocks ───────────────────────────────────────────────────────────────

def test_a_title_still_changing_is_asked_about_more_often_than_a_settled_one():
    today = date(2026, 11, 13)
    airing = freshness.clocks("show", {"status": "Returning Series"}, today)
    ended = freshness.clocks("show", {"status": "Ended"}, today)
    assert airing["metadata"] < ended["metadata"] and airing["ratings"] < ended["ratings"]
    assert freshness.clocks("show", {"status": "Canceled"}, today) == ended
    new = freshness.clocks("movie", {"release_date": "2026-10-20"}, today)
    old = freshness.clocks("movie", {"release_date": "1999-03-31"}, today)
    unreleased = freshness.clocks("movie", {"release_date": "2027-05-01"}, today)
    undated = freshness.clocks("movie", {"release_date": None}, today)
    assert new == unreleased == undated and new["metadata"] < old["metadata"]
    # the server is one small call whatever the title
    assert airing["server"] == ended["server"] == old["server"] == freshness.SERVER_TTL


def test_a_title_never_asked_about_is_stale_everywhere(db):
    mid = _movie(db)
    out = freshness.status(db, "movie", mid)
    assert set(out) == set(freshness.SOURCES)
    assert all(s["stale"] and s["age_seconds"] is None for s in out.values())
    assert freshness.status(db, "movie", 99999) == {}


# ── asking ───────────────────────────────────────────────────────────────────

def test_the_first_visit_asks_every_source_and_the_next_asks_none(db):
    mid = _movie(db)
    calls: list = []
    first = freshness.refresh(db, "movie", mid, ask=_quiet(calls))
    assert first["success"] and first["asked"] == ["server", "metadata", "ratings"]
    assert not any(s["stale"] for s in first["sources"].values())
    again = freshness.refresh(db, "movie", mid, ask=_quiet(calls))
    assert again["asked"] == [] and again["changed"] is False
    assert calls == ["server", "metadata", "ratings"]


def test_only_the_source_whose_clock_ran_out_is_asked_again(db):
    mid = _movie(db)
    freshness.refresh(db, "movie", mid, ask=_quiet([]))
    _age(db, "movie", mid, "server", freshness.SERVER_TTL + 5)
    calls: list = []
    out = freshness.refresh(db, "movie", mid, ask=_quiet(calls))
    assert out["asked"] == ["server"] and calls == ["server"]


def test_asking_by_hand_ignores_the_clocks(db):
    mid = _movie(db)
    freshness.refresh(db, "movie", mid, ask=_quiet([]))
    calls: list = []
    out = freshness.refresh(db, "movie", mid, force=True, ask=_quiet(calls))
    assert out["asked"] == list(freshness.SOURCES) and len(calls) == 3


def test_a_source_that_fails_does_not_stop_the_others_and_waits_out_its_clock(db):
    mid = _movie(db)
    calls: list = []
    ask = _quiet(calls)

    def broken():
        calls.append("metadata")
        raise RuntimeError("tmdb is down")
    ask["metadata"] = broken
    out = freshness.refresh(db, "movie", mid, ask=ask)
    assert out["success"] and calls == ["server", "metadata", "ratings"]
    assert out["notes"] == {"metadata": "failed"}
    # not hammered on every visit
    assert freshness.refresh(db, "movie", mid, ask=ask)["asked"] == []


def test_a_source_that_says_try_again_soon_is_asked_on_the_next_visit(db):
    mid = _movie(db)
    ask = _quiet([])
    ask["ratings"] = lambda: {"ok": False, "reason": "ratings quota spent", "retry": True}
    out = freshness.refresh(db, "movie", mid, ask=ask)
    assert out["sources"]["ratings"]["stale"] and out["notes"]["ratings"] == "ratings quota spent"
    assert freshness.refresh(db, "movie", mid, ask=ask)["asked"] == ["ratings"]


def test_one_refresh_per_title_at_a_time(db):
    mid = _movie(db)
    seen: list = []
    ask = _quiet([])

    def slow():
        # a second visitor arrives while the first is still asking
        seen.append(freshness.refresh(db, "movie", mid, ask=_quiet([])))
        return {"ok": True}
    ask["server"] = slow
    freshness.refresh(db, "movie", mid, ask=ask)
    assert seen[0]["running"] is True and seen[0]["asked"] == []
    # and the title is free again afterwards
    assert "running" not in freshness.refresh(db, "movie", mid, force=True, ask=_quiet([]))


# ── did the page's picture move ──────────────────────────────────────────────

def test_changed_is_only_said_when_something_the_page_shows_moved(db, tmp_path):
    mid = _movie(db)
    assert freshness.refresh(db, "movie", mid, ask=_quiet([]))["changed"] is False
    ask = _quiet([])

    def new_file():
        _add_file(db, movie_id=mid, path=tmp_path / "Movies/Dune (2021)/dune.mkv")
        return {"ok": True}
    ask["server"] = new_file
    assert freshness.refresh(db, "movie", mid, force=True, ask=ask)["changed"] is True

    def ratings():
        db.apply_ratings("movie", mid, {"imdb_rating": 8.1})
        return {"ok": True}
    ask = _quiet([])
    ask["ratings"] = ratings
    assert freshness.refresh(db, "movie", mid, force=True, ask=ask)["changed"] is True
    # the same score again is not a change
    assert freshness.refresh(db, "movie", mid, force=True, ask=ask)["changed"] is False


def test_a_refresh_that_only_moved_its_own_stamps_is_not_a_change(db):
    mid = _movie(db)

    def stamp():
        db.enrichment_apply("tmdb", "movie", mid, matched=True, external_id=438631, metadata={})
        return {"ok": True}
    ask = _quiet([])
    ask["metadata"] = stamp
    freshness.refresh(db, "movie", mid, ask=ask)
    assert freshness.refresh(db, "movie", mid, force=True, ask=ask)["changed"] is False


def test_a_show_that_changed_an_episode_is_a_change(db, tmp_path):
    sid, eps = _show(db, tmp_path)
    freshness.refresh(db, "show", sid, ask=_quiet([]))
    ask = _quiet([])

    def renamed():
        db.backfill_episodes(sid, 2, [{"episode_number": 3, "title": "Bit by a Dead Bee"}])
        return {"ok": True}
    ask["metadata"] = renamed
    assert freshness.refresh(db, "show", sid, force=True, ask=ask)["changed"] is True


# ── the server: never removes, follows a re-key ──────────────────────────────

def test_a_title_the_server_no_longer_has_is_reported_and_left_in_place(db):
    mid = _movie(db)
    ask = _quiet([])
    ask["server"] = lambda: {"ok": True, "gone": True}
    out = freshness.refresh(db, "movie", mid, ask=ask)
    assert out["gone"] is True and out["moved_to"] is None
    assert _row(db, "SELECT COUNT(*) FROM movies WHERE id=?", mid) == (1,)


def test_a_rekeyed_title_sends_the_page_to_its_new_row(db):
    mid = _movie(db)
    other = _movie(db, title="Dune", sid="200", tmdb=438631)
    ask = _quiet([])
    ask["server"] = lambda: {"ok": True, "moved_to": other}
    out = freshness.refresh(db, "movie", mid, ask=ask)
    assert out["moved_to"] == other and out["changed"] is True
    # the new row is not asked again straight away
    assert out["sources"]["server"]["stale"] is False


def test_the_sync_itself_leaves_a_missing_title_alone_when_told_to(db, monkeypatch):
    from core.video import movie_sync, show_sync
    mid = _movie(db)
    sid, _eps = _show(db, None)

    class Source:
        server_name = "plex"

        def movie_item(self, *a, **k):
            return None

        def show_tree(self, *a, **k):
            return None

    class Scanner:
        def get_status(self):
            return {"state": "idle"}
    monkeypatch.setattr("core.video.sources.get_active_video_source", lambda: Source())
    monkeypatch.setattr("core.video.scanner.get_video_scanner", lambda _db: Scanner())
    assert movie_sync.sync_movie(db, mid, remove_when_gone=False, refresh_metadata=False)["gone"] is True
    assert show_sync.sync_show(db, sid, remove_when_gone=False, refresh_schedule=False)["gone"] is True
    assert _row(db, "SELECT COUNT(*) FROM movies WHERE id=?", mid) == (1,)
    assert _row(db, "SELECT COUNT(*) FROM shows WHERE id=?", sid) == (1,)
    # pressed by hand, a title the server no longer has is still removed
    assert movie_sync.sync_movie(db, mid)["movie_removed"] is True
    assert _row(db, "SELECT COUNT(*) FROM movies WHERE id=?", mid) == (0,)


def test_no_server_is_said_not_raised_and_is_not_asked_on_every_visit(db, monkeypatch):
    mid = _movie(db)

    class Scanner:
        def get_status(self):
            return {"state": "idle"}
    monkeypatch.setattr("core.video.sources.get_active_video_source", lambda: None)
    monkeypatch.setattr("core.video.scanner.get_video_scanner", lambda _db: Scanner())
    ask = _quiet([])
    del ask["server"]                                    # the real reader
    out = freshness.refresh(db, "movie", mid, ask=ask)
    assert out["success"] and "No video server" in out["notes"]["server"]
    assert out["sources"]["server"]["stale"] is False


def test_a_scan_in_the_way_is_tried_again_on_the_next_visit(db, monkeypatch):
    mid = _movie(db)

    class Scanner:
        def get_status(self):
            return {"state": "running"}
    monkeypatch.setattr("core.video.scanner.get_video_scanner", lambda _db: Scanner())
    ask = _quiet([])
    del ask["server"]
    out = freshness.refresh(db, "movie", mid, ask=ask)
    assert "scan" in out["notes"]["server"].lower() and out["sources"]["server"]["stale"] is True


# ── what a refresh may overwrite ─────────────────────────────────────────────

def test_a_refresh_moves_the_facts_that_change_and_only_fills_the_rest(db):
    sid, _eps = _show(db, None)
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1396,
                        metadata={"status": "Returning Series", "overview": "The first telling."})
    changed = db.refresh_volatile("show", sid, {"status": "Ended", "last_air_date": "2013-09-29",
                                                "overview": "A later telling.", "airs_time": ""})
    assert changed == 2
    assert _row(db, "SELECT status, last_air_date, overview FROM shows WHERE id=?", sid) == \
        ("Ended", "2013-09-29", "The first telling.")
    # the same answer again changes nothing, and a blank never blanks
    assert db.refresh_volatile("show", sid, {"status": "Ended", "last_air_date": ""}) == 0
    assert _row(db, "SELECT last_air_date FROM shows WHERE id=?", sid) == ("2013-09-29",)


def test_a_movie_joins_the_collection_it_was_added_to_later(db):
    mid = _movie(db)
    db.refresh_volatile("movie", mid, {"tmdb_collection_id": 726871,
                                       "tmdb_collection_name": "Dune Collection",
                                       "release_date": "2021-10-22", "status": "Released"})
    assert _row(db, "SELECT tmdb_collection_id, tmdb_collection_name, status FROM movies WHERE id=?",
                mid) == (726871, "Dune Collection", "Released")


def test_a_placeholder_episode_title_gives_way_to_the_real_one_and_a_real_one_stays(db):
    sid, eps = _show(db, None)
    conn = db._get_connection()
    conn.execute("UPDATE episodes SET title='Episode 1' WHERE id=?", (eps[(2, 1)],))
    conn.execute("UPDATE episodes SET title='TBA' WHERE id=?", (eps[(2, 2)],))
    conn.commit()
    conn.close()
    db.backfill_episodes(sid, 2, [{"episode_number": 1, "title": "Seven Thirty-Seven"},
                                  {"episode_number": 2, "title": "Grilled"}])
    db.backfill_episodes(sid, 1, [{"episode_number": 1, "title": "Something Else"},
                                  {"episode_number": 2, "title": "Episode 2"}])
    assert _row(db, "SELECT title FROM episodes WHERE id=?", eps[(2, 1)]) == ("Seven Thirty-Seven",)
    assert _row(db, "SELECT title FROM episodes WHERE id=?", eps[(2, 2)]) == ("Grilled",)
    # a real title is never replaced, least of all by a placeholder
    assert _row(db, "SELECT title FROM episodes WHERE id=?", eps[(1, 1)]) == ("Pilot",)
    assert _row(db, "SELECT title FROM episodes WHERE id=?", eps[(1, 2)]) == ("Cat's in the Bag",)


# ── a re-read keeps a file's id ──────────────────────────────────────────────

def test_a_scan_keeps_the_id_and_the_added_date_of_a_file_it_still_sees(db):
    def item(files):
        return {"server_id": "100", "title": "Dune", "year": 2021, "tmdb_id": 438631, "files": files}
    a = {"relative_path": "/m/Dune/dune.1080.mkv", "size_bytes": 10, "resolution": "1080"}
    b = {"relative_path": "/m/Dune/dune.2160.mkv", "size_bytes": 40, "resolution": "2160"}
    mid = db.upsert_movie("plex", item([a, b]))
    conn = db._get_connection()
    conn.execute("UPDATE media_files SET added_at='2024-01-02 03:04:05' WHERE movie_id=?", (mid,))
    conn.commit()
    before = {r[1]: r[0] for r in conn.execute(
        "SELECT id, relative_path FROM media_files WHERE movie_id=?", (mid,))}
    conn.close()

    c = {"relative_path": "/m/Dune/dune.remux.mkv", "size_bytes": 80, "resolution": "2160"}
    db.upsert_movie("plex", item([dict(a, size_bytes=11), c]))     # b went, c came, a changed size

    conn = db._get_connection()
    after = {r[1]: (r[0], r[2], r[3]) for r in conn.execute(
        "SELECT id, relative_path, size_bytes, added_at FROM media_files WHERE movie_id=?", (mid,))}
    conn.close()
    assert set(after) == {a["relative_path"], c["relative_path"]}
    assert after[a["relative_path"]] == (before[a["relative_path"]], 11, "2024-01-02 03:04:05")
    assert after[c["relative_path"]][0] not in before.values()
    assert after[c["relative_path"]][2] != "2024-01-02 03:04:05"


# ── the route ────────────────────────────────────────────────────────────────

def test_the_page_asks_through_the_route_and_any_profile_may(db, monkeypatch):
    mid = _movie(db)
    seen: list = []

    def fake(_db, kind, item_id, *, force=False, ask=None):
        seen.append((kind, item_id, force))
        return {"success": True, "changed": False, "asked": [], "sources": {}}
    monkeypatch.setattr(freshness, "refresh", fake)
    viewer = _client(db, is_admin=False)
    assert viewer.post("/api/video/detail/movie/%d/freshen" % mid, json={}).get_json()["success"]
    # asking by hand spends every source's budget: an admin's call
    assert viewer.post("/api/video/detail/movie/%d/freshen" % mid, json={"force": True}).status_code == 403
    admin = _client(db)
    assert admin.post("/api/video/detail/movie/%d/freshen" % mid, json={"force": True}).status_code == 200
    assert seen == [("movie", mid, False), ("movie", mid, True)]
    got = admin.get("/api/video/detail/movie/%d/freshen" % mid).get_json()
    assert got["success"] and set(got["sources"]) == set(freshness.SOURCES)
    assert admin.get("/api/video/detail/movie/99999/freshen").status_code == 404
    assert admin.post("/api/video/detail/person/1/freshen", json={}).status_code == 400
