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
from core.video.enrichment.clients import TMDBClient, TVDBClient, OMDBClient


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


def test_refresh_show_art_backfills_seasons_even_when_already_matched(db):
    # The exact failing case: a MATCHED show (won't re-run via the queue) still
    # has no season posters. Lazy refresh fetches + caches them anyway.
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tmdb_id": 1396,
                                       "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1396)   # already matched
    assert db.show_detail(sid)["seasons"][0]["has_poster"] is False

    class C:
        enabled = True
        def match(self, kind, title, year, known_id=None):
            assert known_id == 1396
            return {"id": 1396, "metadata": {"seasons": [
                {"season_number": 1, "poster_url": "https://img/s1.jpg"}]}}
        def season_episodes(self, tv, sn): return None

    eng = VideoEnrichmentEngine(db, {"tmdb": C()})
    assert eng.refresh_show_art(sid)["ok"] is True
    assert db.show_detail(sid)["seasons"][0]["has_poster"] is True


def test_refresh_show_art_needs_tmdb_configured(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": []})

    class Off:
        enabled = False
        def match(self, *a, **k): return None

    res = VideoEnrichmentEngine(db, {"tmdb": Off()}).refresh_show_art(sid)
    assert res["ok"] is False and res["reason"] == "tmdb_not_configured"


def test_episode_sync_next_only_matched_unsynced(db):
    a = db.upsert_show_tree("plex", {"server_id": "s1", "title": "A", "tmdb_id": 1, "seasons": []})
    db.upsert_show_tree("plex", {"server_id": "s2", "title": "B", "seasons": []})   # no tmdb_id
    assert db.episode_sync_next()["id"] == a            # a is matched + unsynced; b skipped
    db.mark_episodes_synced(a)
    assert db.episode_sync_next() is None and db.episode_sync_pending_count() == 0


def test_worker_background_episode_sync_pulls_full_list(db):
    # A show matched BEFORE the cascade feature: tmdb_id set, only owned episodes,
    # episodes_synced still 0. With the match queue clear, the worker syncs it.
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tmdb_id": 1396, "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": 1, "file": {"relative_path": "e1.mkv"}}]}]})
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1396)
    assert db.episode_sync_pending_count() == 1

    class C:
        enabled = True
        def match(self, kind, title, year, known_id=None):
            return {"id": 1396, "metadata": {"seasons": [{"season_number": 1, "poster_url": None}]}}
        def season_episodes(self, tv, sn):
            return {"episodes": [{"episode_number": 1}, {"episode_number": 2}, {"episode_number": 3}]}

    w = VideoEnrichmentWorker(db, "tmdb", C())
    assert w.process_one() is True                       # no match pending → episode sync runs
    s1 = db.show_detail(sid)["seasons"][0]
    assert (s1["episode_total"], s1["episode_owned"]) == (3, 1)   # full list now
    assert db.episode_sync_pending_count() == 0


def test_detail_backfill_next_only_matched_unsynced(db):
    # Matched items (have tmdb_id) that haven't had details backfilled are queued;
    # un-matched ones are skipped. Shows + movies are independent queues.
    a = db.upsert_show_tree("plex", {"server_id": "s1", "title": "A", "tmdb_id": 1, "seasons": []})
    db.upsert_show_tree("plex", {"server_id": "s2", "title": "B", "seasons": []})   # no tmdb_id → skip
    nx = db.detail_backfill_next("show")
    assert nx and nx["id"] == a and nx["kind"] == "show" and nx["tmdb_id"] == 1
    db.mark_details_synced("show", a)
    assert db.detail_backfill_next("show") is None
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M", "tmdb_id": 9})
    assert db.detail_backfill_next("movie")["id"] == mid
    db.mark_details_synced("movie", mid)
    assert db.detail_backfill_next("movie") is None
    assert db.detail_backfill_pending_count() == 0


def test_worker_detail_backfill_fills_status(db):
    # The real bug: a show pre-matched by the server (tmdb_id set) + already
    # episode-synced, but `status` never captured (server doesn't supply it, the
    # matcher skips matched rows). With the match + episode queues clear, the worker
    # re-fetches details and gap-fills `status` — what the airing-watchlist needs.
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tmdb_id": 1396, "seasons": []})
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=1396)
    db.mark_episodes_synced(sid)                       # episode queue clear → detail backfill runs
    assert db.detail_backfill_pending_count() == 1
    client = FakeClient({"id": 1396, "metadata": {"status": "Returning Series", "network": "HBO"}})
    w = VideoEnrichmentWorker(db, "tmdb", client)
    assert w.process_one() is True                     # no match/episode work → detail backfill
    assert client.calls == [("show", "S", None, 1396)]  # enrich BY id, no re-search
    with db.connect() as c:
        row = c.execute("SELECT status, details_synced FROM shows WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "Returning Series"
    assert row["details_synced"] == 1
    assert db.detail_backfill_pending_count() == 0     # done → not re-picked


def test_detail_backfill_marks_done_even_when_status_absent(db):
    # If TMDB returns no status, we still mark it attempted so it isn't re-fetched
    # forever (bounded: one attempt per item).
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tmdb_id": 5, "seasons": []})
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=5)
    db.mark_episodes_synced(sid)
    w = VideoEnrichmentWorker(db, "tmdb", FakeClient({"id": 5, "metadata": {}}))
    assert w.process_one() is True
    assert db.detail_backfill_pending_count() == 0


def test_show_match_info(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "year": 2019,
                                       "tmdb_id": 1396, "seasons": []})
    assert db.show_match_info(sid) == {"title": "S", "year": 2019, "tmdb_id": 1396}
    assert db.show_match_info(999999) is None


