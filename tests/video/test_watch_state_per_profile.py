"""Per-profile video watch state (#1941).

The scan reads watch state through the server account, so the shared columns
are the server owner's. A profile linked to its own Plex account reads and
writes its own rows in video_watch_state; any other profile has none.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from core.video import member_watch, watch_scope
from core.video import metadata as med
from database.video_database import VideoDatabase

MEMBER = 7


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _movie(db, sid="m1", title="Movie", **watch):
    item = {"server_id": sid, "title": title, "runtime_minutes": 100,
            "file": {"relative_path": f"{sid}.mkv"}}
    item.update(watch)
    return db.upsert_movie("plex", item)


def _show(db):
    """a show whose owner has watched S1E1 and is part-way into S1E2; S1E3 is
    a row the server doesn't hold."""
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Show", "seasons": [
            {"season_number": 1, "episodes": [
                {"episode_number": 1, "title": "E1", "server_id": "e1", "play_count": 1,
                 "last_viewed_at": "2026-10-01 10:00:00", "runtime_minutes": 40,
                 "file": {"relative_path": "e1.mkv"}},
                {"episode_number": 2, "title": "E2", "server_id": "e2",
                 "view_offset_ms": 10 * 60000, "last_viewed_at": "2026-10-02 10:00:00",
                 "runtime_minutes": 40, "file": {"relative_path": "e2.mkv"}},
                {"episode_number": 3, "title": "E3", "server_id": "e3", "runtime_minutes": 40,
                 "file": {"relative_path": "e3.mkv"}}]}]})
    conn = db._get_connection()
    try:
        conn.execute("UPDATE shows SET watched_episodes=1 WHERE id=?", (sid,))
        conn.commit()
        eps = {r[1]: r[0] for r in conn.execute(
            "SELECT id, episode_number FROM episodes WHERE show_id=?", (sid,))}
    finally:
        conn.close()
    return sid, eps


def _rows(db, profile_id):
    conn = db._get_connection()
    try:
        return sorted(tuple(r) for r in conn.execute(
            "SELECT kind, item_id, play_count, last_viewed_at, view_offset_ms "
            "FROM video_watch_state WHERE profile_id=?", (profile_id,)))
    finally:
        conn.close()


def _shared_movie(db, mid):
    conn = db._get_connection()
    try:
        return tuple(conn.execute("SELECT play_count, view_offset_ms FROM movies WHERE id=?",
                                  (mid,)).fetchone())
    finally:
        conn.close()


# ── readers ──────────────────────────────────────────────────────────────────
def test_continue_watching_reads_whose_state_it_is_asked_for(db):
    _movie(db, "m1", "Owner's movie", view_offset_ms=30 * 60000,
           last_viewed_at="2026-10-03 10:00:00")
    mine = _movie(db, "m2", "Member's movie")
    _show(db)
    db.save_profile_watch_state(MEMBER, [("movie", mine, 0, "2026-10-04 10:00:00", 20 * 60000)])

    owner = {(r["kind"], r["title"]) for r in db.continue_watching()}
    assert owner == {("movie", "Owner's movie"), ("show", "Show")}
    member = [(r["kind"], r["title"], r["view_offset_ms"])
              for r in db.continue_watching(watcher=MEMBER)]
    assert member == [("movie", "Member's movie", 20 * 60000)]
    assert db.continue_watching(watcher=8) == []          # no rows, nothing to resume


def test_show_detail_and_next_up_follow_the_watcher(db):
    sid, eps = _show(db)
    owner = db.show_detail(sid)
    assert owner["watched_episodes"] == 1
    assert owner["next_up"]["episode_number"] == 2 and owner["next_up"]["resume"]

    # the member finished E1 and E2, so E3 is next and nothing is resumed
    db.save_profile_watch_state(MEMBER, [("episode", eps[1], 1, "2026-10-05 10:00:00", 0),
                                         ("episode", eps[2], 2, "2026-10-06 10:00:00", 0)])
    member = db.show_detail(sid, watcher=MEMBER)
    assert member["watched_episodes"] == 2
    assert member["next_up"]["episode_number"] == 3 and not member["next_up"]["resume"]
    watched = {e["episode_number"]: e["watched"] for e in member["seasons"][0]["episodes"]}
    assert watched == {1: True, 2: True, 3: False}

    nobody = db.show_detail(sid, watcher=8)
    assert nobody["watched_episodes"] == 0 and nobody["next_up"] is None
    assert db.show_next_up(sid, watcher=8) is None


