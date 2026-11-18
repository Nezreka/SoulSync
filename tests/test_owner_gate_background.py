"""background wants follow the owner's permissions (#2166, piece C).

every path that adds a want for a profile that isn't at the screen asks one
OwnerGate (core/owner_gate.py) at add time: the airing auto-wishlist,
people and studio scans, collections, import lists, the youtube follow
scans, and the music artist and label watchlists. the contexts come from
tests/permissions_fixtures.py (members on built-in templates with changes, a
moderator, an admin, a profile whose map couldn't be built). video requests
land in a real VideoDatabase in a temp folder; nothing reaches a live db.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from core import permissions as P
from core.owner_gate import OwnerGate
from database.video_database import VideoDatabase
from tests.permissions_fixtures import admin_ctx, broken_ctx, member_ctx

OWNER = 7


def gate(ctx=None, pid=OWNER, **kw):
    return OwnerGate(pid, context=ctx if ctx is not None else member_ctx(profile_id=pid), **kw)


def ask_first(pid=OWNER, **more):
    return member_ctx(profile_id=pid, **{"movies.get": "ask", "tv.get": "ask", "music.get": "ask"}, **more)


@pytest.fixture
def vdb(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _requests(db, pid=OWNER):
    return sorted((r["kind"], r["tmdb_id"], r["season_number"], r["episode_number"], r["status"])
                  for r in db.list_video_requests(profile_id=pid, limit=1000))


# ── the gate ──────────────────────────────────────────────────────────────────

def test_admin_owner_never_reads_the_database():
    def boom(pid):
        raise AssertionError("profile 1 must not read permissions")
    g = OwnerGate(1, load_context=boom, rating_of=boom)
    for media in ("movie", "tv", "music"):
        assert g.want(media, tmdb_id=5, quality_id=3).add
    assert g.want("movie", quality_id=3).quality_profile_id == 3
    assert g.may_youtube()


def test_a_non_1_admin_gets_everything():
    g = gate(admin_ctx(9), pid=9)
    assert g.want("movie", tmdb_id=1).add and g.want("music", explicit=True).add


def test_a_map_that_cant_be_built_adds_nothing_and_says_so_once(caplog):
    g = gate(broken_ctx(OWNER))
    with caplog.at_level(logging.WARNING):
        assert all(g.want(m).skip for m in ("movie", "tv", "music", "movie"))
    assert not g.may_youtube()


def test_a_loader_that_fails_is_no_rights():
    def boom(pid):
        raise RuntimeError("db locked")
    assert OwnerGate(OWNER, load_context=boom).want("movie").skip


@pytest.mark.parametrize("changes,media,action", [
    ({}, "movie", "add"),
    ({"movies.access": False}, "movie", "skip"),
    ({"movies.access": False}, "tv", "add"),
    ({"tv.access": False}, "tv", "skip"),
    ({"movies.get": "none"}, "movie", "skip"),
    ({"movies.get": "ask"}, "movie", "request"),
    ({"tv.get": "ask"}, "tv", "request"),
    ({"music.get": "ask"}, "music", "request"),
    ({"music.get": "none"}, "music", "skip"),
    ({"music.access": False}, "music", "skip"),
    # a moderator approves others' asks; it never lets them skip their own
    ({"requests.moderate": True, "movies.get": "ask"}, "movie", "request"),
])
def test_access_and_get_modes(changes, media, action):
    assert gate(member_ctx(profile_id=OWNER, **changes)).want(media).action == action


def test_the_feature_part_is_checked_when_given():
    g = gate(member_ctx(profile_id=OWNER, **{"music.watchlist": False}))
    assert g.want("music", part="music.watchlist").skip
    assert g.want("music").add


def test_rating_caps_per_type():
    ratings = {("movie", 1): "R", ("movie", 2): "PG", ("show", 3): "TV-MA", ("movie", 4): None}
    looked = []

    def rating_of(kind, tid):
        looked.append((kind, tid))
        return ratings.get((kind, tid))
    g = gate(member_ctx(profile_id=OWNER, **{"movies.max_rating": "PG-13", "tv.max_rating": "any"}),
             rating_of=rating_of)
    assert g.want("movie", tmdb_id=1).skip              # R over PG-13
    assert g.want("movie", tmdb_id=2).add
    assert g.want("movie", tmdb_id=4).skip              # no rating under a cap stays out
    assert g.want("tv", tmdb_id=3).add                  # tv has no cap of its own
    assert ("show", 3) not in looked                    # and isn't looked up
    follows = gate(member_ctx(profile_id=OWNER, **{"movies.max_rating": "PG"}), rating_of=rating_of)
    assert follows.want("tv", tmdb_id=3).skip           # tv follows the movie cap
    free = gate(rating_of=lambda *a: pytest.fail("an uncapped owner never looks a rating up"))
    assert free.want("movie", tmdb_id=1).add


def test_hide_explicit_holds_for_music():
    g = gate(member_ctx(template="kids", profile_id=OWNER))
    assert g.want("music", explicit=True).skip
    assert g.want("music", explicit=False).action == "request"   # kids ask first
    assert gate().want("music", explicit=True).add


def test_quality_goes_through_the_resolver_and_falls_back_to_the_default():
    calls = []

    def resolver(profile, media, wanted, perms=None):
        calls.append((profile["id"], media, wanted, perms is not None))
        if wanted == 5:
            return P.QualityDecision(None, False, refused=True, error=P.QUALITY_NOT_ALLOWED)
        return P.QualityDecision(wanted if wanted is not None else 2, False)
    g = gate(resolver=resolver, load_profile=lambda pid: {"id": pid, "name": "Maya"})
    assert g.want("movie", quality_id=3).quality_profile_id == 3
    w = g.want("movie", quality_id=5)
    assert w.add and w.quality_profile_id == 2
    assert calls[-2:] == [(OWNER, "movie", 5, True), (OWNER, "movie", None, True)]


def test_a_quality_that_needs_an_ok_turns_a_download_into_a_request():
    g = gate(resolver=lambda p, m, w, perms=None: P.QualityDecision(9, True))
    w = g.want("tv")
    assert w.request and w.quality_profile_id == 9


def test_no_quality_at_all_adds_nothing():
    refuse = lambda p, m, w, perms=None: P.QualityDecision(None, False, refused=True)  # noqa: E731
    assert gate(resolver=refuse).want("movie", quality_id=4).skip


def test_a_source_quality_is_held_to_what_the_owner_may_pick():
    """the real resolver (#2168): a pick in the owner's set is kept, one
    outside it falls to their default, no pick uses their default."""
    g = gate()
    assert g.want("music", quality_id=4).quality_profile_id == 4
    assert g.want("movie", quality_id=4).quality_profile_id == 4        # open set
    limited = gate(member_ctx(profile_id=OWNER, **{"movies.quality": [3, 4], "movies.quality_default": 3}))
    assert limited.want("movie", quality_id=4).quality_profile_id == 4
    assert limited.want("movie", quality_id=9).quality_profile_id == 3  # not theirs: default
    assert limited.want("movie").quality_profile_id == 3


def test_rights_are_read_again_during_a_long_scan():
    now = [0.0]
    state = {"ctx": member_ctx(profile_id=OWNER)}
    loads = []

    def load(pid):
        loads.append(pid)
        return state["ctx"]
    g = OwnerGate(OWNER, load_context=load, clock=lambda: now[0])
    assert g.want("movie").add
    state["ctx"] = member_ctx(profile_id=OWNER, **{"movies.get": "none"})
    assert g.want("movie").add                          # inside the minute: the same read
    now[0] += OwnerGate.TTL
    assert g.want("movie").skip                         # the switch counts from here
    assert loads == [OWNER, OWNER]


def test_youtube_needs_the_part_and_a_download_mode():
    assert gate().may_youtube()
    assert not gate(member_ctx(profile_id=OWNER, **{"video.youtube": False})).may_youtube()
    assert not gate(ask_first()).may_youtube()
    assert gate(member_ctx(profile_id=OWNER, **{"movies.get": "ask"})).may_youtube()   # tv still downloads


# ── requests the gate files ───────────────────────────────────────────────────

def test_file_request_once_and_never_again_after_a_decline(vdb):
    g = gate(ask_first(), load_profile=lambda pid: {"id": pid, "name": "Maya"})
    events = []
    pub = lambda e, d: events.append((e, d))  # noqa: E731
    assert g.file_video_request(vdb, kind="movie", tmdb_id=10, title="Heat", publish=pub) == "filed"
    assert g.file_video_request(vdb, kind="movie", tmdb_id=10, title="Heat", publish=pub) == "asked"
    # the owner's ask, whoever's job filed it (#2166 follow-up 25)
    assert events == [("video_request_created", {"kind": "movie", "title": "Heat", "requester": "Maya",
                                                 "_event_owner": 7})]
    rid = vdb.list_video_requests(profile_id=OWNER)[0]["id"]
    vdb.resolve_video_request(rid, status="denied", resolved_by=1)
    fresh = gate(ask_first())
    assert fresh.file_video_request(vdb, kind="movie", tmdb_id=10, title="Heat", publish=pub) == "asked"
    assert _requests(vdb) == [("movie", 10, None, None, "denied")]


def test_file_request_holds_the_request_limit(vdb):
    g = gate(ask_first(**{"requests.limit": {"n": 2, "days": 7}}))
    out = [g.file_video_request(vdb, kind="movie", tmdb_id=t, title="T%d" % t, publish=lambda *a: None)
           for t in (1, 2, 3)]
    assert out == ["filed", "filed", "over_limit"]
    assert len(_requests(vdb)) == 2


def test_episode_requests_are_per_episode(vdb):
    g = gate(ask_first())
    for en in (1, 2, 1):
        g.file_video_request(vdb, kind="episode", tmdb_id=50, title="S", season_number=1,
                             episode_number=en, publish=lambda *a: None)
    assert _requests(vdb) == [("episode", 50, 1, 1, "pending"), ("episode", 50, 1, 2, "pending")]


def test_an_unreadable_request_list_files_nothing():
    class Broken:
        def list_video_requests(self, **kw):
            raise RuntimeError("locked")
    assert gate(ask_first()).file_video_request(Broken(), kind="movie", tmdb_id=1, title="x") == "asked"


# ── people and studio scans ──────────────────────────────────────────────────

def _scan(handler_name, ctx, vdb, *, wished=None):
    import importlib
    from tests.test_video_scan_watchlist_people import _credit
    mod = importlib.import_module("core.automation.handlers.video_scan_watchlist_%s" % handler_name)
    fn = getattr(mod, "auto_video_scan_watchlist_%s" % handler_name)
    adds = []

    def add_movie(tid, title, *, year, poster_url, status, detail_json, **kw):
        adds.append((tid, status, kw.get("quality_profile_id")))
        return True
    items = [{"tmdb_id": 500, "title": "Followed", "date_added": "1980-01-01"}]
    films = [dict(_credit(3, "Released", date="1990-01-01"), vote_count=5000),
             dict(_credit(4, "Coming", date="2026-12-01"), vote_count=5000)]
    seams = {"people": dict(fetch_people=lambda: items, fetch_credits=lambda pid: films),
             "studios": dict(fetch_studios=lambda: items, fetch_films=lambda sid: films)}[handler_name]
    deps = SimpleNamespace(update_progress=lambda *a, **k: None)
    res = fn({"_automation_id": "a"}, deps, fetch_detail=lambda tid: None, owned_ids=lambda: set(),
             ignored_ids=lambda: [], wishlisted_status=lambda: dict(wished or {}), add_movie=add_movie,
             today_fn=lambda: "2026-06-25", gate=gate(ctx, rating_of=lambda *a: "PG"),
             request_db=lambda: vdb, **seams)
    return res, adds


@pytest.mark.parametrize("which", ["people", "studios"])
def test_scan_downloader_adds(which, vdb):
    res, adds = _scan(which, member_ctx(profile_id=OWNER), vdb)
    assert sorted(adds) == [(3, "wanted", None), (4, "monitored", None)]
    assert res["requested"] == 0 and _requests(vdb) == []


@pytest.mark.parametrize("which", ["people", "studios"])
def test_scan_ask_first_owner_files_requests_instead(which, vdb):
    res, adds = _scan(which, ask_first(), vdb)
    assert adds == []
    assert res["requested"] == 2 and res["movies_added"] == 0
    assert _requests(vdb) == [("movie", 3, None, None, "pending"), ("movie", 4, None, None, "pending")]
    # the next scan doesn't ask twice
    res2, _ = _scan(which, ask_first(), vdb)
    assert res2["requested"] == 0 and len(_requests(vdb)) == 2


@pytest.mark.parametrize("which", ["people", "studios"])
@pytest.mark.parametrize("ctx", [member_ctx(profile_id=OWNER, **{"movies.get": "none"}),
                                 member_ctx(profile_id=OWNER, **{"movies.access": False}),
                                 member_ctx(profile_id=OWNER, **{"video.watchlist": False}),
                                 member_ctx(profile_id=OWNER, **{"movies.max_rating": "G"}),
                                 broken_ctx(OWNER)])
def test_scan_owner_who_cant_gets_nothing(which, ctx, vdb):
    res, adds = _scan(which, ctx, vdb)
    assert adds == [] and _requests(vdb) == [] and res["skipped"] == 2


@pytest.mark.parametrize("which", ["people", "studios"])
def test_scan_promotion_waits_for_download_rights(which, vdb):
    res, adds = _scan(which, ask_first(), vdb, wished={3: "monitored"})
    assert all(a[0] != 3 for a in adds) and res["promoted"] == 0
    res, adds = _scan(which, member_ctx(profile_id=OWNER), vdb, wished={3: "monitored"})
    assert (3, "wanted", None) in adds and res["promoted"] == 1


def test_scan_quality_reaches_the_add(vdb, monkeypatch):
    monkeypatch.setattr(P, "resolve_request_quality",
                        lambda profile, media, wanted, perms=None: P.QualityDecision(6, False))
    _res, adds = _scan("people", member_ctx(profile_id=OWNER), vdb)
    assert {a[2] for a in adds} == {6}


# ── airing auto-wishlist ────────────────────────────────────────────────────

def _airing(ctx, vdb, rating="TV-PG"):
    from core.automation.handlers.video_auto_wishlist_airing import auto_video_add_airing_episodes
    from tests.test_video_auto_wishlist_airing import _Deps, _row
    added, offered = [], set()
    res = auto_video_add_airing_episodes(
        {"_automation_id": "a", "prune_ended": False}, _Deps(),
        fetch_airing=lambda s, e: [_row(1, "Show", 1, 1), _row(1, "Show", 1, 2)],
        add_episodes=lambda tid, title, eps, *a, **k: added.append((tid, len(eps))) or len(eps),
        today_fn=lambda: "2026-06-21", season_meta=lambda *a: None, get_bookmark=lambda: None,
        get_offered=lambda keys: {k for k in keys if k in offered}, mark_offered=offered.update,
        is_seeded=lambda: True, set_seeded=lambda: None, unowned_follows=lambda: [],
        tmdb_airings=lambda tid, s, e: {"episodes": []},
        gate=gate(ctx, rating_of=lambda *a: rating), request_db=lambda: vdb)
    return res, added, offered


def test_airing_downloader_adds():
    res, added, offered = _airing(member_ctx(profile_id=OWNER), None)
    assert added == [(1, 2)] and offered == {(1, 1, 1), (1, 1, 2)}


def test_airing_ask_first_files_one_request_per_episode(vdb):
    res, added, offered = _airing(ask_first(), vdb)
    assert added == [] and res["requested"] == 2
    assert _requests(vdb) == [("episode", 1, 1, 1, "pending"), ("episode", 1, 1, 2, "pending")]
    assert vdb.list_video_requests(profile_id=OWNER)[0]["title"].startswith("Show S01E0")
    assert offered == {(1, 1, 1), (1, 1, 2)}


def test_airing_an_approved_show_request_covers_its_new_episodes(vdb):
    rid, _ = vdb.add_video_request(profile_id=OWNER, requester_name="Maya", kind="show", tmdb_id=1, title="Show")
    vdb.resolve_video_request(rid, status="approved", resolved_by=1)
    res, added, _ = _airing(ask_first(), vdb)
    assert added == [(1, 2)] and res["requested"] == 0


def test_airing_over_the_limit_leaves_no_receipt(vdb):
    res, added, offered = _airing(ask_first(**{"requests.limit": {"n": 1, "days": 7}}), vdb)
    assert res["requested"] == 1 and offered == {(1, 1, 1)}


@pytest.mark.parametrize("ctx,rating", [(member_ctx(profile_id=OWNER, **{"tv.get": "none"}), "TV-PG"),
                                        (member_ctx(profile_id=OWNER, **{"tv.access": False}), "TV-PG"),
                                        (member_ctx(profile_id=OWNER, **{"tv.max_rating": "PG"}), "TV-MA"),
                                        (broken_ctx(OWNER), "TV-PG")])
def test_airing_owner_who_cant_gets_nothing_and_no_receipt(ctx, rating, vdb):
    res, added, offered = _airing(ctx, vdb, rating)
    assert added == [] and offered == set() and _requests(vdb) == [] and res["skipped"] == 2


# ── collections ───────────────────────────────────────────────────────────────

_LIST = {"id": 3, "kind": "list", "media_type": "movie", "wishlist_missing": True}
_MISSING = [{"tmdb_id": 31, "title": "One"}, {"tmdb_id": 32, "title": "Two"}]


def test_collections_downloader_adds(vdb):
    from core.video.collections.sync import wishlist_missing_movies
    assert wishlist_missing_movies(vdb, _LIST, _MISSING, gate=gate(rating_of=lambda *a: "PG")) == 2


def test_collections_ask_first_requests_and_counts_them_fed(vdb):
    from core.video.collections.sync import _fed_ids, wishlist_missing_movies
    assert wishlist_missing_movies(vdb, _LIST, _MISSING, gate=gate(ask_first())) == 0
    assert _requests(vdb) == [("movie", 31, None, None, "pending"), ("movie", 32, None, None, "pending")]
    assert _fed_ids(vdb, 3) == {31, 32}
    assert vdb.wishlisted_movie_status(profile_id=OWNER) == {}


def test_collections_cant_leaves_titles_unfed_for_later(vdb):
    from core.video.collections.sync import _fed_ids, wishlist_missing_movies
    g = gate(member_ctx(profile_id=OWNER, **{"movies.get": "none"}))
    assert wishlist_missing_movies(vdb, _LIST, _MISSING, gate=g) == 0
    assert _fed_ids(vdb, 3) == set() and _requests(vdb) == []
    # rights come back: the next sync feeds them
    assert wishlist_missing_movies(vdb, _LIST, _MISSING, gate=gate()) == 2


def test_collections_rating_cap(vdb):
    from core.video.collections.sync import wishlist_missing_movies
    ratings = {31: "PG", 32: "R"}
    g = gate(member_ctx(profile_id=OWNER, **{"movies.max_rating": "PG-13"}),
             rating_of=lambda kind, tid: ratings[tid])
    assert wishlist_missing_movies(vdb, _LIST, _MISSING, gate=g) == 1
    assert set(vdb.wishlisted_movie_status(profile_id=1)) == {31}


def test_collection_shows_ask_first_request_instead_of_follow(vdb):
    from core.video.collections.sync import watchlist_missing_shows

    class Eng:
        def tmdb_full_detail(self, kind, tid):
            return {"status": "Returning Series"}
    d = {"id": 4, "kind": "list", "media_type": "show", "wishlist_missing": True}
    n = watchlist_missing_shows(vdb, d, [{"tmdb_id": 41, "title": "S"}], engine=Eng(), gate=gate(ask_first()))
    assert n == 0 and _requests(vdb) == [("show", 41, None, None, "pending")]
    n = watchlist_missing_shows(vdb, d, [{"tmdb_id": 42, "title": "T"}], engine=Eng(),
                                gate=gate(member_ctx(profile_id=OWNER, **{"tv.access": False})))
    assert n == 0 and len(_requests(vdb)) == 1


# ── import lists ──────────────────────────────────────────────────────────────

@pytest.fixture
def lists_db(vdb):
    import api.video as videoapi
    import core.video.import_lists as il
    videoapi._video_db = vdb
    il.save_list(vdb, {"name": "L", "source": "tmdb_chart", "ref": "trending_movies", "media": "both",
                       "monitor": "future"})
    yield vdb
    videoapi._video_db = None


_FEED = [{"kind": "movie", "tmdb_id": 61, "title": "M", "year": 2020},
         {"kind": "show", "tmdb_id": 62, "title": "S", "year": 2020}]


def test_import_list_downloader_adds(lists_db):
    import core.video.import_lists as il
    out = il.sync(fetch=lambda e: _FEED, gate=gate(rating_of=lambda *a: "PG"))
    assert out["added_movies"] == 1 and out["added_shows"] == 1 and out["requested"] == 0


def test_import_list_ask_first_files_requests(lists_db):
    import core.video.import_lists as il
    out = il.sync(fetch=lambda e: _FEED, gate=gate(ask_first()))
    assert out["added_movies"] == 0 and out["added_shows"] == 0 and out["requested"] == 2
    assert _requests(lists_db) == [("movie", 61, None, None, "pending"), ("show", 62, None, None, "pending")]
    again = il.sync(fetch=lambda e: _FEED, gate=gate(ask_first()))
    assert again["requested"] == 0 and len(_requests(lists_db)) == 2


def test_import_list_owner_who_cant_gets_nothing_until_they_can(lists_db):
    import core.video.import_lists as il
    out = il.sync(fetch=lambda e: _FEED, gate=gate(member_ctx(profile_id=OWNER, **{"movies.get": "none",
                                                                                     "tv.access": False})))
    assert out["refused"] == 2 and out["added_movies"] == 0 and out["added_shows"] == 0
    # not marked seen: once the owner may, the next tick adds them
    out = il.sync(fetch=lambda e: _FEED, gate=gate(rating_of=lambda *a: "PG"))
    assert out["added_movies"] == 1 and out["added_shows"] == 1


def test_import_list_disallowed_list_quality_falls_back_to_the_owners_default(lists_db, monkeypatch):
    import core.video.import_lists as il
    il.save_list(lists_db, {"name": "Q", "source": "tmdb_chart", "ref": "x", "media": "movie",
                            "quality_profile_id": 5})
    seen = []

    def resolver(profile, media, wanted, perms=None):
        seen.append(wanted)
        if wanted == 5:
            return P.QualityDecision(None, False, refused=True)
        return P.QualityDecision(None, False)
    out = il.sync(fetch=lambda e: [_FEED[0]] if e["name"] == "Q" else [],
                  gate=gate(resolver=resolver, rating_of=lambda *a: "PG"))
    assert seen == [5, None] and out["added_movies"] == 1


# ── youtube follow scans ────────────────────────────────────────────────────

@pytest.mark.parametrize("module,fetch", [("video_scan_watchlist_channels", "_default_fetch_channels"),
                                          ("video_scan_watchlist_playlists", "_default_fetch_playlists")])
def test_youtube_scans_ask_the_owners_video_rights(module, fetch, monkeypatch, vdb):
    import importlib

    import api.video as videoapi
    import core.owner_gate as og
    mod = importlib.import_module("core.automation.handlers." + module)
    videoapi._video_db = vdb
    try:
        vdb.add_channel_to_watchlist({"youtube_id": "UC1", "title": "C"}, profile_id=OWNER)
        vdb.add_playlist_to_watchlist({"playlist_id": "PL1", "title": "L"}, profile_id=OWNER)
        monkeypatch.setattr(mod, "_profile", lambda: OWNER)
        state = {}
        monkeypatch.setattr(og, "_default_context", lambda pid: state["ctx"])
        state["ctx"] = member_ctx(profile_id=OWNER)
        assert len(getattr(mod, fetch)()) == 1
        for ctx in (ask_first(), member_ctx(profile_id=OWNER, **{"video.youtube": False}), broken_ctx(OWNER)):
            state["ctx"] = ctx
            assert getattr(mod, fetch)() == []
    finally:
        videoapi._video_db = None


# ── music: artist watchlist and labels ───────────────────────────────────────

class _MusicDB:
    def __init__(self):
        self.calls = []

    def add_to_wishlist(self, **kw):
        self.calls.append(kw)
        return True


def _music_add(ctx, explicit=False, quality=None, monkeypatch=None):
    from core.watchlist_scanner import WatchlistScanner
    from tests.sync.test_wishlist_quality_profile_passthrough import _watchlist_artist
    scanner = WatchlistScanner(spotify_client=object())
    db = _MusicDB()
    monkeypatch.setattr(type(scanner), "database", property(lambda _self: db))
    scanner.__dict__["_owner_gates"] = {OWNER: gate(ctx)}
    artist = _watchlist_artist(profile_id=OWNER, quality_profile_id=quality)
    ok = scanner.add_track_to_wishlist({"id": "t", "name": "T", "explicit": explicit, "artists": []},
                                       {"name": "A", "id": "a", "release_date": "", "images": [],
                                        "album_type": "album", "total_tracks": 1, "artists": []}, artist)
    return ok, db.calls


@pytest.mark.parametrize("ctx,explicit,added", [
    (member_ctx(profile_id=OWNER), True, True),
    (ask_first(), False, True),                         # the row is the request
    (member_ctx(profile_id=OWNER, **{"music.get": "none"}), False, False),
    (member_ctx(profile_id=OWNER, **{"music.access": False}), False, False),
    (member_ctx(profile_id=OWNER, **{"music.watchlist": False}), False, False),
    (member_ctx(profile_id=OWNER, **{"music.hide_explicit": True}), True, False),
    (member_ctx(profile_id=OWNER, **{"music.hide_explicit": True}), False, True),
    (broken_ctx(OWNER), False, False),
])
def test_music_watchlist_new_releases(ctx, explicit, added, monkeypatch):
    ok, calls = _music_add(ctx, explicit=explicit, monkeypatch=monkeypatch)
    assert ok is added and bool(calls) is added
    if added:
        assert calls[0]["profile_id"] == OWNER


def test_music_watchlist_quality_is_resolved_for_the_owner(monkeypatch):
    monkeypatch.setattr(P, "resolve_request_quality", lambda profile, media, wanted, perms=None: (
        P.QualityDecision(None, False, refused=True) if wanted == 4 else P.QualityDecision(2, False)))
    ok, calls = _music_add(member_ctx(profile_id=OWNER), quality=4, monkeypatch=monkeypatch)
    assert ok and calls[0]["quality_profile_id"] == 2


def test_music_scanner_reads_rights_from_its_own_database(monkeypatch):
    """the scanner's gate reads permissions through context_for over the
    scanner's database, not a global one."""
    from core.watchlist_scanner import WatchlistScanner
    scanner = WatchlistScanner(spotify_client=object())
    db = object()
    monkeypatch.setattr(type(scanner), "database", property(lambda _self: db))
    seen = []
    import core.permission_store as ps
    monkeypatch.setattr(ps, "context_for", lambda d, pid: seen.append((d, pid)) or broken_ctx(pid))
    g = scanner._owner_gate(OWNER)
    assert g is scanner._owner_gate(OWNER)
    assert g.want("music").skip and seen == [(db, OWNER)]


def test_label_scan_asks_the_follower(monkeypatch):
    import core.automation.handlers.scan_watchlist_labels as sl
    db = _MusicDB()
    monkeypatch.setattr(sl, "resolve_album_for_release", lambda gd, artist, album: (
        {"name": album, "artists": [{"name": artist}], "images": []},
        [{"id": "t1", "name": "Clean", "explicit": False}, {"id": "t2", "name": "Dirty", "explicit": True}]))
    seams = sl.build_default_seams(database=db, get_deezer=lambda: None, profile_id=OWNER,
                                   gate=gate(member_ctx(profile_id=OWNER, **{"music.hide_explicit": True})))
    assert seams["add_release"]({"artist": "A", "album": "B"}, {}) is True
    assert [c["spotify_track_data"]["id"] for c in db.calls] == ["t1"]
    db.calls.clear()
    seams = sl.build_default_seams(database=db, get_deezer=lambda: None, profile_id=OWNER,
                                   gate=gate(member_ctx(profile_id=OWNER, **{"music.get": "none"})))
    assert seams["add_release"]({"artist": "A", "album": "B"}, {}) is False and db.calls == []