class _Resp:
    def __init__(self, b): self._b = b
    def raise_for_status(self): pass
    def json(self): return self._b


def test_tmdb_search_parses_multi(monkeypatch):
    body = {"results": [
        {"media_type": "movie", "id": 1, "title": "Dune", "release_date": "2021-10-22",
         "poster_path": "/d.jpg", "vote_average": 8.0},
        {"media_type": "tv", "id": 2, "name": "Loki", "first_air_date": "2021-06-09", "poster_path": "/l.jpg"},
        {"media_type": "person", "id": 3, "name": "Zendaya", "profile_path": "/z.jpg",
         "known_for_department": "Acting",
         "known_for": [{"title": "Dune"}, {"name": "Euphoria"}]},
        {"media_type": "company", "id": 9, "name": "ignore me"}]}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    res = TMDBClient("KEY").search("d")
    kinds = [r["kind"] for r in res]
    assert kinds == ["movie", "show", "person"]              # company dropped
    assert res[0] == {"kind": "movie", "tmdb_id": 1, "title": "Dune", "year": "2021",
                      "overview": None, "rating": 8.0,
                      "poster": "https://image.tmdb.org/t/p/w300/d.jpg"}
    assert res[2]["known_for"] == "Dune, Euphoria"


def test_tmdb_full_detail_movie(monkeypatch):
    detail = {"id": 1, "title": "Dune", "overview": "O", "release_date": "2021-10-22",
              "runtime": 155, "vote_average": 8.0, "tagline": "Fear is the mind-killer",
              "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
              "genres": [{"name": "Sci-Fi"}], "production_companies": [{"name": "Legendary"}],
              "external_ids": {"imdb_id": "tt1160419"},
              "credits": {"cast": [{"name": "Timothée", "id": 11, "character": "Paul", "profile_path": "/t.jpg"}],
                          "crew": [{"name": "Denis", "id": 12, "job": "Director"}]},
              "images": {"logos": [{"iso_639_1": "en", "file_path": "/logo.png"}]}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    d = TMDBClient("KEY").full_detail("movie", 1)
    assert d["title"] == "Dune" and d["year"] == "2021" and d["studio"] == "Legendary"
    assert d["poster_url"] == "https://image.tmdb.org/t/p/original/p.jpg"
    assert d["cast"][0] == {"name": "Timothée", "character": "Paul",
                            "photo": "https://image.tmdb.org/t/p/w185/t.jpg", "tmdb_id": 11}
    assert d["imdb_id"] == "tt1160419" and d["logo"].endswith("/logo.png")
    assert "videos" not in d["_extras"] and "similar" not in d["_extras"]   # none in this body
    assert d["_extras"]["cast_full"][0]["character"] == "Paul"              # full cast parsed


def test_engine_tmdb_detail_redirects_when_owned(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Owned", "tmdb_id": 77})

    class Tmdb:
        enabled = True
        def full_detail(self, kind, tid, region="US"): raise AssertionError("must not fetch an owned title")
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    assert eng.tmdb_detail("movie", 77) == {"redirect": {"source": "library", "kind": "movie", "id": mid}}


def test_engine_tmdb_detail_assembles_show(db):
    class Tmdb:
        enabled = True
        def full_detail(self, kind, tid, region="US"):
            return {"kind": "show", "tmdb_id": tid, "title": "Loki", "imdb_id": None,
                    "poster_url": "http://p", "backdrop_url": None, "cast": [], "crew": [],
                    "_extras": {"similar": [{"title": "X", "tmdb_id": 5, "kind": "show"}]},
                    "_seasons": [{"season_number": 1, "title": "Season 1",
                                  "poster_url": "http://s1", "episode_count": 6}]}
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    d = eng.tmdb_detail("show", 84958)
    assert d["source"] == "tmdb" and d["owned"] is False and d["id"] == 84958
    assert d["has_poster"] is True and d["has_backdrop"] is False
    assert d["similar"][0]["tmdb_id"] == 5
    s = d["seasons"][0]
    assert s["episode_total"] == 6 and s["episode_owned"] == 0 and s["episodes"] == []
    assert d["season_count"] == 1 and d["episode_total"] == 6


def test_engine_search_annotates_library(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Owned", "tmdb_id": 1})

    class Tmdb:
        enabled = True
        def search(self, q):
            return [{"kind": "movie", "tmdb_id": 1, "title": "Owned"},
                    {"kind": "movie", "tmdb_id": 2, "title": "Not owned"},
                    {"kind": "person", "tmdb_id": 3, "title": "Someone"}]
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    res = eng.search("own")
    assert res[0]["library_id"] == mid
    assert res[1]["library_id"] is None
    assert "library_id" not in res[2]                         # people aren't library-matched


def test_person_detail_caches_tmdb_call(db):
    calls = []
    class Tmdb:
        enabled = True
        def person(self, tid): calls.append(tid); return {"tmdb_id": tid, "name": "P", "credits": []}
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    eng.person_detail(7); eng.person_detail(7)
    assert calls == [7]                           # second view served from cache


def test_tmdb_season_caches_tmdb_call(db):
    calls = []
    class Tmdb:
        enabled = True
        def season_episodes(self, tv, sn): calls.append((tv, sn)); return {"overview": "S", "episodes": []}
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    eng.tmdb_season(9, 1); eng.tmdb_season(9, 1)
    assert calls == [(9, 1)]


def test_engine_trending_annotates_library(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Owned", "tmdb_id": 1})

    class Tmdb:
        enabled = True
        def trending(self):
            return [{"kind": "movie", "tmdb_id": 1, "title": "Owned"},
                    {"kind": "show", "tmdb_id": 2, "title": "Hot show"}]
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    res = eng.trending()
    assert res[0]["library_id"] == mid and res[1]["library_id"] is None


def test_tmdb_trending_parses(monkeypatch):
    body = {"results": [
        {"media_type": "movie", "id": 1, "title": "A", "release_date": "2024-01-01", "poster_path": "/a.jpg"},
        {"media_type": "tv", "id": 2, "name": "B", "first_air_date": "2023-05-05"},
        {"media_type": "person", "id": 3, "name": "C"}]}     # people excluded from the rail
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    res = TMDBClient("KEY").trending()
    assert [r["kind"] for r in res] == ["movie", "show"]
    assert res[0]["poster"] == "https://image.tmdb.org/t/p/w300/a.jpg"


def test_tmdb_person_credits_carry_department(monkeypatch):
    body = {"id": 5, "name": "X", "known_for_department": "Acting",
            "combined_credits": {
                "cast": [{"id": 1, "media_type": "movie", "title": "M", "character": "Hero",
                          "release_date": "2020-01-01", "popularity": 9}],
                "crew": [{"id": 2, "media_type": "movie", "title": "D", "job": "Director",
                          "department": "Directing", "release_date": "2019-01-01", "popularity": 3}]}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    p = TMDBClient("KEY").person(5)
    by = {c["title"]: c for c in p["credits"]}
    assert by["M"]["department"] == "Acting" and by["M"]["role"] == "Hero"
    assert by["D"]["department"] == "Directing" and by["D"]["role"] == "Director"


def test_engine_person_detail_annotates_credits(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Owned", "tmdb_id": 1})

    class Tmdb:
        enabled = True
        def person(self, tid):
            return {"tmdb_id": tid, "name": "P", "credits": [
                {"kind": "movie", "tmdb_id": 1, "title": "Owned"},
                {"kind": "show", "tmdb_id": 9, "title": "Other"}]}
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    p = eng.person_detail(55)
    assert p["credits"][0]["library_id"] == mid
    assert p["credits"][1]["library_id"] is None


def test_library_id_for_tmdb(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M", "tmdb_id": 500})
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tmdb_id": 600, "seasons": []})
    assert db.library_id_for_tmdb("movie", 500) == mid
    assert db.library_id_for_tmdb("show", 600) == sid
    assert db.library_id_for_tmdb("movie", 999) is None
    assert db.library_id_for_tmdb("bogus", 500) is None
    assert db.library_id_for_tmdb("movie", None) is None


def test_omdb_worker_processes_ratings_queue(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "A", "imdb_id": "tt1"})
    db.upsert_movie("plex", {"server_id": "m2", "title": "B"})        # no imdb_id → skipped

    class Omdb:
        enabled = True
        def ratings(self, imdb_id):
            assert imdb_id == "tt1"
            return {"imdb_rating": 8.0, "rt_rating": 90, "metacritic": 77}

    w = VideoEnrichmentWorker(db, "omdb", Omdb())
    assert w.is_ratings is True                       # ratings mode, not a matcher
    assert w.process_one() is True
    with db.connect() as c:
        r = c.execute("SELECT imdb_rating, ratings_synced FROM movies WHERE server_id='m1'").fetchone()
    assert r["imdb_rating"] == 8.0 and r["ratings_synced"] == 1
    assert db.ratings_next() is None                  # B has no imdb_id → nothing left
    assert w.process_one() is False


def test_omdb_worker_pauses_on_bad_key_without_burning_items(db):
    # A rejected key affects every title — the worker must pause (not churn the
    # whole library) and must NOT mark items synced, so they retry once fixed.
    from core.video.enrichment.clients import OMDbAuthError
    db.upsert_movie("plex", {"server_id": "m1", "title": "A", "imdb_id": "tt1"})

    class Omdb:
        enabled = True
        def ratings(self, imdb_id): raise OMDbAuthError("401")

    w = VideoEnrichmentWorker(db, "omdb", Omdb())
    assert w.process_one() is False               # backed off, didn't process
    assert w.paused is True and w.note            # paused itself with a reason
    assert db.ratings_next() is not None          # item NOT burned to 'synced'


def test_omdb_worker_cools_down_on_daily_limit_not_hard_pause(db):
    # 'Request limit reached!' is the free-tier daily quota — cool down + auto
    # resume (don't hard-pause), so a big library spreads across days.
    from core.video.enrichment.clients import OMDbAuthError
    db.upsert_movie("plex", {"server_id": "m1", "title": "A", "imdb_id": "tt1"})

    class Omdb:
        enabled = True
        def ratings(self, imdb_id): raise OMDbAuthError("Request limit reached!")

    w = VideoEnrichmentWorker(db, "omdb", Omdb())
    assert w.process_one() is False
    assert w.paused is False                       # NOT a hard pause (auto-resumes)
    assert w._cooldown_until > 0                    # cooling down instead
    assert w.get_stats()["cooldown"] is True and w.get_stats()["paused"] is True
    assert db.ratings_next() is not None           # item not burned to synced


def test_omdb_worker_keeps_item_on_transient_error(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "A", "imdb_id": "tt1"})

    class Omdb:
        enabled = True
        def ratings(self, imdb_id): raise RuntimeError("network blip")

    w = VideoEnrichmentWorker(db, "omdb", Omdb())
    assert w.process_one() is False
    assert db.ratings_next() is not None          # not marked synced → retried later
    assert w.stats["errors"] == 1 and w.paused is False   # one blip doesn't pause


def test_omdb_test_surfaces_real_error(monkeypatch):
    # The Test button should show OMDb's actual reason, not just "HTTP 401".
    class _R:
        status_code = 401
        def json(self): return {"Response": "False", "Error": "Request limit reached!"}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _R()))
    ok, msg = OMDBClient("KEY").test()
    assert ok is False and "Request limit reached!" in msg

    class _R2(_R):
        def json(self): return {"Response": "False", "Error": "Invalid API key!"}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _R2()))
    ok2, msg2 = OMDBClient("KEY").test()
    assert ok2 is False and "activation" in msg2.lower()   # nudges toward the email link


def test_omdb_ratings_raises_on_invalid_key(monkeypatch):
    from core.video.enrichment.clients import OMDbAuthError

    class _R:
        status_code = 401
        text = ""
        def raise_for_status(self): raise AssertionError("should short-circuit on 401")
        def json(self): return {}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _R()))
    with pytest.raises(OMDbAuthError):
        OMDBClient("BADKEY").ratings("tt0111161")


