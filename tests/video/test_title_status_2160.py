"""where a movie or show stands, on every video poster and detail page (#2160).

a member browsing the video side couldn't tell whether a title was already in
the library, partly there, asked for, approved, downloading or failed. the
seerr api (#2151) already worked that out per title; the rule now lives in
core/video/title_status.py and both the seerr layer and the pages read it.

these tests use a real video db in a temp folder. no network, no live db.
"""

from __future__ import annotations

import pytest
from flask import Flask, g, request

from core.seerr_compat import shapes
from core.video import title_status as ts
from database.video_database import VideoDatabase

TODAY = "2026-06-01"
OWNED_MOVIE, WISHED_MOVIE, ASKED_MOVIE, NEW_MOVIE, GRABBING_MOVIE, FAILED_MOVIE = 101, 102, 103, 104, 105, 106
# a grab started by hand from the preview page: no wishlist row, still coming
HAND_GRAB_MOVIE = 107
PARTIAL_SHOW, NEW_SHOW, FOLLOWED_SHOW = 201, 202, 203


def _sql(db, query, args=()):
    conn = db._get_connection()
    try:
        cur = conn.execute(query, args)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _seed(db):
    _sql(db, "INSERT INTO movies (server_source, server_id, tmdb_id, title, has_file) "
             "VALUES ('plex', 'm1', ?, 'Owned', 1)", (OWNED_MOVIE,))
    db.add_movie_to_wishlist(WISHED_MOVIE, "Wished", profile_id=1)
    db.add_video_request(profile_id=5, requester_name="Kid", kind="movie",
                         tmdb_id=ASKED_MOVIE, title="Asked")
    db.add_movie_to_wishlist(GRABBING_MOVIE, "Grabbing", profile_id=1)
    dl = db.add_video_download({"kind": "movie", "title": "Grabbing", "media_id": str(GRABBING_MOVIE),
                                "media_source": "tmdb", "status": "downloading"})
    db.update_video_download(dl, progress=42.4)   # the monitor ticks it like this
    db.add_video_download({"kind": "movie", "title": "Hand", "media_id": str(HAND_GRAB_MOVIE),
                           "media_source": "tmdb", "status": "searching"})
    db.add_movie_to_wishlist(FAILED_MOVIE, "Failed", profile_id=1)
    db.add_video_download({"kind": "movie", "title": "Failed", "media_id": str(FAILED_MOVIE),
                           "media_source": "tmdb", "status": "failed"})
    show = _sql(db, "INSERT INTO shows (server_source, server_id, tmdb_id, title) "
                    "VALUES ('plex', 's1', ?, 'Partial')", (PARTIAL_SHOW,))
    for sn in (1, 2):
        season = _sql(db, "INSERT INTO seasons (show_id, season_number) VALUES (?, ?)", (show, sn))
        for en in (1, 2):
            _sql(db, "INSERT INTO episodes (show_id, season_id, server_source, season_number, "
                     "episode_number, air_date, has_file) VALUES (?, ?, 'plex', ?, ?, '2020-01-01', ?)",
                 (show, season, sn, en, 1 if sn == 1 else 0))
    db.add_episodes_to_wishlist(PARTIAL_SHOW, "Partial", [{"season_number": 2, "episode_number": 1}],
                                profile_id=1)
    db.add_to_watchlist("show", FOLLOWED_SHOW, "Followed", profile_id=1)


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    _seed(d)
    return d


def _store(db):
    return ts.TitleStatusStore(lambda: db._get_connection())


# ---- the rule --------------------------------------------------------------------