def test_movie_detail_follows_the_watcher(db):
    mid = _movie(db, play_count=3, last_viewed_at="2026-10-01 10:00:00")
    assert db.movie_detail(mid)["watched"] is True
    member = db.movie_detail(mid, watcher=MEMBER)
    assert (member["watched"], member["play_count"], member["last_viewed_at"]) == (False, 0, None)
    db.save_profile_watch_state(MEMBER, [("movie", mid, 1, "2026-10-09 10:00:00", 0)])
    member = db.movie_detail(mid, watcher=MEMBER)
    assert (member["watched"], member["last_viewed_at"]) == (True, "2026-10-09 10:00:00")
    assert member["title"] == "Movie"                      # the rest of the row is untouched


def test_library_watched_filters_follow_the_watcher(db):
    seen = _movie(db, "m1", "Owner saw", play_count=1)
    other = _movie(db, "m2", "Member saw")
    sid, eps = _show(db)
    db.save_profile_watch_state(MEMBER, [("movie", other, 1, None, 0)])

    def titles(kind, status, watcher=None):
        return [i["title"] for i in db.query_library(kind, status=status, watcher=watcher)["items"]]
    assert titles("movies", "watched") == ["Owner saw"]
    assert titles("movies", "watched", MEMBER) == ["Member saw"]
    assert titles("movies", "unwatched", MEMBER) == ["Owner saw"]
    assert titles("shows", "watched") == ["Show"]
    assert titles("shows", "watched", MEMBER) == []
    assert titles("shows", "unwatched", MEMBER) == ["Show"]
    row = db.query_library("movies", watcher=MEMBER)["items"]
    assert {r["title"]: r["play_count"] or 0 for r in row} == {"Owner saw": 0, "Member saw": 1}
    db.save_profile_watch_state(MEMBER, [("episode", eps[3], 1, None, 0)])
    show_row = db.query_library("shows", watcher=MEMBER)["items"][0]
    assert show_row["watched_episodes"] == 1
    assert seen != other


def test_library_tab_reads_follow_the_watcher(db):
    sid, eps = _show(db)
    db.save_profile_watch_state(MEMBER, [("episode", eps[3], 1, None, 0)])
    assert db.show_library_meta(sid)["watched_episodes"] == 1
    assert db.show_library_meta(sid, watcher=MEMBER)["watched_episodes"] == 1
    owner = {e["episode_number"]: e["play_count"] or 0 for e in db.library_show_episodes(sid)}
    member = {e["episode_number"]: e["play_count"] or 0
              for e in db.library_show_episodes(sid, watcher=MEMBER)}
    assert owner == {1: 1, 2: 0, 3: 0}
    assert member == {1: 0, 2: 0, 3: 1}


# ── writes ───────────────────────────────────────────────────────────────────
def test_a_profiles_toggle_lands_in_its_own_rows_not_the_shared_columns(db):
    mid = _movie(db, view_offset_ms=5000)
    assert db.set_watch_state("movie", mid, True, watcher=MEMBER)
    assert _shared_movie(db, mid) == (None, 5000)           # the owner's state is untouched
    assert _rows(db, MEMBER) == [("movie", mid, 1, None, 0)]
    assert db.set_watch_state("movie", mid, False, watcher=MEMBER)
    assert _rows(db, MEMBER) == [("movie", mid, 0, None, 0)]
    assert not db.set_watch_state("movie", 999, True, watcher=MEMBER)


def test_marking_a_show_marks_the_episodes_the_server_holds(db):
    sid, eps = _show(db)
    conn = db._get_connection()
    try:   # E3 becomes a row the server doesn't hold (tmdb-only)
        conn.execute("UPDATE episodes SET server_id=NULL WHERE id=?", (eps[3],))
        conn.commit()
    finally:
        conn.close()
    assert db.set_watch_state("show", sid, True, watcher=MEMBER)
    assert [(k, i, pc) for k, i, pc, _lv, _vo in _rows(db, MEMBER)] == \
        [("episode", eps[1], 1), ("episode", eps[2], 1)]
    assert db.show_detail(sid)["watched_episodes"] == 1     # owner's roll-up unchanged
    assert db.set_watch_state("episode", eps[3], True, watcher=MEMBER)
    assert db.show_detail(sid, watcher=MEMBER)["watched_episodes"] == 3