def test_omdb_breakdown_is_ratings_coverage(db):
    a = db.upsert_movie("plex", {"server_id": "m1", "title": "A", "imdb_id": "tt1"})   # pending
    db.upsert_movie("plex", {"server_id": "m2", "title": "B", "imdb_id": "tt2"})
    db.apply_ratings("movie", a, {"imdb_rating": 8.0})                  # one rated
    bd = db.enrichment_breakdown("omdb")
    assert bd["movie"]["matched"] == 1 and bd["movie"]["pending"] == 1
    assert "show" in bd


def test_omdb_ratings_parse(monkeypatch):
    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"Response": "True", "imdbRating": "8.4", "Metascore": "74",
                    "Ratings": [{"Source": "Rotten Tomatoes", "Value": "95%"},
                                {"Source": "Internet Movie Database", "Value": "8.4/10"}]}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp()))
    r = OMDBClient("KEY").ratings("tt1375666")
    assert r == {"imdb_rating": 8.4, "rt_rating": 95, "metacritic": 74}


def test_refresh_show_art_backfills_omdb_ratings(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tmdb_id": 1396,
                                       "imdb_id": "tt0903747", "seasons": []})

    class Tmdb:
        enabled = True
        def match(self, *a, **k): return {"id": 1396, "metadata": {}}
        def season_episodes(self, *a, **k): return None
    class Omdb:
        enabled = True
        def ratings(self, imdb_id):
            assert imdb_id == "tt0903747"
            return {"imdb_rating": 9.5, "rt_rating": 96, "metacritic": 90}

    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()}, ratings_client=Omdb())
    assert eng.refresh_show_art(sid)["ok"] is True
    d = db.show_detail(sid)
    assert (d["imdb_rating"], d["rt_rating"], d["metacritic"]) == (9.5, 96, 90)


