"""#2158: a discover rail always answers.

in theaters now and coming soon sat on their skeleton because the list
request behind them never came back. these pin the server half: the deep
paging waves share one time budget, a slow page is dropped instead of
waited on, and a failed list is a 502 the page can retry, not an empty rail.
"""

from __future__ import annotations

import threading

from core.video.discover_paging import fill_pages


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _collector():
    items = []

    def consume(batch):
        if not batch:
            return False
        items.extend(batch)
        return True
    return items, consume


def test_fills_until_target_then_stops():
    items, consume = _collector()
    asked = []

    def fetch(p):
        asked.append(p)
        return [p * 10 + i for i in range(3)]

    has_more, nxt = fill_pages(fetch, consume, 1, lambda: len(items) < 20, wave=5)
    assert len(items) >= 20
    assert has_more is True
    assert nxt == 11                       # two waves of five, next unused page is 11
    assert sorted(asked) == list(range(1, 11))


def test_an_empty_page_means_no_more():
    items, consume = _collector()
    has_more, _ = fill_pages(lambda p: [p] if p < 3 else [], consume, 1, lambda: True, wave=5)
    assert items == [1, 2]
    assert has_more is False


def test_a_stuck_page_does_not_hold_the_rail():
    """#2158: page 3 never answers. the rail comes back inside the budget with
    pages 1 and 2, and next_page points at page 3 so nothing is skipped."""
    release = threading.Event()
    items, consume = _collector()

    def fetch(p):
        if p == 3:
            release.wait(5)
            return [999]
        return [p]

    try:
        done = threading.Event()
        out = {}

        def run():
            out["r"] = fill_pages(fetch, consume, 1, lambda: True, wave=5, budget_s=0.3)
            done.set()

        t = threading.Thread(target=run)
        t.start()
        assert done.wait(3), "fill_pages waited on the stuck page"
        has_more, nxt = out["r"]
        assert items == [1, 2]             # in order, nothing after the stuck page
        assert 999 not in items
        assert has_more is True
        assert nxt == 3
        t.join(1)
    finally:
        release.set()


def test_the_budget_stops_new_waves():
    clock = _Clock()
    items, consume = _collector()

    def fetch(p):
        clock.t += 4.0                     # each page "takes" four seconds of budget
        return [p]

    has_more, nxt = fill_pages(fetch, consume, 1, lambda: True, wave=2, max_pages=20,
                               budget_s=10.0, clock=clock)
    # a wave already started finishes, but no new wave starts once the budget is spent
    assert len(items) < 20
    assert has_more is True
    assert nxt == len(items) + 1


def test_a_page_that_raises_reads_as_empty():
    items, consume = _collector()

    def fetch(p):
        if p == 2:
            raise RuntimeError("tmdb said no")
        return [p]

    has_more, _ = fill_pages(fetch, consume, 1, lambda: True, wave=3)
    assert has_more is False
    assert items == [1, 3]


def _client(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    from flask import Flask
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_list_route_answers_with_a_stuck_page(tmp_path, monkeypatch):
    """#2158: the in theaters rail (curated + language filter, so it pages in
    waves) answers even when one tmdb page hangs."""
    import core.video.discover_paging as paging
    import core.video.enrichment.engine as eng_mod

    client = _client(tmp_path)
    release = threading.Event()

    class FakeEng:
        def discover_curated(self, key, page=1):
            if page == 2:
                release.wait(5)
            return [{"kind": "movie", "tmdb_id": page * 100 + i, "original_language": "en"}
                    for i in range(3)]

    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: FakeEng())
    monkeypatch.setattr(paging, "DEFAULT_BUDGET_S", 0.3)
    try:
        r = client.get("/api/video/discover/list?key=now_playing")
        d = r.get_json()
        assert r.status_code == 200
        assert [it["tmdb_id"] for it in d["items"]] == [100, 101, 102]
        assert d["has_more"] is True and d["next_page"] == 2
    finally:
        release.set()


def test_list_route_failure_is_not_an_empty_rail(tmp_path, monkeypatch):
    """#2158: a broken list used to answer 200 with no items, which the page
    read as 'nothing here' and dropped the row. now it's a 502 the page retries."""
    import core.video.enrichment.engine as eng_mod

    client = _client(tmp_path)

    class FakeEng:
        def trending(self, window="week", kind=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: FakeEng())
    r = client.get("/api/video/discover/list?key=trending_movies_today&lang=any")
    assert r.status_code == 502
    assert r.get_json()["error"]