def test_rule_order_library_then_grab_then_wanted_then_asked():
    """#2160: one status per title, library first."""
    assert ts.title_status("movie", owned=True) == ts.AVAILABLE
    assert ts.title_status("show", owned=True, coverage={1: (2, 1)}) == ts.PARTIAL
    assert ts.title_status("movie", owned=False, state={"wished": True, "downloading": True}) == ts.DOWNLOADING
    assert ts.title_status("movie", owned=False, state={"wished": True, "searching": True}) == ts.SEARCHING
    assert ts.title_status("movie", owned=False, state={"wished": True, "failed": True}) == ts.FAILED
    assert ts.title_status("movie", owned=False, state={"approved": True, "pending": True}) == ts.PROCESSING
    assert ts.title_status("movie", owned=False, state={"pending": True}) == ts.PENDING
    assert ts.title_status("movie", owned=False) is None


def test_a_failed_grab_nobody_wants_any_more_is_just_history():
    """#2160: failed only shows on something still wanted."""
    assert ts.title_status("movie", owned=False, state={"failed": True}) is None


def test_seerr_codes_did_not_move():
    """#2151 answers exactly as before the rule moved (#2160)."""
    assert shapes.media_status("show", owned=True, coverage={1: (2, 2)}) == shapes.STATUS_AVAILABLE
    assert shapes.media_status("show", owned=True, coverage={1: (2, 1)}) == shapes.STATUS_PARTIAL
    assert shapes.media_status("show", owned=True, coverage={1: (2, 0)},
                               state={"pending": True}) == shapes.STATUS_PENDING
    assert shapes.media_status("movie", owned=False, state={"approved": True}) == shapes.STATUS_PROCESSING
    assert shapes.media_status("movie", owned=False, state={"wished": True, "failed": True}) \
        == shapes.STATUS_PROCESSING
    assert shapes.media_status("movie", owned=False) is None


def test_duplicate_asks_are_blocked_but_partial_shows_and_failures_stay_open():
    """#2160: asking for what's already coming is a duplicate."""
    assert ts.blocks_request("movie", ts.PROCESSING, {"wished": True})
    assert ts.blocks_request("movie", ts.DOWNLOADING, {})
    assert ts.blocks_request("show", ts.PROCESSING, {"approved": True})
    assert not ts.blocks_request("show", ts.PROCESSING, {"followed": True})
    assert not ts.blocks_request("show", ts.PARTIAL, {"wished": True})
    assert not ts.blocks_request("movie", ts.FAILED, {"wished": True})
    assert not ts.blocks_request("movie", ts.PENDING, {"pending": True})


def test_request_state_per_viewer():
    rows = [{"profile_id": 5, "status": "pending"}]
    assert ts.request_state("movie", ts.PENDING, {"pending": True}, rows, 5) == "mine"
    assert ts.request_state("movie", ts.PENDING, {"pending": True}, rows, 6) == "others"
    assert ts.request_state("movie", ts.PROCESSING, {"wished": True}, [], 6) == "on_the_way"
    assert ts.request_state("movie", ts.AVAILABLE, {}, [], 6) == "available"
    assert ts.request_state("show", ts.PARTIAL, {}, [], 6) == "open"


def test_members_never_see_who_else_asked():
    """#2160: who asked is shown subject to permission (admins only)."""
    rows = [{"profile_id": 5, "requester_name": "Kid", "status": "pending"},
            {"profile_id": 6, "requester_name": "Tom", "status": "pending"}]
    member = ts.public_requests(rows, 5, is_admin=False)
    assert [(r["mine"], r["who"]) for r in member] == [(True, None), (False, None)]
    admin = ts.public_requests(rows, 1, is_admin=True)
    assert [r["who"] for r in admin] == ["Kid", "Tom"]


# ---- the store -------------------------------------------------------------------

def test_store_reads_grabs_and_failures(db):
    """#2160: download state follows the queue (searching, downloading, failed)."""
    st = _store(db).title_states("movie", [GRABBING_MOVIE, FAILED_MOVIE, NEW_MOVIE])
    assert st[GRABBING_MOVIE]["downloading"] and st[GRABBING_MOVIE]["progress"] == 42.4
    assert st[FAILED_MOVIE]["failed"] and st[FAILED_MOVIE]["wished"]
    assert NEW_MOVIE not in st
    assert _store(db).title_states("movie", [HAND_GRAB_MOVIE])[HAND_GRAB_MOVIE] == {"searching": True}