def test_refresh_movie_art_backfills_cast_and_genres(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 438631})

    class C:
        enabled = True
        def match(self, kind, title, year, known_id=None):
            assert kind == "movie" and known_id == 438631
            return {"id": 438631, "metadata": {"genres": ["Sci-Fi"],
                                               "cast": [{"name": "Timothee", "tmdb_id": 1}]}}

    assert VideoEnrichmentEngine(db, {"tmdb": C()}).refresh_movie_art(mid)["ok"] is True
    d = db.movie_detail(mid)
    assert d["genres"] == ["Sci-Fi"] and d["cast"][0]["name"] == "Timothee"


def test_movie_match_info(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "year": 2021, "tmdb_id": 438631})
    assert db.movie_match_info(mid) == {"title": "Dune", "year": 2021, "tmdb_id": 438631}
    assert db.movie_match_info(999999) is None


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


def test_tmdb_extras_parse(monkeypatch):
    class _Resp:
        def __init__(self, b): self._b = b
        def raise_for_status(self): pass
        def json(self): return self._b
    detail = {
        "videos": {"results": [
            {"site": "YouTube", "type": "Teaser", "key": "tease"},
            {"site": "YouTube", "type": "Trailer", "key": "trail"}]},
        "watch/providers": {"results": {"US": {"link": "http://w", "flatrate": [
            {"provider_name": "Netflix", "logo_path": "/n.jpg"}]}}},
        "similar": {"results": [{"id": 5, "title": "Other", "poster_path": "/o.jpg"}]}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    ex = TMDBClient("KEY").extras("movie", 438631)
    assert ex["trailer"]["key"] == "trail"                           # Trailer beats Teaser
    assert ex["providers"][0] == {"name": "Netflix", "logo": "https://image.tmdb.org/t/p/original/n.jpg"}
    assert ex["providers_link"] == "http://w"
    assert ex["similar"][0]["title"] == "Other" and ex["similar"][0]["kind"] == "movie"


def _no_server_config(monkeypatch):
    """Stub the shared config_manager so no media-server watch link is added
    (keeps these tests independent of the dev machine's Plex/Jellyfin config)."""
    import config.settings as cs
    class CM:
        def get_plex_config(self): return {}
        def get_jellyfin_config(self): return {}
    monkeypatch.setattr(cs, "config_manager", CM())


def test_tmdb_extras_recommendations_and_collection(monkeypatch):
    detail = {
        "recommendations": {"results": [
            {"id": 7, "title": "Rec", "media_type": "movie", "poster_path": "/r.jpg"}]},
        "belongs_to_collection": {"id": 99, "name": "Saga Collection", "poster_path": "/c.jpg"}}
    collection = {"parts": [
        {"id": 2, "title": "Second", "release_date": "2003-01-01"},
        {"id": 1, "title": "First", "release_date": "2001-01-01", "poster_path": "/1.jpg"}]}

    def fake_get(url, **k):
        return _Resp(collection if "/collection/" in url else detail)
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
    ex = TMDBClient("KEY").extras("movie", 1)
    assert ex["recommendations"][0]["tmdb_id"] == 7 and ex["recommendations"][0]["kind"] == "movie"
    assert ex["collection"]["name"] == "Saga Collection"
    assert [c["title"] for c in ex["collection"]["items"]] == ["First", "Second"]   # release order


def test_tmdb_extras_gallery_videos_keywords_facts(monkeypatch):
    detail = {
        "budget": 63000000, "revenue": 463517383, "original_language": "en",
        "production_countries": [{"name": "United States"}],
        "images": {"backdrops": [{"file_path": "/b.jpg"}], "posters": [{"file_path": "/p.jpg"}]},
        "videos": {"results": [
            {"site": "YouTube", "type": "Featurette", "key": "f1", "name": "Making of"},
            {"site": "YouTube", "type": "Trailer", "key": "t1", "name": "Trailer"}]},
        "keywords": {"keywords": [{"name": "hacker"}, {"name": "dystopia"}]},
        "credits": {"cast": [{"id": 1, "name": "Keanu", "character": "Neo", "profile_path": "/k.jpg"}]}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    ex = TMDBClient("KEY").extras("movie", 603)
    assert ex["facts"]["budget"] == 63000000 and ex["facts"]["countries"] == ["United States"]
    assert ex["gallery"]["backdrops"][0]["thumb"].endswith("/w780/b.jpg")
    assert ex["gallery"]["backdrops"][0]["full"].endswith("/original/b.jpg")
    assert [v["type"] for v in ex["videos"]] == ["Trailer", "Featurette"]   # trailer ordered first
    assert ex["keywords"] == ["hacker", "dystopia"]
    assert ex["cast_full"][0]["character"] == "Neo"


def test_tmdb_extras_featured_review(monkeypatch):
    detail = {"reviews": {"results": [
        {"author": "Roger", "content": "A masterpiece.", "author_details": {"rating": 9},
         "created_at": "2021-10-22T00:00:00.000Z"}]}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    ex = TMDBClient("KEY").extras("movie", 1)
    assert ex["review"] == {"author": "Roger", "content": "A masterpiece.",
                            "rating": 9, "created": "2021-10-22"}


def test_tmdb_extras_tv_full_cast_episode_counts(monkeypatch):
    detail = {"aggregate_credits": {"cast": [
        {"id": 1, "name": "Actor", "total_episode_count": 42, "roles": [{"character": "Lead"}]}]}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    ex = TMDBClient("KEY").extras("show", 1399)
    c = ex["cast_full"][0]
    assert c["character"] == "Lead" and c["episode_count"] == 42


def test_item_extras_caches_tmdb_call(db, monkeypatch):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "A", "tmdb_id": 603})
    import config.settings as cs
    class CM:
        def get_plex_config(self): return {}
        def get_jellyfin_config(self): return {}
    monkeypatch.setattr(cs, "config_manager", CM())
    calls = []
    class Tmdb:
        enabled = True
        def extras(self, kind, tid, region="US"): calls.append(tid); return {"keywords": ["x"]}
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    a = eng.item_extras("movie", mid)
    b = eng.item_extras("movie", mid)
    assert a == b == {"keywords": ["x"]}
    assert calls == [603]                     # second view served from cache


def test_tmdb_extras_tv_next_episode(monkeypatch):
    detail = {"next_episode_to_air": {"season_number": 3, "episode_number": 4,
                                      "name": "Finale", "air_date": "2026-12-25"}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    ex = TMDBClient("KEY").extras("show", 5)
    assert ex["next_episode"] == {"season_number": 3, "episode_number": 4, "name": "Finale",
                                  "air_date": "2026-12-25", "overview": None}


def test_item_extras_needs_tmdb_and_id(db, monkeypatch):
    _no_server_config(monkeypatch)
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "seasons": []})   # no tmdb_id

    class C:
        enabled = True
        def extras(self, kind, tid, region="US"): return {"trailer": {"key": "x"}}

    eng = VideoEnrichmentEngine(db, {"tmdb": C()})
    assert eng.item_extras("show", sid) == {}                        # no tmdb_id → no call
    sid2 = db.upsert_show_tree("plex", {"server_id": "s2", "title": "T", "tmdb_id": 1, "seasons": []})
    assert eng.item_extras("show", sid2) == {"trailer": {"key": "x"}}


def test_item_extras_adds_jellyfin_watch_link(db, monkeypatch):
    mid = db.upsert_movie("jellyfin", {"server_id": "abc123", "title": "Owned"})
    import config.settings as cs
    class CM:
        def get_jellyfin_config(self): return {"base_url": "http://jelly:8096/"}
        def get_plex_config(self): return {}
    monkeypatch.setattr(cs, "config_manager", CM())
    ex = VideoEnrichmentEngine(db, {}).item_extras("movie", mid)      # no tmdb worker needed
    assert ex["server"] == {"server": "Jellyfin",
                            "url": "http://jelly:8096/web/index.html#!/details?id=abc123"}


def test_item_extras_adds_plex_watch_link(db, monkeypatch):
    mid = db.upsert_movie("plex", {"server_id": "555", "title": "Owned"})
    import config.settings as cs
    class CM:
        def get_plex_config(self): return {"base_url": "http://plex:32400", "token": "T"}
        def get_jellyfin_config(self): return {}
    monkeypatch.setattr(cs, "config_manager", CM())
    class _R:
        text = ""
        def json(self): return {"MediaContainer": {"machineIdentifier": "MID123"}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _R()))
    ex = VideoEnrichmentEngine(db, {}).item_extras("movie", mid)
    assert ex["server"]["server"] == "Plex"
    assert "app.plex.tv" in ex["server"]["url"] and "MID123" in ex["server"]["url"]
    assert "%2Flibrary%2Fmetadata%2F555" in ex["server"]["url"]      # url-encoded item key


