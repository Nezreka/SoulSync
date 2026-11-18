"""server activity: posters that always find a picture, and a drawer that loads
fast.

the proxy walks an item's family when its own picture is missing (episode,
season, show; track, album, artist; movie poster, then art), asks plex's photo
transcoder for the size drawn, keeps what it got on disk with an etag, and
never lets more than a few requests at a time reach the server. history and
stats keep their answers while the play log stands still; the live view
answers from the shared snapshot and polls plex and jellyfin side by side.
"""

from __future__ import annotations

import threading
import time

import pytest
import requests

from core.server_activity import history, images, live, stats, store
from core.server_activity import plex as px
from tests.sa_helpers import FakePlex, FakeResponse, jf_sessions, sa_env  # noqa: F401 - fixture

JPEG = b"\xff\xd8poster"
PLEX = {"base_url": "http://plex.test:32400", "token": "secret-plex-token"}
JF = {"base_url": "http://jf.test:8096", "api_key": "secret-jf-key"}


def _upstream(monkeypatch, answers):
    """requests.get answered from {path or item id: reply}; every call logged.
    a reply is bytes (an image), an int (that status, no body), "empty" (a 200
    with no body), "json" (a 200 that is not an image) or an exception."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        params = dict(params or {})
        calls.append((url, params))
        key = params.get("url") if url.endswith("/photo/:/transcode") else url.split(":32400")[-1]
        if "/Items/" in url:
            key = "jf:" + url.split("/Items/")[1]
        reply = answers.get(key, 404)
        if isinstance(reply, BaseException):
            raise reply
        if reply == "empty":
            return FakeResponse(200, None, ctype="image/jpeg", content=b"")
        if reply == "json":
            return FakeResponse(200, {"error": 1})
        if isinstance(reply, int):
            return FakeResponse(reply, None, ctype="text/html", content=b"nope")
        return FakeResponse(200, None, ctype="image/jpeg", content=reply)
    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def _plex(sa_env):
    sa_env.plex_cfg = dict(PLEX)


# ── the chain ───────────────────────────────────────────────────────────────

def test_an_episode_tries_its_show_then_season_then_itself_then_art():
    got = images.candidates({"path": "/library/metadata/32468/thumb/1789894583", "kind": "episode",
                             "rk": "573980", "pk": "572734", "gk": "32468"})
    assert [c[1] for c in got] == [
        "/library/metadata/32468/thumb/1789894583",     # the show poster the row names
        "/library/metadata/572734/thumb",               # the season
        "/library/metadata/573980/thumb",               # the episode still
        "/library/metadata/32468/art",                  # the show's art
        "/library/metadata/573980/art",
    ]                                                   # (the show thumb is not asked twice)


def test_a_movie_falls_back_to_its_art_and_a_track_to_album_then_artist():
    movie = images.candidates({"path": "/library/metadata/593411/thumb/1792515718", "kind": "movie",
                               "rk": "593411"})
    assert [c[1] for c in movie] == ["/library/metadata/593411/thumb/1792515718", "/library/metadata/593411/art"]
    track = images.candidates({"path": "", "kind": "track", "rk": "9", "pk": "8", "gk": "7"})
    assert [c[1] for c in track] == ["/library/metadata/8/thumb", "/library/metadata/9/thumb",
                                     "/library/metadata/7/thumb"]
    artist = images.candidates({"path": "/library/metadata/8/thumb/1", "kind": "track", "role": "artist",
                                "gk": "7"})
    assert [c[1] for c in artist][:2] == ["/library/metadata/8/thumb/1", "/library/metadata/7/thumb"]


def test_plex_minus_one_thumbs_are_rebuilt_not_refused():
    # 62 of the maintainer's plex history rows name an album cover as thumb/-1
    got = images.candidates({"path": "/library/metadata/556994/thumb/-1", "kind": "track", "rk": "556995"})
    assert [c[1] for c in got] == ["/library/metadata/556994/thumb", "/library/metadata/556995/thumb"]
    assert not images.is_image_path("/library/metadata/556994/thumb/-1")   # the whitelist itself is unchanged


@pytest.mark.parametrize("spec", [
    {"path": "/status/sessions", "kind": "movie"},
    {"path": "/library/metadata/1/thumb/1/../../../status/sessions", "kind": "movie"},
    {"path": "/library/metadata/1/thumb?X-Plex-Token=x", "kind": "movie"},
    {"path": "https://example.com/x.jpg", "kind": "movie"},
])
def test_nothing_but_artwork_ever_enters_the_chain(spec):
    assert images.candidates(spec) == []


def test_each_whitelist_layer_holds_on_its_own(monkeypatch):
    # the stored path is cleaned first ...
    assert images._plex_path("/status/sessions") is None
    assert images._plex_path("/library/metadata/1/thumb/1/../../x") is None
    # ... and whatever comes out of the chain is checked again before a fetch
    monkeypatch.setattr(images, "_plex_path", lambda path: path)
    assert images.candidates({"path": "/status/sessions", "kind": "movie"}) == []


def test_keys_from_the_query_string_are_checked_too():
    spec = images.spec_from_args({"path": "/status/sessions", "k": "episode", "rk": "../../accounts",
                                  "gk": "1/../2", "pk": "12", "r": "evil", "s": "ftp"})
    assert spec == {"path": "/status/sessions", "kind": "episode", "pk": "12"}
    assert [c[1] for c in images.candidates(spec)] == ["/library/metadata/12/thumb"]


def test_jellyfin_walks_series_season_item_then_backdrop():
    got = images.candidates({"path": "jf:ep1", "kind": "episode", "rk": "ep1", "pk": "season1",
                             "gk": "series1", "server": "jellyfin"})
    assert got == [("jellyfin", "ep1", "Primary"), ("jellyfin", "series1", "Primary"),
                   ("jellyfin", "season1", "Primary"), ("jellyfin", "series1", "Backdrop"),
                   ("jellyfin", "ep1", "Backdrop")]


# ── fetching ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("failure", [404, 500, "empty", "json", requests.Timeout("slow"),
                                     requests.ConnectionError("reset")])
def test_each_failure_moves_on_to_the_next_picture(sa_env, monkeypatch, failure):
    _plex(sa_env)
    calls = _upstream(monkeypatch, {"/library/metadata/32468/thumb/1789894583": failure,
                                    "/library/metadata/572734/thumb": failure,
                                    "/library/metadata/573980/thumb": JPEG})
    out = images.serve_image({"path": "/library/metadata/32468/thumb/1789894583", "kind": "episode",
                              "rk": "573980", "pk": "572734", "gk": "32468"}, 96)
    assert out["status"] == 200 and out["content"] == JPEG and out["source"] == "fallback 2"
    # the token rides on the server's request only, and the size drawn is asked for
    first = calls[0]
    assert first[0] == "http://plex.test:32400/photo/:/transcode"
    assert first[1]["width"] == 96 and first[1]["height"] == 144 and first[1]["minSize"] == 1


def test_a_transcoder_that_says_404_is_not_asked_twice(sa_env, monkeypatch):
    _plex(sa_env)
    calls = _upstream(monkeypatch, {"/library/metadata/1/thumb": 404, "/library/metadata/1/art": JPEG})
    out = images.serve_image({"path": "/library/metadata/1/thumb", "kind": "movie", "rk": "1"}, 160)
    assert out["status"] == 200
    assert [c[0].rsplit(":32400", 1)[1] for c in calls] == ["/photo/:/transcode", "/photo/:/transcode"]


def test_a_transcoder_that_cannot_falls_back_to_the_artwork_itself(sa_env, monkeypatch):
    _plex(sa_env)

    def fake_get(url, params=None, timeout=None, **kw):
        if url.endswith("/photo/:/transcode"):
            return FakeResponse(400, None, ctype="text/plain", content=b"bad")
        return FakeResponse(200, None, ctype="image/png", content=b"png")
    monkeypatch.setattr(requests, "get", fake_get)
    out = images.serve_image({"path": "/library/metadata/5/thumb/1", "kind": "movie"}, 96)
    assert out["status"] == 200 and out["ctype"] == "image/png"


def test_jellyfin_asks_for_the_size_drawn_with_the_auth_header(sa_env, monkeypatch):
    sa_env.jf_cfg = dict(JF)
    calls = _upstream(monkeypatch, {"jf:series1/Images/Primary": JPEG})
    out = images.serve_image({"path": "jf:ep1", "kind": "episode", "gk": "series1", "server": "jellyfin"}, 96)
    assert out["status"] == 200
    url, params = calls[-1]
    assert url == "http://jf.test:8096/Items/series1/Images/Primary"
    assert params["maxWidth"] == 96 and params["maxHeight"] == 144 and "api_key" not in params


def test_when_every_picture_is_missing_the_answer_is_404_and_remembered(sa_env, monkeypatch):
    _plex(sa_env)
    calls = _upstream(monkeypatch, {})
    spec = {"path": "/library/metadata/1/thumb/2", "kind": "movie", "rk": "1"}
    assert images.serve_image(spec, 96)["status"] == 404
    n = len(calls)
    assert images.serve_image(spec, 96) == {"status": 404, "source": "miss cache"}
    assert len(calls) == n


# ── the cache ───────────────────────────────────────────────────────────────

def test_a_poster_is_fetched_once_then_served_from_disk(sa_env, monkeypatch):
    _plex(sa_env)
    calls = _upstream(monkeypatch, {"/library/metadata/7/thumb/1": JPEG})
    spec = {"path": "/library/metadata/7/thumb/1", "kind": "movie"}
    first = images.serve_image(spec, 96)
    second = images.serve_image(spec, 96)
    assert first["status"] == second["status"] == 200
    assert second["source"] == "cache" and second["content"] == JPEG and second["etag"] == first["etag"]
    assert len(calls) == 1
    # another size is another file; another server is another key
    images.serve_image(spec, 480)
    assert len(calls) == 2
    key = images.cache_key(spec, 96)
    sa_env.plex_cfg = {"base_url": "http://other:32400", "token": "t"}
    assert images.cache_key(spec, 96) != key
    files = list((sa_env.db.parent / "sa_images").glob("*/*.img"))
    assert len(files) == 2


def test_an_old_file_is_fetched_again(sa_env, monkeypatch):
    _plex(sa_env)
    calls = _upstream(monkeypatch, {"/library/metadata/7/thumb/1": JPEG})
    spec = {"path": "/library/metadata/7/thumb/1", "kind": "movie"}
    images.serve_image(spec, 96)
    later = time.time() + images.CACHE_TTL + 10
    assert images.serve_image(spec, 96, now=later)["source"] == "own"
    assert len(calls) == 2


def test_the_cache_stays_under_its_cap_oldest_out(sa_env, monkeypatch):
    _plex(sa_env)
    big = b"\xff\xd8" + b"x" * 1000
    _upstream(monkeypatch, {f"/library/metadata/{i}/thumb": big for i in range(1, 6)})
    monkeypatch.setattr(images, "CACHE_MAX_BYTES", 3500)
    import os
    for i in range(1, 6):
        images.serve_image({"path": f"/library/metadata/{i}/thumb", "kind": "movie"}, 96)
        for f in (sa_env.db.parent / "sa_images").glob("*/*.img"):
            os.utime(f, (f.stat().st_mtime - 1, f.stat().st_mtime - 1))   # older each round
    files = list((sa_env.db.parent / "sa_images").glob("*/*.img"))
    assert sum(f.stat().st_size for f in files) <= 3500
    newest = images._file(images.cache_key({"path": "/library/metadata/5/thumb", "kind": "movie"}, 96))
    oldest = images._file(images.cache_key({"path": "/library/metadata/1/thumb", "kind": "movie"}, 96))
    assert newest.exists() and not oldest.exists()


def test_the_same_poster_asked_at_once_reaches_plex_once(sa_env, monkeypatch):
    _plex(sa_env)
    gate = threading.Event()
    calls = []

    def fake_get(url, params=None, timeout=None, **kw):
        calls.append(url)
        gate.wait(2)
        return FakeResponse(200, None, ctype="image/jpeg", content=JPEG)
    monkeypatch.setattr(requests, "get", fake_get)
    spec = {"path": "/library/metadata/9/thumb/1", "kind": "movie"}
    out = []
    threads = [threading.Thread(target=lambda: out.append(images.serve_image(spec, 96))) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert [o["status"] for o in out] == [200] * 5
    assert len(calls) == 1


def test_no_more_than_a_few_requests_reach_the_server_at_once(sa_env, monkeypatch):
    _plex(sa_env)
    lock = threading.Lock()
    now = {"n": 0, "peak": 0}

    def fake_get(url, params=None, timeout=None, **kw):
        with lock:
            now["n"] += 1
            now["peak"] = max(now["peak"], now["n"])
        time.sleep(0.05)
        with lock:
            now["n"] -= 1
        return FakeResponse(200, None, ctype="image/jpeg", content=JPEG)
    monkeypatch.setattr(requests, "get", fake_get)
    threads = [threading.Thread(target=images.serve_image,
                                args=({"path": f"/library/metadata/{i}/thumb", "kind": "movie"}, 96))
               for i in range(1, 17)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert now["peak"] <= images.UPSTREAM_SLOTS


def test_a_busy_server_is_try_again_not_missing(sa_env, monkeypatch):
    _plex(sa_env)
    _upstream(monkeypatch, {"/library/metadata/9/thumb": JPEG})
    monkeypatch.setattr(images, "_upstream", threading.BoundedSemaphore(1))
    monkeypatch.setattr(images, "_SLOT_WAIT", 0.05)
    images._upstream.acquire()
    try:
        spec = {"path": "/library/metadata/9/thumb", "kind": "movie"}
        assert images.serve_image(spec, 96) == {"status": 503}
    finally:
        images._upstream.release()
    assert images.serve_image(spec, 96)["status"] == 200     # not remembered as missing


# ── the route ───────────────────────────────────────────────────────────────

@pytest.fixture()
def client(sa_env):
    from flask import Flask

    from api import server_activity as routes
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint())
    _plex(sa_env)
    return app.test_client()


def test_the_route_sends_a_week_of_cache_and_an_etag(client, monkeypatch):
    _upstream(monkeypatch, {"/library/metadata/32468/thumb/1": JPEG})
    r = client.get("/api/server-activity/image?path=/library/metadata/32468/thumb/1&k=episode"
                   "&gk=32468&rk=573980&w=96")
    assert r.status_code == 200 and r.data == JPEG
    assert r.headers["Cache-Control"] == "private, max-age=604800"
    etag = r.headers["ETag"]
    assert r.headers["X-Image-Source"] == "own"
    again = client.get("/api/server-activity/image?path=/library/metadata/32468/thumb/1&k=episode"
                       "&gk=32468&rk=573980&w=96", headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.data == b""


def test_the_route_404s_when_nothing_has_a_picture_and_503s_when_busy(client, monkeypatch):
    _upstream(monkeypatch, {})
    r = client.get("/api/server-activity/image?path=/library/metadata/1/thumb&w=96")
    assert r.status_code == 404 and "max-age=900" in r.headers["Cache-Control"]
    monkeypatch.setattr(images, "serve_image", lambda spec, width=None: {"status": 503})
    import core.server_activity as sa
    monkeypatch.setattr(sa, "serve_image", lambda spec, width=None: {"status": 503})
    r = client.get("/api/server-activity/image?path=/library/metadata/2/thumb&w=96")
    assert r.status_code == 503 and r.headers["Cache-Control"] == "no-store"


def test_the_route_never_fetches_a_plex_api_path(client, monkeypatch):
    calls = _upstream(monkeypatch, {})
    for q in ("path=/status/sessions", "path=/accounts&rk=../x", "path=/status/sessions&k=episode&gk=abc"):
        assert client.get(f"/api/server-activity/image?{q}").status_code == 404
    assert calls == []


# ── the payloads carry the query ────────────────────────────────────────────

def _play(key, start, **kw):
    row = {c: None for c in store.PLAY_COLUMNS}
    row.update(play_key=key, group_key=kw.pop("group", key), source="live", server="plex", kind="episode",
               title=key, user="maya", started_at=start, stopped_at=start + 60, last_seen=start + 60,
               played_ms=60_000, paused_ms=0, buffering_ms=0, pause_count=0, buffer_count=0, sent_kb=0,
               remote=0, reasons=[], segments=[], percent=100)
    row.update(kw)
    return row


def test_a_run_of_episodes_shows_the_show_poster(sa_env):
    now = time.time()
    store.apply_ops([("play", _play("e1", now - 900, group="g", grandparent_title="Andor", rating_key="11",
                                    parent_key="10", grandparent_key="9", thumb="/library/metadata/9/thumb/5")),
                     ("play", _play("e2", now - 600, group="g", grandparent_title="Andor", rating_key="12",
                                    parent_key="10", grandparent_key="9", thumb="/library/metadata/9/thumb/5"))])
    g = history.get_history(now=now, tz="UTC")["groups"][0]
    assert g["count"] == 2
    spec = images.spec_from_args(dict(x.split("=", 1) for x in g["image"].replace("%2F", "/").split("&")))
    assert images.candidates(spec)[0][1] == "/library/metadata/9/thumb/5"
    assert "gk=9" in g["image"] and "k=episode" in g["image"]


def test_an_imported_row_with_no_thumb_still_has_its_own_key_to_try(sa_env):
    now = time.time()
    store.apply_ops([("play", _play("h1", now - 600, source="plex_history", kind="movie", rating_key="77",
                                    thumb=None))])
    g = history.get_history(now=now, tz="UTC")["groups"][0]
    assert g["thumb"] is None and "rk=77" in g["image"]


def test_stats_shelves_and_live_sessions_carry_the_query_too(sa_env):
    now = time.time()
    store.apply_ops([("play", _play("t1", now - 600, kind="track", grandparent_title="Mac", parent_title="Rumours",
                                    rating_key="3", parent_key="2", grandparent_key="1",
                                    thumb="/library/metadata/2/thumb/9"))])
    out = stats.get_stats(days=7, tz="UTC", now=now)
    artist = out["top"]["artists"][0]
    assert "r=artist" in artist["image"] and "gk=1" in artist["image"]
    sa_env.use_plex()
    s = live.get_activity(max_age=0)["sessions"][0]
    assert s["image"] and s["backdrop"] and "r=art" in s["backdrop"]


def test_plex_history_import_keeps_the_family_keys(sa_env):
    from xml.etree import ElementTree as ET
    els = [ET.Element("Video", type="episode", ratingKey=str(100 + i), parentKey="/library/metadata/50",
                      grandparentKey="/library/metadata/40", grandparentTitle="Phony", title=f"E{i}", index=str(i),
                      parentIndex="1", viewedAt=str(1_790_000_000 + i * 1500), duration="1200000", accountID="1",
                      grandparentThumb="/library/metadata/40/thumb/1")
           for i in range(3)]
    rows = history.plex_history_rows(els, machine="m", accounts={"1": "maya"}, devices={}, sections={},
                                     until=1_800_000_000, server_name="Plex")
    assert {r["grandparent_key"] for r in rows} == {"40"} and {r["parent_key"] for r in rows} == {"50"}
    # with the show known, back to back episodes fold into one row like live ones do
    assert len({r["group_key"] for r in rows}) == 1


# ── speed: history and stats keep their answers ─────────────────────────────

def test_history_answers_from_its_cache_until_a_play_lands(sa_env, monkeypatch):
    now = time.time()
    store.apply_ops([("play", _play("m1", now - 600, kind="movie"))])
    history.invalidate()
    first = history.get_history(tz="UTC", days=7)
    seen = []
    real = store.query
    monkeypatch.setattr(store, "query", lambda sql, params=(): seen.append(sql) or real(sql, params))
    assert history.get_history(tz="UTC", days=7) is first
    assert seen == []
    store.apply_ops([("play", _play("m2", now - 300, kind="movie"))])
    assert len(history.get_history(tz="UTC", days=7)["groups"]) == 2


def test_a_later_page_reuses_page_ones_summary(sa_env, monkeypatch):
    now = time.time()
    store.apply_ops([("play", _play(f"m{i}", now - 6000 + i * 100, kind="movie")) for i in range(5)])
    history.invalidate()
    history.get_history(tz="UTC", days=7, per_page=2)
    seen = []
    real = store.query
    monkeypatch.setattr(store, "query", lambda sql, params=(): seen.append(sql) or real(sql, params))
    p1 = history.get_history(tz="UTC", days=7, per_page=2, page=1)
    assert p1["summary"]["plays"] == 5 and p1["facets"] is None
    assert not any("COUNT(DISTINCT user)" in q for q in seen)


def test_stats_serve_the_last_sheet_while_a_new_one_is_worked_out(sa_env):
    now = time.time()
    store.apply_ops([("play", _play("a", now - 3600, kind="movie"))])
    stats.invalidate()
    first = stats.get_stats(days=30, tz="UTC")
    assert first["tiles"]["plays"]["value"] == 1
    # the other ranges are worked out next, in the background
    assert len(sa_env.spawned) == 1
    sa_env.spawned.pop()()
    assert set(k[0] for k in stats._cache) == {7, 30, 90, 365}
    store.apply_ops([("play", _play("b", now - 1800, kind="movie"))])
    assert stats.get_stats(days=30, tz="UTC")["tiles"]["plays"]["value"] == 1   # at once, the last sheet
    assert len(sa_env.spawned) == 1
    sa_env.spawned.pop()()
    assert stats.get_stats(days=30, tz="UTC")["tiles"]["plays"]["value"] == 2
    assert sa_env.spawned == []


def test_a_stats_sheet_too_old_is_worked_out_on_the_spot(sa_env):
    now = time.time()
    store.apply_ops([("play", _play("a", now - 3600, kind="movie"))])
    stats.get_stats(days=30, tz="UTC", now=now)
    store.apply_ops([("play", _play("b", now - 1800, kind="movie"))])
    later = now + stats.STALE_TTL + 1
    assert stats.get_stats(days=30, tz="UTC", now=later)["tiles"]["plays"]["value"] == 2


def test_the_new_indexes_answer_the_history_queries(sa_env):
    store.apply_ops([("play", _play("a", time.time() - 60, kind="movie"))])
    c = sa_env.factory()
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_server_plays_group_started", "idx_server_plays_source", "idx_server_plays_user_avatar",
            "idx_server_plays_server"} <= names

    def plan(sql, args=()):
        return " ".join(r[3] for r in c.execute("EXPLAIN QUERY PLAN " + sql, args))
    assert "COVERING INDEX idx_server_plays_group_started" in plan(
        "SELECT group_key, MAX(started_at) AS last FROM server_plays WHERE 1=1 GROUP BY group_key "
        "ORDER BY last DESC LIMIT 51")
    assert "idx_server_plays_source" in plan("SELECT MIN(started_at) AS t FROM server_plays WHERE source = 'live'")
    assert "COVERING INDEX idx_server_plays_user_avatar" in plan(
        "SELECT user, MAX(user_avatar) AS avatar, COUNT(*) AS n FROM server_plays WHERE 1=1 GROUP BY user")
    assert "idx_server_plays_server" in plan("SELECT 1 AS x FROM server_plays WHERE server = ? LIMIT 1", ("plex",))


# ── speed: the live view ────────────────────────────────────────────────────

def test_the_drawer_opening_gets_the_snapshot_at_once_with_its_age(sa_env):
    plex = sa_env.use_plex()
    live.get_activity(max_age=0, stale_ok=0)
    live._snapshot.update(at=time.time() - 6)
    plex.calls.clear()
    out = live.get_activity()
    assert plex.calls == [] and 5500 <= out["age_ms"] <= 8000
    assert len(sa_env.spawned) == 1          # a fresh poll behind it
    sa_env.spawned.pop()()
    assert any(c[0] == "/status/sessions" for c in plex.calls)
    # a second open while that poll runs starts no other
    live._snapshot.update(at=time.time() - 6)
    live._refreshing["on"] = True
    live.get_activity()
    assert sa_env.spawned == []
    live._refreshing["on"] = False


def test_the_sampler_and_the_controls_always_poll(sa_env):
    plex = sa_env.use_plex()
    live.get_activity(max_age=0, stale_ok=0)
    live._snapshot.update(at=time.time() - 6)
    plex.calls.clear()
    live.sampler_tick(True, emit=lambda d: None)
    assert any(c[0] == "/status/sessions" for c in plex.calls)
    assert "age_ms" not in live._snapshot["data"]


def test_new_streams_are_looked_up_side_by_side(sa_env):
    gate = threading.Barrier(3, timeout=2)

    class Slow(FakePlex):
        def query(self, path, params=None, **kw):
            if path.startswith("/library/metadata/") and not path.endswith("/children"):
                gate.wait()          # three lookups must be in flight together
            return super().query(path, params, **kw)
    plex = sa_env.use_plex(Slow())
    keys = [el.get("ratingKey") for el in plex.root][:3]
    budget = [px.SOURCE_FETCH_BUDGET]
    px.prefetch_sources(plex, keys, budget)
    assert budget[0] == px.SOURCE_FETCH_BUDGET - 3
    assert sum(1 for c in plex.calls if c[0].startswith("/library/metadata/")) == 3
    # cached now: the poll's own lookups cost nothing
    plex.calls.clear()
    px.prefetch_sources(plex, keys, budget)
    assert plex.calls == []


def test_plex_and_jellyfin_are_polled_at_the_same_time(sa_env, monkeypatch):
    jf_started = threading.Event()

    class WaitsForJellyfin(FakePlex):
        def query(self, path, params=None, **kw):
            if path == "/status/sessions":
                assert jf_started.wait(2), "plex was asked before jellyfin started: the poll is serial"
            return super().query(path, params, **kw)
    sa_env.use_plex(WaitsForJellyfin())
    sa_env.jf_cfg = dict(JF)

    def fake_get(url, params=None, headers=None, timeout=None, **kw):
        jf_started.set()
        if url.endswith("/Sessions"):
            return FakeResponse(200, jf_sessions())
        return FakeResponse(404, None)
    monkeypatch.setattr(requests, "get", fake_get)
    out = live.get_activity(max_age=0)
    assert {s["server"] for s in out["sessions"]} == {"plex", "jellyfin"}


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_jellyfin_poll_that_blows_up_leaves_plex_on_screen(sa_env, monkeypatch):
    sa_env.use_plex()
    sa_env.jf_cfg = dict(JF)
    monkeypatch.setattr(live, "_jellyfin_sessions", lambda db=None: (_ for _ in ()).throw(RuntimeError("x")))
    out = live.get_activity(max_age=0)
    assert out["ok"] and all(s["server"] == "plex" for s in out["sessions"])
    jf = next(i for i in out["servers"] if i["server"] == "jellyfin")
    assert jf["reachable"] is False