def test_a_later_success_clears_an_older_failure(db):
    db.add_video_download({"kind": "movie", "title": "Failed", "media_id": str(FAILED_MOVIE),
                           "media_source": "tmdb", "status": "completed"})
    assert not _store(db).title_states("movie", [FAILED_MOVIE])[FAILED_MOVIE].get("failed")


def test_batch_puts_every_status_on_a_page(db):
    got = ts.batch(db, _store(db), "movie",
                   [OWNED_MOVIE, WISHED_MOVIE, ASKED_MOVIE, NEW_MOVIE, GRABBING_MOVIE, FAILED_MOVIE],
                   server="plex", today=TODAY)
    assert {k: v["status"] for k, v in got.items()} == {
        OWNED_MOVIE: ts.AVAILABLE, WISHED_MOVIE: ts.PROCESSING, ASKED_MOVIE: ts.PENDING,
        NEW_MOVIE: None, GRABBING_MOVIE: ts.DOWNLOADING, FAILED_MOVIE: ts.FAILED}
    shows = ts.batch(db, _store(db), "show", [PARTIAL_SHOW, NEW_SHOW, FOLLOWED_SHOW],
                     server="plex", today=TODAY)
    assert shows[PARTIAL_SHOW]["status"] == ts.PARTIAL
    assert shows[FOLLOWED_SHOW]["status"] == ts.PROCESSING
    assert shows[NEW_SHOW]["status"] is None


def test_season_states_say_what_each_season_has(db):
    store = _store(db)
    cov = store.show_coverage([PARTIAL_SHOW], TODAY, "plex")[PARTIAL_SHOW]
    seasons = ts.season_states(cov, store.wished_seasons(PARTIAL_SHOW))
    assert [(s["season"], s["status"]) for s in seasons] == [(1, ts.AVAILABLE), (2, ts.PROCESSING)]


# ---- the api ---------------------------------------------------------------------

@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import api.video.requests as vreq
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    monkeypatch.setattr(vreq, "_tmdb_lookup", lambda kind, tid: None)
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _persona():
        g.profile_id = int(request.headers.get("X-Test-Profile", 1))
        g.profile_name = {1: "Admin", 5: "Kid", 6: "Tom"}.get(g.profile_id, "P")
        g.is_admin = g.profile_id == 1
        g.can_download = g.profile_id == 1
        g.allowed_sides = "both"
        g.request_limit = 1 if g.profile_id == 6 else 0
        g.request_limit_days = 7

    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def _as(pid):
    return {"X-Test-Profile": str(pid)}


def test_one_post_answers_a_page_of_posters(client):
    """#2160: a page of posters is one request, and a member can read it."""
    out = client.post("/api/video/title-status", headers=_as(6), json={
        "movies": [OWNED_MOVIE, WISHED_MOVIE, ASKED_MOVIE, NEW_MOVIE, GRABBING_MOVIE],
        "shows": [PARTIAL_SHOW, NEW_SHOW]}).get_json()
    st = out["statuses"]
    assert st["movie:%d" % OWNED_MOVIE]["status"] == "available"
    assert st["movie:%d" % WISHED_MOVIE] == {"status": "processing", "reason": "wished", "progress": None,
                                             "mine": False, "wished": False, "request": "on_the_way"}
    assert st["movie:%d" % ASKED_MOVIE]["status"] == "pending"
    assert st["movie:%d" % ASKED_MOVIE]["request"] == "others"
    assert st["movie:%d" % GRABBING_MOVIE]["progress"] == 42.4
    assert st["show:%d" % PARTIAL_SHOW]["status"] == "partial"
    assert "movie:%d" % NEW_MOVIE not in st and "show:%d" % NEW_SHOW not in st