def test_item_extras_no_server_link_when_unowned(db, monkeypatch):
    # A wishlist-style row with no server id → no watch link.
    import config.settings as cs
    class CM:
        def get_plex_config(self): return {"base_url": "http://plex:32400", "token": "T"}
        def get_jellyfin_config(self): return {}
    monkeypatch.setattr(cs, "config_manager", CM())
    with db.connect() as c:
        c.execute("INSERT INTO movies (title, server_source, server_id) VALUES ('W', NULL, NULL)")
        c.commit()
        rid = c.execute("SELECT id FROM movies WHERE title='W'").fetchone()["id"]
    assert "server" not in VideoEnrichmentEngine(db, {}).item_extras("movie", rid)


def test_tmdb_episode_detail_parses_guests(monkeypatch):
    body = {"still_path": "/s.jpg", "vote_average": 8.4, "overview": "O", "runtime": 52,
            "air_date": "2024-01-01",
            "credits": {}, "guest_stars": [
                {"id": 5, "name": "Guest", "character": "Villain", "profile_path": "/g.jpg"},
                {"id": 6, "name": "NoPic"}]}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    d = TMDBClient("KEY").episode_detail(1399, 1, 1)
    assert d["still_url"] == "https://image.tmdb.org/t/p/original/s.jpg" and d["rating"] == 8.4
    assert d["guest_stars"][0] == {"name": "Guest", "character": "Villain", "tmdb_id": 5,
                                   "photo": "https://image.tmdb.org/t/p/w185/g.jpg"}
    assert d["guest_stars"][1]["photo"] is None


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


