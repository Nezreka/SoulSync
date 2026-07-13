"""Rolling re-enrichment automation: keep matched library metadata from going stale.

Every run re-pulls the N STALEST matched movies/shows (oldest-refreshed first) by their
stored TMDB id, skipping anything inside its per-kind freshness floor (movies 30d, shows
14d by default — TV drifts faster). Covers the DB staleness query, the pure handler with
its seams injected, and the seed/block wiring contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.automation.handlers.video_reenrich_stale import auto_video_reenrich_stale
from database.video_database import VideoDatabase


# ── fakes ───────────────────────────────────────────────────────────────────────
class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _logs(deps):
    return " ".join(p.get("log_line") or "" for p in deps.progress)


# ── handler ─────────────────────────────────────────────────────────────────────
def test_refreshes_each_item_by_kind_and_tallies():
    items = [{"kind": "movie", "id": 1, "title": "A"},
             {"kind": "show", "id": 2, "title": "B"},
             {"kind": "movie", "id": 3, "title": "C"}]
    seen = []

    def refresh(kind, item_id):
        seen.append((kind, item_id))
        return {"ok": item_id != 2}                      # the show fails to refresh

    deps = _Deps()
    res = auto_video_reenrich_stale(
        {"_automation_id": "a"}, deps,
        fetch_stale=lambda limit, md, sd: items, refresh_item=refresh, sleep=lambda s: None)
    assert res["status"] == "completed" and res["items"] == 3
    assert res["refreshed"] == 2 and res["failed"] == 1
    assert seen == [("movie", 1), ("show", 2), ("movie", 3)]   # every item attempted, in order
    assert "Refreshed 2 item(s)" in _logs(deps) and "1 failed" in _logs(deps)


def test_config_knobs_flow_through_to_the_query():
    captured = {}

    def fetch(limit, movie_days, show_days):
        captured.update(limit=limit, movie_days=movie_days, show_days=show_days)
        return []

    auto_video_reenrich_stale(
        {"_automation_id": "a", "batch_size": 250, "movie_stale_days": 45, "show_stale_days": 7},
        _Deps(), fetch_stale=fetch)
    assert captured == {"limit": 250, "movie_days": 45, "show_days": 7}


def test_defaults_when_config_absent_or_junk():
    captured = {}

    def fetch(limit, movie_days, show_days):
        captured.update(limit=limit, movie_days=movie_days, show_days=show_days)
        return []

    auto_video_reenrich_stale(
        {"_automation_id": "a", "batch_size": "oops", "movie_stale_days": None},
        _Deps(), fetch_stale=fetch)
    assert captured == {"limit": 500, "movie_days": 30, "show_days": 14}


def test_nothing_stale_is_a_clean_noop():
    deps = _Deps()
    res = auto_video_reenrich_stale({"_automation_id": "a"}, deps,
                                    fetch_stale=lambda l, m, s: [])
    assert res["status"] == "completed" and res["items"] == 0 and res["refreshed"] == 0
    assert not any(p.get("status") == "error" for p in deps.progress)
    assert "fresh" in _logs(deps).lower()


def test_one_item_raising_does_not_stop_the_rest():
    def refresh(kind, item_id):
        if item_id == 1:
            raise RuntimeError("tmdb timeout")
        return {"ok": True}

    res = auto_video_reenrich_stale(
        {"_automation_id": "a"}, _Deps(),
        fetch_stale=lambda l, m, s: [{"kind": "movie", "id": 1, "title": "A"},
                                     {"kind": "show", "id": 2, "title": "B"}],
        refresh_item=refresh, sleep=lambda s: None)
    assert res["status"] == "completed" and res["refreshed"] == 1 and res["failed"] == 1


def test_top_level_error_is_caught():
    def boom(l, m, s):
        raise RuntimeError("db down")

    res = auto_video_reenrich_stale({"_automation_id": "x"}, _Deps(), fetch_stale=boom)
    assert res["status"] == "error" and "db down" in res["error"]


def test_pause_is_between_items_only_not_after_the_last():
    naps = []
    auto_video_reenrich_stale(
        {"_automation_id": "a"}, _Deps(),
        fetch_stale=lambda l, m, s: [{"kind": "movie", "id": i, "title": str(i)} for i in (1, 2, 3)],
        refresh_item=lambda k, i: {"ok": True}, sleep=lambda s: naps.append(s))
    assert len(naps) == 2                                 # 3 items → 2 gaps, no trailing sleep


def test_default_refresh_routes_movie_vs_show(monkeypatch):
    import core.automation.handlers.video_reenrich_stale as mod
    seen = {}

    class _Eng:
        def refresh_movie_art(self, i):
            seen["movie"] = i
            return {"ok": True}

        def refresh_show_art(self, i, *, with_ratings=True):
            seen["show"], seen["with_ratings"] = i, with_ratings
            return {"ok": True}

    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine", lambda: _Eng())
    assert mod._default_refresh("movie", 7) == {"ok": True}
    assert mod._default_refresh("show", 9) == {"ok": True}
    assert seen == {"movie": 7, "show": 9, "with_ratings": True}   # shows re-pull WITH ratings


# ── DB staleness query ──────────────────────────────────────────────────────────
@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed_movie(db, server_id, title, tmdb_id):
    return db.upsert_movie("plex", {"server_id": server_id, "title": title, "tmdb_id": tmdb_id})


def _seed_show(db, server_id, title, tmdb_id):
    return db.upsert_show_tree("plex", {"server_id": server_id, "title": title, "tmdb_id": tmdb_id,
                                        "seasons": []})


def _mark(db, table, row_id, *, days_ago, status="matched"):
    """Force a row's match status + how long ago it was last refreshed."""
    ts = None if days_ago is None else (
        datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db._get_connection()
    conn.execute("UPDATE %s SET tmdb_match_status=?, tmdb_last_attempted=? WHERE id=?" % table,
                 (status, ts, row_id))
    conn.commit()
    conn.close()


def test_stale_query_respects_per_kind_floors_and_orders_oldest_first(db):
    m_old = _seed_movie(db, "m1", "OldMovie", 101);  _mark(db, "movies", m_old, days_ago=40)
    m_new = _seed_movie(db, "m2", "FreshMovie", 102); _mark(db, "movies", m_new, days_ago=10)
    s_old = _seed_show(db, "s1", "OldShow", 201);    _mark(db, "shows", s_old, days_ago=20)
    s_new = _seed_show(db, "s2", "FreshShow", 202);  _mark(db, "shows", s_new, days_ago=5)

    out = db.stale_enriched_items(limit=10, movie_days=30, show_days=14)
    ids = [(r["kind"], r["id"]) for r in out]
    # movie 10d ago is inside its 30d floor → skip; show 5d ago inside 14d → skip.
    # movie 40d and show 20d are overdue; oldest-refreshed first → movie(40) before show(20).
    assert ids == [("movie", m_old), ("show", s_old)]


def test_stale_query_excludes_unmatched_and_includes_never_stamped(db):
    m_unmatched = _seed_movie(db, "m1", "NoMatch", 101); _mark(db, "movies", m_unmatched,
                                                               days_ago=99, status="not_found")
    m_null = _seed_movie(db, "m2", "NeverStamped", 102); _mark(db, "movies", m_null, days_ago=None)
    m_ok = _seed_movie(db, "m3", "Stale", 103);          _mark(db, "movies", m_ok, days_ago=40)

    out = db.stale_enriched_items(limit=10, movie_days=30, show_days=14)
    ids = [r["id"] for r in out]
    assert m_unmatched not in ids                         # not matched → never re-enriched
    assert m_null in ids and m_ok in ids                  # NULL last_attempted counts as stale
    assert ids[0] == m_null                               # NULL sorts first (oldest)


def test_stale_query_caps_at_limit(db):
    for i in range(5):
        mid = _seed_movie(db, "m%d" % i, "M%d" % i, 100 + i)
        _mark(db, "movies", mid, days_ago=40 + i)
    out = db.stale_enriched_items(limit=2, movie_days=30, show_days=14)
    assert len(out) == 2


# ── wiring contract ─────────────────────────────────────────────────────────────
def test_seeded_as_a_six_hourly_video_automation():
    import core.automation_engine as ae
    row = next((a for a in ae.SYSTEM_AUTOMATIONS if a.get("action_type") == "video_reenrich_stale"), None)
    assert row is not None
    assert row["owned_by"] == "video"
    assert row["trigger_type"] == "schedule"
    assert row["trigger_config"] == {"interval": 6, "unit": "hours"}
    assert row["action_config"]["batch_size"] == 500


def test_handler_is_registered():
    src = (__import__("pathlib").Path(__file__).resolve().parent.parent
           / "core" / "automation" / "handlers" / "registration.py").read_text(encoding="utf-8")
    assert "'video_reenrich_stale'" in src
    assert "auto_video_reenrich_stale" in src


def test_block_exposes_the_three_config_fields():
    from core.automation.blocks import ACTIONS, blocks_for_scope
    block = next((b for b in ACTIONS if b.get("type") == "video_reenrich_stale"), None)
    assert block is not None and block["scope"] == "video"
    keys = {f["key"] for f in block.get("config_fields", [])}
    assert keys == {"batch_size", "movie_stale_days", "show_stale_days"}
    # and it surfaces on the video Automations page (not the music one)
    vid = {b["type"] for b in blocks_for_scope("video")["actions"]}
    music = {b["type"] for b in blocks_for_scope("music")["actions"]}
    assert "video_reenrich_stale" in vid and "video_reenrich_stale" not in music
