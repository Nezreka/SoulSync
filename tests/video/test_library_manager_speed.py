"""the library page answers from memory.

every list, every filter count and the Files view are worked out over rows
that were read once, so a page turn, a filter or a sort never waits on the
database. these pin what was read how often, and that a write, a scan and an
idle side each leave the rows right.
"""

from __future__ import annotations

import time

from core.video import library_manager as lm
from tests.video.test_library_manager import _client, db  # noqa: F401 - fixture


def _count_queries(database, monkeypatch):
    """every statement the database is asked from here on"""
    seen = []
    real = database._get_connection

    class Counted:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a):
            seen.append(" ".join(str(sql).split())[:60])
            return self._c.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self._c, name)
    monkeypatch.setattr(database, "_get_connection", lambda: Counted(real()))
    return seen


# ── the Files view ───────────────────────────────────────────────────────────

def test_a_page_turn_a_filter_and_a_sort_of_the_files_ask_the_database_nothing(db, monkeypatch):
    database, _ids = db
    first = lm.files(database, {"kind": "movies"})
    assert first["totals"]["files"] == 5
    seen = _count_queries(database, monkeypatch)
    lm.files(database, {"kind": "movies", "page": "2", "limit": "2"})
    lm.files(database, {"kind": "movies", "sort": "size", "dir": "desc"})
    lm.files(database, {"kind": "movies", "codec": "hevc"})
    lm.files(database, {"kind": "movies", "search": "alien", "needs": "copies"})
    assert seen == []
    # a show's page reads one thing: the titles of the episodes it is showing
    lm.files(database, {"kind": "shows"})
    seen.clear()
    lm.files(database, {"kind": "shows", "sort": "size"})
    assert len(seen) == 1 and seen[0].startswith("SELECT id, title FROM episodes WHERE id IN")


def test_the_files_come_back_the_same_from_memory_as_they_did_from_the_query(db):
    database, ids = db
    by_title = lm.files(database, {"kind": "movies"})["items"]
    assert [f["title"] for f in by_title] == ["Alien", "Alien", "Brazil", "The Matrix", "Zodiac"]
    biggest = lm.files(database, {"kind": "movies", "sort": "size"})["items"]
    assert [f["size_bytes"] for f in biggest] == [9000, 5000, 4000, 3000, 700]
    smallest = lm.files(database, {"kind": "movies", "sort": "size", "dir": "asc"})["items"]
    assert [f["size_bytes"] for f in smallest] == [700, 3000, 4000, 5000, 9000]
    assert [f["title"] for f in lm.files(database, {"kind": "movies", "dir": "desc"})["items"]][0] == "Zodiac"
    hevc = lm.files(database, {"kind": "movies", "codec": "hevc"})
    assert (hevc["totals"]["files"], hevc["totals"]["size_bytes"]) == (1, 9000)
    assert hevc["items"][0]["resolution"] == "4K" and hevc["items"][0]["dynamic_range"] == "HDR10"
    assert [f["title"] for f in lm.files(database, {"kind": "movies", "hdr": "0"})["items"]].count("The Matrix") == 0
    assert lm.files(database, {"kind": "movies", "resolution": "720"})["totals"]["files"] == 1
    assert lm.files(database, {"kind": "movies", "audio_language": "ja"})["totals"]["files"] == 1
    assert lm.files(database, {"kind": "movies", "subtitle_language": "fr"})["totals"]["files"] == 1
    assert lm.files(database, {"kind": "movies", "drive": "/mnt/b"})["totals"]["files"] >= 1
    assert lm.files(database, {"kind": "movies", "title_id": str(ids["Alien"])})["totals"]["files"] == 2
    assert lm.files(database, {"kind": "movies", "search": "ZODIAC"})["totals"]["files"] == 1
    assert lm.files(database, {"kind": "movies", "search": "a.1080"})["totals"]["files"] == 1   # by path too
    # the "needs a look" filters, per file
    assert [f["title"] for f in lm.files(database, {"kind": "movies", "needs": "copies"})["items"]] == ["Alien", "Alien"]
    assert [f["title"] for f in lm.files(database, {"kind": "movies", "needs": "unprobed"})["items"]] == ["Zodiac"]
    assert [f["title"] for f in lm.files(database, {"kind": "movies", "needs": "no_subtitles"})["items"]] == ["Brazil"]


