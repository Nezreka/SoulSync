"""#2121: manual video search takes the query as typed, like prowlarr's own
search box. the modal opens with the usual query prefilled; leave it and the
usual search runs (ids, structured, scene text), edit it and exactly the typed
words go to every source, nothing added.
"""

from __future__ import annotations

import pytest
from flask import Flask

import core.video.prowlarr_search as ps
from api.video.downloads import _search_queries, _torrent_lane_hits, custom_query
from database.video_database import VideoDatabase


class _Client:
    def __init__(self):
        self.calls = []

    def is_configured(self):
        return True

    def _search_sync(self, q, cats, ids, limit, search_type="search", extra_params=None,
                     max_wait_seconds=None):
        self.calls.append((search_type, q, list(extra_params or [])))
        return []


@pytest.fixture()
def client(monkeypatch):
    c = _Client()
    monkeypatch.setattr(ps, "_client", lambda: c)
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    return c


def test_custom_query_is_the_typed_words_or_none():
    assert custom_query({"query": "  Primal   S02  MULTi "}) == "Primal S02 MULTi"
    assert custom_query({"query": ""}) is None
    assert custom_query({}) is None
    assert custom_query(None) is None
    assert len(custom_query({"query": "x" * 500})) == 200


def test_prowlarr_runs_only_the_typed_text(client):
    ps.prowlarr_search("episode", "Primal", season=2, episode=3, tvdb_id=5, source="torrent",
                       query="Primal 2019 S02 VOSTFR")
    assert client.calls == [("search", "Primal 2019 S02 VOSTFR", [])]


def test_without_a_query_the_usual_strategies_run(client):
    ps.prowlarr_search("episode", "Primal", season=2, episode=3, tvdb_id=5, source="torrent")
    types = {t for t, _q, _e in client.calls}
    assert types == {"tvsearch", "search"}


def test_the_torrent_lane_passes_the_query_through(client, monkeypatch):
    import core.video.extto_search as ex
    seen = []
    monkeypatch.setattr(ex, "extto_search",
                        lambda q, **k: seen.append(q) or {"configured": True, "hits": []})
    _torrent_lane_hits({"query": "Dune 2021 MULTi", "title": "Dune"},
                       "movie", "Dune", None, None, "torrent")
    assert client.calls == [("search", "Dune 2021 MULTi", [])]
    assert seen == ["Dune 2021 MULTi"]


def test_queries_shown_are_the_typed_ones():
    assert _search_queries({"query": "Dune MULTi"}, "torrent", "movie", "Dune", None, None) == [
        "search: Dune MULTi"]
    assert _search_queries({"query": "Dune MULTi"}, "soulseek", "movie", "Dune", None, None) == [
        "Dune MULTi"]


@pytest.fixture()
def app(tmp_path):
    import api.video as videoapi
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = vdb
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def test_soulseek_search_starts_with_the_typed_text(app, monkeypatch):
    import core.video.slskd_search as ss
    seen = []
    monkeypatch.setattr(ss, "start_search",
                        lambda q, **k: seen.append(q) or {"configured": True, "id": "abc"})
    out = app.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "Primal", "season": 2, "episode": 3,
        "source": "soulseek", "query": "Primal S02E03 FRENCH"}).get_json()
    assert out["id"] == "abc" and seen == ["Primal S02E03 FRENCH"]
    seen.clear()
    app.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "Primal", "season": 2, "episode": 3, "source": "soulseek"})
    assert seen == ["Primal S02E03"]
