"""Import lists (arr-parity P6) — recurring auto-add from external lists.

The sync is driven with an injected fetcher; the key semantic under test is
the per-list SEEN set: only members NEW to the list are added, so a user's
removal never boomerangs back on the next sync (the thing Radarr users have
to fight with exclusion lists).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

import core.video.import_lists as il
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    import api.video as videoapi
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    yield d
    videoapi._video_db = None


def _mk(db, **kw):
    base = {"name": "Trending", "source": "tmdb_chart", "ref": "trending_movies",
            "media": "both", "monitor": "future"}
    base.update(kw)
    return il.save_list(db, base)


# ---------------------------------------------------------------------------
# Config store
# ---------------------------------------------------------------------------

def test_normalize_and_crud(db):
    assert il.save_list(db, {"source": "nope", "ref": "x"}) is None
    assert il.save_list(db, {"source": "tmdb_list", "ref": ""}) is None       # needs a ref
    assert il.save_list(db, {"source": "plex_watchlist", "ref": ""}) is not None   # except plex
    e = _mk(db, monitor="ALL", media="MOVIE", limit="999999")
    assert e["monitor"] == "all" and e["media"] == "movie" and e["limit"] == 500
    assert len(il.load_lists(db)) == 2
    assert il.delete_list(db, e["id"]) is True
    assert len(il.load_lists(db)) == 1


# ---------------------------------------------------------------------------
# Sync semantics
# ---------------------------------------------------------------------------

def _members(*specs):
    return [{"kind": k, "tmdb_id": t, "title": n, "year": 2020} for k, t, n in specs]


def test_sync_adds_movies_and_shows(db):
    _mk(db)
    feed = _members(("movie", 1, "Heat"), ("show", 2, "Severance"))
    out = il.sync(fetch=lambda e: feed)
    assert out["status"] == "completed"
    assert out["added_movies"] == 1 and out["added_shows"] == 1
    assert db.wishlist_counts().get("movie") == 1
    assert db.watchlist_states("show").get(2) == "follow"


def test_removals_never_boomerang_back(db):
    _mk(db)
    feed = _members(("movie", 1, "Heat"))
    il.sync(fetch=lambda e: feed)
    # the user changes their mind and clears the wish
    db.remove_from_wishlist("movie", tmdb_id=1)
    out = il.sync(fetch=lambda e: feed)
    assert out["added_movies"] == 0
    assert db.wishlist_counts().get("movie", 0) == 0
    # a NEW member still lands
    out2 = il.sync(fetch=lambda e: feed + _members(("movie", 3, "Ronin")))
    assert out2["added_movies"] == 1


def test_fetch_failure_leaves_seen_set_untouched(db):
    _mk(db)
    il.sync(fetch=lambda e: _members(("movie", 1, "Heat")))
    out = il.sync(fetch=lambda e: None)      # a broken tick
    assert out["added_movies"] == 0
    # next healthy tick: 1 is still seen, 2 is new
    out2 = il.sync(fetch=lambda e: _members(("movie", 1, "Heat"), ("movie", 2, "Fargo")))
    assert out2["added_movies"] == 1


def test_media_filter_and_profile_stamp(db):
    import core.video.quality_profile as qp
    prof = qp.save_named(db, None, "4K", {"cutoff_resolution": "2160p"})
    _mk(db, media="movie", quality_profile_id=prof["id"])
    il.sync(fetch=lambda e: _members(("movie", 1, "Heat"), ("show", 2, "Severance")))
    assert db.wishlist_counts().get("movie") == 1
    assert db.watchlist_states("show") == {}          # shows filtered out
    items = db.movie_wishlist_to_download()
    assert items[0]["quality_profile_id"] == prof["id"]


def test_monitor_policy_expands_for_new_shows(db, monkeypatch):
    class _Eng:
        def tmdb_detail(self, kind, tid):
            return {"seasons": [{"season_number": 1}]}

        def tmdb_season(self, tid, sn):
            return {"episodes": [{"episode_number": 1, "title": "Pilot", "air_date": "2025-01-01"}]}
    import core.video.enrichment.engine as eng_mod
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Eng())
    _mk(db, media="show", monitor="pilot")
    il.sync(fetch=lambda e: _members(("show", 2, "Severance")))
    assert db.wishlist_counts().get("episode") == 1


def test_no_lists_skips_and_overlap_guard(db):
    assert il.sync(fetch=lambda e: [])["reason"] == "no_lists"
    il._running = True
    try:
        assert il.sync(fetch=lambda e: [])["reason"] == "already_running"
    finally:
        il._running = False


# ---------------------------------------------------------------------------
# API + wiring + UI contracts
# ---------------------------------------------------------------------------

def test_api_crud(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    c = app.test_client()
    created = c.post("/api/video/downloads/config/import-lists",
                     json={"name": "IMDb Top", "source": "imdb_list", "ref": "ls000000001"}).get_json()
    assert created["success"] and created["id"] == 1
    assert c.get("/api/video/downloads/config/import-lists").get_json()["lists"][0]["name"] == "IMDb Top"
    assert c.post("/api/video/downloads/config/import-lists", json={"source": "bad"}).status_code == 400
    assert c.delete("/api/video/downloads/config/import-lists/1").get_json()["success"]


def test_wiring_and_ui():
    import core.automation.blocks as blocks_mod
    import core.automation.handlers.registration as reg
    import core.automation_engine as eng_mod
    assert '"type": "video_import_lists"' in open(blocks_mod.__file__, encoding="utf-8").read()
    assert "'video_import_lists'" in open(reg.__file__, encoding="utf-8").read()
    assert "'action_type': 'video_import_lists'" in open(eng_mod.__file__, encoding="utf-8").read()
    assert 'id="vq-implist-rows"' in _INDEX and "data-vq-implist-add" in _INDEX
    assert "IMPLIST_URL" in _SETTINGS_JS and "loadImportLists()" in _SETTINGS_JS