def test_a_shows_files_carry_their_episode_and_sort_in_episode_order(db):
    database, _ids = db
    out = lm.files(database, {"kind": "shows"})
    assert [(f["season_number"], f["episode_number"]) for f in out["items"]] == [(1, 1), (1, 2), (1, 2)]
    assert out["items"][0]["episode_title"] == "One" and out["items"][0]["kind"] == "show"
    assert lm.files(database, {"kind": "shows", "needs": "copies"})["totals"]["files"] == 2
    page = lm.files(database, {"kind": "shows", "limit": "2", "page": "2"})
    assert len(page["items"]) == 1 and page["pagination"]["total_pages"] == 2


def test_a_write_to_one_title_shows_in_the_files_without_reading_the_rest_again(db, monkeypatch):
    database, ids = db
    assert lm.files(database, {"kind": "movies"})["totals"]["files"] == 5
    conn = database._get_connection()
    conn.execute("DELETE FROM media_files WHERE movie_id=? AND resolution='720'", (ids["Alien"],))
    conn.commit()
    conn.close()
    # nobody told the manager yet: the rows it holds still say five
    assert lm.files(database, {"kind": "movies"})["totals"]["files"] == 5
    seen = _count_queries(database, monkeypatch)
    lm.refresh(database, "movies", [ids["Alien"]])
    out = lm.files(database, {"kind": "movies"})
    assert out["totals"]["files"] == 4
    assert [f["title"] for f in out["items"]] == ["Alien", "Brazil", "The Matrix", "Zodiac"]
    # only that title's files were read again
    file_reads = [q for q in seen if "FROM media_files mf JOIN movies" in q or "mf.relative_path" in q]
    assert seen and all("t.id IN" in q or "media_files" not in q for q in seen if q in file_reads) is True


def test_a_side_nobody_has_looked_at_in_a_while_gives_its_room_back(db, monkeypatch):
    database, _ids = db
    lm.files(database, {"kind": "movies"})
    lm.files(database, {"kind": "shows"})
    assert len(lm._file_snaps) == 2
    for snap in lm._file_snaps.values():
        if snap["rows"] and snap["rows"][0][lm.F_EP_ID] is None:      # the movies
            snap["used"] = time.monotonic() - lm._FILE_IDLE - 5
    lm.files(database, {"kind": "shows"})
    assert len(lm._file_snaps) == 1
    # asked for again, it is simply read again
    assert lm.files(database, {"kind": "movies"})["totals"]["files"] == 5


# ── the filter counts ────────────────────────────────────────────────────────

def test_the_filter_counts_are_worked_out_once_and_kept_until_the_rows_change(db, monkeypatch):
    database, ids = db
    first = lm.facets(database, "movies")
    calls = {"n": 0}
    real = lm._need_hit

    def counted(*a):
        calls["n"] += 1
        return real(*a)
    monkeypatch.setattr(lm, "_need_hit", counted)
    assert lm.facets(database, "movies") == first
    assert calls["n"] == 0                                   # read from what was kept
    lm.refresh(database, "movies", [ids["Alien"]])           # the rows changed
    again = lm.facets(database, "movies")
    assert calls["n"] == len(lm.NEEDS) * again["titles"]     # one pass, every filter at once
    assert again["needs"] == first["needs"]


def test_a_count_is_always_the_number_of_rows_its_filter_shows(db):
    database, _ids = db
    for kind in lm.KINDS:
        counts = lm.facets(database, kind)["needs"]
        for need in lm.NEEDS:
            shown = lm.titles(database, kind, {"needs": need, "limit": "500"})["totals"]["titles"]
            assert counts[need] == shown, (kind, need)