def test_the_requester_sees_their_own_ask(client):
    st = client.post("/api/video/title-status", headers=_as(5),
                     json={"movies": [ASKED_MOVIE]}).get_json()["statuses"]
    assert st["movie:%d" % ASKED_MOVIE]["mine"] is True
    assert st["movie:%d" % ASKED_MOVIE]["request"] == "mine"


def test_detail_block_names_the_requester_for_admins_only(client):
    """#2160: who asked, subject to permission."""
    admin = client.get("/api/video/title-status/movie/%d" % ASKED_MOVIE, headers=_as(1)).get_json()
    assert admin["status"] == "pending" and admin["requests"][0]["who"] == "Kid"
    member = client.get("/api/video/title-status/movie/%d" % ASKED_MOVIE, headers=_as(6)).get_json()
    assert member["requests"][0]["who"] is None and member["requests"][0]["mine"] is False
    assert member["request"] == "others"


def test_detail_block_carries_seasons_for_a_partial_show(client):
    out = client.get("/api/video/title-status/show/%d" % PARTIAL_SHOW, headers=_as(6)).get_json()
    assert out["status"] == "partial"
    assert out["episodes"] == {"aired": 4, "owned": 2}
    assert [(s["season"], s["status"]) for s in out["seasons"]] == [(1, "available"), (2, "processing")]


def test_asking_for_what_is_already_coming_is_refused(client, db):
    """#2160: duplicate prevention server side, not only a hidden button."""
    r = client.post("/api/video/requests", headers=_as(5),
                    json={"kind": "movie", "tmdb_id": WISHED_MOVIE, "title": "Wished"})
    assert r.status_code == 409 and r.get_json()["on_the_way"] is True
    assert not [x for x in db.list_video_requests(status="pending") if x["tmdb_id"] == WISHED_MOVIE]


def test_joining_someone_elses_ask_costs_no_quota(client, db):
    """#2160: a second member asking for a waiting title joins that ask."""
    db.add_video_request(profile_id=6, requester_name="Tom", kind="movie", tmdb_id=999, title="Spent")
    out = client.post("/api/video/requests", headers=_as(6),
                      json={"kind": "movie", "tmdb_id": ASKED_MOVIE, "title": "Asked"}).get_json()
    assert out["success"] and out["joined"] is True
    # a fresh title still hits the quota
    r = client.post("/api/video/requests", headers=_as(6),
                    json={"kind": "movie", "tmdb_id": NEW_MOVIE, "title": "New"})
    assert r.status_code == 429


def test_the_page_and_seerr_can_never_disagree(db):
    """#2160: the seerr api and the posters read one rule."""
    from core.seerr_compat.service import SeerrCompat
    store = _store(db)
    svc = SeerrCompat(engine=lambda: None, video_db=lambda: db, store=store,
                      get_profile=lambda pid: None, list_profiles=lambda: [], jellyfin_users=lambda: [],
                      fallback_profile_id=lambda: 1, server=lambda: "plex",
                      title_allowed=lambda *a: True, publish=lambda *a: None,
                      show_wanted=lambda *a: None, today=lambda: TODAY)
    items = [{"kind": "movie", "tmdb_id": t} for t in
             (OWNED_MOVIE, WISHED_MOVIE, ASKED_MOVIE, NEW_MOVIE, GRABBING_MOVIE, FAILED_MOVIE,
              HAND_GRAB_MOVIE)]
    items += [{"kind": "show", "tmdb_id": t} for t in (PARTIAL_SHOW, NEW_SHOW, FOLLOWED_SHOW)]
    infos = svc._infos(items)
    for it in items:
        page = ts.batch(db, store, it["kind"], [it["tmdb_id"]], server="plex",
                        today=TODAY)[it["tmdb_id"]]["status"]
        info = infos[(it["kind"], it["tmdb_id"])]
        assert (info["status"] if info else None) == ts.seerr_code(page), it
