"""Shelf Life in the library manager: each row carries its score or gate and
the answer carries the job's threshold, and the titles sort lowest score
first. none of it shows while the job is off or before it has scored a side."""

from __future__ import annotations

import pytest

from core.video import library_manager as lm
from database.video_database import VideoDatabase


def _movie(db, title, year=2000):
    return db.upsert_movie("plex", {"server_id": title, "title": title, "year": year})


def _score(db, results):
    db.shelf_life_save("movie", [{"item_id": i, "score": s, "gate": g, "signals": {}, "reasons": [], "size_bytes": 1}
                                 for i, s, g in results])


@pytest.fixture()
def library(tmp_path):
    lm.forget()
    db = VideoDatabase(database_path=str(tmp_path / "lib.db"))
    ids = {t: _movie(db, t) for t in ("Alien", "Brazil", "Cube", "Dune")}
    # Alien 70, Brazil 12, Cube gated, Dune never scored
    _score(db, [(ids["Alien"], 70, None), (ids["Brazil"], 12, None), (ids["Cube"], None, "keep")])
    yield db, ids
    lm.forget()


def _titles(result):
    return [it["title"] for it in result["items"]]


def test_the_titles_sort_lowest_shelf_life_first(library):
    db, _ = library
    shelf = db.shelf_life_badges("movie")
    out = lm.titles(db, "movies", {"sort": "shelf_life", "dir": "asc"}, server_source="plex", shelf_life=shelf)
    # scored lowest first, then gated, then never scored
    assert _titles(out) == ["Brazil", "Alien", "Cube", "Dune"]
    # highest first still leaves the gated and the unscored at the end
    out = lm.titles(db, "movies", {"sort": "shelf_life", "dir": "desc"}, server_source="plex", shelf_life=shelf)
    assert _titles(out) == ["Alien", "Brazil", "Cube", "Dune"]


def test_without_shelf_life_the_sort_is_title_order(library):
    db, _ = library
    out = lm.titles(db, "movies", {"sort": "shelf_life"}, server_source="plex")
    assert _titles(out) == ["Alien", "Brazil", "Cube", "Dune"]
    assert all("shelf_life" not in it for it in out["items"])


def _client(db, monkeypatch, *, admin=True, enabled=True):
    from flask import Flask, g

    import api.video as videoapi
    import core.video.repair.worker as worker_mod
    videoapi._video_db = db
    worker_mod._worker = worker_mod.VideoRepairWorker(db)
    worker_mod._worker.set_job_config("shelf_life", enabled=enabled)
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex")
    app = Flask(__name__)

    @app.before_request
    def _who():
        g.is_admin = admin
        g.profile_id = 1 if admin else 2

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), worker_mod


@pytest.fixture()
def routes(monkeypatch):
    import api.video as videoapi
    made = []

    def _make(db, **kw):
        c, wm = _client(db, monkeypatch, **kw)
        made.append(wm)
        return c
    yield _make
    for wm in made:
        if wm._worker:
            wm._worker.stop()
        wm._worker = None
    videoapi._video_db = None


def test_each_row_carries_its_score_or_gate_and_the_answer_the_threshold(library, routes):
    c = routes(library[0])
    body = c.get("/api/video/manager/titles?kind=movies&sort=shelf_life&dir=asc").get_json()
    assert _titles(body) == ["Brazil", "Alien", "Cube", "Dune"]
    by = {it["title"]: it.get("shelf_life") for it in body["items"]}
    assert by["Brazil"] == {"score": 12, "gate": None, "gate_text": None}
    assert by["Cube"]["gate"] == "keep" and by["Cube"]["gate_text"]
    assert by["Dune"] is None                     # never scored: no badge
    assert body["shelf_life_threshold"] == 30


def test_the_threshold_follows_the_jobs_setting(library, routes):
    c = routes(library[0])
    from core.video.repair.worker import get_video_repair_worker
    get_video_repair_worker().set_job_config("shelf_life", settings={"threshold": 45})
    assert c.get("/api/video/manager/titles?kind=movies").get_json()["shelf_life_threshold"] == 45


def test_nothing_until_the_job_has_scored_that_side(tmp_path, routes):
    # a fresh library (the job never ran) answers as it always has: no
    # threshold, so no Shelf Life column or sort, and no badges
    lm.forget()
    db = VideoDatabase(database_path=str(tmp_path / "fresh.db"))
    _movie(db, "Alien")
    c = routes(db)
    body = c.get("/api/video/manager/titles?kind=movies").get_json()
    assert "shelf_life_threshold" not in body
    assert all("shelf_life" not in it for it in body["items"])
    # scored movies say nothing about shows
    _score(db, [(_movie(db, "Brazil"), 12, None)])
    assert "shelf_life_threshold" in c.get("/api/video/manager/titles?kind=movies").get_json()
    assert "shelf_life_threshold" not in c.get("/api/video/manager/titles?kind=shows").get_json()
    lm.forget()


def test_the_job_switched_off_shows_nothing(library, routes):
    # stored scores stay, but nothing refreshes them, so nothing is shown
    c = routes(library[0], enabled=False)
    body = c.get("/api/video/manager/titles?kind=movies&sort=shelf_life").get_json()
    assert "shelf_life_threshold" not in body
    assert all("shelf_life" not in it for it in body["items"])
    assert _titles(body) == ["Alien", "Brazil", "Cube", "Dune"]    # title order
    from core.video.repair.worker import get_video_repair_worker
    get_video_repair_worker().set_job_config("shelf_life", enabled=True)
    body = c.get("/api/video/manager/titles?kind=movies").get_json()
    assert body["shelf_life_threshold"] == 30
    assert {it["title"]: it.get("shelf_life") for it in body["items"]}["Brazil"]["score"] == 12


def test_a_member_never_reaches_it(library, routes):
    # the manager is the admin's; a member browses the plain posters
    c = routes(library[0], admin=False)
    assert c.get("/api/video/manager/titles?kind=movies&sort=shelf_life").status_code == 403
