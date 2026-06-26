"""Auto-retry decision engine — next_query / plan_retry / merge_candidates. Pure,
isolated from music."""

from __future__ import annotations

import json

from core.video.retry import MAX_ATTEMPTS, merge_candidates, next_query, plan_retry


def test_next_query_movie_drops_year_then_exhausts():
    ctx = {"scope": "movie", "title": "Dune", "year": 2021}
    assert next_query(ctx, []) == "Dune 2021"
    assert next_query(ctx, ["Dune 2021"]) == "Dune"          # fall back to no-year
    assert next_query(ctx, ["Dune 2021", "Dune"]) is None    # exhausted


def test_next_query_tv_variants():
    ep = {"scope": "episode", "title": "The Wire", "season": 2, "episode": 5}
    assert next_query(ep, []) == "The Wire S02E05"
    assert next_query(ep, ["The Wire S02E05"]) == "The Wire 2x05"
    se = {"scope": "season", "title": "The Wire", "season": 2}
    assert next_query(se, []) == "The Wire S02"
    assert next_query(se, ["The Wire S02"]) == "The Wire Season 2"


def _row(**kw):
    base = {"attempts": 0, "candidates": "[]", "tried_files": "[]",
            "search_ctx": json.dumps({"scope": "movie", "title": "Dune", "year": 2021}),
            "tried_queries": json.dumps(["Dune 2021"])}
    base.update(kw)
    return base


def test_plan_retry_next_candidate_first():
    cands = [{"filename": "a.mkv", "username": "u"}, {"filename": "b.mkv", "username": "v"}]
    plan = plan_retry(_row(candidates=json.dumps(cands)))
    assert plan["action"] == "candidate" and plan["candidate"]["filename"] == "a.mkv"
    assert [c["filename"] for c in plan["rest"]] == ["b.mkv"]


def test_plan_retry_skips_already_tried_candidate():
    cands = [{"filename": "a.mkv"}, {"filename": "b.mkv"}]
    plan = plan_retry(_row(candidates=json.dumps(cands), tried_files=json.dumps(["a.mkv"])))
    assert plan["action"] == "candidate" and plan["candidate"]["filename"] == "b.mkv"


def test_plan_retry_requeries_when_candidates_exhausted():
    plan = plan_retry(_row(candidates="[]"))   # no candidates, year-query already tried
    assert plan["action"] == "requery" and plan["query"] == "Dune"   # the no-year variant


def test_plan_retry_fails_when_everything_exhausted():
    plan = plan_retry(_row(candidates="[]", tried_queries=json.dumps(["Dune 2021", "Dune"])))
    assert plan["action"] == "fail"


def test_plan_retry_respects_budget():
    cands = [{"filename": "a.mkv"}]
    plan = plan_retry(_row(candidates=json.dumps(cands), attempts=MAX_ATTEMPTS))
    assert plan["action"] == "fail" and "budget" in plan["reason"]


def test_merge_candidates_dedupes_against_tried():
    new = [{"filename": "a.mkv", "username": "u", "title": "Dune.2021.1080p"},
           {"filename": "b.mkv", "username": "v"}, {"filename": "a.mkv", "username": "w"}]
    out = merge_candidates(new, ["b.mkv"])
    assert [c["filename"] for c in out] == ["a.mkv"]   # b excluded (tried), dup a collapsed
    assert out[0]["release_title"] == "Dune.2021.1080p"


# ── bounded search stops its slskd search (so they don't pile up) ─────────────
def test_search_for_retry_stops_the_slskd_search(monkeypatch):
    """The bounded auto-grab search MUST stop its slskd search when done — slskd otherwise
    keeps each running ~60s, and back-to-back searches pile up and swamp it. Stopped even on
    an early break (12+ hits arrive fast)."""
    import core.video.slskd_search as ss
    from core.video import download_monitor as dm
    stopped = []
    monkeypatch.setattr(ss, "start_search", lambda q: {"id": "S1"})
    monkeypatch.setattr(ss, "poll_search", lambda sid: {"hits": list(range(12)), "total_files": 12})
    monkeypatch.setattr(ss, "stop_search", lambda sid: stopped.append(sid))
    res = dm._search_for_retry("Dune 2021")
    assert len(res["hits"]) == 12 and stopped == ["S1"]      # early-broke AND stopped


def test_search_for_retry_no_id_skips_stop(monkeypatch):
    import core.video.slskd_search as ss
    from core.video import download_monitor as dm
    stopped = []
    monkeypatch.setattr(ss, "start_search", lambda q: {"configured": True, "error": "boom"})
    monkeypatch.setattr(ss, "stop_search", lambda sid: stopped.append(sid))
    assert dm._search_for_retry("x") == {"hits": [], "total_files": 0}
    assert stopped == []                                     # nothing started → nothing to stop