def test_a_profiles_own_watched_titles_get_their_own_counts(db):
    database, ids = db
    shared = lm.facets(database, "movies")["needs"]["unwatched"]
    conn = database._get_connection()
    for title in ("Brazil", "The Matrix"):
        conn.execute("INSERT INTO video_watch_state (profile_id, kind, item_id, play_count) "
                     "VALUES (7, 'movie', ?, 1)", (ids[title],))
    conn.commit()
    conn.close()
    mine = lm.facets(database, "movies", watcher=7)["needs"]["unwatched"]
    assert (shared, mine) == (3, 2)
    assert lm.facets(database, "movies")["needs"]["unwatched"] == shared   # not mixed up


# ── the filters are read once ────────────────────────────────────────────────

def test_the_filters_read_once_still_pick_the_same_titles(db):
    database, _ids = db
    rows = lm._snapshot(database, "movies", None)

    def picked(args):
        passes = lm._compile(args, None)
        return sorted(r["title"] for r in rows if passes(r))
    everything = sorted(r["title"] for r in rows)
    assert picked({}) == everything
    assert picked({"search": "al"}) == ["Alien"]
    assert picked({"letter": "a"}) == ["Alien"]
    assert picked({"status": "not_owned"}) == ["Wanted Film"]
    assert picked({"monitored": "0"}) == ["Brazil"]
    assert picked({"resolution": "4k"}) == ["The Matrix"]
    assert picked({"resolution": "nonsense"}) == []
    assert picked({"codec": "hevc"}) == ["The Matrix"]
    assert picked({"hdr": "1"}) == ["The Matrix"]
    assert "The Matrix" not in picked({"hdr": "0"}) and "Wanted Film" not in picked({"hdr": "0"})
    assert picked({"audio_language": "ja"}) == ["The Matrix"]
    assert picked({"subtitle_language": "fr"}) == ["The Matrix"]
    assert picked({"year_from": "1980", "year_to": "2000"}) == ["Brazil", "The Matrix"]
    assert picked({"needs": "copies"}) == ["Alien"]
    assert picked({"needs": "copies,unwatched"}) == []            # every filter has to pass
    assert picked({"search": "a", "status": "owned", "codec": "h264"}) == ["Alien", "Brazil"]
    # and one row asked on its own gets the same answer
    alien = next(r for r in rows if r["title"] == "Alien")
    assert lm._matches(alien, {"needs": "copies"}, None) is True
    assert lm._matches(alien, {"resolution": "4k"}, None) is False


# ── a scan, and the tab that is not open ─────────────────────────────────────

def test_after_a_scan_the_old_rows_answer_while_the_new_ones_are_summed(db):
    database, ids = db
    assert lm.titles(database, "movies", {})["totals"]["titles"] == 5
    assert lm.files(database, {"kind": "movies"})["totals"]["files"] == 5
    conn = database._get_connection()
    conn.execute("DELETE FROM movies WHERE id=?", (ids["Wanted Film"],))
    conn.commit()
    conn.close()
    lm.expire()
    # straight away: the rows that were there, not a wait
    assert lm.titles(database, "movies", {})["totals"]["titles"] == 5
    deadline = time.time() + 20
    while time.time() < deadline and lm.titles(database, "movies", {})["totals"]["titles"] != 4:
        time.sleep(0.05)
    assert lm.titles(database, "movies", {})["totals"]["titles"] == 4


def test_the_other_tab_is_summed_up_in_the_background(db):
    database, _ids = db
    lm.forget()
    lm.warm(database, "shows", None)
    key = lm._snap_key(database, "shows", None)
    deadline = time.time() + 20
    while time.time() < deadline and key not in lm._snapshots:
        time.sleep(0.05)
    assert key in lm._snapshots
    lm.warm(database, "shows", None)                 # already there: nothing to do
    assert not lm._warming


def test_the_page_asks_which_server_is_active_once_not_on_every_request(db, monkeypatch):
    database, _ids = db
    calls = {"n": 0}

    def resolve():
        calls["n"] += 1
        return "plex"
    monkeypatch.setattr("core.video.sources.resolve_video_server", resolve)
    client = _client(database)
    for path in ("titles?kind=movies", "titles?kind=movies&page=2", "facets?kind=movies",
                 "files?kind=movies", "titles?kind=movies&search=a"):
        assert client.get("/api/video/manager/" + path).get_json()["success"]
    assert calls["n"] == 1