def test_saving_replaces_or_merges(db):
    a, b, c = _movie(db, "m1"), _movie(db, "m2"), _movie(db, "m3")
    db.save_profile_watch_state(MEMBER, [("movie", a, 1, None, 0), ("movie", b, 1, None, 0)])
    db.save_profile_watch_state(MEMBER, [("movie", c, 2, "x", 0)])               # a delta merges
    assert {r[1] for r in _rows(db, MEMBER)} == {a, b, c}
    db.save_profile_watch_state(MEMBER, [("movie", b, 3, "y", 9)], replace_kinds=("movie",))
    assert _rows(db, MEMBER) == [("movie", b, 3, "y", 9)]                        # a full read replaces
    db.save_profile_watch_state(8, [("movie", a, 1, None, 0)])
    assert db.clear_profile_watch_state(MEMBER) == 1
    assert _rows(db, MEMBER) == [] and len(_rows(db, 8)) == 1


def test_profile_delete_sweeps_its_watch_state(db):
    mid = _movie(db)
    db.save_profile_watch_state(MEMBER, [("movie", mid, 1, None, 0)])
    db.delete_profile_data(MEMBER)
    assert _rows(db, MEMBER) == []


def test_watch_item_ids_maps_server_ids_on_one_server(db):
    mid = _movie(db, "m1")
    db.upsert_movie("jellyfin", {"server_id": "m1", "title": "Other server",
                                 "file": {"relative_path": "j.mkv"}})
    sid, eps = _show(db)
    assert db.watch_item_ids("movie", "plex", ["m1", "nope"]) == {"m1": mid}
    assert db.watch_item_ids("episode", "plex", ["e2"]) == {"e2": eps[2]}
    assert db.watch_item_ids("show", "plex", ["s1"]) == {}


# ── who reads what ───────────────────────────────────────────────────────────
def test_watcher_for(monkeypatch):
    monkeypatch.setattr(watch_scope, "_plex_link",
                        lambda pid: {"id": "u", "title": "U", "token": "tok"} if pid == MEMBER else None)
    assert watch_scope.watcher_for(MEMBER, False) == watch_scope.Watcher(rows=MEMBER, token="tok")
    assert watch_scope.watcher_for(1, True) == watch_scope.SHARED                   # the owner
    assert watch_scope.watcher_for(8, False) == watch_scope.Watcher(rows=8, can_mark=False)
    assert watch_scope.watcher_for(None, False) == watch_scope.SHARED              # no profile
    # an admin linked to an account of its own reads that account's state
    assert watch_scope.watcher_for(MEMBER, True).rows == MEMBER


# ── the watched click ────────────────────────────────────────────────────────
class FakeSource:
    server_name = "plex"

    def __init__(self):
        self.watched = []

    def set_watched(self, server_id, watched, kind="movie"):
        self.watched.append((server_id, watched, kind))
        return {"ok": True}


def test_a_members_click_plays_on_its_own_account_never_the_shared_one(db, monkeypatch):
    mid = _movie(db)
    shared, own, tokens = FakeSource(), FakeSource(), []

    def fake_member_source(token, movies_lib=None, tv_lib=None, db=None):
        tokens.append(token)
        return own
    monkeypatch.setattr(member_watch, "member_source", fake_member_source)
    res = med.set_watched(db, "movie", mid, True, source=shared,
                          watcher=MEMBER, member_token="tok-7")
    assert res["ok"] and res["pushed"]
    assert own.watched == [("m1", True, "movie")] and shared.watched == []
    assert tokens == ["tok-7"]
    assert _shared_movie(db, mid)[0] is None and _rows(db, MEMBER)[0][2] == 1

    # no token: the profile's own row still changes, nothing goes to any server
    res = med.set_watched(db, "movie", mid, False, source=shared, watcher=MEMBER)
    assert res["ok"] and not res["pushed"] and shared.watched == []
    assert _rows(db, MEMBER)[0][2] == 0

    # the owner's click is unchanged: shared columns, shared account
    assert med.set_watched(db, "movie", mid, True, source=shared)["pushed"]
    assert shared.watched == [("m1", True, "movie")] and _shared_movie(db, mid)[0] == 1


