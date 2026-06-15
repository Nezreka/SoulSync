"""Seam tests for the video enrichment workers (experimental branch).

The worker's loop/queue/status logic is driven by a FAKE client so it's tested
without hitting TMDB/TVDB. Also guards that the enrichment package imports
nothing from the music side.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from database.video_database import VideoDatabase
from core.video.enrichment.worker import VideoEnrichmentWorker
from core.video.enrichment.engine import VideoEnrichmentEngine
from core.video.enrichment.clients import TMDBClient, TVDBClient


class FakeClient:
    enabled = True

    def __init__(self, result):
        self._result = result
        self.calls = []

    def match(self, kind, title, year, known_id=None):
        self.calls.append((kind, title, year, known_id))
        return self._result


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_worker_process_one_matches(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "year": 2021})
    client = FakeClient({"id": 438631, "metadata": {"overview": "O", "backdrop_url": "/b.jpg"}})
    w = VideoEnrichmentWorker(db, "tmdb", client)
    assert w.process_one() is True
    assert client.calls == [("movie", "Dune", 2021, None)]    # no server id → search
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, tmdb_match_status, overview FROM movies").fetchone()
    assert (row["tmdb_id"], row["tmdb_match_status"], row["overview"]) == (438631, "matched", "O")
    assert w.stats["matched"] == 1


def test_worker_enriches_by_server_id_instead_of_searching(db):
    # The server already gave us tmdb_id during the scan → the worker must pass
    # it through (enrich BY ID, no title re-search).
    db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "year": 2021, "tmdb_id": 438631})
    client = FakeClient({"id": 438631, "metadata": {"overview": "O"}})
    w = VideoEnrichmentWorker(db, "tmdb", client)
    assert w.process_one() is True
    assert client.calls == [("movie", "Dune", 2021, 438631)]   # known_id forwarded
    assert db.enrichment_next("tmdb") is None                  # nothing left pending


def test_enrichment_next_surfaces_known_id(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "A", "tmdb_id": 99})
    nxt = db.enrichment_next("tmdb")
    assert nxt["known_id"] == 99 and nxt["kind"] == "movie"


def test_tmdb_client_with_known_id_skips_the_search(monkeypatch):
    # With a known id, the client must hit /movie/<id> directly and never the
    # /search endpoint (no chance of a wrong title-search match).
    urls = []

    class _Resp:
        def json(self):
            return {"overview": "by-id overview", "backdrop_path": "/b.jpg"}

    fake = types.SimpleNamespace(get=lambda url, **kw: (urls.append(url), _Resp())[1])
    monkeypatch.setitem(sys.modules, "requests", fake)

    res = TMDBClient("KEY").match("movie", "Whatever", 2021, known_id=438631)
    assert res["id"] == 438631
    assert any("/movie/438631" in u for u in urls)
    assert not any("/search/" in u for u in urls)


def test_tmdb_pulls_full_metadata(monkeypatch):
    # Everything TMDB offers comes from the one detail call.
    class _Resp:
        def __init__(self, b): self._b = b
        def raise_for_status(self): pass
        def json(self): return self._b
    detail = {"overview": "O", "tagline": "Fear", "vote_average": 8.4, "runtime": 155,
              "genres": [{"name": "Sci-Fi"}, {"name": "Drama"}], "status": "Released",
              "backdrop_path": "/b.jpg", "external_ids": {"imdb_id": "tt1"}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    m = TMDBClient("KEY").match("movie", "Dune", 2021, known_id=438631)["metadata"]
    assert m["tagline"] == "Fear" and m["rating"] == 8.4 and m["runtime_minutes"] == 155
    assert m["genres"] == ["Sci-Fi", "Drama"] and m["status"] == "Released" and m["imdb_id"] == "tt1"


def test_enrichment_next_priority_pins_kind_first(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": []})
    assert db.enrichment_next("tmdb")["kind"] == "movie"                 # default: movie first
    assert db.enrichment_next("tmdb", priority="show")["kind"] == "show"  # pinned
    assert db.enrichment_next("tmdb", priority="movie")["kind"] == "movie"


def test_worker_respects_global_priority_setting(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": []})
    db.set_setting("enrichment_priority", "show")
    client = FakeClient({"id": 1, "metadata": {}})
    VideoEnrichmentWorker(db, "tmdb", client).process_one()
    assert client.calls[0][0] == "show"                                  # processed shows first


def test_show_worker_cascades_episode_backfill(db):
    # A matched show backfills its episodes' art via the client's season_episodes
    # cascade (episodes ride along with their show — no separate queue).
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1}, {"episode_number": 2}]}]})

    class CascadeClient:
        enabled = True
        def match(self, kind, title, year, known_id=None):
            return {"id": 1396, "metadata": {}}
        def season_episodes(self, tv_id, snum):
            assert tv_id == 1396
            return {"overview": "S%d" % snum, "episodes": [
                {"episode_number": 1, "still_url": "/e1.jpg", "overview": "O1", "rating": 8.0}]}

    w = VideoEnrichmentWorker(db, "tmdb", CascadeClient())
    assert w.process_one() is True
    eps = {e["episode_number"]: e for e in db.show_detail(sid)["seasons"][0]["episodes"]}
    assert eps[1]["has_still"] is True and eps[2]["has_still"] is False   # cascade filled E1


def test_get_stats_excludes_episode_coverage_from_pending(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1}]}]})   # episode has no still
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1)   # show done
    stats = VideoEnrichmentWorker(db, "tmdb", FakeClient(None)).get_stats()
    assert "episode" in stats["breakdown"]            # manager sees episode coverage
    assert stats["stats"]["pending"] == 0             # but it doesn't block "Complete"


def test_tmdb_season_episodes_parses(monkeypatch):
    class _Resp:
        def __init__(self, b): self._b = b
        def raise_for_status(self): pass
        def json(self): return self._b
    body = {"overview": "Season 1", "episodes": [
        {"episode_number": 1, "still_path": "/a.jpg", "overview": "O", "vote_average": 8.1},
        {"episode_number": 2, "still_path": None, "overview": "P", "vote_average": 0}]}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    res = TMDBClient("KEY").season_episodes(1396, 1)
    assert res["overview"] == "Season 1" and len(res["episodes"]) == 2
    assert res["episodes"][0]["still_url"] == "https://image.tmdb.org/t/p/original/a.jpg"
    assert "still_url" not in res["episodes"][1]      # no still_path → omitted


def test_tmdb_show_returns_season_posters(monkeypatch):
    class _Resp:
        def __init__(self, b): self._b = b
        def raise_for_status(self): pass
        def json(self): return self._b
    detail = {"overview": "O", "external_ids": {}, "seasons": [
        {"season_number": 1, "poster_path": "/a.jpg"},
        {"season_number": 2, "poster_path": None}]}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    m = TMDBClient("KEY").match("show", "Show", 2020, known_id=1396)["metadata"]
    assert m["seasons"] == [{"season_number": 1,
                             "poster_url": "https://image.tmdb.org/t/p/original/a.jpg"}]


def test_tmdb_client_raises_on_rate_limit(monkeypatch):
    # A 429 must raise (→ worker records 'error'), not silently return no-match.
    class _Resp429:
        status_code = 429
        def raise_for_status(self):
            raise RuntimeError("429 Too Many Requests")
        def json(self):
            return {}

    fake = types.SimpleNamespace(get=lambda url, **kw: _Resp429())
    monkeypatch.setitem(sys.modules, "requests", fake)
    with pytest.raises(Exception):
        TMDBClient("KEY").match("movie", "Dune", 2021)


def test_tvdb_client_refreshes_expired_token(monkeypatch):
    # Cached token returns 401 → the client must re-login once and retry, not fail.
    logins = []
    state = {"calls": 0}

    class _Resp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("HTTP " + str(self.status_code))
        def json(self):
            return self._body

    def fake_post(url, **kw):
        logins.append(url)
        return _Resp(200, {"data": {"token": "tok%d" % len(logins)}})

    def fake_get(url, **kw):
        state["calls"] += 1
        if state["calls"] == 1:
            return _Resp(401, {})                      # stale token rejected
        return _Resp(200, {"data": [{"tvdb_id": 77, "overview": "O"}]})

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get, post=fake_post))
    res = TVDBClient("KEY").match("show", "Some Show", 2020)
    assert res["id"] == 77
    assert len(logins) == 2          # initial login + one refresh after the 401


def test_worker_logs_match_progress(db, caplog):
    # The worker must log each match at INFO (under soulsync.*) so progress is
    # visible in app.log like the music workers — otherwise it looks dead.
    db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "year": 2021})
    w = VideoEnrichmentWorker(db, "tmdb", FakeClient({"id": 438631, "metadata": {}}))
    with caplog.at_level("INFO", logger="soulsync.video_enrichment.worker"):
        w.process_one()
    assert any("Matched movie 'Dune'" in r.message and "TMDB ID: 438631" in r.message
               for r in caplog.records)


def test_worker_process_one_not_found(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "X"})
    w = VideoEnrichmentWorker(db, "tmdb", FakeClient(None))
    assert w.process_one() is True
    with db.connect() as c:
        assert c.execute("SELECT tmdb_match_status FROM movies").fetchone()["tmdb_match_status"] == "not_found"
    assert w.stats["not_found"] == 1


def test_worker_process_one_no_items_returns_false(db):
    assert VideoEnrichmentWorker(db, "tmdb", FakeClient(None)).process_one() is False


def test_worker_match_exception_marks_error_not_notfound(db):
    # A failed CALL must be recorded as 'error' (a transient failure), NOT
    # 'not_found' — otherwise a network blip permanently logs the item as "no
    # match" and it won't retry for retry_days. Mirrors the music workers.
    db.upsert_movie("plex", {"server_id": "m1", "title": "X"})

    class Boom:
        enabled = True
        def match(self, *a, **k): raise RuntimeError("api down")

    w = VideoEnrichmentWorker(db, "tmdb", Boom())
    assert w.process_one() is True   # doesn't crash the loop
    assert w.stats["errors"] == 1
    with db.connect() as c:
        assert c.execute("SELECT tmdb_match_status FROM movies").fetchone()["tmdb_match_status"] == "error"


def test_errored_item_is_retried_after_retry_days(db):
    # An 'error' row is re-queued by enrichment_next once it's older than the
    # retry window (just like 'not_found'), so transient failures recover.
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "X"})
    db.enrichment_apply("tmdb", "movie", mid, matched=False, error=True)
    # Just attempted → still inside the retry window → not yet due.
    assert db.enrichment_next("tmdb", retry_days=30) is None
    # Backdate the attempt → now older than the window → re-queued for retry.
    with db.connect() as c:
        c.execute("UPDATE movies SET tmdb_last_attempted='2000-01-01 00:00:00' WHERE id=?", (mid,))
        c.commit()
    again = db.enrichment_next("tmdb", retry_days=30)
    assert again is not None and again["id"] == mid


def test_worker_get_stats_shape(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "X"})
    s = VideoEnrichmentWorker(db, "tmdb", FakeClient(None)).get_stats()
    assert {"enabled", "running", "paused", "idle", "current_item", "stats",
            "progress", "breakdown"} <= set(s)
    assert s["enabled"] is True
    assert s["stats"]["pending"] == 1


def test_worker_disabled_without_key(db):
    class NoKey:
        enabled = False

    w = VideoEnrichmentWorker(db, "tmdb", NoKey())
    assert w.enabled is False
    assert w.get_stats()["enabled"] is False


def test_engine_builds_and_lists_workers(db):
    eng = VideoEnrichmentEngine(db, {"tmdb": FakeClient(None), "tvdb": FakeClient(None)})
    assert {s["id"] for s in eng.services()} == {"tmdb", "tvdb"}
    assert eng.worker("tmdb") is not None and eng.worker("nope") is None


def test_pause_persists_to_db_and_resume_clears_it(db):
    w = VideoEnrichmentWorker(db, "tmdb", FakeClient(None))
    w.pause()
    assert db.get_setting("tmdb_paused") == "1"
    w.resume()
    assert db.get_setting("tmdb_paused") == "0"


def test_paused_state_survives_a_fresh_worker(db):
    VideoEnrichmentWorker(db, "tmdb", FakeClient(None)).pause()
    # A brand-new worker (as if after restart) restores the saved pause.
    fresh = VideoEnrichmentWorker(db, "tmdb", FakeClient(None))
    assert fresh.paused is False           # not restored until asked
    fresh.restore_paused()
    assert fresh.paused is True


def test_engine_restores_paused_workers_on_build(db):
    db.set_setting("tvdb_paused", "1")     # tvdb was paused before "restart"
    eng = VideoEnrichmentEngine(db, {"tmdb": FakeClient(None), "tvdb": FakeClient(None)})
    assert eng.worker("tvdb").paused is True
    assert eng.worker("tmdb").paused is False


def test_scan_pause_resumes_only_what_it_paused(db):
    eng = VideoEnrichmentEngine(db, {"tmdb": FakeClient(None), "tvdb": FakeClient(None)})
    # User manually paused tvdb (persisted); tmdb is running.
    eng.worker("tvdb").pause()
    assert eng.worker("tvdb").paused and not eng.worker("tmdb").paused

    paused = eng.pause_for_scan()
    assert paused == {"tmdb"}                        # only the running one
    assert eng.worker("tmdb").paused and eng.worker("tvdb").paused

    eng.resume_after_scan()
    assert not eng.worker("tmdb").paused             # we paused it → resumed
    assert eng.worker("tvdb").paused                 # user's pause left alone


def test_scan_pause_does_not_persist(db):
    eng = VideoEnrichmentEngine(db, {"tmdb": FakeClient(None)})
    eng.pause_for_scan()
    # Transient: the durable flag is untouched, so a restart mid-scan won't
    # restore the worker as "paused".
    assert (db.get_setting("tmdb_paused") or "") != "1"
    eng.resume_after_scan()
    assert (db.get_setting("tmdb_paused") or "") != "1"


def test_enrichment_package_imports_nothing_from_music():
    base = Path(__file__).resolve().parent.parent / "core" / "video" / "enrichment"
    for py in base.glob("*.py"):
        for line in py.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("import ") or s.startswith("from "):
                assert "music" not in s.lower(), f"{py.name}: music import leaked: {s!r}"