def test_tmdb_parses_cast_and_crew(monkeypatch):
    class _Resp:
        def __init__(self, b): self._b = b
        def raise_for_status(self): pass
        def json(self): return self._b
    detail = {"overview": "O", "external_ids": {}, "created_by": [{"id": 9, "name": "The Creator"}],
              "credits": {
                  "cast": [{"id": 1, "name": "Lead", "character": "Hero", "profile_path": "/p.jpg"},
                           {"id": 2, "name": "Support", "character": "Sidekick"}],
                  "crew": [{"id": 3, "name": "Dir", "job": "Director"},
                           {"id": 4, "name": "Edit", "job": "Editor"}]}}   # Editor filtered out
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    m = TMDBClient("KEY").match("show", "S", 2020, known_id=1396)["metadata"]
    assert [c["name"] for c in m["cast"]] == ["Lead", "Support"]
    assert m["cast"][0]["photo_url"] == "https://image.tmdb.org/t/p/w185/p.jpg"
    jobs = {(c["name"], c["job"]) for c in m["crew"]}
    assert ("Dir", "Director") in jobs and ("The Creator", "Creator") in jobs
    assert not any(c["name"] == "Edit" for c in m["crew"])   # non-headline job dropped


def test_tmdb_picks_english_clearlogo(monkeypatch):
    class _Resp:
        def __init__(self, b): self._b = b
        def raise_for_status(self): pass
        def json(self): return self._b
    detail = {"overview": "O", "external_ids": {}, "images": {"logos": [
        {"iso_639_1": "de", "file_path": "/de.png"},
        {"iso_639_1": "en", "file_path": "/en.png"}]}}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(detail)))
    m = TMDBClient("KEY").match("movie", "M", 2020, known_id=1)["metadata"]
    assert m["logo_url"] == "https://image.tmdb.org/t/p/w500/en.png"   # English preferred


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
    # The FULL season list now comes back (poster None where TMDB has none) so the
    # episode cascade can represent missing seasons too.
    assert m["seasons"] == [
        {"season_number": 1, "poster_url": "https://image.tmdb.org/t/p/original/a.jpg"},
        {"season_number": 2, "poster_url": None}]


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
    ids = {s["id"] for s in eng.services()}
    assert {"tmdb", "tvdb"} <= ids                         # the matcher workers
    # The backfill workers (artwork / subtitles / no-key YouTube extras) are always
    # registered alongside, so the manager/API can drive them too.
    assert {"ryd", "sponsorblock", "fanart", "opensubtitles"} <= ids
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
    assert "tmdb" in paused and "tvdb" not in paused  # only running ones (tvdb was user-paused)
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


# ── Discover (browse TMDB by curated list / genre / year / decade) ────────────

def test_tmdb_curated_parses_with_forced_kind(monkeypatch):
    # A canned TV list → every row forced to kind 'show' (no media_type in body),
    # carrying the backdrop the Discover hero needs.
    captured = {}
    body = {"results": [
        {"id": 7, "name": "Show", "first_air_date": "2022-03-03", "poster_path": "/p.jpg",
         "backdrop_path": "/b.jpg", "vote_average": 8.1, "overview": "O"}]}

    def fake_get(url, **kw):
        captured["url"] = url
        return _Resp(body)
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
    res = TMDBClient("KEY").curated("popular_shows")
    assert "/tv/popular" in captured["url"]
    assert res[0]["kind"] == "show" and res[0]["title"] == "Show" and res[0]["year"] == "2022"
    assert res[0]["backdrop"] == "https://image.tmdb.org/t/p/w780/b.jpg"
    assert res[0]["poster"] == "https://image.tmdb.org/t/p/w300/p.jpg"


def test_tmdb_curated_unknown_key_returns_empty():
    # An unrecognised list key never hits the network.
    assert TMDBClient("KEY").curated("not_a_real_list") == []