# ── the member pass after a scan ─────────────────────────────────────────────
class FakeMemberSource:
    def __init__(self, items=None, fail=False):
        self.items, self.fail, self.calls = items or [], fail, []

    def iter_watch_state(self, since=None, media_type="all"):
        self.calls.append((since, media_type))
        if self.fail:
            raise RuntimeError("plex said no")
        want = {"movie": {"movie"}, "show": {"episode"}}.get(media_type, {"movie", "episode"})
        yield from (it for it in self.items if it["kind"] in want)


class ScanSource:
    server_name = "plex"
    _movies_lib, _tv_lib = "Movies", ["TV Shows"]


def _item(kind, sid, pc=1, lv="2026-10-10 10:00:00", vo=0):
    return {"kind": kind, "server_id": sid, "play_count": pc, "last_viewed_at": lv,
            "view_offset_ms": vo}


def test_member_pass_full_read_replaces_and_delta_merges(db, monkeypatch):
    a, b = _movie(db, "m1"), _movie(db, "m2")
    sid, eps = _show(db)
    db.save_profile_watch_state(MEMBER, [("movie", a, 1, None, 0)])
    src = FakeMemberSource([_item("movie", "m2"), _item("episode", "e2", 0, vo=5000),
                            _item("movie", "not-in-library")])
    got = {}

    def fake_member_source(token, movies_lib=None, tv_lib=None, db=None):
        got.update(token=token, movies=movies_lib, tv=tv_lib)
        return src
    monkeypatch.setattr(member_watch, "member_source", fake_member_source)
    out = member_watch.sync_member_watch_state(db, ScanSource(), profiles=[(MEMBER, "tok")])
    assert out == {"profiles": 1, "rows": 2, "failed": 0}
    assert got == {"token": "tok", "movies": "Movies", "tv": ["TV Shows"]}
    assert src.calls == [(None, "movie"), (None, "show")]
    assert [(k, i) for k, i, *_ in _rows(db, MEMBER)] == [("episode", eps[2]), ("movie", b)]

    since = datetime(2026, 10, 20)
    src.items, src.calls = [_item("movie", "m1", 4)], []
    member_watch.sync_member_watch_state(db, ScanSource(), since=since, media_type="movie",
                                         profiles=[(MEMBER, "tok")])
    assert src.calls == [(since, "movie")]
    assert {i for _k, i, *_ in _rows(db, MEMBER)} == {a, b, eps[2]}   # merged, not replaced


def test_member_pass_movie_only_full_read_keeps_episode_rows(db, monkeypatch):
    a = _movie(db, "m1")
    sid, eps = _show(db)
    db.save_profile_watch_state(MEMBER, [("movie", a, 1, None, 0), ("episode", eps[1], 1, None, 0)])
    monkeypatch.setattr(member_watch, "member_source",
                        lambda *a_, **k: FakeMemberSource([]))
    member_watch.sync_member_watch_state(db, ScanSource(), media_type="movie",
                                         profiles=[(MEMBER, "tok")])
    assert _rows(db, MEMBER) == [("episode", eps[1], 1, None, 0)]


def test_a_failed_read_leaves_that_profiles_rows_and_the_others_run(db, monkeypatch):
    a = _movie(db, "m1")
    db.save_profile_watch_state(MEMBER, [("movie", a, 1, None, 0)])
    sources = {"bad": FakeMemberSource(fail=True), "good": FakeMemberSource([_item("movie", "m1")])}
    monkeypatch.setattr(member_watch, "member_source",
                        lambda token, **k: sources[token])
    out = member_watch.sync_member_watch_state(db, ScanSource(),
                                               profiles=[(MEMBER, "bad"), (8, "good")])
    assert out == {"profiles": 1, "rows": 1, "failed": 1}
    assert _rows(db, MEMBER) == [("movie", a, 1, None, 0)]
    assert len(_rows(db, 8)) == 1


