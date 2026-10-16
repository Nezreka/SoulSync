"""search-all cap + cancel-all stop for the video wishlist.

a user clicked "search all" on a 700-item wishlist: it grabbed 17 and counting,
and cancel all only cancelled the rows, the search kept going and grabbing.
search-all now honours the drain's 'Max downloads per run', and cancel all
bumps a stop generation that search-all, manual searches and the drain check.
"""

from __future__ import annotations

import threading

from core.automation.handlers import video_process_wishlist as vpw
from core.automation.handlers.video_process_wishlist import auto_video_process_wishlist, item_key
from core.video import wishlist_search as ws


def _items(n):
    return [{"tmdb_id": i + 1, "title": "Movie %d" % (i + 1)} for i in range(n)]


def _cand(item):
    return {"filename": "rel-%s.mkv" % item["tmdb_id"], "title": item["title"], "username": "u",
            "size_bytes": 1000, "quality_label": "WEBDL-1080p", "accepted": True, "score": 10}


class _Deps:
    def update_progress(self, automation_id, **kw):
        pass


def _fake_seams(monkeypatch, enq, *, search=None):
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/movies")
    monkeypatch.setattr(vpw, "_default_search", search or (lambda it, mt: ([_cand(it)], None)))
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda it, best, cands, mt, root: enq.append(it["tmdb_id"]) or True)


# ── search-all cap ───────────────────────────────────────────────────────────
def test_search_all_batch_stops_at_the_cap(monkeypatch):
    enq = []
    _fake_seams(monkeypatch, enq)
    ws._run_batch(_items(40), "movie", cap=3)
    assert len(enq) == 3


def test_search_all_batch_skips_searching_once_the_cap_is_spent(monkeypatch):
    enq, searched = [], []

    def search(it, mt):
        searched.append(it["tmdb_id"])
        return [_cand(it)], None
    _fake_seams(monkeypatch, enq, search=search)
    ws._run_batch(_items(40), "movie", cap=2)
    assert len(enq) == 2
    assert len(searched) < 40   # a spent budget doesn't burn 700 searches


def test_refused_grab_hands_the_slot_back(monkeypatch):
    enq = []
    _fake_seams(monkeypatch, enq)
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda it, best, cands, mt, root: it["tmdb_id"] > 2 and (enq.append(it["tmdb_id"]) or True))
    ws._run_batch(_items(10), "movie", cap=3)
    assert len(enq) == 3


def test_search_all_passes_the_drain_cap(monkeypatch):
    seen = {}
    monkeypatch.setattr(vpw, "_backfill_movie_available_dates", lambda limit=25: None)
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/movies")
    monkeypatch.setattr(vpw, "_default_max_downloads", lambda mt: 4)
    monkeypatch.setattr(ws, "_run_batch", lambda todo, mt, cap=None, **kw: seen.setdefault(mt, cap))

    class _Db:
        def movie_wishlist_to_download(self, **kw):
            return _items(5)

        def episode_wishlist_to_download(self, **kw):
            return []
    import api.video as videoapi
    monkeypatch.setattr(videoapi, "get_video_db", lambda: _Db())
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    out = ws.search_all()
    assert out["movie"] == "started"
    for _ in range(200):
        if "movie" in seen:
            break
        threading.Event().wait(0.01)
    assert seen == {"movie": 4}


def test_drain_cap_reads_the_automation_config(monkeypatch):
    import database.music_database as mdb

    class _Db:
        def get_system_automation_by_action(self, action):
            return {"action_config": '{"max_simultaneous_downloads": 7}'} \
                if action == "video_process_movie_wishlist" else None
    monkeypatch.setattr(mdb, "get_database", lambda *a, **k: _Db())
    assert vpw._default_max_downloads("movie") == 7
    assert vpw._default_max_downloads("episode") == 3


# ── cancel all stops searches ────────────────────────────────────────────────
def test_stop_mid_batch_grabs_nothing_more(monkeypatch):
    enq, searched = [], []

    def search(it, mt):
        searched.append(it["tmdb_id"])
        if len(searched) == 2:
            ws.stop_all()          # cancel all lands while searches are running
        return [_cand(it)], None
    _fake_seams(monkeypatch, enq, search=search)
    ws._run_batch(_items(50), "movie")   # uncapped: only the stop limits it
    assert len(enq) <= 1                 # nothing whose search ended after the stop
    assert len(searched) <= 4            # the pool's in-flight searches, not 50


def test_a_stop_before_the_batch_does_not_poison_the_next_one(monkeypatch):
    enq = []
    _fake_seams(monkeypatch, enq)
    ws.stop_all()
    ws._run_batch(_items(2), "movie")
    assert len(enq) == 2


def test_stop_halts_the_hourly_drain(monkeypatch):
    enq, searched = [], []

    def search(item, mt):
        searched.append(item["tmdb_id"])
        if len(searched) == 1:
            vpw.request_stop()
        return [_cand(item)]

    res = auto_video_process_wishlist(
        {"_automation_id": "stop-test", "max_concurrent": 1, "max_simultaneous_downloads": 50},
        _Deps(), media_type="movie",
        fetch_items=lambda mt: _items(20), active_keys=lambda mt: set(),
        target_dir=lambda mt: "/movies", search=search,
        enqueue=lambda it, best, cands, mt, root: enq.append(item_key(it, mt)) or True,
        record_outcome=lambda *a, **k: None, record_note=lambda *a, **k: None)
    assert res["status"] == "completed"
    assert enq == []
    assert searched == [1]


def test_stop_endpoint_bumps_the_generation():
    from flask import Flask
    import api.video as videoapi
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    gen = vpw.stop_generation()
    r = app.test_client().post("/api/video/wishlist/search-stop")
    assert r.get_json() == {"success": True}
    assert vpw.stop_requested(gen)