def test_tmdb_discover_builds_filtered_params(monkeypatch):
    # genre + decade → with_genres + a date *range*; sort passes through.
    captured = {}
    body = {"results": [
        {"id": 1, "title": "A", "release_date": "2015-01-01", "poster_path": "/a.jpg",
         "backdrop_path": "/b.jpg", "vote_average": 7.5, "overview": "O"}]}

    def fake_get(url, **kw):
        captured["url"] = url
        captured["params"] = kw.get("params")
        return _Resp(body)
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))
    res = TMDBClient("KEY").discover("movie", genre=28, decade=2010, sort_by="vote_average.desc")
    assert "/discover/movie" in captured["url"]
    p = captured["params"]
    assert p["with_genres"] == 28 and p["sort_by"] == "vote_average.desc"
    assert p["primary_release_date.gte"] == "2010-01-01" and p["primary_release_date.lte"] == "2019-12-31"
    assert res[0] == {"kind": "movie", "tmdb_id": 1, "title": "A", "year": "2015",
                      "rating": 7.5, "overview": "O",
                      "poster": "https://image.tmdb.org/t/p/w300/a.jpg",
                      "backdrop": "https://image.tmdb.org/t/p/w780/b.jpg"}


def test_tmdb_discover_tv_uses_first_air_date_year(monkeypatch):
    # The TV side filters on first_air_date_year, not primary_release_year.
    captured = {}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        get=lambda u, **k: (captured.update(url=u, params=k.get("params")), _Resp({"results": []}))[1]))
    TMDBClient("KEY").discover("show", year=2020)
    assert "/discover/tv" in captured["url"]
    assert captured["params"]["first_air_date_year"] == 2020
    assert "primary_release_year" not in captured["params"]


def test_tmdb_genres_parses(monkeypatch):
    body = {"genres": [{"id": 28, "name": "Action"}, {"id": 12, "name": "Adventure"},
                       {"name": "NoId"}]}     # rows without an id are dropped
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    assert TMDBClient("KEY").genres("movie") == [{"id": 28, "name": "Action"},
                                                 {"id": 12, "name": "Adventure"}]


def test_engine_discover_curated_annotates_and_caches(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Owned", "tmdb_id": 1})
    calls = []

    class Tmdb:
        enabled = True
        def curated(self, key, page=1):
            calls.append((key, page))
            return [{"kind": "movie", "tmdb_id": 1, "title": "Owned"},
                    {"kind": "movie", "tmdb_id": 2, "title": "Nope"}]
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    res = eng.discover_curated("popular_movies")
    assert res[0]["library_id"] == mid and res[1]["library_id"] is None
    eng.discover_curated("popular_movies")
    assert calls == [("popular_movies", 1)]            # second call served from cache


def test_engine_discover_filter_normalizes_kind_and_passes_filters(db):
    captured = {}

    class Tmdb:
        enabled = True
        def discover(self, kind, *, genre=None, year=None, decade=None, providers=None,
                     sort_by="popularity.desc", page=1, region="US"):
            captured.update(kind=kind, genre=genre, decade=decade, providers=providers, sort_by=sort_by)
            return [{"kind": "movie", "tmdb_id": 9, "title": "X"}]
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    res = eng.discover_filter("bogus", genre=28, decade=2010, providers=8, sort_by="vote_average.desc")
    assert captured["kind"] == "movie"                 # unknown kind normalised
    assert captured["genre"] == 28 and captured["decade"] == 2010 and captured["providers"] == 8
    assert captured["sort_by"] == "vote_average.desc"
    assert res[0]["library_id"] is None                # not owned → None


def test_tmdb_discover_provider_sets_watch_region(monkeypatch):
    captured = {}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        get=lambda u, **k: (captured.update(params=k.get("params")), _Resp({"results": []}))[1]))
    TMDBClient("KEY").discover("movie", providers=8, region="GB")
    p = captured["params"]
    assert p["with_watch_providers"] == 8 and p["watch_region"] == "GB"
    assert p["with_watch_monetization_types"] == "flatrate"


def test_engine_genre_list_caches(db):
    calls = []

    class Tmdb:
        enabled = True
        def genres(self, kind):
            calls.append(kind)
            return [{"id": 28, "name": "Action"}]
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    assert eng.genre_list("movie")[0]["name"] == "Action"
    eng.genre_list("movie")
    assert calls == ["movie"]                           # long-cached → one call


def test_engine_discover_disabled_worker_returns_empty(db):
    class Off:
        enabled = False
    eng = VideoEnrichmentEngine(db, {"tmdb": Off()})
    assert eng.discover_curated("popular_movies") == []
    assert eng.discover_filter("movie", genre=1) == []
    assert eng.genre_list("movie") == []


def test_engine_discover_swallows_client_errors(db):
    # A TMDB blip must yield an empty shelf, never crash the page.
    class Boom:
        enabled = True
        def curated(self, key, page=1): raise RuntimeError("tmdb down")
        def discover(self, *a, **k): raise RuntimeError("tmdb down")
        def genres(self, kind): raise RuntimeError("tmdb down")
    eng = VideoEnrichmentEngine(db, {"tmdb": Boom()})
    assert eng.discover_curated("popular_movies") == []
    assert eng.discover_filter("movie", genre=1) == []
    assert eng.genre_list("movie") == []


def test_library_ids_for_tmdb_batched(db):
    m1 = db.upsert_movie("plex", {"server_id": "m1", "title": "A", "tmdb_id": 10})
    m2 = db.upsert_movie("plex", {"server_id": "m2", "title": "B", "tmdb_id": 20})
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "S", "tmdb_id": 30, "seasons": []})
    assert db.library_ids_for_tmdb("movie", [10, 20, 99]) == {10: m1, 20: m2}   # 99 not owned
    assert db.library_ids_for_tmdb("show", [30, 31]) == {30: sid}
    assert db.library_ids_for_tmdb("movie", []) == {}
    assert db.library_ids_for_tmdb("bogus", [10]) == {}


