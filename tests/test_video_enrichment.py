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
from core.video.enrichment.clients import TMDBClient


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


def test_worker_process_one_not_found(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "X"})
    w = VideoEnrichmentWorker(db, "tmdb", FakeClient(None))
    assert w.process_one() is True
    with db.connect() as c:
        assert c.execute("SELECT tmdb_match_status FROM movies").fetchone()["tmdb_match_status"] == "not_found"
    assert w.stats["not_found"] == 1


def test_worker_process_one_no_items_returns_false(db):
    assert VideoEnrichmentWorker(db, "tmdb", FakeClient(None)).process_one() is False


def test_worker_match_exception_marks_not_found_and_counts_error(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "X"})

    class Boom:
        enabled = True
        def match(self, *a, **k): raise RuntimeError("api down")

    w = VideoEnrichmentWorker(db, "tmdb", Boom())
    assert w.process_one() is True   # doesn't crash the loop
    assert w.stats["errors"] == 1
    with db.connect() as c:
        assert c.execute("SELECT tmdb_match_status FROM movies").fetchone()["tmdb_match_status"] == "not_found"


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