def test_member_pass_is_plex_only(db, monkeypatch):
    class Jelly:
        server_name = "jellyfin"
    monkeypatch.setattr(member_watch, "member_source",
                        lambda *a, **k: pytest.fail("no member read off plex"))
    assert member_watch.sync_member_watch_state(db, Jelly(), profiles=[(MEMBER, "t")]) == \
        {"profiles": 0, "rows": 0, "failed": 0}


def test_scan_runs_the_member_pass_with_its_window(db, monkeypatch):
    import core.video.scanner as sc
    monkeypatch.setattr(sc, "INCREMENTAL_MIN_LIBRARY", 1)
    calls = []

    def fake_sync(db_, source, since=None, media_type="all"):
        calls.append((since, media_type))
    monkeypatch.setattr(member_watch, "sync_member_watch_state", fake_sync)

    class Src:
        server_name = "plex"

        def iter_movies(self, incremental=False, since=None):
            return iter([{"server_id": "m1", "title": "A", "file": {"relative_path": "a.mkv"}}])

        def iter_shows(self, incremental=False, since=None):
            return iter([])
    scanner = sc.VideoLibraryScanner(db)
    scanner.scan_sync(lambda: Src(), mode="full")
    scanner.scan_sync(lambda: Src(), mode="incremental")
    assert calls[0] == (None, "all")
    assert isinstance(calls[1][0], datetime) and calls[1][1] == "all"

    class JellySrc(Src):
        server_name = "jellyfin"
    scanner.scan_sync(lambda: JellySrc(), mode="full")
    assert len(calls) == 2                                   # never for jellyfin


# ── the Plex read itself ─────────────────────────────────────────────────────
def test_plex_iter_watch_state_asks_only_for_what_the_user_touched():
    from core.video.sources import PlexVideoSource

    class Item:
        def __init__(self, rk, vc=0, vo=0, lv=None):
            self.ratingKey, self.viewCount, self.viewOffset, self.lastViewedAt = rk, vc, vo, lv

    class Section:
        def __init__(self, kind, title, items):
            self.type, self.title, self.items, self.searches = kind, title, items, []

        def search(self, **kw):
            self.searches.append(kw)
            return self.items

    movies = Section("movie", "Movies", [Item(11, 1, 0, datetime(2026, 10, 1, 9, 30))])
    tv = Section("show", "TV Shows", [Item(22, 0, 90000)])

    class Server:
        class library:
            @staticmethod
            def sections():
                return [movies, tv]

    src = PlexVideoSource(Server(), movies_lib="Movies", tv_lib="TV Shows")
    rows = list(src.iter_watch_state())
    assert rows == [
        {"kind": "movie", "server_id": "11", "play_count": 1,
         "last_viewed_at": "2026-10-01 09:30:00", "view_offset_ms": 0},
        {"kind": "episode", "server_id": "22", "play_count": 0,
         "last_viewed_at": None, "view_offset_ms": 90000}]
    assert movies.searches == [{"filters": {"lastViewedAt>>": datetime(2000, 1, 1)},
                                "sort": "lastViewedAt:desc"}]
    assert tv.searches == [{"libtype": "episode", "filters": {"lastViewedAt>>": datetime(2000, 1, 1)},
                            "sort": "lastViewedAt:desc"}]
    since = datetime(2026, 10, 20)
    list(src.iter_watch_state(since=since, media_type="movie"))
    assert movies.searches[-1]["filters"] == {"lastViewedAt>>": since} and len(tv.searches) == 1


def test_a_profile_with_nothing_stored_is_read_in_full_even_on_an_incremental(db, monkeypatch):
    """just linked, or the first scan after this shipped: a delta alone would
    leave out everything the profile watched before the last scan."""
    a = _movie(db, "m1")
    src = FakeMemberSource([_item("movie", "m1")])
    monkeypatch.setattr(member_watch, "member_source", lambda *a_, **k: src)
    since = datetime(2026, 10, 20)
    member_watch.sync_member_watch_state(db, ScanSource(), since=since, profiles=[(MEMBER, "t")])
    assert src.calls == [(None, "movie"), (None, "show")]
    assert [i for _k, i, *_ in _rows(db, MEMBER)] == [a]
    src.calls = []
    member_watch.sync_member_watch_state(db, ScanSource(), since=since, profiles=[(MEMBER, "t")])
    # movies have rows now, so a delta; episodes still have none, so in full
    assert src.calls == [(since, "movie"), (None, "show")]