def test_library_ids_for_tmdb_scopes_by_server(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "A", "tmdb_id": 10})
    db.upsert_movie("jellyfin", {"server_id": "j1", "title": "A", "tmdb_id": 11})
    got = db.library_ids_for_tmdb("movie", [10, 11], server_source="plex")
    assert 10 in got and 11 not in got                     # the other server's copy doesn't count


def test_stamp_owned_is_one_query_per_kind(db, monkeypatch):
    # The whole rail must cost one ownership query per kind, not one per item.
    db.upsert_movie("plex", {"server_id": "m1", "title": "Owned", "tmdb_id": 1})
    calls = {"n": 0}
    real = db.library_ids_for_tmdb
    def counted(kind, ids, server_source=None):
        calls["n"] += 1
        return real(kind, ids, server_source)
    monkeypatch.setattr(db, "library_ids_for_tmdb", counted)
    eng = VideoEnrichmentEngine(db, {})
    items = [{"kind": "movie", "tmdb_id": i, "title": "T%d" % i} for i in range(1, 21)]
    items.append({"kind": "show", "tmdb_id": 99, "title": "Show"})
    eng._stamp_owned(items)
    assert calls["n"] == 2                                  # one movie query + one show query
    assert items[0]["library_id"] is not None and items[1]["library_id"] is None


def test_tmdb_recommendations_parses_with_forced_kind(monkeypatch):
    body = {"results": [
        {"id": 5, "title": "Rec", "release_date": "2019-01-01", "poster_path": "/r.jpg",
         "backdrop_path": "/b.jpg", "vote_average": 7.0, "overview": "O"}]}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    res = TMDBClient("KEY").recommendations("movie", 1)
    assert res[0]["kind"] == "movie" and res[0]["tmdb_id"] == 5 and res[0]["year"] == "2019"


def test_tmdb_video_trailer_prefers_trailer_over_teaser(monkeypatch):
    body = {"results": [
        {"site": "YouTube", "type": "Teaser", "key": "teasekey", "name": "Teaser"},
        {"site": "Vimeo", "type": "Trailer", "key": "nope"},          # wrong site, ignored
        {"site": "YouTube", "type": "Trailer", "key": "trailkey", "name": "Official"}]}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    assert TMDBClient("KEY").video_trailer("movie", 1) == {"key": "trailkey", "name": "Official"}


def test_tmdb_video_trailer_falls_back_to_teaser(monkeypatch):
    body = {"results": [{"site": "YouTube", "type": "Teaser", "key": "teasekey", "name": "T"}]}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp(body)))
    assert TMDBClient("KEY").video_trailer("show", 1) == {"key": "teasekey", "name": "T"}
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=lambda u, **k: _Resp({"results": []})))
    assert TMDBClient("KEY").video_trailer("movie", 1) is None     # nothing → None


def test_engine_recommendations_annotates_and_caches(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Owned", "tmdb_id": 1})
    calls = []

    class Tmdb:
        enabled = True
        def recommendations(self, kind, tmdb_id, page=1):
            calls.append((kind, tmdb_id, page))
            return [{"kind": "movie", "tmdb_id": 1, "title": "Owned"},
                    {"kind": "movie", "tmdb_id": 2, "title": "Other"}]
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    res = eng.recommendations("movie", 99)
    assert res[0]["library_id"] == mid and res[1]["library_id"] is None
    eng.recommendations("movie", 99)
    assert calls == [("movie", 99, 1)]                              # cached on repeat


def test_engine_trailer_caches(db):
    calls = []

    class Tmdb:
        enabled = True
        def video_trailer(self, kind, tmdb_id):
            calls.append((kind, tmdb_id)); return {"key": "abc", "name": "T"}
    eng = VideoEnrichmentEngine(db, {"tmdb": Tmdb()})
    assert eng.trailer("movie", 5)["key"] == "abc"
    eng.trailer("movie", 5)
    assert calls == [("movie", 5)]


def test_engine_trailer_none_when_no_video(db):
    class Tmdb:
        enabled = True
        def video_trailer(self, kind, tmdb_id): return None
    assert VideoEnrichmentEngine(db, {"tmdb": Tmdb()}).trailer("movie", 5) is None


def test_random_owned_titles_only_owned_with_tmdb(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "Owned", "tmdb_id": 1, "file": {"relative_path": "a.mkv"}})
    db.upsert_movie("plex", {"server_id": "m2", "title": "NoFile", "tmdb_id": 2})            # not owned
    db.upsert_movie("plex", {"server_id": "m3", "title": "NoTmdb", "file": {"relative_path": "c.mkv"}})  # no tmdb
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "tmdb_id": 9, "seasons": []})
    seeds = db.random_owned_titles(5, server_source="plex")
    by_title = {s["title"]: s for s in seeds}
    assert "Owned" in by_title and by_title["Owned"]["tmdb_id"] == 1
    assert "Show" in by_title                                        # library shows seed too
    assert "NoFile" not in by_title and "NoTmdb" not in by_title


def test_top_owned_genres_orders_by_count(db):
    db.upsert_movie("plex", {"server_id": "m1", "title": "A", "tmdb_id": 1,
                             "genres": ["Action", "Drama"], "file": {"relative_path": "a.mkv"}})
    db.upsert_movie("plex", {"server_id": "m2", "title": "B", "tmdb_id": 2,
                             "genres": ["Action"], "file": {"relative_path": "b.mkv"}})
    db.upsert_movie("plex", {"server_id": "m3", "title": "C", "tmdb_id": 3,
                             "genres": ["Comedy"]})           # no file → not owned → excluded
    g = db.top_owned_genres("movie", server_source="plex", limit=5)
    assert g[0] == "Action"                                  # owned twice → first
    assert "Drama" in g and "Comedy" not in g                # Comedy movie isn't owned
